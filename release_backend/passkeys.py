"""Origin-bound WebAuthn and S256-bound system-browser authorization codes."""

import base64
import hashlib
import hmac
import json
import re
from urllib.parse import urlparse, urlencode
from flask import Blueprint, current_app, g, jsonify
from sqlalchemy import select, and_, update
from webauthn import (
    generate_authentication_options,
    verify_authentication_response,
    generate_registration_options,
    verify_registration_response,
    options_to_json,
)
from webauthn.helpers.structs import (
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)
from . import models as m
from .security import (
    require_auth,
    fail,
    json_body,
    now,
    uid,
    secret,
    digest,
    rate_limit,
    recent_auth,
)
from .auth import (
    get_challenge,
    challenge,
    issue_session,
    requires_mfa,
    allowed_mfa_methods,
    enrollment_identity,
)

bp = Blueprint("release_passkeys", __name__, url_prefix="/api/v1")


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def unb64(value):
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def configuration():
    origin = current_app.config["PUBLIC_ORIGIN"]
    parsed = urlparse(origin)
    rp = current_app.config.get("WEBAUTHN_RP_ID") or parsed.hostname
    if (
        parsed.scheme != "https"
        or not rp
        or (parsed.hostname != rp and not parsed.hostname.endswith("." + rp))
    ):
        fail(
            "passkeys_unavailable",
            "Passkeys require this server’s configured stable HTTPS origin.",
            503,
        )
    return rp, origin


def available():
    try:
        configuration()
        return True
    except Exception:
        return False


@bp.post("/auth/passkeys/authenticate/options")
def options():
    body = json_body()
    rate_limit("passkey_options", limit=20)
    pending = get_challenge(body.get("challenge_token"), "mfa")
    user = (
        g.release_db.execute(select(m.users).where(m.users.c.id == pending["user_id"]))
        .mappings()
        .first()
    )
    if not user or user["disabled"]:
        fail("invalid_challenge", "This request has expired.", 401)
    if "passkey" not in allowed_mfa_methods(user):
        fail("passkeys_unavailable", "This server requires an authenticator code.", 403)
    credentials = (user["mfa"] or {}).get("passkeys", [])
    if not credentials:
        fail("passkeys_unavailable", "No passkey is enrolled for this account.", 400)
    rp, _ = configuration()
    options = generate_authentication_options(
        rp_id=rp,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=unb64(item["credential_id"]))
            for item in credentials
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    token = challenge(
        user["id"],
        "webauthn_authentication",
        {
            "challenge": b64(options.challenge),
            "parent_challenge_id": pending["id"],
            "device_name": pending["data"].get("device_name"),
        },
        ttl=120,
    )
    return jsonify(
        options=json.loads(options_to_json(options)), passkey_challenge_token=token
    )


