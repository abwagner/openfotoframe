"""First-party device authentication. Credentials never appear in URLs or logs."""

import hmac
from flask import Blueprint, current_app, g, jsonify, request, session, redirect
from sqlalchemy import select, and_, or_, update, text
from cryptography.fernet import Fernet, InvalidToken
import pyotp
from . import models as m
from .security import (
    require_auth,
    fail,
    json_body,
    now,
    uid,
    secret,
    digest,
    password_hash,
    verify_password,
    rate_limit,
    email,
    bounded_name,
    household,
    collection,
    recent_auth,
    set_scope,
)

bp = Blueprint("release_auth", __name__, url_prefix="/api/v1")


def public_user(user):
    return {k: user[k] for k in ("id", "email", "email_verified")}


def identity(user, session_id):
    current_app.extensions["release_store"].set_scope(g.release_db, user["id"])
    rows = (
        g.release_db.execute(
            select(m.households.c.id, m.households.c.name, m.memberships.c.role)
            .join(m.memberships, m.households.c.id == m.memberships.c.household_id)
            .where(
                and_(
                    m.memberships.c.user_id == user["id"],
                    m.memberships.c.active.is_(True),
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "user": public_user(user),
        "session_id": session_id,
        "households": [dict(row) for row in rows],
    }


def issue_session(user, device_name, mfa_verified=False, mfa_method=None):
    access, refresh = secret(), secret()
    sid = uid()
    g.release_db.execute(
        m.sessions.insert().values(
            id=sid,
            user_id=user["id"],
            device_name=str(device_name or "New device")[:120],
            access_hash=digest(access),
            access_expires=now() + 900,
            refresh_hash=digest(refresh),
            refresh_expires=now() + 30 * 86400,
            revoked=False,
            authenticated_at=now(),
            mfa_verified=mfa_verified,
            mfa_method=mfa_method,
            created_at=now(),
        )
    )
    return dict(
        identity(user, sid), access_token=access, refresh_token=refresh, expires_in=900
    )


def challenge(user_id, purpose, data=None, ttl=300):
    token = secret()
    g.release_db.execute(
        m.challenges.insert().values(
            id=uid(),
            token_hash=digest(token),
            user_id=user_id,
            purpose=purpose,
            data=data or {},
            expires_at=now() + ttl,
            attempts=0,
            consumed=False,
        )
    )
    return token


def get_challenge(token, purpose):
    if not isinstance(token, str) or len(token) > 200:
        fail("invalid_challenge", "This request has expired. Start again.", 401)
    row = (
        g.release_db.execute(
            select(m.challenges)
            .where(
                and_(
                    m.challenges.c.token_hash == digest(token),
                    m.challenges.c.purpose == purpose,
                )
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if not row or row["consumed"] or row["expires_at"] <= now() or row["attempts"] >= 5:
        fail("invalid_challenge", "This request has expired. Start again.", 401)
    return dict(row)


def validate_mfa(user, code):
    if not isinstance(code, str) or len(code) > 100:
        return False
    # Lock the current factor record, not a stale request snapshot. Recovery and
    # TOTP counters must be consumed atomically across concurrent devices.
    current = (
        g.release_db.execute(
            select(m.users).where(m.users.c.id == user["id"]).with_for_update()
        )
        .mappings()
        .first()
    )
    if not current or current["disabled"]:
        return False
    data = dict(current["mfa"] or {})
    codes = list(data.get("recovery_code_hashes", []))
    # Legacy recovery codes were lower-case hexadecimal with a hyphen. New
    # opaque codes remain case-sensitive; accept exact input and the established
    # legacy normalization without weakening new credentials.
    candidates = (digest(code), digest(code.strip().lower()))
    for saved in codes:
        if any(hmac.compare_digest(saved, candidate) for candidate in candidates):
            codes.remove(saved)
            data["recovery_code_hashes"] = codes
            g.release_db.execute(
                update(m.users).where(m.users.c.id == user["id"]).values(mfa=data)
            )
            g.release_factor = "recovery"
            return True
    encrypted = data.get("totp_secret")
    if encrypted:
        for key in (
            current_app.config.get("MFA_ENCRYPTION_KEY"),
            current_app.config.get("LEGACY_MFA_KEY"),
        ):
            if key:
                try:
                    plain = Fernet(key).decrypt(encrypted.encode()).decode()
                    totp = pyotp.TOTP(plain)
                    counter = int(__import__("time").time()) // totp.interval
                    last = int(data.get("last_totp_counter", -1))
                    for candidate in range(counter - 1, counter + 2):
                        if candidate > last and hmac.compare_digest(
                            totp.at(candidate * totp.interval), code.strip()
                        ):
                            data["last_totp_counter"] = candidate
                            g.release_db.execute(
                                update(m.users)
                                .where(m.users.c.id == user["id"])
                                .values(mfa=data)
                            )
                            g.release_factor = "totp"
                            return True
                except (InvalidToken, ValueError):
                    continue
    return False


def requires_mfa(user):
    if current_app.config.get("REQUIRE_MFA"):
        return True
    current_app.extensions["release_store"].set_scope(g.release_db, user["id"])
    rows = (
        g.release_db.execute(
            select(m.households.c.settings, m.memberships.c.role)
            .join(m.memberships, m.memberships.c.household_id == m.households.c.id)
            .where(
                and_(
                    m.memberships.c.user_id == user["id"],
                    m.memberships.c.active.is_(True),
                )
            )
        )
        .mappings()
        .all()
    )
    return any(
        row["settings"].get("mfa_mode") in ("required", "required_all")
        or (
            row["settings"].get("mfa_mode") in ("admin_required", "required_admins")
            and row["role"] in ("owner", "admin")
        )
        for row in rows
    )


def allowed_mfa_methods(user):
    allowed = {"totp", "passkey"}
    configured = current_app.config.get("MFA_METHODS", "either")
    policies = [configured]
    current_app.extensions["release_store"].set_scope(g.release_db, user["id"])
    rows = (
        g.release_db.execute(
            select(m.households.c.settings)
            .join(m.memberships, m.memberships.c.household_id == m.households.c.id)
            .where(
                and_(
                    m.memberships.c.user_id == user["id"],
                    m.memberships.c.active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    policies.extend(value.get("mfa_methods", "either") for value in rows)
    for policy in policies:
        if policy in ("totp", "totp_only"):
            allowed &= {"totp"}
        elif policy in ("passkey", "passkey_only"):
            allowed &= {"passkey"}
        elif policy != "either":
            fail(
                "invalid_mfa_policy",
                "The server’s MFA policy requires operator review.",
                503,
            )
    if not allowed:
        fail(
            "incompatible_mfa_policy",
            "Your households have incompatible MFA policies. Contact their owners.",
            403,
        )
    return allowed


def finish_login(user, device_name):
    mfa = user["mfa"] or {}
    allowed = allowed_mfa_methods(user)
    if mfa.get("totp_secret") or mfa.get("passkeys"):
        from .passkeys import available

        methods = []
        if mfa.get("totp_secret") and "totp" in allowed:
            methods.append("totp")
        if mfa.get("passkeys") and "passkey" in allowed and available():
            methods.append("passkey")
        if mfa.get("recovery_code_hashes"):
            methods.append("recovery")
        if not methods:
            fail(
                "passkeys_unavailable",
                "This account requires its original HTTPS passkey origin or operator-assisted recovery.",
                403,
            )
        return jsonify(
            mfa_required=True,
            methods=methods,
            challenge_token=challenge(user["id"], "mfa", {"device_name": device_name}),
        ), 202
    if requires_mfa(user) and not mfa.get("totp_secret") and not mfa.get("passkeys"):
        return jsonify(
            mfa_enrollment_required=True,
            methods=sorted(allowed),
            challenge_token=challenge(
                user["id"], "enroll_mfa", {"device_name": device_name}
            ),
        ), 202
    if user["must_change_password"]:
        return jsonify(
            password_change_required=True,
            challenge_token=challenge(
                user["id"], "password_change", {"device_name": device_name}
            ),
        ), 202
    return jsonify(issue_session(user, device_name))


@bp.get("/server")
def server():
    from .passkeys import available

    return jsonify(
        api_version=1,
        minimum_client_api_version=1,
        server_id=current_app.config["SERVER_ID"],
        deployment_mode=current_app.config["RELEASE_MODE"],
        capabilities={
            "password_login": True,
            "mfa": True,
            "invites": True,
            "pairing": True,
            "web_bridge": True,
            "scoped_collections": True,
            "scoped_frames": True,
            "passkeys": available(),
            "auth_code_pkce": current_app.config["PUBLIC_ORIGIN"].startswith(
                "https://"
            ),
        },
        registration_enabled=bool(current_app.config["REGISTER_ENABLED"]),
        bootstrap_required=g.release_db.execute(
            select(m.server_metadata).where(
                m.server_metadata.c.key == "bootstrap_complete"
            )
        ).first()
        is None,
    )


@bp.post("/auth/login")
def login():
    body = json_body()
    login_id = str(body.get("email", "")).strip().lower()
    rate_limit("login_ip", limit=60)
    rate_limit("login", login_id)
    user = (
        g.release_db.execute(
            select(m.users)
            .where(or_(m.users.c.email == login_id, m.users.c.username == login_id))
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if not user or user["disabled"] or not verify_password(user, body.get("password")):
        fail("invalid_credentials", "Email or password was not recognized.", 401)
    if not user["password_hash"].startswith(("$2a$", "$2b$", "$2y$")):
        g.release_db.execute(
            update(m.users)
            .where(m.users.c.id == user["id"])
            .values(password_hash=password_hash(body["password"]), legacy_salt=None)
        )
    return finish_login(dict(user), str(body.get("device_name", "New device"))[:120])


@bp.post("/auth/mfa")
def complete_mfa():
    body = json_body()
    rate_limit("mfa", limit=20)
    pending = get_challenge(body.get("challenge_token"), "mfa")
    user = (
        g.release_db.execute(
            select(m.users).where(m.users.c.id == pending["user_id"]).with_for_update()
        )
        .mappings()
        .first()
    )
    if (
        not user
        or user["disabled"]
        or (
            "totp" not in allowed_mfa_methods(user)
            and not {
                digest(str(body.get("code", ""))),
                digest(str(body.get("code", "")).strip().lower()),
            }.intersection((user["mfa"] or {}).get("recovery_code_hashes", []))
        )
        or not validate_mfa(user, body.get("code"))
    ):
        g.release_db.execute(
            update(m.challenges)
            .where(m.challenges.c.id == pending["id"])
            .values(attempts=pending["attempts"] + 1)
        )
        fail("invalid_code", "The authentication code was not recognized.", 401)
    g.release_db.execute(
        update(m.challenges)
        .where(m.challenges.c.id == pending["id"])
        .values(consumed=True)
    )
    if user["must_change_password"]:
        return jsonify(
            password_change_required=True,
            challenge_token=challenge(
                user["id"],
                "password_change",
                dict(
                    pending["data"],
                    mfa_verified=True,
                    mfa_method=getattr(g, "release_factor", "totp"),
                ),
            ),
        ), 202
    return jsonify(
        issue_session(
            user,
            pending["data"].get("device_name"),
            mfa_verified=True,
            mfa_method=getattr(g, "release_factor", "totp"),
        )
    )


@bp.post("/auth/change-password")
def change_password():
    body = json_body()
    rate_limit("change_password", limit=10)
    pending = get_challenge(body.get("challenge_token"), "password_change")
    user = (
        g.release_db.execute(select(m.users).where(m.users.c.id == pending["user_id"]))
        .mappings()
        .first()
    )
    if not user or user["disabled"]:
        fail("invalid_challenge", "This request has expired.", 401)
    hashed = password_hash(body.get("password"))
    g.release_commit_on_error = False
    g.release_db.execute(
        update(m.challenges)
        .where(m.challenges.c.id == pending["id"])
        .values(consumed=True)
    )
    g.release_db.execute(
        update(m.users)
        .where(m.users.c.id == user["id"])
        .values(password_hash=hashed, legacy_salt=None, must_change_password=False)
    )
    g.release_db.execute(
        update(m.sessions)
        .where(m.sessions.c.user_id == user["id"])
        .values(revoked=True)
    )
    return jsonify(
        issue_session(
            dict(user),
            pending["data"].get("device_name"),
            mfa_verified=pending["data"].get("mfa_verified", False),
            mfa_method=pending["data"].get("mfa_method"),
        )
    )


@bp.post("/auth/refresh")
def refresh():
    body = json_body()
    rate_limit("refresh", limit=60)
    token = body.get("refresh_token", "")
    if not isinstance(token, str) or len(token) > 200:
        fail("unauthorized", "Sign in again.", 401)
    hashed = digest(token)
    row = (
        g.release_db.execute(
            select(m.sessions)
            .where(m.sessions.c.refresh_hash == hashed)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if not row:
        previous = (
            g.release_db.execute(
                select(m.refresh_history).where(
                    m.refresh_history.c.token_hash == hashed
                )
            )
            .mappings()
            .first()
        )
        if previous:
            g.release_db.execute(
                update(m.sessions)
                .where(m.sessions.c.id == previous["session_id"])
                .values(revoked=True)
            )
        fail("unauthorized", "Sign in again.", 401)
    user = (
        g.release_db.execute(select(m.users).where(m.users.c.id == row["user_id"]))
        .mappings()
        .first()
    )
    if (
        row["revoked"]
        or row["refresh_expires"] <= now()
        or not user
        or user["disabled"]
        or user["must_change_password"]
    ):
        fail("unauthorized", "Sign in again.", 401)
    access, new_refresh = secret(), secret()
    g.release_db.execute(
        m.refresh_history.insert().values(token_hash=hashed, session_id=row["id"])
    )
    g.release_db.execute(
        update(m.sessions)
        .where(m.sessions.c.id == row["id"])
        .values(
            access_hash=digest(access),
            access_expires=now() + 900,
            refresh_hash=digest(new_refresh),
        )
    )
    return jsonify(
        dict(
            identity(user, row["id"]),
            access_token=access,
            refresh_token=new_refresh,
            expires_in=900,
        )
    )


@bp.get("/auth/me")
@require_auth
def me():
    return jsonify(identity(g.release_user, g.release_session["id"]))


@bp.post("/auth/logout")
@require_auth
def logout():
    g.release_db.execute(
        update(m.sessions)
        .where(m.sessions.c.id == g.release_session["id"])
        .values(revoked=True)
    )
    session.clear()
    response = jsonify(ok=True)
    response.delete_cookie("release_session")
    return response


@bp.get("/auth/sessions")
@require_auth
def list_sessions():
    rows = (
        g.release_db.execute(
            select(
                m.sessions.c.id,
                m.sessions.c.device_name,
                m.sessions.c.created_at,
                m.sessions.c.revoked,
            ).where(m.sessions.c.user_id == g.release_user["id"])
        )
        .mappings()
        .all()
    )
    return jsonify(sessions=[dict(row) for row in rows])


@bp.delete("/auth/sessions/<sid>")
@require_auth
def revoke_session(sid):
    result = g.release_db.execute(
        update(m.sessions)
        .where(
            and_(m.sessions.c.id == sid, m.sessions.c.user_id == g.release_user["id"])
        )
        .values(revoked=True)
    )
    if not result.rowcount:
        fail("not_found", "Not found.", 404)
    return jsonify(ok=True)


@bp.post("/auth/bootstrap")
def bootstrap():
    body = json_body()
    rate_limit("bootstrap", limit=5)
    expected = current_app.config.get("BOOTSTRAP_SECRET")
    if (
        current_app.config["RELEASE_MODE"] != "self_hosted"
        or not expected
        or not hmac.compare_digest(str(body.get("bootstrap_secret", "")), expected)
    ):
        fail("invalid_bootstrap", "Bootstrap was not authorized.", 403)
    if g.release_db.dialect.name == "postgresql":
        g.release_db.execute(text("SELECT pg_advisory_xact_lock(8294771401)"))
    if g.release_db.execute(
        select(m.server_metadata).where(m.server_metadata.c.key == "bootstrap_complete")
    ).first():
        fail("already_initialized", "This server already has an owner.", 409)
    account = email(body.get("email"))
    hashed = password_hash(body.get("password"))
    g.release_commit_on_error = False
    user = {
        "id": uid(),
        "email": account,
        "username": None,
        "email_verified": False,
        "password_hash": hashed,
        "legacy_salt": None,
        "mfa": {},
        "disabled": False,
        "must_change_password": False,
        "created_at": now(),
    }
    g.release_db.execute(m.users.insert().values(**user))
    hid = uid()
    g.release_user = user
    set_scope(hid)
    name = bounded_name(body.get("household_name"), "My household")
    if g.release_db.dialect.name == "postgresql":
        g.release_db.execute(
            text("SELECT release_create_household(:hid,:name,:mid,:cid)"),
            {"hid": hid, "name": name, "mid": uid(), "cid": uid()},
        )
    else:
        g.release_db.execute(
            m.households.insert().values(
                id=hid, name=name, settings={}, created_at=now()
            )
        )
        g.release_db.execute(
            m.memberships.insert().values(
                id=uid(),
                household_id=hid,
                user_id=user["id"],
                role="owner",
                active=True,
            )
        )
        g.release_db.execute(
            m.collections.insert().values(
                id=uid(), household_id=hid, name="Photos", settings={}
            )
        )
    g.release_db.execute(
        m.server_metadata.insert().values(
            key="bootstrap_complete", value={"household_id": hid}
        )
    )
    return finish_login(user, body.get("device_name"))


@bp.post("/auth/register")
def register():
    body = json_body()
    rate_limit("register", limit=5)
    account = email(body.get("email"))
    hashed = password_hash(body.get("password"))
    invitation = body.get("invitation_token")
    if not current_app.config["REGISTER_ENABLED"] and not invitation:
        fail("registration_disabled", "Registration requires an invitation.", 403)
    if g.release_db.execute(
        select(m.users.c.id).where(m.users.c.email == account)
    ).first():
        fail(
            "registration_failed",
            "Account creation could not be completed. Try signing in or recovering your account.",
            409,
        )
    # An email-bound invite cannot grant access before mailbox verification.
    # Registration returns an account/session; accept after verified-email step.
    if invitation:
        from .invitations import lookup_invitation

        lookup_invitation(invitation)
    g.release_commit_on_error = False
    user = {
        "id": uid(),
        "email": account,
        "username": None,
        "email_verified": False,
        "password_hash": hashed,
        "legacy_salt": None,
        "mfa": {},
        "disabled": False,
        "must_change_password": False,
        "created_at": now(),
    }
    g.release_db.execute(m.users.insert().values(**user))
    if invitation:
        from .invitations import accept_invitation

        g.release_user = user
        row = lookup_invitation(invitation)
        if not row["recipient_email"]:
            accept_invitation(invitation)
    return finish_login(user, body.get("device_name"))


@bp.post("/auth/verification/request")
@require_auth
def request_verification():
    rate_limit("verification", g.release_user["id"], limit=3, seconds=3600)
    delivery = current_app.config.get("VERIFICATION_DELIVERY")
    if not callable(delivery):
        fail(
            "verification_unavailable",
            "Email verification is not configured on this server.",
            503,
        )
    token = challenge(g.release_user["id"], "verify_email", ttl=3600)
    delivery(g.release_user["email"], token)
    return jsonify(ok=True)


@bp.post("/auth/verification/complete")
@require_auth
def complete_verification():
    body = json_body()
    pending = get_challenge(body.get("token"), "verify_email")
    if pending["user_id"] != g.release_user["id"]:
        fail("invalid_challenge", "This verification link is invalid.", 403)
    g.release_db.execute(
        update(m.challenges)
        .where(m.challenges.c.id == pending["id"])
        .values(consumed=True)
    )
    g.release_db.execute(
        update(m.users)
        .where(m.users.c.id == g.release_user["id"])
        .values(email_verified=True)
    )
    return jsonify(ok=True)


@bp.post("/auth/recovery/request")
def request_recovery():
    body = json_body()
    account = str(body.get("email", "")).lower()
    rate_limit("recovery", account, limit=3, seconds=3600)
    delivery = current_app.config.get("RECOVERY_DELIVERY")
    user = (
        g.release_db.execute(select(m.users).where(m.users.c.email == account))
        .mappings()
        .first()
    )
    if user and not user["disabled"] and callable(delivery):
        token = challenge(user["id"], "recovery", ttl=1800)
        delivery(account, token)
    return jsonify(
        ok=True,
        message="If recovery is available for this account, instructions will be sent. Otherwise contact the server operator.",
    )


@bp.post("/auth/recovery/complete")
def complete_recovery():
    body = json_body()
    rate_limit("recovery_complete", limit=10)
    pending = get_challenge(body.get("token"), "recovery")
    user = (
        g.release_db.execute(select(m.users).where(m.users.c.id == pending["user_id"]))
        .mappings()
        .first()
    )
    if not user or user["disabled"]:
        fail("invalid_challenge", "This recovery link is invalid.", 401)
    hashed = password_hash(body.get("password"))
    # Mailbox reset does not bypass enrolled MFA. Operator-issued recovery clears
    # the factor separately through an audited offline command if truly lost.
    if any(
        (user["mfa"] or {}).get(key)
        for key in ("totp_secret", "passkeys", "recovery_code_hashes")
    ) and not validate_mfa(user, body.get("code")):
        g.release_db.execute(
            update(m.challenges)
            .where(m.challenges.c.id == pending["id"])
            .values(attempts=pending["attempts"] + 1)
        )
        fail("mfa_required", "An authenticator or recovery code is required.", 401)
    g.release_commit_on_error = False
    g.release_db.execute(
        update(m.challenges)
        .where(m.challenges.c.id == pending["id"])
        .values(consumed=True)
    )
    g.release_db.execute(
        update(m.users)
        .where(m.users.c.id == user["id"])
        .values(password_hash=hashed, legacy_salt=None, must_change_password=False)
    )
    g.release_db.execute(
        update(m.sessions)
        .where(m.sessions.c.user_id == user["id"])
        .values(revoked=True)
    )
    return jsonify(ok=True)


@bp.post("/auth/web-bridge")
@require_auth
def web_bridge():
    body = json_body()
    hid = body.get("household_id")
    cid = body.get("collection_id")
    fid = body.get("frame_id")
    path = body.get("path", "/upload")
    if path not in ("/upload", "/display", "/workspace"):
        fail("invalid_destination", "Choose a supported web screen.")
    if hid:
        household(hid)
    elif path != "/workspace":
        fail("household_required", "Choose a household.")
    if cid:
        collection(hid, cid)
    if path == "/upload" and not cid:
        fail("collection_required", "Choose a collection.")
    if fid:
        from .security import frame

        frame(hid, fid)
    token = secret()
    scope = {"household_id": hid, "collection_id": cid, "frame_id": fid, "path": path}
    g.release_db.execute(
        m.web_bridges.insert().values(
            token_hash=digest(token),
            session_id=g.release_session["id"],
            scope=scope,
            expires_at=now() + 30,
            consumed=False,
        )
    )
    return jsonify(bridge_token=token, expires_in=30, redirect_path=path)


@bp.post("/auth/web-bridge/redeem")
def redeem_bridge():
    from .security import credential_install_origin

    credential_install_origin()
    body = request.get_json(silent=True) or request.form
    token = body.get("bridge_token", "")
    if not isinstance(token, str) or len(token) > 200:
        fail("invalid_bridge", "This sign-in request has expired.", 401)
    row = (
        g.release_db.execute(
            select(m.web_bridges)
            .where(m.web_bridges.c.token_hash == digest(token))
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if not row or row["consumed"] or row["expires_at"] <= now():
        fail("invalid_bridge", "This sign-in request has expired.", 401)
    parent = (
        g.release_db.execute(
            select(m.sessions).where(m.sessions.c.id == row["session_id"])
        )
        .mappings()
        .first()
    )
    user = (
        g.release_db.execute(select(m.users).where(m.users.c.id == parent["user_id"]))
        .mappings()
        .first()
        if parent
        else None
    )
    if (
        not parent
        or parent["revoked"]
        or parent["refresh_expires"] <= now()
        or not user
        or user["disabled"]
        or user["must_change_password"]
    ):
        fail("invalid_bridge", "Sign in again.", 401)
    if requires_mfa(user) and (
        not parent["mfa_verified"]
        or parent["mfa_method"] not in allowed_mfa_methods(user) | {"recovery"}
    ):
        fail("invalid_bridge", "Sign in and complete multi-factor authentication.", 401)
    scope = row["scope"]
    g.release_user = dict(user)
    g.release_session = dict(parent)
    if scope.get("household_id"):
        household(scope["household_id"])
    if scope.get("collection_id"):
        collection(scope["household_id"], scope["collection_id"])
    if scope.get("frame_id"):
        from .security import frame

        frame(scope["household_id"], scope["frame_id"])
    for key, value in scope.items():
        if key in body and body[key] != value:
            fail("invalid_bridge", "The selected web destination does not match.", 403)
    web_token, csrf = secret(), secret()
    g.release_db.execute(
        update(m.web_bridges)
        .where(m.web_bridges.c.token_hash == row["token_hash"])
        .values(consumed=True)
    )
    g.release_db.execute(
        m.web_sessions.insert().values(
            token_hash=digest(web_token),
            session_id=parent["id"],
            csrf_hash=digest(csrf),
            expires_at=min(now() + 86400, parent["refresh_expires"]),
        )
    )
    session.clear()
    for key, value in scope.items():
        session["release_" + key] = value
    session["release_csrf"] = csrf
    response = redirect(scope["path"], code=303)
    secure = current_app.config["PUBLIC_ORIGIN"].startswith("https://")
    response.set_cookie(
        "release_session",
        web_token,
        httponly=True,
        secure=secure,
        samesite="Strict",
        max_age=86400,
        path="/",
    )
    response.set_cookie(
        "release_csrf",
        csrf,
        httponly=False,
        secure=secure,
        samesite="Strict",
        max_age=86400,
        path="/",
    )
    return response


def enrollment_identity(body):
    if body.get("challenge_token"):
        pending = get_challenge(body["challenge_token"], "enroll_mfa")
        user = (
            g.release_db.execute(
                select(m.users).where(m.users.c.id == pending["user_id"])
            )
            .mappings()
            .first()
        )
        if not user or user["disabled"]:
            fail("invalid_challenge", "This request has expired.", 401)
        return dict(user), pending
    require_auth()
    recent_auth()
    return g.release_user, None


@bp.post("/auth/mfa/enroll/start")
def start_mfa():
    body = json_body()
    rate_limit("enroll_mfa", limit=5)
    user, pending = enrollment_identity(body)
    if "totp" not in allowed_mfa_methods(user):
        fail(
            "passkey_enrollment_required",
            "This server requires passkey enrollment in the browser.",
            403,
        )
    key = current_app.config.get("MFA_ENCRYPTION_KEY")
    if not key:
        fail(
            "mfa_unavailable",
            "An encryption key must be configured by the server operator.",
            503,
        )
    if (user["mfa"] or {}).get("totp_secret"):
        fail("already_enrolled", "An authenticator is already enrolled.", 409)
    value = pyotp.random_base32()
    encrypted = Fernet(key).encrypt(value.encode()).decode()
    token = challenge(
        user["id"],
        "mfa_setup",
        {
            "encrypted_secret": encrypted,
            "enrollment_challenge_id": pending["id"] if pending else None,
            "device_name": (pending or {})
            .get("data", {})
            .get("device_name", "New device"),
        },
    )
    return jsonify(
        setup_token=token,
        secret=value,
        provisioning_uri=pyotp.TOTP(value).provisioning_uri(
            name=user["email"], issuer_name="OpenFotoFrame"
        ),
    )


@bp.post("/auth/mfa/enroll/complete")
def complete_mfa_setup():
    body = json_body()
    rate_limit("enroll_mfa_complete", limit=10)
    pending = get_challenge(body.get("setup_token"), "mfa_setup")
    user = (
        g.release_db.execute(select(m.users).where(m.users.c.id == pending["user_id"]))
        .mappings()
        .first()
    )
    if not user or user["disabled"]:
        fail("invalid_challenge", "This request has expired.", 401)
    enrollment_id = pending["data"].get("enrollment_challenge_id")
    if enrollment_id:
        enrollment = (
            g.release_db.execute(
                select(m.challenges)
                .where(m.challenges.c.id == enrollment_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if (
            not enrollment
            or enrollment["user_id"] != user["id"]
            or enrollment["purpose"] != "enroll_mfa"
            or enrollment["consumed"]
            or enrollment["expires_at"] <= now()
        ):
            fail("invalid_challenge", "This request has expired.", 401)
    else:
        require_auth()
        recent_auth()
        if g.release_user["id"] != user["id"]:
            fail("invalid_challenge", "This request is invalid.", 403)
    value = (
        Fernet(current_app.config["MFA_ENCRYPTION_KEY"])
        .decrypt(pending["data"]["encrypted_secret"].encode())
        .decode()
    )
    if "totp" not in allowed_mfa_methods(user):
        fail(
            "passkey_enrollment_required",
            "This server requires passkey enrollment.",
            403,
        )
    totp = pyotp.TOTP(value)
    counter = int(__import__("time").time()) // totp.interval
    matched_counter = next(
        (
            candidate
            for candidate in range(counter - 1, counter + 2)
            if hmac.compare_digest(
                totp.at(candidate * totp.interval), str(body.get("code", ""))
            )
        ),
        None,
    )
    if matched_counter is None:
        g.release_db.execute(
            update(m.challenges)
            .where(m.challenges.c.id == pending["id"])
            .values(attempts=pending["attempts"] + 1)
        )
        fail("invalid_code", "The authentication code was not recognized.", 401)
    codes = [secret()[:20] for _ in range(10)]
    data = dict(
        user["mfa"] or {},
        totp_secret=pending["data"]["encrypted_secret"],
        last_totp_counter=matched_counter,
        recovery_code_hashes=[digest(code) for code in codes],
    )
    g.release_commit_on_error = False
    g.release_db.execute(
        update(m.users).where(m.users.c.id == user["id"]).values(mfa=data)
    )
    g.release_db.execute(
        update(m.challenges)
        .where(m.challenges.c.id == pending["id"])
        .values(consumed=True)
    )
    if enrollment_id:
        g.release_db.execute(
            update(m.challenges)
            .where(m.challenges.c.id == enrollment_id)
            .values(consumed=True)
        )
    g.release_db.execute(
        update(m.sessions)
        .where(m.sessions.c.user_id == user["id"])
        .values(revoked=True)
    )
    result = {"recovery_codes": codes}
    if user["must_change_password"]:
        result.update(
            password_change_required=True,
            challenge_token=challenge(
                user["id"],
                "password_change",
                {
                    "device_name": pending["data"].get("device_name"),
                    "mfa_verified": True,
                    "mfa_method": "totp",
                },
            ),
        )
    else:
        result.update(
            issue_session(
                user,
                pending["data"].get("device_name"),
                mfa_verified=True,
                mfa_method=getattr(g, "release_factor", "totp"),
            )
        )
    return jsonify(result)


@bp.delete("/auth/mfa")
@require_auth
def remove_mfa():
    recent_auth()
    body = json_body()
    rate_limit("remove_mfa", g.release_user["id"], limit=5)
    if requires_mfa(g.release_user):
        fail(
            "mfa_required",
            "This server or household requires multi-factor authentication.",
            403,
        )
    if not verify_password(g.release_user, body.get("password")) or not validate_mfa(
        g.release_user, body.get("code")
    ):
        fail(
            "invalid_credentials",
            "Password or authentication code was not recognized.",
            401,
        )
    g.release_db.execute(
        update(m.users).where(m.users.c.id == g.release_user["id"]).values(mfa={})
    )
    g.release_db.execute(
        update(m.sessions)
        .where(m.sessions.c.user_id == g.release_user["id"])
        .values(revoked=True)
    )
    return jsonify(ok=True)


@bp.post("/auth/password")
@require_auth
def account_password():
    recent_auth()
    body = json_body()
    rate_limit("password", g.release_user["id"], limit=5)
    hashed = password_hash(body.get("password"))
    if not verify_password(g.release_user, body.get("current_password")):
        fail("invalid_credentials", "The current password was not recognized.", 401)
    if (
        any(
            (g.release_user["mfa"] or {}).get(key)
            for key in ("totp_secret", "passkeys", "recovery_code_hashes")
        )
        and not g.release_session["mfa_verified"]
        and not validate_mfa(g.release_user, body.get("code"))
    ):
        fail("invalid_code", "An authenticator or recovery code is required.", 401)
    g.release_commit_on_error = False
    g.release_db.execute(
        update(m.users)
        .where(m.users.c.id == g.release_user["id"])
        .values(password_hash=hashed, legacy_salt=None)
    )
    g.release_db.execute(
        update(m.sessions)
        .where(m.sessions.c.user_id == g.release_user["id"])
        .values(revoked=True)
    )
    return jsonify(ok=True)
