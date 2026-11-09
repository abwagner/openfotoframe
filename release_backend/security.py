"""Opaque credentials, request identity and shared collection/frame authorization."""

import hashlib
import hmac
import secrets
import time
from functools import wraps
from flask import current_app, g, request
from sqlalchemy import select, and_, update, text
from werkzeug.exceptions import HTTPException
import bcrypt
from . import models as m


class APIError(HTTPException):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = status
        self.error_code = code


def fail(code, message, status=400):
    raise APIError(code, message, status)


def credential_install_origin():
    """Native requests omit Origin; browser credential installation must be same-origin."""
    origin = request.headers.get("Origin")
    if (
        origin is not None and origin != current_app.config["PUBLIC_ORIGIN"]
    ) or request.headers.get("Sec-Fetch-Site") == "cross-site":
        fail("invalid_origin", "This sign-in request must come from this server.", 403)


def now():
    return int(current_app.config.get("CLOCK", time.time)())


def uid():
    return secrets.token_hex(16)


def secret():
    return secrets.token_urlsafe(32)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(value):
    if not isinstance(value, str) or len(value) < 12 or len(value.encode()) > 72:
        fail("invalid_password", "Use a password between 12 and 72 UTF-8 bytes.")
    return bcrypt.hashpw(value.encode(), bcrypt.gensalt()).decode()


def verify_password(user, value):
    if not isinstance(value, str) or len(value.encode()) > 72:
        return False
    stored = user["password_hash"]
    if stored.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(value.encode(), stored.encode())
        except ValueError:
            return False
    # Preserve the original application's salt + password SHA256 migration.
    candidate = hashlib.sha256(
        ((user.get("legacy_salt") or "") + value).encode()
    ).hexdigest()
    return hmac.compare_digest(candidate, stored)


def json_body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        fail("invalid_request", "A JSON object is required.")
    return body


def bounded_name(value, default=""):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        if default:
            return default
        fail("invalid_name", "A nonempty name of at most 200 characters is required.")
    return value.strip()


def email(value):
    if (
        not isinstance(value, str)
        or len(value) > 320
        or "@" not in value
        or any(c.isspace() for c in value)
    ):
        fail("invalid_email", "Enter a valid email address.")
    return value.strip().lower()


def set_scope(household_id=None, frame_id=None):
    current_app.extensions["release_store"].set_scope(
        g.release_db, (g.release_user or {}).get("id"), household_id, frame_id
    )