@bp.post("/auth/passkeys/authenticate/verify")
def verify():
    body = json_body()
    rate_limit("passkey_verify", limit=20)
    pending = get_challenge(
        body.get("passkey_challenge_token"), "webauthn_authentication"
    )
    parent = (
        g.release_db.execute(
            select(m.challenges)
            .where(m.challenges.c.id == pending["data"]["parent_challenge_id"])
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if (
        not parent
        or parent["consumed"]
        or parent["expires_at"] <= now()
        or parent["attempts"] >= 5
    ):
        fail("invalid_challenge", "This request has expired.", 401)
    user = (
        g.release_db.execute(
            select(m.users).where(m.users.c.id == pending["user_id"]).with_for_update()
        )
        .mappings()
        .first()
    )
    if not user or user["disabled"]:
        fail("invalid_challenge", "This request has expired.", 401)
    if "passkey" not in allowed_mfa_methods(user):
        fail("passkeys_unavailable", "This server requires an authenticator code.", 403)
    credential = body.get("credential")
    if not isinstance(credential, dict):
        fail("invalid_credential", "The passkey response could not be verified.", 401)
    data = dict(user["mfa"] or {})
    credentials = list(data.get("passkeys", []))
    selected = next(
        (item for item in credentials if item["credential_id"] == credential.get("id")),
        None,
    )
    rp, origin = configuration()
    try:
        if not selected:
            raise ValueError("unrecognized credential")
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=unb64(pending["data"]["challenge"]),
            expected_rp_id=rp,
            expected_origin=origin,
            credential_public_key=unb64(selected["public_key"]),
            credential_current_sign_count=int(selected.get("sign_count", 0)),
            require_user_verification=True,
        )
    except Exception:
        g.release_db.execute(
            update(m.challenges)
            .where(m.challenges.c.id == pending["id"])
            .values(attempts=pending["attempts"] + 1)
        )
        g.release_db.execute(
            update(m.challenges)
            .where(m.challenges.c.id == parent["id"])
            .values(attempts=parent["attempts"] + 1)
        )
        fail("invalid_credential", "The passkey response could not be verified.", 401)
    selected["sign_count"] = verified.new_sign_count
    selected["last_used"] = now()
    data["passkeys"] = credentials
    g.release_db.execute(
        update(m.users).where(m.users.c.id == user["id"]).values(mfa=data)
    )
    g.release_db.execute(
        update(m.challenges)
        .where(m.challenges.c.id.in_([pending["id"], parent["id"]]))
        .values(consumed=True)
    )
    if user["must_change_password"]:
        return jsonify(
            password_change_required=True,
            challenge_token=challenge(
                user["id"],
                "password_change",
                {
                    "device_name": pending["data"].get("device_name"),
                    "mfa_verified": True,
                    "mfa_method": "passkey",
                },
            ),
        ), 202
    return jsonify(
        issue_session(
            user,
            pending["data"].get("device_name"),
            mfa_verified=True,
            mfa_method="passkey",
        )
    )


@bp.post("/auth/passkeys/register/options")
def registration_options():
    body = json_body()
    user, enrollment = enrollment_identity(body)
    rate_limit("passkey_register", user["id"], limit=5, seconds=600)
    if "passkey" not in allowed_mfa_methods(user):
        fail("passkeys_unavailable", "This server requires an authenticator code.", 403)
    rp, _ = configuration()
    credentials = (user["mfa"] or {}).get("passkeys", [])
    options = generate_registration_options(
        rp_id=rp,
        rp_name="OpenFotoFrame",
        user_id=user["id"].encode(),
        user_name=user["email"],
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=unb64(item["credential_id"]))
            for item in credentials
        ],
    )
    token = challenge(
        user["id"],
        "webauthn_registration",
        {
            "challenge": b64(options.challenge),
            "name": str(body.get("name", "Passkey"))[:80],
            "enrollment_id": enrollment["id"] if enrollment else None,
        },
        ttl=120,
    )
    return jsonify(
        options=json.loads(options_to_json(options)), registration_token=token
    )


