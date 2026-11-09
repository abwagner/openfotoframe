"""Verify real signed WebAuthn assertions and one-use S256 code exchanges."""

import base64
import hashlib
import json
from urllib.parse import urlparse, parse_qs
import pytest
import cbor2
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from sqlalchemy import select, update
from tests.test_release_backend import release, bootstrap, auth, post, get
from release_backend import models as m


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def unb64(value):
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def key_material():
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.public_key().public_numbers()
    cose = cbor2.dumps(
        {
            1: 2,
            3: -7,
            -1: 1,
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        }
    )
    return key, cose


def assertion(
    key,
    credential_id,
    challenge,
    origin="https://frame.example.test",
    rp="frame.example.test",
    count=1,
):
    client = json.dumps(
        {
            "type": "webauthn.get",
            "challenge": challenge,
            "origin": origin,
            "crossOrigin": False,
        },
        separators=(",", ":"),
    ).encode()
    authenticator = (
        hashlib.sha256(rp.encode()).digest() + b"\x05" + count.to_bytes(4, "big")
    )
    signature = key.sign(
        authenticator + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256())
    )
    return {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {
            "clientDataJSON": b64(client),
            "authenticatorData": b64(authenticator),
            "signature": b64(signature),
            "userHandle": None,
        },
        "clientExtensionResults": {},
    }


def test_preserved_passkey_only_account_requires_valid_origin_and_signature(release):
    owner = bootstrap(release)
    key, cose = key_material()
    cid = b64(b"virtual-credential")
    with release.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.users)
            .where(m.users.c.id == owner["user"]["id"])
            .values(
                mfa={
                    "passkeys": [
                        {"credential_id": cid, "public_key": b64(cose), "sign_count": 0}
                    ]
                }
            )
        )
    pending = post(
        release,
        "/auth/login",
        json={"email": "owner@example.test", "password": "fixture-owner-password"},
    )
    assert pending.status_code == 202 and pending.json["methods"] == ["passkey"]
    options = post(
        release,
        "/auth/passkeys/authenticate/options",
        json={"challenge_token": pending.json["challenge_token"]},
    )
    assert options.status_code == 200, options.json
    challenge = options.json["options"]["challenge"]
    token = options.json["passkey_challenge_token"]
    wrong = assertion(key, cid, challenge, origin="https://evil.example.test")
    assert (
        post(
            release,
            "/auth/passkeys/authenticate/verify",
            json={"passkey_challenge_token": token, "credential": wrong},
        ).status_code
        == 401
    )
    correct = assertion(key, cid, challenge)
    verified = post(
        release,
        "/auth/passkeys/authenticate/verify",
        json={"passkey_challenge_token": token, "credential": correct},
    )
    assert verified.status_code == 200, verified.json
    assert get(release, "/auth/me", verified.json).status_code == 200
    assert (
        post(
            release,
            "/auth/passkeys/authenticate/verify",
            json={"passkey_challenge_token": token, "credential": correct},
        ).status_code
        == 401
    )
    with release.extensions["release_store"].transaction() as db:
        saved = db.execute(
            select(m.users.c.mfa).where(m.users.c.id == owner["user"]["id"])
        ).scalar()
        assert saved["passkeys"][0]["sign_count"] == 1


