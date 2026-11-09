"""Release authorization contracts: exercise attacker identities, not only helpers."""

import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
import pyotp
from cryptography.fernet import Fernet
from PIL import Image
from sqlalchemy import select, update
from release_backend import create_app
from release_backend import models as m
from release_backend.security import digest


@pytest.fixture
def release(tmp_path):
    clock = [1_800_000_000]
    app = create_app(
        {
            "TESTING": True,
            "INIT_SCHEMA": True,
            "DATABASE_URL": "sqlite:///" + str(tmp_path / "release.db"),
            "MEDIA_ROOT": str(tmp_path / "media"),
            "PUBLIC_ORIGIN": "https://frame.example.test",
            "SECRET_KEY": "test-only-secret-key-that-is-long-enough",
            "BOOTSTRAP_SECRET": "fixture-bootstrap-secret",
            "MFA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "CLOCK": lambda: clock[0],
        }
    )
    app.clock = clock
    return app


def bootstrap(app):
    c = app.test_client()
    r = c.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "fixture-bootstrap-secret",
            "email": "owner@example.test",
            "password": "fixture-owner-password",
            "household_name": "Home",
            "device_name": "Owner phone",
        },
    )
    assert r.status_code in (200, 201), r.json
    return r.json


def auth(session):
    return {"Authorization": "Bearer " + session["access_token"]}


def post(app, path, session=None, **kwargs):
    return app.test_client().post(
        "/api/v1" + path, headers=auth(session) if session else {}, **kwargs
    )


def get(app, path, session=None):
    return app.test_client().get(
        "/api/v1" + path, headers=auth(session) if session else {}
    )


def user(app, address="member@example.test", verified=False):
    with app.extensions["release_store"].transaction() as db:
        old = app.config["REGISTER_ENABLED"]
        app.config["REGISTER_ENABLED"] = True
    r = post(
        app,
        "/auth/register",
        json={"email": address, "password": "fixture-member-password"},
    )
    app.config["REGISTER_ENABLED"] = old
    assert r.status_code in (200, 201), r.json
    if verified:
        with app.extensions["release_store"].transaction() as db:
            db.execute(
                update(m.users)
                .where(m.users.c.id == r.json["user"]["id"])
                .values(email_verified=True)
            )
    return r.json


def collection(app, session, hid, name="Family"):
    r = post(app, f"/households/{hid}/collections", session, json={"name": name})
    assert r.status_code == 201, r.json
    return r.json["id"]


def frame(app, session, hid, name="Kitchen"):
    r = post(app, f"/households/{hid}/frames", session, json={"name": name})
    assert r.status_code == 201, r.json
    return r.json["id"]


def invite(app, owner, hid, collections=None, frames=None, **extra):
    r = post(
        app,
        f"/households/{hid}/invitations",
        owner,
        json={
            "grants": {"collections": collections or [], "frames": frames or []},
            "transferable": True,
            **extra,
        },
    )
    assert r.status_code == 201, r.json
    return r.json["token"]


def accept(app, session, token):
    return post(app, "/invitations/accept", session, json={"token": token})


def image_bytes(color="red"):
    out = io.BytesIO()
    Image.new("RGB", (20, 30), color).save(out, format="PNG")
    return out.getvalue()


def upload(app, session, hid, cid, data=None):
    return post(
        app,
        f"/households/{hid}/collections/{cid}/assets",
        session,
        data={"file": (io.BytesIO(data or image_bytes()), "photo.png")},
        content_type="multipart/form-data",
    )


def test_bootstrap_requires_secret_and_is_one_time(release):
    assert (
        post(
            release,
            "/auth/bootstrap",
            json={"email": "evil@example.test", "password": "long-password"},
        ).status_code
        == 403
    )
    owner = bootstrap(release)
    assert len(owner["households"]) == 1
    assert (
        post(
            release,
            "/auth/bootstrap",
            json={
                "bootstrap_secret": "fixture-bootstrap-secret",
                "email": "next@example.test",
                "password": "another-password",
            },
        ).status_code
        == 409
    )
    assert get(release, "/server").json["deployment_mode"] == "self_hosted"


