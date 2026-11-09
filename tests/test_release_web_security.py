"""Boundary regressions for browser compatibility and internal route dispatch."""

import io

from sqlalchemy import select
from tests.test_release_backend import release, auth, user, invite, accept, get, post
from tests.test_release_web import web, upload
from release_backend import models as m


def test_browser_scope_context_is_never_an_authentication_credential(web):
    app, owner, hid, cid, fid, headers = web
    aid = upload(app, headers)
    client = app.test_client()
    with client.session_transaction() as context:
        context.update(
            release_household_id=hid, release_collection_id=cid, release_frame_id=fid
        )
    for path in (
        "/api/gallery",
        "/uploads/" + aid,
        "/thumbnails/" + aid,
        "/snapshots/" + aid + ".display.jpg",
    ):
        assert client.get(path).status_code == 401


def test_root_frame_enrollment_rejects_cross_site_before_consuming_token(web):
    app, owner, hid, cid, fid, headers = web
    token = post(
        app, f"/households/{hid}/frames/{fid}/enrollments", owner, json={}
    ).json["enrollment_token"]
    client = app.test_client()
    denied = client.post(
        "/display/enroll",
        data={"token": token},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert denied.status_code == 403
    assert client.get_cookie("release_frame") is None
    accepted = client.post("/display/enroll", data={"token": token})
    assert accepted.status_code == 303
    assert client.get_cookie("release_frame") is not None


def test_per_display_photo_override_requires_both_asset_and_frame_grants(web):
    app, owner, hid, cid, fid, headers = web
    member = user(app)
    assert (
        accept(
            app,
            member,
            invite(app, owner, hid, collections=[{"id": cid, "role": "contributor"}]),
        ).status_code
        == 200
    )
    member_headers = headers | auth(member)
    aid = upload(app, member_headers)
    result = app.test_client().patch(
        "/api/gallery/" + aid + "/displays/" + fid,
        headers=member_headers,
        json={"no_mat": True},
    )
    assert result.status_code == 404
    saved = get(app, f"/households/{hid}/collections/{cid}/assets/{aid}", member).json[
        "asset"
    ]
    assert not saved["metadata"].get("display_overrides")


def test_multi_file_upload_rolls_back_metadata_and_media_on_later_invalid_image(web):
    from tests.test_release_backend import image_bytes

    app, owner, hid, cid, fid, headers = web
    result = app.test_client().post(
        "/api/upload",
        headers=headers,
        data={
            "files": [
                (io.BytesIO(image_bytes()), "First.jpg"),
                (io.BytesIO(b"not an image"), "Invalid.jpg"),
            ]
        },
    )
    assert result.status_code == 415
    with app.extensions["release_store"].transaction() as db:
        assert not db.execute(
            select(m.assets).where(m.assets.c.collection_id == cid)
        ).first()
    root = app.extensions["release_storage"].root
    assert not [entry for entry in root.rglob("*") if entry.is_file()]


def test_pending_bridge_cannot_install_cookie_after_frame_grant_revocation(web):
    from sqlalchemy import delete

    app, owner, hid, cid, fid, headers = web
    member = user(app)
    assert (
        accept(
            app, member, invite(app, owner, hid, frames=[{"id": fid, "role": "viewer"}])
        ).status_code
        == 200
    )
    bridge = post(
        app,
        "/auth/web-bridge",
        member,
        json={"household_id": hid, "frame_id": fid, "path": "/display"},
    )
    assert bridge.status_code == 200, bridge.json
    with app.extensions["release_store"].transaction() as db:
        db.execute(delete(m.frame_grants).where(m.frame_grants.c.frame_id == fid))
    client = app.test_client()
    rejected = client.post(
        "/api/v1/auth/web-bridge/redeem",
        json={"bridge_token": bridge.json["bridge_token"]},
    )
    assert rejected.status_code == 404
    assert client.get_cookie("release_session") is None


def test_pending_bridge_cannot_skip_new_household_mfa_requirement(web):
    from sqlalchemy import update

    app, owner, hid, cid, fid, headers = web
    bridge = post(
        app,
        "/auth/web-bridge",
        owner,
        json={"household_id": hid, "collection_id": cid, "path": "/upload"},
    ).json
    with app.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.households)
            .where(m.households.c.id == hid)
            .values(settings={"mfa_mode": "required_all", "mfa_methods": "either"})
        )
    client = app.test_client()
    rejected = client.post(
        "/api/v1/auth/web-bridge/redeem", json={"bridge_token": bridge["bridge_token"]}
    )
    assert rejected.status_code == 401
    assert client.get_cookie("release_session") is None