def rate_limit(purpose, identifier="", limit=10, seconds=60):
    # Persistent, per-origin request source and operation. Do not trust forwarded
    # addresses unless the explicitly configured deployment proxy validates them.
    key = digest(purpose + ":" + (request.remote_addr or "") + ":" + identifier)
    db = g.release_db
    if db.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(key[:15], 16)}
        )
    row = (
        db.execute(
            select(m.rate_limits)
            .where(m.rate_limits.c.key_hash == key)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    window = now() // seconds
    count = row["attempts"] + 1 if row and row["window"] == window else 1
    if row:
        db.execute(
            update(m.rate_limits)
            .where(m.rate_limits.c.key_hash == key)
            .values(window=window, attempts=count)
        )
    else:
        db.execute(
            m.rate_limits.insert().values(key_hash=key, window=window, attempts=count)
        )
    g.release_commit_on_error = True
    if count > limit:
        fail("rate_limited", "Too many attempts. Try again later.", 429)


def require_auth(function=None):
    if function is not None:

        @wraps(function)
        def wrapped(*args, **kwargs):
            require_auth()
            return function(*args, **kwargs)

        return wrapped
    db = g.release_db
    authorization = request.headers.get("Authorization", "")
    browser = False
    if authorization and not authorization.startswith("Bearer "):
        fail("unauthorized", "A device credential is required.", 401)
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        if not token or len(token) > 200:
            fail("unauthorized", "Sign in again.", 401)
        row = (
            db.execute(
                select(m.sessions).where(m.sessions.c.access_hash == digest(token))
            )
            .mappings()
            .first()
        )
        if not row or row["access_expires"] <= now():
            fail("unauthorized", "Sign in again.", 401)
    else:
        token = request.cookies.get("release_session", "")
        web = (
            db.execute(
                select(m.web_sessions).where(
                    m.web_sessions.c.token_hash == digest(token)
                )
            )
            .mappings()
            .first()
            if token
            else None
        )
        if not web or web["expires_at"] <= now():
            fail("unauthorized", "Sign in again.", 401)
        row = (
            db.execute(select(m.sessions).where(m.sessions.c.id == web["session_id"]))
            .mappings()
            .first()
        )
        browser = True
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            csrf = (
                request.headers.get("X-CSRFToken")
                or request.form.get("csrf_token")
                or ""
            )
            if not hmac.compare_digest(web["csrf_hash"], digest(csrf)):
                fail("csrf_failed", "The web session could not be verified.", 403)
    if not row or row["revoked"] or row["refresh_expires"] <= now():
        fail("unauthorized", "Sign in again.", 401)
    user = (
        db.execute(select(m.users).where(m.users.c.id == row["user_id"]))
        .mappings()
        .first()
    )
    if not user or user["disabled"] or user["must_change_password"]:
        fail("unauthorized", "Sign in again.", 401)
    from .auth import requires_mfa, allowed_mfa_methods

    g.release_user = dict(user)
    if requires_mfa(user) and (
        not row["mfa_verified"]
        or (
            row["mfa_method"] not in allowed_mfa_methods(user)
            and row["mfa_method"] != "recovery"
        )
    ):
        fail("mfa_required", "Sign in and complete multi-factor authentication.", 401)
    g.release_session = dict(row)
    g.release_browser = browser
    set_scope()
    return g.release_user


def household(hid, manage=False, owner=False):
    db = g.release_db
    set_scope(hid)
    if db.dialect.name == "postgresql":
        membership = (
            db.execute(
                text("SELECT * FROM release_lock_membership(:hid)"), {"hid": hid}
            )
            .mappings()
            .first()
        )
    else:
        membership = (
            db.execute(
                select(m.memberships)
                .where(
                    and_(
                        m.memberships.c.household_id == hid,
                        m.memberships.c.user_id == g.release_user["id"],
                        m.memberships.c.active.is_(True),
                    )
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
    if not membership:
        fail("not_found", "Not found.", 404)
    if (owner and membership["role"] != "owner") or (
        manage and membership["role"] not in ("owner", "admin")
    ):
        fail("forbidden", "You do not have permission for this action.", 403)
    return dict(membership)


def collection(hid, cid, action="read"):
    membership = household(hid)
    row = (
        g.release_db.execute(
            select(m.collections).where(
                and_(m.collections.c.household_id == hid, m.collections.c.id == cid)
            )
        )
        .mappings()
        .first()
    )
    if not row:
        fail("not_found", "Not found.", 404)
    role = membership["role"]
    if role == "member":
        grant = (
            g.release_db.execute(
                select(m.collection_grants).where(
                    and_(
                        m.collection_grants.c.household_id == hid,
                        m.collection_grants.c.membership_id == membership["id"],
                        m.collection_grants.c.collection_id == cid,
                    )
                )
            )
            .mappings()
            .first()
        )
        if not grant:
            fail("not_found", "Not found.", 404)
        role = grant["role"]
    allowed = {
        "read": ("viewer", "contributor", "editor", "admin", "owner"),
        "upload": ("contributor", "editor", "admin", "owner"),
        "edit": ("editor", "admin", "owner"),
        "purge": ("admin", "owner"),
    }
    if role not in allowed[action]:
        fail("forbidden", "You do not have permission for this action.", 403)
    return dict(row), role


def frame(hid, fid, action="read"):
    membership = household(hid)
    row = (
        g.release_db.execute(
            select(m.frames).where(
                and_(m.frames.c.household_id == hid, m.frames.c.id == fid)
            )
        )
        .mappings()
        .first()
    )
    if not row:
        fail("not_found", "Not found.", 404)
    role = membership["role"]
    if role == "member":
        grant = (
            g.release_db.execute(
                select(m.frame_grants).where(
                    and_(
                        m.frame_grants.c.household_id == hid,
                        m.frame_grants.c.membership_id == membership["id"],
                        m.frame_grants.c.frame_id == fid,
                    )
                )
            )
            .mappings()
            .first()
        )
        if not grant:
            fail("not_found", "Not found.", 404)
        role = grant["role"]
    if action == "control" and role not in ("controller", "owner", "admin"):
        fail("forbidden", "You do not have permission for this action.", 403)
    return dict(row), role


def recent_auth():
    if g.release_session["authenticated_at"] < now() - 300:
        fail("reauthentication_required", "Sign in again before continuing.", 403)


def audit(hid, action, resource_id=None):
    g.release_db.execute(
        m.audit_events.insert().values(
            id=uid(),
            household_id=hid,
            actor_id=g.release_user["id"],
            action=action,
            resource_id=resource_id,
            created_at=now(),
        )
    )