@pytest.mark.parametrize("required", [False, True])
def test_passkey_enrollment_validates_real_attestation_and_revokes_other_devices(
    release,
    required,
):
    owner = bootstrap(release)
    other = post(
        release,
        "/auth/login",
        json={"email": "owner@example.test", "password": "fixture-owner-password"},
    ).json
    registration_session = owner
    options_body = {"name": "Synthetic key"}
    if required:
        release.config.update(REQUIRE_MFA=True, MFA_METHODS="passkey")
        login = post(
            release,
            "/auth/login",
            json={"email": "owner@example.test", "password": "fixture-owner-password"},
        )
        assert login.status_code == 202 and login.json["methods"] == ["passkey"]
        registration_session = None
        options_body["challenge_token"] = login.json["challenge_token"]
        assert (
            post(
                release,
                "/auth/mfa/enroll/start",
                json={"challenge_token": login.json["challenge_token"]},
            ).status_code
            == 403
        )
    request = post(
        release,
        "/auth/passkeys/register/options",
        registration_session,
        json=options_body,
    )
    assert request.status_code == 200, request.json
    key, cose = key_material()
    cid = b"new-passkey-credential"
    challenge = request.json["options"]["challenge"]
    client = json.dumps(
        {
            "type": "webauthn.create",
            "challenge": challenge,
            "origin": "https://frame.example.test",
            "crossOrigin": False,
        },
        separators=(",", ":"),
    ).encode()
    authenticator = (
        hashlib.sha256(b"frame.example.test").digest()
        + b"\x45"
        + (0).to_bytes(4, "big")
        + bytes(16)
        + len(cid).to_bytes(2, "big")
        + cid
        + cose
    )
    attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": authenticator})
    credential = {
        "id": b64(cid),
        "rawId": b64(cid),
        "type": "public-key",
        "response": {
            "clientDataJSON": b64(client),
            "attestationObject": b64(attestation),
            "transports": ["internal"],
        },
        "clientExtensionResults": {},
    }
    result = post(
        release,
        "/auth/passkeys/register/verify",
        registration_session,
        json={
            "registration_token": request.json["registration_token"],
            "credential": credential,
        },
    )
    assert result.status_code == 200, result.json
    assert get(release, "/auth/me", other).status_code == 401
    assert (
        get(release, "/auth/me", result.json if required else owner).status_code == 200
    )
    if required:
        assert len(result.json["recovery_codes"]) == 10
        assert (
            post(
                release,
                "/auth/passkeys/register/verify",
                json={
                    "registration_token": request.json["registration_token"],
                    "credential": credential,
                },
            ).status_code
            == 401
        )


def test_pkce_requires_browser_approval_verifier_and_one_time_exchange(release):
    owner = bootstrap(release)
    verifier = "A" * 43
    state = "B" * 43
    r = post(
        release,
        "/auth/authorization-requests",
        json={
            "code_challenge": b64(hashlib.sha256(verifier.encode()).digest()),
            "state": state,
            "redirect_uri": "openfotoframe://auth/callback",
            "device_name": "Native phone",
        },
    )
    assert r.status_code == 201, r.json
    rid = r.json["id"]
    assert r.json["authorization_url"] == "https://frame.example.test/authorize/" + rid
    assert "user_id" not in get(release, "/auth/authorization-requests/" + rid).json
    assert (
        post(
            release,
            "/auth/authorization-code",
            json={"request_id": rid, "code": "guess", "code_verifier": verifier},
        ).status_code
        == 401
    )
    approved = post(
        release, f"/auth/authorization-requests/{rid}/approve", owner, json={}
    )
    assert approved.status_code == 200, approved.json
    callback = urlparse(approved.json["redirect_uri"])
    query = parse_qs(callback.query)
    assert (
        callback.scheme == "openfotoframe"
        and callback.netloc == "auth"
        and callback.path == "/callback"
    )
    assert query["state"] == [state] and query["iss"] == ["https://frame.example.test"]
    code = query["code"][0]
    assert (
        post(
            release,
            "/auth/authorization-code",
            json={"request_id": rid, "code": code, "code_verifier": "wrong" * 10},
        ).status_code
        == 401
    )
    result = post(
        release,
        "/auth/authorization-code",
        json={"request_id": rid, "code": code, "code_verifier": verifier},
    )
    assert result.status_code == 200, result.json
    assert result.json["user"]["id"] == owner["user"]["id"]
    assert (
        post(
            release,
            "/auth/authorization-code",
            json={"request_id": rid, "code": code, "code_verifier": verifier},
        ).status_code
        == 404
    )


def test_pkce_rejects_unregistered_callbacks_and_revoked_source(release):
    owner = bootstrap(release)
    verifier = "C" * 43
    body = {
        "code_challenge": b64(hashlib.sha256(verifier.encode()).digest()),
        "state": "D" * 43,
        "redirect_uri": "https://evil.example.test/callback",
    }
    assert post(release, "/auth/authorization-requests", json=body).status_code == 400
    body["redirect_uri"] = "openfotoframe://auth/callback"
    rid = post(release, "/auth/authorization-requests", json=body).json["id"]
    callback = post(
        release, f"/auth/authorization-requests/{rid}/approve", owner, json={}
    ).json["redirect_uri"]
    code = parse_qs(urlparse(callback).query)["code"][0]
    assert post(release, "/auth/logout", owner, json={}).status_code == 200
    assert (
        post(
            release,
            "/auth/authorization-code",
            json={"request_id": rid, "code": code, "code_verifier": verifier},
        ).status_code
        == 401
    )