@bp.post("/auth/passkeys/register/verify")
def register_verify():
    body = json_body()
    pending = get_challenge(body.get("registration_token"), "webauthn_registration")
    enrollment_id = pending["data"].get("enrollment_id")
    enrollment = None
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
            or enrollment["consumed"]
            or enrollment["expires_at"] <= now()
            or enrollment["user_id"] != pending["user_id"]
            or enrollment["purpose"] != "enroll_mfa"
        ):
            fail("invalid_challenge", "This request has expired.", 401)
        user = (
            g.release_db.execute(
                select(m.users)
                .where(m.users.c.id == pending["user_id"])
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if not user or user["disabled"]:
            fail("invalid_challenge", "This request has expired.", 401)
    else:
        require_auth()
        recent_auth()
        user = g.release_user
        if pending["user_id"] != user["id"]:
            fail("invalid_challenge", "This request has expired.", 401)
    if "passkey" not in allowed_mfa_methods(user):
        fail("passkeys_unavailable", "This server requires an authenticator code.", 403)
    rp, origin = configuration()
    try:
        verified = verify_registration_response(
            credential=body.get("credential"),
            expected_challenge=unb64(pending["data"]["challenge"]),
            expected_rp_id=rp,
            expected_origin=origin,
            require_user_verification=True,
        )
    except Exception:
        g.release_db.execute(
            update(m.challenges)
            .where(m.challenges.c.id == pending["id"])
            .values(attempts=pending["attempts"] + 1)
        )
        g.release_commit_on_error = True
        fail("invalid_credential", "The passkey response could not be verified.", 401)
    data = dict(user["mfa"] or {})
    credentials = list(data.get("passkeys", []))
    if len(credentials) >= 20:
        fail(
            "passkey_limit", "Remove an existing passkey before enrolling another.", 409
        )
    encoded = b64(verified.credential_id)
    if any(item["credential_id"] == encoded for item in credentials):
        fail("already_enrolled", "This passkey is already enrolled.", 409)
    credentials.append(
        {
            "credential_id": encoded,
            "public_key": b64(verified.credential_public_key),
            "sign_count": verified.sign_count,
            "name": pending["data"]["name"],
            "created_at": now(),
            "last_used": None,
        }
    )
    data["passkeys"] = credentials
    recovery_codes = []
    if not data.get("recovery_code_hashes"):
        recovery_codes = [secret()[:20] for _ in range(10)]
        data["recovery_code_hashes"] = [digest(code) for code in recovery_codes]
    g.release_db.execute(
        update(m.users).where(m.users.c.id == user["id"]).values(mfa=data)
    )
    g.release_db.execute(
        update(m.challenges)
        .where(m.challenges.c.id == pending["id"])
        .values(consumed=True)
    )
    if enrollment:
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
        if user["must_change_password"]:
            return jsonify(
                password_change_required=True,
                recovery_codes=recovery_codes,
                challenge_token=challenge(
                    user["id"],
                    "password_change",
                    {
                        "device_name": enrollment["data"].get("device_name"),
                        "mfa_verified": True,
                        "mfa_method": "passkey",
                    },
                ),
            )
        result = issue_session(
            user,
            enrollment["data"].get("device_name"),
            mfa_verified=True,
            mfa_method="passkey",
        )
        result["recovery_codes"] = recovery_codes
        return jsonify(result)
    g.release_db.execute(
        update(m.sessions)
        .where(
            and_(
                m.sessions.c.user_id == user["id"],
                m.sessions.c.id != g.release_session["id"],
            )
        )
        .values(revoked=True)
    )
    g.release_db.execute(
        update(m.sessions)
        .where(m.sessions.c.id == g.release_session["id"])
        .values(mfa_verified=True, mfa_method="passkey")
    )
    return jsonify(ok=True, recovery_codes=recovery_codes)


@bp.delete("/auth/passkeys/<credential_id>")
@require_auth
def remove_passkey(credential_id):
    recent_auth()
    data = dict(g.release_user["mfa"] or {})
    credentials = data.get("passkeys", [])
    remaining = [item for item in credentials if item["credential_id"] != credential_id]
    if len(remaining) == len(credentials):
        fail("not_found", "Not found.", 404)
    if requires_mfa(g.release_user) and not remaining and not data.get("totp_secret"):
        fail(
            "mfa_required",
            "Enroll another factor before removing the last passkey.",
            403,
        )
    data["passkeys"] = remaining
    g.release_db.execute(
        update(m.users).where(m.users.c.id == g.release_user["id"]).values(mfa=data)
    )
    return jsonify(ok=True)


@bp.post("/auth/authorization-requests")
def authorization_request():
    body = json_body()
    rate_limit("authorization_request", limit=10, seconds=300)
    code_challenge = body.get("code_challenge", "")
    state = body.get("state", "")
    redirect_uri = body.get("redirect_uri", "")
    if not isinstance(code_challenge, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{43}", code_challenge
    ):
        fail("invalid_pkce", "Use an S256 PKCE challenge.")
    if not isinstance(state, str) or not 32 <= len(state) <= 256:
        fail("invalid_state", "Provide a random state value of at least 32 characters.")
    allowed = current_app.config.get(
        "AUTH_REDIRECT_URIS", ["openfotoframe://auth/callback"]
    )
    if redirect_uri not in allowed:
        fail("invalid_redirect", "This application callback is not registered.")
    if urlparse(current_app.config["PUBLIC_ORIGIN"]).scheme != "https":
        fail("secure_origin_required", "Browser authorization requires HTTPS.", 400)
    rid = uid()
    g.release_db.execute(
        m.authorization_requests.insert().values(
            id=rid,
            code_challenge=code_challenge,
            state=state,
            redirect_uri=redirect_uri,
            device_name=str(body.get("device_name", "New device"))[:120],
            expires_at=now() + 300,
            consumed=False,
        )
    )
    return jsonify(
        id=rid,
        authorization_url=current_app.config["PUBLIC_ORIGIN"] + "/authorize/" + rid,
        expires_in=300,
    ), 201


def pending_request(rid):
    row = (
        g.release_db.execute(
            select(m.authorization_requests)
            .where(m.authorization_requests.c.id == rid)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if not row or row["consumed"] or row["expires_at"] <= now():
        fail("invalid_authorization_request", "This sign-in request has expired.", 404)
    return dict(row)


@bp.get("/auth/authorization-requests/<rid>")
def authorization_metadata(rid):
    row = pending_request(rid)
    return jsonify(
        id=rid,
        device_name=row["device_name"],
        expires_at=row["expires_at"],
        issuer=current_app.config["PUBLIC_ORIGIN"],
    )


@bp.post("/auth/authorization-requests/<rid>/approve")
@require_auth
def approve_request(rid):
    recent_auth()
    row = pending_request(rid)
    if row["code_hash"]:
        fail("already_approved", "This sign-in request has already been approved.", 409)
    code = secret()
    g.release_db.execute(
        update(m.authorization_requests)
        .where(m.authorization_requests.c.id == rid)
        .values(
            user_id=g.release_user["id"],
            source_session_id=g.release_session["id"],
            code_hash=digest(code),
            expires_at=min(row["expires_at"], now() + 60),
        )
    )
    query = urlencode(
        {
            "code": code,
            "state": row["state"],
            "iss": current_app.config["PUBLIC_ORIGIN"],
        }
    )
    return jsonify(redirect_uri=row["redirect_uri"] + "?" + query)


@bp.post("/auth/authorization-code")
def exchange_code():
    body = json_body()
    rate_limit("authorization_code", limit=20)
    rid = body.get("request_id")
    code = body.get("code")
    verifier = body.get("code_verifier")
    if (
        not isinstance(verifier, str)
        or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
        or not isinstance(code, str)
        or len(code) > 200
    ):
        fail(
            "invalid_authorization_code",
            "This sign-in request could not be verified.",
            401,
        )
    row = pending_request(rid)
    if (
        not row["code_hash"]
        or not hmac.compare_digest(row["code_hash"], digest(code))
        or not hmac.compare_digest(
            row["code_challenge"], b64(hashlib.sha256(verifier.encode()).digest())
        )
    ):
        fail(
            "invalid_authorization_code",
            "This sign-in request could not be verified.",
            401,
        )
    parent = (
        g.release_db.execute(
            select(m.sessions).where(m.sessions.c.id == row["source_session_id"])
        )
        .mappings()
        .first()
    )
    user = (
        g.release_db.execute(select(m.users).where(m.users.c.id == row["user_id"]))
        .mappings()
        .first()
    )
    if (
        not parent
        or parent["revoked"]
        or parent["refresh_expires"] <= now()
        or not user
        or user["disabled"]
        or user["must_change_password"]
        or (
            requires_mfa(user)
            and (
                not parent["mfa_verified"]
                or parent["mfa_method"] not in allowed_mfa_methods(user) | {"recovery"}
            )
        )
    ):
        fail("invalid_authorization_code", "Sign in again.", 401)
    g.release_db.execute(
        update(m.authorization_requests)
        .where(m.authorization_requests.c.id == rid)
        .values(consumed=True)
    )
    return jsonify(
        issue_session(
            user,
            row["device_name"],
            mfa_verified=parent["mfa_verified"],
            mfa_method=parent["mfa_method"],
        )
    )
