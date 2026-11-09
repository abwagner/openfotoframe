"""Opt-in release server. Importing this module never loads or migrates legacy data."""

from urllib.parse import urlparse
import secrets
from flask import Flask, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, DBAPIError
from werkzeug.exceptions import HTTPException
from .store import Store
from . import models as m
from .security import APIError
from .storage import StorageError


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        RELEASE_MODE="self_hosted",
        DATABASE_URL=None,
        MEDIA_ROOT=None,
        PUBLIC_ORIGIN=None,
        SECRET_KEY=None,
        BOOTSTRAP_SECRET=None,
        REGISTER_ENABLED=False,
        REQUIRE_MFA=False,
        MAX_UPLOAD_BYTES=25 * 1024 * 1024,
        MAX_CONTENT_LENGTH=26 * 1024 * 1024,
        SESSION_COOKIE_NAME="release_context",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=True,
        INIT_SCHEMA=False,
        SERVER_ID=None,
        STORAGE_BACKEND="filesystem",
        S3_BUCKET=None,
        S3_PREFIX="",
        S3_ENDPOINT_URL=None,
    )
    app.config.update(config or {})
    mode = app.config["RELEASE_MODE"]
    if mode not in ("self_hosted", "hosted"):
        raise ValueError("RELEASE_MODE must be self_hosted or hosted")
    origin = urlparse(app.config.get("PUBLIC_ORIGIN") or "")
    if (
        origin.scheme not in ("http", "https")
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
    ):
        raise ValueError(
            "PUBLIC_ORIGIN must be an explicit server origin without credentials or path"
        )
    if mode == "hosted" and origin.scheme != "https":
        raise ValueError("Hosted PUBLIC_ORIGIN requires HTTPS")
    if len(app.config.get("SECRET_KEY") or "") < 32:
        raise ValueError("SECRET_KEY must contain at least 32 characters")
    database = app.config.get("DATABASE_URL")
    if not database:
        raise ValueError("DATABASE_URL is required")
    if (
        mode == "hosted"
        and not database.startswith("postgresql")
        and not app.config.get("TESTING")
    ):
        raise ValueError(
            "Hosted mode requires PostgreSQL with enforced row-level security"
        )
    from .storage import FileStorage, S3Storage

    storage = app.config.get("MEDIA_STORAGE")
    if storage is None:
        if app.config["STORAGE_BACKEND"] == "s3":
            storage = S3Storage(
                app.config["S3_BUCKET"],
                app.config["S3_PREFIX"],
                app.config["S3_ENDPOINT_URL"],
            )
        elif app.config["STORAGE_BACKEND"] == "filesystem":
            if mode == "hosted" and not app.config.get("TESTING"):
                raise ValueError("Hosted mode requires private S3 storage")
            if not app.config.get("MEDIA_ROOT"):
                raise ValueError("MEDIA_ROOT is required")
            storage = FileStorage(app.config["MEDIA_ROOT"])
        else:
            raise ValueError("Unsupported STORAGE_BACKEND")
    if mode == "hosted" and not app.config.get("TESTING"):
        if not hasattr(storage, "validate_private"):
            raise ValueError(
                "Hosted storage must verify its private delivery configuration"
            )
        storage.validate_private()
    app.extensions["release_storage"] = storage
    try:
        from release_billing import register as billing_register
    except ImportError:
        billing_register = None
    store = Store(database)
    app.extensions["release_store"] = store
    if app.config["INIT_SCHEMA"]:
        if mode == "hosted" and not app.config.get("TESTING"):
            raise ValueError(
                "Initialize hosted schema separately with a migration role"
            )
        store.create_schema()
    if mode == "hosted" and not app.config.get("TESTING"):
        store.validate_application_role()
    if app.config.get("MFA_ENCRYPTION_KEY"):
        from cryptography.fernet import Fernet

        Fernet(app.config["MFA_ENCRYPTION_KEY"])
    elif app.config.get("REQUIRE_MFA"):
        raise ValueError("Required MFA needs a configured MFA_ENCRYPTION_KEY")
    app.config["SESSION_COOKIE_SECURE"] = origin.scheme == "https"
    app.config["PUBLIC_ORIGIN"] = app.config["PUBLIC_ORIGIN"].rstrip("/")
    # Stable issuing-server identity is a deployment setting; random startup IDs
    # would invalidate saved client profiles on each process restart.
    if not app.config["SERVER_ID"]:
        with store.transaction() as db:
            row = (
                db.execute(
                    select(m.server_metadata).where(
                        m.server_metadata.c.key == "server_id"
                    )
                )
                .mappings()
                .first()
            )
            if row:
                app.config["SERVER_ID"] = row["value"]["id"]
            else:
                app.config["SERVER_ID"] = secrets.token_hex(16)
                db.execute(
                    m.server_metadata.insert().values(
                        key="server_id", value={"id": app.config["SERVER_ID"]}
                    )
                )

    @app.before_request
    def transaction():
        g.release_db = store.engine.connect()
        g.release_transaction = g.release_db.begin()
        store.set_scope(g.release_db)
        g.release_conn = g.release_db
        g.release_user = None
        g.release_session = None
        g.release_commit_on_error = False
        g.release_created_media = []
        if (
            not app.config.get("TESTING")
            and request.host.lower() != origin.netloc.lower()
        ):
            raise APIError(
                "invalid_host", "Connect using this server’s configured address.", 400
            )

    def cleanup_media():
        for key in getattr(g, "release_created_media", []):
            try:
                app.extensions["release_storage"].delete(key)
            except (StorageError, OSError):
                # The metadata transaction is already rolled back. Any retained
                # generated object remains private and needs operator cleanup.
                app.logger.warning("Private media rollback cleanup requires retry")

    @app.after_request
    def finish(response):
        tx = getattr(g, "release_transaction", None)
        if tx and tx.is_active:
            if response.status_code < 400 or getattr(
                g, "release_commit_on_error", False
            ):
                tx.commit()
            else:
                tx.rollback()
                cleanup_media()
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; script-src-attr 'none'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.teardown_request
    def close(error):
        if getattr(g, "release_nested_dispatch", 0):
            return
        db = getattr(g, "release_db", None)
        if db:
            if db.in_transaction():
                db.rollback()
            db.close()
        if error:
            cleanup_media()

    @app.errorhandler(APIError)
    def api_error(error):
        return jsonify(
            error={"code": error.error_code, "message": error.description}
        ), error.code

    @app.errorhandler(StorageError)
    @app.errorhandler(OSError)
    def storage_failure(error):
        g.release_commit_on_error = False
        return jsonify(
            error={
                "code": "storage_unavailable",
                "message": "Private photo storage is temporarily unavailable. Retry the request.",
            }
        ), 503

    @app.errorhandler(IntegrityError)
    def conflict(error):
        # Constraint failures cannot leave a half-applied membership or group.
        g.release_commit_on_error = False
        return jsonify(
            error={
                "code": "conflict",
                "message": "The operation conflicts with existing data.",
            }
        ), 409

    @app.errorhandler(DBAPIError)
    def database_failure(error):
        # RLS/serialization failures must roll back, never leave a request in an
        # aborted transaction or expose SQL parameters in an HTTP response.
        g.release_commit_on_error = False
        return jsonify(
            error={
                "code": "operation_failed",
                "message": "The operation could not be completed. Retry the request.",
            }
        ), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(
            error={
                "code": "not_found" if error.code == 404 else "request_failed",
                "message": "Not found." if error.code == 404 else error.description,
            }
        ), error.code

    from .auth import bp as auth
    from .invitations import bp as invitations
    from .content import bp as content
    from .jobs import bp as jobs
    from .passkeys import bp as passkeys
    from .schedules import bp as schedules

    app.register_blueprint(auth)
    app.register_blueprint(invitations)
    app.register_blueprint(content)
    app.register_blueprint(jobs)
    app.register_blueprint(passkeys)
    app.register_blueprint(schedules)
    # Optional billing integration shares the same transaction and policy service.
    if billing_register:
        billing_register(app)
    return app