def test_membership_does_not_grant_future_or_unselected_collections(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    public = collection(release, owner, hid)
    private = collection(release, owner, hid, "Private")
    member = user(release)
    assert (
        accept(
            release,
            member,
            invite(release, owner, hid, [{"id": public, "role": "viewer"}]),
        ).status_code
        == 200
    )
    r = get(release, f"/households/{hid}/collections", member)
    assert [row["id"] for row in r.json["collections"]] == [public]
    assert (
        get(
            release, f"/households/{hid}/collections/{private}/assets", member
        ).status_code
        == 404
    )
    future = collection(release, owner, hid, "Future")
    assert (
        get(release, f"/households/{hid}/collections/{future}", member).status_code
        == 404
    )
    assert upload(release, member, hid, public).status_code == 403


def test_media_downloads_and_duplicate_detection_are_scoped(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    a = collection(release, owner, hid, "A")
    b = collection(release, owner, hid, "B")
    member = user(release)
    photo = upload(release, owner, hid, b)
    assert photo.status_code == 201, photo.json
    aid = photo.json["asset"]["id"]
    assert (
        accept(
            release,
            member,
            invite(release, owner, hid, [{"id": a, "role": "contributor"}]),
        ).status_code
        == 200
    )
    for suffix in ("media", "thumbnail"):
        assert (
            get(release, f"/households/{hid}/assets/{aid}/{suffix}", member).status_code
            == 404
        )
        assert (
            get(release, f"/households/{hid}/assets/{aid}/{suffix}", owner).status_code
            == 200
        )
    r = post(
        release,
        f"/households/{hid}/collections/{a}/duplicates",
        member,
        data={"file": (io.BytesIO(image_bytes()), "photo.png")},
        content_type="multipart/form-data",
    )
    assert r.json["duplicates"] == []
    assert get(release, "/uploads/photo.png", owner).status_code == 404


def test_contributor_cannot_edit_someone_elses_upload(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    member = user(release)
    photo = upload(release, owner, hid, cid).json["asset"]["id"]
    accept(
        release,
        member,
        invite(release, owner, hid, [{"id": cid, "role": "contributor"}]),
    )
    client = release.test_client()
    assert (
        client.patch(
            f"/api/v1/households/{hid}/collections/{cid}/assets/{photo}",
            headers=auth(member),
            json={"title": "stolen"},
        ).status_code
        == 403
    )
    own = upload(release, member, hid, cid).json["asset"]["id"]
    assert (
        client.patch(
            f"/api/v1/households/{hid}/collections/{cid}/assets/{own}",
            headers=auth(member),
            json={"title": "own"},
        ).status_code
        == 200
    )


def test_groups_and_bulk_fail_atomically_for_foreign_assets(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    a = collection(release, owner, hid, "A")
    b = collection(release, owner, hid, "B")
    first = upload(release, owner, hid, a).json["asset"]["id"]
    other = upload(release, owner, hid, b).json["asset"]["id"]
    assert (
        post(
            release,
            f"/households/{hid}/collections/{a}/groups",
            owner,
            json={"asset_ids": [first, other]},
        ).status_code
        == 404
    )
    assert (
        post(
            release,
            f"/households/{hid}/collections/{a}/assets/bulk",
            owner,
            json={"asset_ids": [first, other], "metadata": {"title": "wrong"}},
        ).status_code
        == 404
    )
    assert (
        get(release, f"/households/{hid}/collections/{a}/assets/{first}", owner).json[
            "asset"
        ]["title"]
        == ""
    )


def test_refresh_reuse_revokes_the_family_and_browser_session(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    bridge = post(
        release,
        "/auth/web-bridge",
        owner,
        json={"household_id": hid, "collection_id": cid, "path": "/upload"},
    ).json["bridge_token"]
    browser = release.test_client()
    assert (
        browser.post(
            "/api/v1/auth/web-bridge/redeem", json={"bridge_token": bridge}
        ).status_code
        == 303
    )
    assert browser.get("/api/v1/auth/me").status_code == 200
    rotated = post(
        release, "/auth/refresh", json={"refresh_token": owner["refresh_token"]}
    )
    assert rotated.status_code == 200
    assert get(release, "/auth/me", owner).status_code == 401
    assert (
        post(
            release, "/auth/refresh", json={"refresh_token": owner["refresh_token"]}
        ).status_code
        == 401
    )
    assert get(release, "/auth/me", rotated.json).status_code == 401
    assert browser.get("/api/v1/auth/me").status_code == 401


def test_bridge_is_single_use_scoped_and_csrf_protected(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    assert (
        post(
            release,
            "/auth/web-bridge",
            owner,
            json={
                "household_id": hid,
                "collection_id": cid,
                "path": "https://evil.test",
            },
        ).status_code
        == 400
    )
    token = post(
        release,
        "/auth/web-bridge",
        owner,
        json={"household_id": hid, "collection_id": cid, "path": "/upload"},
    ).json["bridge_token"]
    browser = release.test_client()
    assert (
        browser.post(
            "/api/v1/auth/web-bridge/redeem",
            json={"bridge_token": token, "collection_id": "foreign"},
        ).status_code
        == 403
    )
    assert (
        browser.post(
            "/api/v1/auth/web-bridge/redeem", json={"bridge_token": token}
        ).status_code
        == 303
    )
    assert (
        browser.post(
            "/api/v1/auth/web-bridge/redeem", json={"bridge_token": token}
        ).status_code
        == 401
    )
    assert (
        browser.patch(
            f"/api/v1/households/{hid}/collections/{cid}/settings",
            json={"shuffle": True},
        ).status_code
        == 403
    )
    csrf = browser.get_cookie("release_csrf").value
    assert (
        browser.patch(
            f"/api/v1/households/{hid}/collections/{cid}/settings",
            json={"shuffle": True},
            headers={"X-CSRFToken": csrf},
        ).status_code
        == 200
    )


def test_invitation_previews_do_not_consume_and_recipient_must_be_verified(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    member = user(release)
    r = post(
        release,
        f"/households/{hid}/invitations",
        owner,
        json={
            "recipient_email": member["user"]["email"],
            "grants": {"collections": [{"id": cid, "role": "viewer"}], "frames": []},
        },
    )
    token = r.json["token"]
    for _ in range(2):
        assert (
            post(release, "/invitations/preview", json={"token": token}).status_code
            == 200
        )
    assert (
        accept(release, member, token).json["error"]["code"] == "verification_required"
    )
    with release.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.users)
            .where(m.users.c.id == member["user"]["id"])
            .values(email_verified=True)
        )
    assert accept(release, member, token).status_code == 200
    assert accept(release, member, token).status_code == 200
    stranger = user(release, "stranger@example.test")
    assert accept(release, stranger, token).status_code == 409


def test_revoked_issuer_cannot_grant_invitation_access(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    member = user(release)
    token = invite(release, owner, hid, [{"id": cid, "role": "viewer"}])
    with release.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.memberships)
            .where(m.memberships.c.user_id == owner["user"]["id"])
            .values(active=False)
        )
    assert accept(release, member, token).status_code == 404


def test_frame_controller_gets_no_photo_access_and_display_credentials_are_scoped(
    release,
):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    fid = frame(release, owner, hid)
    member = user(release)
    aid = upload(release, owner, hid, cid).json["asset"]["id"]
    assert (
        release.test_client()
        .patch(
            f"/api/v1/households/{hid}/frames/{fid}",
            headers=auth(owner),
            json={"collection_ids": [cid]},
        )
        .status_code
        == 200
    )
    accept(
        release,
        member,
        invite(release, owner, hid, frames=[{"id": fid, "role": "controller"}]),
    )
    assert (
        post(
            release,
            f"/households/{hid}/frames/{fid}/control",
            member,
            json={"action": "next"},
        ).status_code
        == 200
    )
    assert (
        get(release, f"/households/{hid}/collections/{cid}/assets", member).status_code
        == 404
    )
    assert (
        get(release, f"/households/{hid}/assets/{aid}/media", member).status_code == 404
    )
    token = post(
        release, f"/households/{hid}/frames/{fid}/credentials", owner, json={}
    ).json["frame_token"]
    display = release.test_client()
    headers = {"Authorization": "Frame " + token}
    assert display.get("/api/v1/display/playlist", headers=headers).status_code == 200
    assert display.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert (
        display.get(f"/api/v1/display/assets/{aid}/media", headers=headers).status_code
        == 200
    )
    release.test_client().patch(
        f"/api/v1/households/{hid}/frames/{fid}",
        headers=auth(owner),
        json={"collection_ids": []},
    )
    assert (
        display.get(f"/api/v1/display/assets/{aid}/media", headers=headers).status_code
        == 404
    )


def test_pairing_requires_explicit_approval_secret_and_correct_code(release):
    owner = bootstrap(release)
    pair = post(release, "/pairings", owner, json={}).json
    pending = post(
        release,
        f"/pairings/{pair['id']}/challenge",
        json={"code": pair["code"], "device_name": "Second phone"},
    ).json
    body = {
        "challenge_id": pending["challenge_id"],
        "challenge_secret": pending["challenge_secret"],
    }
    assert (
        post(release, f"/pairings/{pair['id']}/exchange", json=body).status_code == 409
    )
    assert (
        post(
            release,
            f"/pairings/{pair['id']}/approve",
            owner,
            json={
                "challenge_id": pending["challenge_id"],
                "confirmation_code": "wrong",
            },
        ).status_code
        == 403
    )
    assert (
        post(
            release,
            f"/pairings/{pair['id']}/approve",
            owner,
            json={
                "challenge_id": pending["challenge_id"],
                "confirmation_code": pending["confirmation_code"],
            },
        ).status_code
        == 200
    )
    assert (
        post(
            release,
            f"/pairings/{pair['id']}/exchange",
            json=dict(body, challenge_secret="wrong"),
        ).status_code
        == 403
    )
    result = post(release, f"/pairings/{pair['id']}/exchange", json=body)
    assert result.status_code == 200, result.json
    assert result.json["user"]["id"] == owner["user"]["id"]
    assert (
        post(release, f"/pairings/{pair['id']}/exchange", json=body).status_code == 404
    )


def test_recovery_codes_are_single_use_and_mfa_precedes_full_session(release):
    owner = bootstrap(release)
    user_id = owner["user"]["id"]
    with release.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.users)
            .where(m.users.c.id == user_id)
            .values(
                mfa={
                    "totp_secret": Fernet(release.config["MFA_ENCRYPTION_KEY"])
                    .encrypt(b"JBSWY3DPEHPK3PXP")
                    .decode(),
                    "recovery_code_hashes": [digest("fixture-recovery")],
                }
            )
        )
    challenge = post(
        release,
        "/auth/login",
        json={"email": "owner@example.test", "password": "fixture-owner-password"},
    )
    assert challenge.status_code == 202 and "access_token" not in challenge.json
    bad = post(
        release,
        "/auth/mfa",
        json={"challenge_token": challenge.json["challenge_token"], "code": "wrong"},
    )
    assert bad.status_code == 401
    result = post(
        release,
        "/auth/mfa",
        json={
            "challenge_token": challenge.json["challenge_token"],
            "code": "fixture-recovery",
        },
    )
    assert result.status_code == 200, result.json
    assert get(release, "/auth/me", result.json).status_code == 200
    assert (
        post(
            release,
            "/auth/mfa",
            json={
                "challenge_token": challenge.json["challenge_token"],
                "code": "fixture-recovery",
            },
        ).status_code
        == 401
    )


def test_required_mfa_enrollment_cannot_issue_full_session_early(release):
    owner = bootstrap(release)
    release.config["REQUIRE_MFA"] = True
    assert get(release, "/auth/me", owner).status_code == 401
    pending = post(
        release,
        "/auth/login",
        json={"email": "owner@example.test", "password": "fixture-owner-password"},
    )
    assert pending.status_code == 202 and pending.json["mfa_enrollment_required"]
    setup = post(
        release,
        "/auth/mfa/enroll/start",
        json={"challenge_token": pending.json["challenge_token"]},
    )
    assert setup.status_code == 200, setup.json
    # Use wall clock for pyotp generation; the verifier is the same real-time factor.
    complete = post(
        release,
        "/auth/mfa/enroll/complete",
        json={
            "setup_token": setup.json["setup_token"],
            "code": pyotp.TOTP(setup.json["secret"]).now(),
        },
    )
    assert complete.status_code == 200, complete.json
    assert len(complete.json["recovery_codes"]) == 10
    assert get(release, "/auth/me", complete.json).status_code == 200
    assert get(release, "/auth/me", owner).status_code == 401


def test_owner_cannot_remove_the_last_owner(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    mid = get(release, f"/households/{hid}/memberships", owner).json["memberships"][0][
        "id"
    ]
    r = release.test_client().patch(
        f"/api/v1/households/{hid}/memberships/{mid}",
        headers=auth(owner),
        json={"role": "member"},
    )
    assert r.status_code == 409


def test_server_credentials_are_hash_only_and_revoked_grants_deny_next_request(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    member = user(release)
    accept(
        release, member, invite(release, owner, hid, [{"id": cid, "role": "viewer"}])
    )
    assert (
        get(release, f"/households/{hid}/collections/{cid}", member).status_code == 200
    )
    with release.extensions["release_store"].transaction() as db:
        record = (
            db.execute(select(m.sessions).where(m.sessions.c.id == owner["session_id"]))
            .mappings()
            .one()
        )
        assert record["access_hash"] == digest(owner["access_token"]) and record[
            "refresh_hash"
        ] == digest(owner["refresh_token"])
        membership = (
            db.execute(
                select(m.memberships).where(
                    m.memberships.c.user_id == member["user"]["id"]
                )
            )
            .mappings()
            .one()
        )
    r = release.test_client().put(
        f"/api/v1/households/{hid}/memberships/{membership['id']}/grants",
        headers=auth(owner),
        json={"collections": [], "frames": []},
    )
    assert r.status_code == 200
    assert (
        get(release, f"/households/{hid}/collections/{cid}", member).status_code == 404
    )


def test_two_households_and_mixed_membership_never_merge(release):
    owner = bootstrap(release)
    first = owner["households"][0]["id"]
    a = collection(release, owner, first, "Home A")
    stranger = user(release, "stranger@example.test")
    both = user(release, "both@example.test")
    with release.extensions["release_store"].transaction() as db:
        db.execute(
            m.households.insert().values(
                id="second-household",
                name="Home B",
                settings={},
                created_at=release.clock[0],
            )
        )
        db.execute(
            m.memberships.insert().values(
                id="second-owner",
                household_id="second-household",
                user_id=stranger["user"]["id"],
                role="owner",
                active=True,
            )
        )
    b = collection(release, stranger, "second-household", "Home B photos")
    first_photo = upload(release, owner, first, a).json["asset"]["id"]
    second_photo = upload(release, stranger, "second-household", b).json["asset"]["id"]
    accept(release, both, invite(release, owner, first, [{"id": a, "role": "viewer"}]))
    accept(
        release,
        both,
        invite(release, stranger, "second-household", [{"id": b, "role": "viewer"}]),
    )
    assert len(get(release, "/households", both).json["households"]) == 2
    assert (
        get(
            release, f"/households/{first}/assets/{second_photo}/media", both
        ).status_code
        == 404
    )
    assert (
        get(
            release, f"/households/second-household/assets/{first_photo}/media", both
        ).status_code
        == 404
    )
    assert (
        get(
            release, f"/households/{first}/collections/{a}/assets", stranger
        ).status_code
        == 404
    )


def test_concurrent_invitation_acceptance_grants_only_one_recipient(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    one = user(release, "one@example.test")
    two = user(release, "two@example.test")
    token = invite(release, owner, hid, [{"id": cid, "role": "viewer"}])
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda session: accept(release, session, token).status_code, (one, two)
            )
        )
    assert sorted(outcomes) == [200, 409]
    with release.extensions["release_store"].transaction() as db:
        grants = db.execute(
            select(m.collection_grants).where(
                m.collection_grants.c.collection_id == cid
            )
        ).all()
        assert len(grants) == 1


def test_expiry_and_account_disablement_deny_next_request(release):
    owner = bootstrap(release)
    pair = post(release, "/pairings", owner, json={}).json
    release.clock[0] += 121
    assert (
        post(
            release, f"/pairings/{pair['id']}/challenge", json={"code": pair["code"]}
        ).status_code
        == 404
    )
    with release.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.users)
            .where(m.users.c.id == owner["user"]["id"])
            .values(disabled=True)
        )
    assert get(release, "/auth/me", owner).status_code == 401


def test_invalid_bulk_ids_do_not_raise_server_error(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    assert (
        post(
            release,
            f"/households/{hid}/collections/{cid}/assets/bulk",
            owner,
            json={"asset_ids": [{}], "metadata": {}},
        ).status_code
        == 400
    )
    assert (
        post(
            release,
            f"/households/{hid}/collections/{cid}/groups",
            owner,
            json={"asset_ids": [{}, {}]},
        ).status_code
        == 400
    )


def test_exports_revalidate_permissions_and_restore_remaps_ids(release):
    from release_backend.jobs import process_export

    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    target = collection(release, owner, hid, "Restored")
    photo = upload(release, owner, hid, cid).json["asset"]["id"]
    job = post(release, f"/households/{hid}/collections/{cid}/exports", owner, json={})
    assert job.status_code == 202
    assert process_export(release, job.json["id"], hid, owner["user"]["id"])
    archive = get(release, f"/households/{hid}/jobs/{job.json['id']}/download", owner)
    assert archive.status_code == 200
    restored = post(
        release,
        f"/households/{hid}/collections/{target}/restore",
        owner,
        data={"file": (io.BytesIO(archive.data), "export.zip")},
        content_type="multipart/form-data",
    )
    assert restored.status_code == 201, restored.json
    photos = get(release, f"/households/{hid}/collections/{target}/assets", owner).json[
        "assets"
    ]
    assert len(photos) == 1 and photos[0]["id"] != photo
    member = user(release)
    accept(
        release, member, invite(release, owner, hid, [{"id": cid, "role": "viewer"}])
    )
    queued = post(
        release, f"/households/{hid}/collections/{cid}/exports", member, json={}
    ).json["id"]
    with release.extensions["release_store"].transaction() as db:
        mid = db.execute(
            select(m.memberships.c.id).where(
                m.memberships.c.user_id == member["user"]["id"]
            )
        ).scalar()
    release.test_client().put(
        f"/api/v1/households/{hid}/memberships/{mid}/grants",
        headers=auth(owner),
        json={"collections": [], "frames": []},
    )
    assert not process_export(release, queued, hid, member["user"]["id"])
    assert (
        get(release, f"/households/{hid}/jobs/{queued}/download", member).status_code
        == 404
    )


def test_restore_rejects_traversal_before_any_media_write(release):
    import zipfile

    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../outside", "malicious")
    r = post(
        release,
        f"/households/{hid}/collections/{cid}/restore",
        owner,
        data={"file": (io.BytesIO(archive.getvalue()), "bad.zip")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert not list(Path(release.config["MEDIA_ROOT"]).rglob("original"))


def test_public_registration_cannot_activate_paid_service(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    release.config["RELEASE_MODE"] = "hosted"
    assert upload(release, owner, hid, cid).status_code == 503
    assert (
        post(
            release, f"/households/{hid}/frames", owner, json={"name": "Unpaid frame"}
        ).status_code
        == 503
    )


def test_s3_adapter_never_emits_public_urls_or_acls():
    from release_backend.storage import S3Storage

    class Body(io.BytesIO):
        pass

    class Client:
        def __init__(self):
            self.calls = []

        def put_object(self, **kwargs):
            self.calls.append(kwargs)

        def get_object(self, **kwargs):
            return {"Body": Body(b"private")}

        def delete_object(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    storage = S3Storage("private-bucket", prefix="media", client=client)
    storage.write("household/asset/original", b"private")
    assert client.calls[0]["IfNoneMatch"] == "*"
    assert "ACL" not in client.calls[0]
    assert storage.read("household/asset/original") == b"private"
    with pytest.raises(ValueError):
        storage.read("../escape")


def test_reinvitation_does_not_resurrect_revoked_collection_grants(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    a = collection(release, owner, hid, "A")
    b = collection(release, owner, hid, "B")
    member = user(release)
    accept(
        release,
        member,
        invite(
            release,
            owner,
            hid,
            [{"id": a, "role": "editor"}, {"id": b, "role": "editor"}],
        ),
    )
    with release.extensions["release_store"].transaction() as db:
        mid = db.execute(
            select(m.memberships.c.id).where(
                m.memberships.c.user_id == member["user"]["id"]
            )
        ).scalar()
    release.test_client().patch(
        f"/api/v1/households/{hid}/memberships/{mid}",
        headers=auth(owner),
        json={"active": False},
    )
    assert (
        accept(
            release, member, invite(release, owner, hid, [{"id": a, "role": "viewer"}])
        ).status_code
        == 200
    )
    assert get(release, f"/households/{hid}/collections/{b}", member).status_code == 404
    assert (
        get(release, f"/households/{hid}/collections/{a}", member).json["permission"]
        == "viewer"
    )


def test_disabled_issuer_invalidates_unaccepted_invites(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    member = user(release)
    token = invite(release, owner, hid, [{"id": cid, "role": "viewer"}])
    with release.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.users)
            .where(m.users.c.id == owner["user"]["id"])
            .values(disabled=True)
        )
    assert accept(release, member, token).status_code == 404


def test_frame_enrollment_is_one_use_cookie_scoped_and_csrf_protected(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    fid = frame(release, owner, hid)
    aid = upload(release, owner, hid, cid).json["asset"]["id"]
    release.test_client().patch(
        f"/api/v1/households/{hid}/frames/{fid}",
        headers=auth(owner),
        json={"collection_ids": [cid]},
    )
    issued = post(
        release, f"/households/{hid}/frames/{fid}/enrollments", owner, json={}
    )
    assert issued.status_code == 201, issued.json
    physical = release.test_client()
    token = issued.json["enrollment_token"]
    assert (
        physical.post("/api/v1/display/enroll", json={"token": token}).status_code
        == 200
    )
    assert (
        physical.post("/api/v1/display/enroll", json={"token": token}).status_code
        == 401
    )
    assert physical.get("/api/v1/display/playlist").json["assets"][0]["id"] == aid
    assert physical.get("/api/v1/auth/me").status_code == 401
    assert (
        physical.post("/api/v1/display/control", json={"action": "next"}).status_code
        == 403
    )
    csrf = physical.get_cookie("release_frame_csrf").value
    assert (
        physical.post(
            "/api/v1/display/control",
            json={"action": "next", "frame_id": "different-frame"},
            headers={"X-CSRFToken": csrf},
        ).status_code
        == 200
    )
    assert (
        get(release, f"/households/{hid}/frames/{fid}/state", owner).json["state"][
            "index"
        ]
        == 1
    )
    assert (
        physical.get(f"/api/v1/households/{hid}/collections/{cid}/assets").status_code
        == 401
    )
    assert (
        release.test_client()
        .delete(f"/api/v1/households/{hid}/frames/{fid}", headers=auth(owner))
        .status_code
        == 200
    )
    assert physical.get("/api/v1/display/playlist").status_code == 401


def test_invite_membership_applies_required_household_mfa_before_content(release):
    owner = bootstrap(release)
    member = user(release, verified=True)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    token = invite(release, owner, hid, collections=[{"id": cid, "role": "viewer"}])
    with release.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.households)
            .where(m.households.c.id == hid)
            .values(settings={"mfa_mode": "required_all", "mfa_methods": "totp"})
        )
    assert (
        post(release, "/invitations/accept", member, json={"token": token}).status_code
        == 200
    )
    assert (
        get(release, f"/households/{hid}/collections/{cid}/assets", member).status_code
        == 401
    )
    pending = post(
        release,
        "/auth/login",
        json={"email": "member@example.test", "password": "fixture-member-password"},
    )
    assert pending.status_code == 202 and pending.json["mfa_enrollment_required"]


def test_cross_origin_cannot_install_valid_browser_or_frame_credentials(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    fid = frame(release, owner, hid)
    token = post(release, "/auth/web-bridge", owner, json={"path": "/workspace"}).json[
        "bridge_token"
    ]
    enrolled = post(
        release, f"/households/{hid}/frames/{fid}/enrollments", owner, json={}
    ).json
    client = release.test_client()
    for path, body in [
        ("/auth/web-bridge/redeem", {"bridge_token": token}),
        ("/display/enroll", {"token": enrolled["enrollment_token"]}),
    ]:
        for headers in [
            {"Origin": "https://evil.example.test"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
        ]:
            rejected = client.post("/api/v1" + path, json=body, headers=headers)
            assert rejected.status_code == 403
            assert not rejected.headers.getlist("Set-Cookie")
        assert client.post("/api/v1" + path, json=body).status_code in (200, 303)


def test_totp_counter_cannot_be_replayed_across_new_login_challenges(release):
    owner = bootstrap(release)
    value = "JBSWY3DPEHPK3PXP"
    with release.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.users)
            .where(m.users.c.id == owner["user"]["id"])
            .values(
                mfa={
                    "totp_secret": Fernet(release.config["MFA_ENCRYPTION_KEY"])
                    .encrypt(value.encode())
                    .decode()
                }
            )
        )
    first = post(
        release,
        "/auth/login",
        json={"email": "owner@example.test", "password": "fixture-owner-password"},
    )
    code = pyotp.TOTP(value).now()
    accepted = post(
        release,
        "/auth/mfa",
        json={"challenge_token": first.json["challenge_token"], "code": code},
    )
    assert accepted.status_code == 200, accepted.json
    second = post(
        release,
        "/auth/login",
        json={"email": "owner@example.test", "password": "fixture-owner-password"},
    )
    replay = post(
        release,
        "/auth/mfa",
        json={"challenge_token": second.json["challenge_token"], "code": code},
    )
    assert replay.status_code == 401


def test_visual_metadata_preserves_no_mat_and_rejects_invalid_types(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    aid = upload(release, owner, hid, cid).json["asset"]["id"]
    client = release.test_client()
    path = f"/api/v1/households/{hid}/collections/{cid}/assets/{aid}"
    saved = client.patch(
        path,
        headers=auth(owner),
        json={"metadata": {"no_mat": True, "mat_finish": "linen"}},
    )
    assert saved.status_code == 200, saved.json
    assert (
        get(release, f"/households/{hid}/collections/{cid}/assets/{aid}", owner).json[
            "asset"
        ]["metadata"]["no_mat"]
        is True
    )
    for metadata in [
        {"no_mat": 1},
        {"no_mat": "false"},
        {"bevel_width": float("nan")},
        {"mat_finish": "unknown"},
        {"border_effect": []},
    ]:
        assert (
            client.patch(
                path, headers=auth(owner), json={"metadata": metadata}
            ).status_code
            == 400
        )


def test_exif_portrait_dimensions_and_duplicate_hash_use_visible_orientation(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    encoded = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (160, 100), "red").save(encoded, format="JPEG", exif=exif)
    data = encoded.getvalue()
    response = release.test_client().post(
        f"/api/v1/households/{hid}/collections/{cid}/assets",
        headers=auth(owner),
        data={"file": (io.BytesIO(data), "Portrait.jpg")},
    )
    assert response.status_code == 201, response.json
    metadata = response.json["asset"]["metadata"]
    assert (metadata["width"], metadata["height"]) == (100, 160)
    checked = release.test_client().post(
        f"/api/v1/households/{hid}/collections/{cid}/duplicates",
        headers=auth(owner),
        data={"file": (io.BytesIO(data), "Portrait.jpg")},
    )
    assert checked.status_code == 200, checked.json
    assert (checked.json["width"], checked.json["height"]) == (100, 160)
    assert checked.json["phash"] == metadata["phash"]
    original = get(
        release, response.json["asset"]["media_url"].removeprefix("/api/v1"), owner
    )
    assert original.data == data


def test_storage_provider_failure_rolls_back_upload_and_returns_sanitized_503(release):
    from release_backend.storage import S3Storage

    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)

    class Provider:
        calls = 0
        deleted = []

        def put_object(self, **values):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("private provider diagnostics must not escape")

        def delete_object(self, **values):
            self.deleted.append(values["Key"])

    provider = Provider()
    release.extensions["release_storage"] = S3Storage(
        "synthetic-private-bucket", client=provider
    )
    response = upload(release, owner, hid, cid)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "storage_unavailable"
    assert "diagnostics" not in response.get_data(as_text=True)
    assert len(provider.deleted) == 1
    with release.extensions["release_store"].transaction() as db:
        assert not db.execute(
            select(m.assets).where(m.assets.c.collection_id == cid)
        ).first()


def test_export_list_contains_only_requester_jobs_from_current_granted_collection(
    release,
):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    other = collection(release, owner, hid, "Other")
    member = user(release)
    assert (
        accept(
            release,
            member,
            invite(release, owner, hid, collections=[{"id": cid, "role": "viewer"}]),
        ).status_code
        == 200
    )
    job = post(
        release, f"/households/{hid}/collections/{cid}/exports", owner, json={}
    ).json["id"]
    listed = get(release, f"/households/{hid}/collections/{cid}/exports", owner)
    assert listed.status_code == 200 and [
        item["id"] for item in listed.json["jobs"]
    ] == [job]
    assert listed.json["jobs"][0]["download_path"] is None
    assert (
        get(release, f"/households/{hid}/collections/{cid}/exports", member).json[
            "jobs"
        ]
        == []
    )
    assert (
        get(release, f"/households/{hid}/collections/{other}/exports", owner).json[
            "jobs"
        ]
        == []
    )
    assert (
        get(
            release, f"/households/{hid}/collections/{other}/exports", member
        ).status_code
        == 404
    )


def test_export_includes_zip_overhead_in_restore_limit_instead_of_creating_unrestorable_archive(
    release,
):
    from release_backend.jobs import process_export

    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    uploaded = upload(release, owner, hid, cid).json["asset"]
    release.config["MAX_UPLOAD_BYTES"] = uploaded["byte_size"] + 1
    queued = post(
        release, f"/households/{hid}/collections/{cid}/exports", owner, json={}
    )
    assert queued.status_code == 202, queued.json
    jid = queued.json["id"]
    assert not process_export(release, jid, hid, owner["user"]["id"])
    status = get(release, f"/households/{hid}/jobs/{jid}", owner).json
    assert status["state"] == "failed" and status["error_code"] == "export_too_large"
    assert status["download_path"] is None
    with release.extensions["release_store"].transaction() as db:
        assert (
            db.execute(select(m.jobs.c.result_key).where(m.jobs.c.id == jid)).scalar()
            is None
        )
