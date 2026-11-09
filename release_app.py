"""Explicit opt-in entry point; importing the legacy app never enables release mode.

Run only in an isolated rehearsal environment:
    gunicorn 'release_app:create_app()'
"""

import os
import json
from pathlib import Path
from urllib.parse import urlsplit


def environment_config(environ=None):
    env = os.environ if environ is None else environ

    def boolean(key, default=False):
        value = env.get(key, "true" if default else "false").lower()
        if value not in ("true", "false"):
            raise ValueError(f"{key} must be true or false.")
        return value == "true"

    mode = env.get("RELEASE_MODE", "")
    if mode not in ("self_hosted", "hosted"):
        raise ValueError("Set RELEASE_MODE to self_hosted or hosted explicitly.")
    secret = env.get("SECRET_KEY", "")
    if len(secret) < 32:
        raise ValueError("Configure a private SECRET_KEY of at least 32 characters.")
    origin = env.get("PUBLIC_ORIGIN", "").rstrip("/")
    parts = urlsplit(origin)
    try:
        port = parts.port
    except ValueError:
        raise ValueError("PUBLIC_ORIGIN must have a valid port.") from None
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path
        or port == 0
    ):
        raise ValueError(
            "PUBLIC_ORIGIN must be the server root origin without credentials or a path."
        )
    if mode == "hosted" and parts.scheme != "https":
        raise ValueError("Hosted service requires an HTTPS PUBLIC_ORIGIN.")
    database = env.get("DATABASE_URL", "")
    if not database:
        if mode == "hosted":
            raise ValueError(
                "Hosted service requires a configured PostgreSQL DATABASE_URL."
            )
        database = "sqlite:///" + str(
            Path(env.get("DATA_ROOT", "/data")).resolve() / "release.sqlite3"
        )
    if mode == "hosted" and not database.startswith(
        ("postgresql://", "postgresql+psycopg://")
    ):
        raise ValueError("Hosted service requires PostgreSQL.")
    if database.startswith("postgresql://"):
        database = database.replace("postgresql://", "postgresql+psycopg://", 1)
    config = {
        "RELEASE_MODE": mode,
        "DATABASE_URL": database,
        "SECRET_KEY": secret,
        "PUBLIC_ORIGIN": origin,
        "MEDIA_ROOT": env.get("MEDIA_ROOT", "/data/media"),
        "BOOTSTRAP_SECRET": env.get("BOOTSTRAP_SECRET", ""),
        "REGISTER_ENABLED": boolean("REGISTER_ENABLED"),
        "MFA_ENCRYPTION_KEY": env.get("MFA_ENCRYPTION_KEY", ""),
        "LEGACY_MFA_KEY": env.get("LEGACY_MFA_KEY", ""),
        "REQUIRE_MFA": boolean("REQUIRE_MFA"),
        "MFA_METHODS": env.get("MFA_METHODS", "either"),
        "INIT_SCHEMA": False,
        "SERVER_ID": env.get("SERVER_ID") or None,
        "STORAGE_BACKEND": env.get(
            "STORAGE_BACKEND", "s3" if mode == "hosted" else "filesystem"
        ),
        "S3_BUCKET": env.get("S3_BUCKET") or None,
        "S3_PREFIX": env.get("S3_PREFIX", ""),
        "S3_ENDPOINT_URL": env.get("S3_ENDPOINT_URL") or None,
        "SESSION_COOKIE_SECURE": parts.scheme == "https",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_NAME": "release_context",
        "APP_LINK_TEAM_ID": env.get("APP_LINK_TEAM_ID", ""),
        "APP_LINK_SCHEME": env.get("APP_LINK_SCHEME", "openfotoframe"),
        "APP_LINK_BUNDLE_ID": env.get(
            "APP_LINK_BUNDLE_ID", "tech.swagner.openfotoframe"
        ),
    }
    for key in (
        "BILLING_PLANS",
        "APPLE_ROOT_CERTIFICATES",
        "BILLING_CHECKOUT_HOSTS",
        "AUTH_REDIRECT_URIS",
    ):
        if env.get(key):
            try:
                value = json.loads(env[key])
            except (ValueError, TypeError):
                raise ValueError(f"{key} must be a JSON array.") from None
            if not isinstance(value, list):
                raise ValueError(f"{key} must be a JSON array.")
            config[key] = value
    if config["APP_LINK_SCHEME"] not in ("openfotoframe", "openfotoframe-dev"):
        raise ValueError(
            "APP_LINK_SCHEME must identify the production or development app."
        )
    if "AUTH_REDIRECT_URIS" in config:
        allowed = {"openfotoframe://auth/callback", "openfotoframe-dev://auth/callback"}
        values = config["AUTH_REDIRECT_URIS"]
        if not values or any(
            not isinstance(value, str) or value not in allowed for value in values
        ):
            raise ValueError(
                "AUTH_REDIRECT_URIS must contain explicitly supported app callbacks."
            )
    for key in (
        "BILLING_DATABASE_URL",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_SUCCESS_URL",
        "STRIPE_CANCEL_URL",
        "APPLE_BUNDLE_ID",
        "APPLE_ENVIRONMENT",
        "APPLE_SIGNING_KEY_PATH",
        "APPLE_KEY_ID",
        "APPLE_ISSUER_ID",
        "WEBAUTHN_RP_ID",
        "SCHEDULE_TIMEZONE",
    ):
        if env.get(key):
            config[key] = env[key]
    if env.get("APPLE_APP_ID"):
        try:
            config["APPLE_APP_ID"] = int(env["APPLE_APP_ID"])
        except ValueError:
            raise ValueError("APPLE_APP_ID must be an integer.") from None
    config["STRIPE_LIVE_MODE"] = boolean("STRIPE_LIVE_MODE")
    for key in (
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM",
        "SMTP_TLS_MODE",
    ):
        if env.get(key):
            config[key] = env[key]
    if env.get("SMTP_PORT"):
        try:
            config["SMTP_PORT"] = int(env["SMTP_PORT"])
        except ValueError:
            raise ValueError("SMTP_PORT must be an integer.") from None
    if config["MFA_METHODS"] not in ("either", "totp", "passkey"):
        raise ValueError("MFA_METHODS must be either, totp, or passkey.")
    return config


def create_app(config=None):
    from release_backend import create_app as create_backend

    app = create_backend(environment_config() if config is None else config)
    from release_delivery import register as register_delivery

    register_delivery(app)
    from release_web import register

    if "release_compat" not in app.blueprints:
        register(app)
    return app
