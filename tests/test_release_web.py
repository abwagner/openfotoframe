"""Existing companion routes must retain v1 grant/revocation/CSRF boundaries."""

import io
import re
import pytest
from sqlalchemy import select
from tests.test_release_backend import (
    release,  # noqa: F401 - imported pytest fixture
    bootstrap,
    auth,
    collection,
    frame,
    user,
    invite,
    accept,
    image_bytes,
)  # noqa: F401
from release_web import register


@pytest.fixture
def web(release):  # noqa: F811 - imported pytest fixture
    register(release)
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    fid = frame(release, owner, hid)
    headers = auth(owner) | {
        "X-OpenFotoFrame-Household": hid,
        "X-OpenFotoFrame-Collection": cid,
        "X-OpenFotoFrame-Frame": fid,
    }
    return release, owner, hid, cid, fid, headers


def upload(app, headers, name="Landscape.jpg"):
    response = app.test_client().post(
        "/api/upload",
        headers=headers,
        data={"files": (io.BytesIO(image_bytes()), name)},
    )
    assert response.status_code == 200, response.json
    return response.json["uploaded"][0]


def test_gallery_roundtrip_reuses_transaction_and_scoped_media(web):
    app, owner, hid, cid, fid, headers = web
    aid = upload(app, headers)
    client = app.test_client()
    response = client.get("/api/gallery", headers=headers)
    assert response.status_code == 200, response.json
    photo = response.json["images"][0]
    assert photo["filename"] == aid and isinstance(photo["uploaded_at"], str)
    assert photo["width"] > 0 and photo["size"] > 0
    assert client.get("/thumbnails/" + aid, headers=headers).status_code == 200
    assert (
        client.patch(
            "/api/gallery/" + aid,
            headers=headers,
            json={"title": "Family", "mat_color": "#f5f0e6"},
        ).status_code
        == 200
    )
    assert (
        client.get("/api/gallery", headers=headers).json["images"][0]["title"]
        == "Family"
    )
    assert client.get("/static/js/upload.js").status_code == 200


def test_compatibility_never_trusts_loopback_proxy_headers_or_foreign_scope(web):
    app, owner, hid, cid, fid, headers = web
    aid = upload(app, headers)
    client = app.test_client()
    for path in ["/api/gallery", "/uploads/" + aid, "/thumbnails/" + aid]:
        forged = {
            key: value for key, value in headers.items() if key != "Authorization"
        }
        forged.update(
            {"Remote-User": "owner@example.test", "X-Forwarded-For": "127.0.0.1"}
        )
        assert client.get(path, headers=forged).status_code == 401
    member = user(app)
    assert accept(app, member, invite(app, owner, hid)).status_code == 200
    foreign = headers | auth(member)
    assert client.get("/api/gallery", headers=foreign).status_code == 404
    assert client.get("/uploads/" + aid, headers=foreign).status_code == 404


def test_bulk_mutations_preflight_every_asset_before_writes(web):
    app, owner, hid, cid, fid, headers = web
    aid = upload(app, headers)
    other = collection(app, owner, hid, "Private")
    other_headers = headers | {"X-OpenFotoFrame-Collection": other}
    foreign = upload(app, other_headers, "Private.jpg")
    client = app.test_client()
    assert (
        client.post(
            "/api/gallery/bulk",
            headers=headers,
            json={"filenames": [aid, foreign], "action": "disable"},
        ).status_code
        == 404
    )
    assert (
        client.get("/api/gallery", headers=headers).json["images"][0]["enabled"] is True
    )
    assert (
        client.post(
            "/api/gallery/bulk",
            headers=headers,
            json={"filenames": [aid], "action": "disable"},
        ).status_code
        == 200
    )
    assert (
        client.get("/api/gallery", headers=headers).json["images"][0]["enabled"]
        is False
    )


def test_web_bridge_csrf_and_parent_revocation_protect_advanced_editor(web):
    app, owner, hid, cid, fid, headers = web
    aid = upload(app, headers)
    client = app.test_client()
    bridge = client.post(
        "/api/v1/auth/web-bridge",
        headers=auth(owner),
        json={
            "household_id": hid,
            "collection_id": cid,
            "frame_id": fid,
            "path": "/upload",
        },
    )
    assert bridge.status_code == 200, bridge.json
    response = client.post(
        "/api/v1/auth/web-bridge/redeem",
        data={"bridge_token": bridge.json["bridge_token"]},
    )
    assert response.status_code == 303
    page = client.get("/upload")
    assert page.status_code == 200, page.get_data(as_text=True)
    csrf = re.search(
        r'name="csrf-token" content="([^"]+)"', page.get_data(as_text=True)
    ).group(1)
    assert (
        client.patch("/api/gallery/" + aid, json={"title": "Blocked"}).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/gallery/" + aid,
            headers={"X-CSRFToken": csrf},
            json={"title": "Allowed"},
        ).status_code
        == 200
    )
    assert (
        app.test_client().post("/api/v1/auth/logout", headers=auth(owner)).status_code
        == 200
    )
    assert client.get("/api/gallery").status_code == 401
    assert client.get("/uploads/" + aid).status_code == 401


def test_browser_login_and_explicit_workspace_selection(web):
    app, owner, hid, cid, fid, headers = web
    client = app.test_client()
    page = client.get("/login")
    csrf = re.search(
        r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)
    ).group(1)
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf,
            "email": "owner@example.test",
            "password": "fixture-owner-password",
        },
    )
    assert response.status_code == 303, response.get_data(as_text=True)
    page = client.get("/workspace?household_id=" + hid)
    assert page.status_code == 200 and b"Family" in page.data
    csrf = re.search(
        r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)
    ).group(1)
    response = client.post(
        "/workspace",
        data={
            "csrf_token": csrf,
            "household_id": hid,
            "collection_id": cid,
            "frame_id": fid,
        },
    )
    assert response.status_code == 303
    assert client.get("/upload").status_code == 200


def test_duplicate_preflight_and_display_state_match_native_decoders(web):
    app, owner, hid, cid, fid, headers = web
    aid = upload(app, headers)
    client = app.test_client()
    response = client.post(
        "/api/check-duplicates",
        headers=headers,
        data={"files": (io.BytesIO(image_bytes()), "Again.jpg")},
    )
    assert response.status_code == 200, response.json
    match = response.json["results"]["Again.jpg"]
    assert match["width"] > 0 and match["height"] > 0
    assert match["matches"][0]["filename"] == aid
    response = client.post(
        "/api/display/control", headers=headers, json={"action": "pause"}
    )
    assert response.status_code == 200 and response.json["paused"] is True
    response = client.get("/api/display/state", headers=headers)
    assert isinstance(response.json["index"], int)


def test_invitation_preview_get_does_not_accept_and_landing_has_no_third_party(web):
    app, owner, hid, cid, fid, headers = web
    token = invite(app, owner, hid, collections=[{"id": cid, "role": "viewer"}])
    client = app.test_client()
    response = client.post("/accept", data={"token": token})
    assert response.status_code == 303
    assert client.get("/accept").status_code == 200
    assert client.get("/accept").status_code == 200
    from release_backend import models as m

    with app.extensions["release_store"].transaction() as db:
        assert db.execute(select(m.invitations.c.accepted_by)).scalar_one() is None
    page = client.get("/connect")
    assert page.status_code == 200
    assert "https://" not in page.get_data(as_text=True)


def test_frame_browser_enrollment_is_one_use_and_cannot_become_management_access(web):
    app, owner, hid, cid, fid, headers = web
    upload(app, headers)
    operator = app.test_client()
    assert (
        operator.patch(
            f"/api/v1/households/{hid}/frames/{fid}",
            headers=auth(owner),
            json={"collection_ids": [cid], "settings": {"width": 640, "height": 360}},
        ).status_code
        == 200
    )
    created = operator.post(
        f"/api/v1/households/{hid}/frames/{fid}/enrollments",
        headers=auth(owner),
        json={},
    )
    assert created.status_code == 201, created.json
    display = app.test_client()
    redeemed = display.post(
        "/display/enroll", data={"token": created.json["enrollment_token"]}
    )
    assert redeemed.status_code == 303, redeemed.json
    assert display.get("/display").status_code == 200
    playlist = display.get("/frame/playlist")
    assert playlist.status_code == 200, playlist.json
    rendered = display.get(playlist.json["slides"][0]["snapshot_url"])
    assert rendered.status_code == 200 and rendered.mimetype == "image/jpeg"
    assert (
        app.test_client()
        .post("/display/enroll", data={"token": created.json["enrollment_token"]})
        .status_code
        == 401
    )
    assert display.get("/api/v1/auth/me").status_code == 401
    assert (
        display.get(
            "/api/gallery",
            headers={
                "X-OpenFotoFrame-Household": hid,
                "X-OpenFotoFrame-Collection": cid,
            },
        ).status_code
        == 401
    )
    assert display.post("/frame/control", json={"action": "pause"}).status_code == 403
    csrf = display.get_cookie("release_frame_csrf").value
    assert (
        display.post(
            "/frame/control", headers={"X-CSRFToken": csrf}, json={"action": "pause"}
        ).status_code
        == 200
    )
    assert (
        operator.delete(
            f"/api/v1/households/{hid}/frames/{fid}/credentials", headers=auth(owner)
        ).status_code
        == 200
    )
    assert display.get("/frame/playlist").status_code == 401
    assert display.get(playlist.json["slides"][0]["snapshot_url"]).status_code == 401


def test_frame_controller_does_not_gain_photo_previews_and_snapshot_requires_current_grant(
    web,
):
    app, owner, hid, cid, fid, headers = web
    aid = upload(app, headers)
    client = app.test_client()
    assert (
        client.patch(
            f"/api/v1/households/{hid}/frames/{fid}",
            headers=auth(owner),
            json={"collection_ids": [cid]},
        ).status_code
        == 200
    )
    member = user(app)
    assert (
        accept(
            app,
            member,
            invite(app, owner, hid, frames=[{"id": fid, "role": "controller"}]),
        ).status_code
        == 200
    )
    member_headers = headers | auth(member)
    assert client.get("/api/display/state", headers=member_headers).status_code == 200
    assert (
        client.post(
            "/api/display/control", headers=member_headers, json={"action": "pause"}
        ).status_code
        == 200
    )
    assert client.get("/frame/playlist", headers=member_headers).status_code == 403
    assert (
        client.get(
            f"/snapshots/{aid}.{fid}.display.png", headers=member_headers
        ).status_code
        == 404
    )
    rendered = client.get(f"/snapshots/{aid}.{fid}.display.png", headers=headers)
    assert rendered.status_code == 200 and rendered.mimetype == "image/jpeg"


def test_pkce_browser_approval_requires_explicit_post_and_preserves_requester_verifier(
    web,
):
    import hashlib
    import base64

    app, owner, hid, cid, fid, headers = web
    verifier = "a" * 43
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    client = app.test_client()
    pending = client.post(
        "/api/v1/auth/authorization-requests",
        json={
            "code_challenge": challenge,
            "redirect_uri": "openfotoframe://auth/callback",
            "state": "a-secure-state-longer-than-sixteen",
            "device_name": "New phone",
        },
    )
    assert pending.status_code == 201, pending.json
    rid = pending.json["id"]
    assert client.get("/authorize/" + rid).status_code == 200
    page = client.get("/login")
    csrf = re.search(
        r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)
    ).group(1)
    login = client.post(
        "/login",
        data={
            "csrf_token": csrf,
            "email": "owner@example.test",
            "password": "fixture-owner-password",
        },
    )
    assert login.status_code == 303 and login.headers["Location"] == "/authorize/" + rid
    page = client.get("/authorize/" + rid)
    assert b"Approve this device" in page.data
    csrf = re.search(
        r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)
    ).group(1)
    approved = client.post("/authorize/" + rid, data={"csrf_token": csrf})
    assert approved.status_code == 303 and approved.headers["Location"].startswith(
        "openfotoframe://auth/callback?"
    )
    assert (
        client.post("/authorize/" + rid, data={"csrf_token": csrf}).status_code == 409
    )


def test_portrait_pairing_uses_frame_crop_rotation_and_collection_boundaries():
    from release_web.display import slides

    def photo(aid, cid="c", **metadata):
        return {
            "id": aid,
            "collection_id": cid,
            "metadata": {"width": 1200, "height": 800, **metadata},
        }

    data = {
        "frame_id": "f",
        "assets": [
            photo(
                "a",
                display_overrides={"f": {"crop": {"x": 0, "y": 0, "w": 0.3, "h": 1}}},
            ),
            photo("b", rotation=90),
            photo("c", cid="other", rotation=90),
            photo("d", rotation=90, crop={"x": 0, "y": 0, "w": 1, "h": 0.2}),
        ],
    }
    result = slides(data)
    assert [item["asset_ids"] for item in result] == [["a", "b"], ["d"], ["c"]]
    data["collection_settings"] = {"c": {"auto_pair_portraits": False}}
    assert all(len(item["asset_ids"]) == 1 for item in slides(data))


def test_snapshot_uses_backend_mat_and_frame_override_and_invalidates_revision(web):
    from PIL import Image

    app, owner, hid, cid, fid, headers = web
    aid = upload(app, headers)
    client = app.test_client()
    assert (
        client.patch(
            f"/api/v1/households/{hid}/frames/{fid}",
            headers=auth(owner),
            json={"collection_ids": [cid], "settings": {"width": 640, "height": 360}},
        ).status_code
        == 200
    )
    assert (
        client.patch(
            "/api/gallery/" + aid,
            headers=headers,
            json={
                "mat_color": "#f00000",
                "mat_finish": "flat",
                "border_effect": "none",
            },
        ).status_code
        == 200
    )
    before = client.get("/frame/playlist", headers=headers).json["slides"][0][
        "snapshot_url"
    ]
    image = Image.open(io.BytesIO(client.get(before, headers=headers).data))
    r, g, b = image.getpixel((0, 0))
    assert r > 230 and g < 10 and b < 10
    assert (
        client.patch(
            f"/api/gallery/{aid}/displays/{fid}",
            headers=headers,
            json={"mat_color": "#0000f0"},
        ).status_code
        == 200
    )
    after = client.get("/frame/playlist", headers=headers).json["slides"][0][
        "snapshot_url"
    ]
    assert before != after
    image = Image.open(io.BytesIO(client.get(after, headers=headers).data))
    r, g, b = image.getpixel((0, 0))
    assert b > 230 and r < 10 and g < 10


def test_group_snapshot_applies_rotation_before_crop_without_changing_original(
    web, monkeypatch
):
    from PIL import Image
    from release_web import display

    app, owner, hid, cid, fid, headers = web
    first, second = upload(app, headers), upload(app, headers, "Second.jpg")
    client = app.test_client()
    for aid in (first, second):
        assert (
            client.patch(
                "/api/gallery/" + aid,
                headers=headers,
                json={"rotation": 90, "crop": {"x": 0, "y": 0, "w": 0.4, "h": 1}},
            ).status_code
            == 200
        )
    original = client.get("/uploads/" + first, headers=headers).data
    assert (
        client.patch(
            f"/api/v1/households/{hid}/frames/{fid}",
            headers=auth(owner),
            json={"collection_ids": [cid], "settings": {"width": 640, "height": 360}},
        ).status_code
        == 200
    )
    real = display.render_group_slide
    seen = []

    def inspect(slide, settings, directory, size):
        for photo in slide["images"]:
            with Image.open(directory / photo["filename"]) as image:
                assert image.size == (30, 20)
                assert image.size == (photo["width"], photo["height"])
                assert photo["rotation"] == 0 and photo["crop"]["w"] == 0.4
            seen.append(photo["filename"])
        return real(slide, settings, directory, size)

    monkeypatch.setattr(display, "render_group_slide", inspect)
    playlist = client.get("/frame/playlist", headers=headers)
    assert len(playlist.json["slides"]) == 1
    assert (
        client.get(
            playlist.json["slides"][0]["snapshot_url"], headers=headers
        ).status_code
        == 200
    )
    assert seen == [first, second]
    assert client.get("/uploads/" + first, headers=headers).data == original


def test_browser_frame_only_member_can_select_and_control_without_photos(web):
    app, owner, hid, cid, fid, headers = web
    member = user(app)
    assert (
        app.test_client()
        .patch(
            f"/api/v1/households/{hid}/frames/{fid}",
            headers=auth(owner),
            json={"collection_ids": [cid]},
        )
        .status_code
        == 200
    )
    assert (
        accept(
            app,
            member,
            invite(app, owner, hid, frames=[{"id": fid, "role": "controller"}]),
        ).status_code
        == 200
    )
    client = app.test_client()
    bridge = client.post(
        "/api/v1/auth/web-bridge", headers=auth(member), json={"path": "/workspace"}
    )
    assert bridge.status_code == 200
    assert (
        client.post(
            "/api/v1/auth/web-bridge/redeem",
            data={"bridge_token": bridge.json["bridge_token"]},
        ).status_code
        == 303
    )
    page = client.get("/workspace?household_id=" + hid)
    csrf = re.search(
        r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)
    ).group(1)
    chosen = client.post(
        "/workspace", data={"csrf_token": csrf, "household_id": hid, "frame_id": fid}
    )
    assert chosen.status_code == 303 and chosen.headers["Location"] == "/controls"
    page = client.get("/controls")
    assert page.status_code == 200 and b"Frame controls do not grant" in page.data
    assert client.post("/controls", data={"action": "pause"}).status_code == 403
    assert (
        client.post(
            "/controls", data={"csrf_token": csrf, "action": "pause"}
        ).status_code
        == 303
    )
    assert client.get("/api/gallery").status_code == 409
    assert client.get("/frame/playlist").status_code == 403


def test_collection_reorder_is_reflected_in_frame_playlist(web):
    app, owner, hid, cid, fid, headers = web
    first, second = upload(app, headers), upload(app, headers, "Second.jpg")
    client = app.test_client()
    assert (
        client.patch(
            f"/api/v1/households/{hid}/frames/{fid}",
            headers=auth(owner),
            json={"collection_ids": [cid]},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/settings", headers=headers, json={"auto_pair_portraits": False}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/reorder", headers=headers, json={"order": [second, first]}
        ).status_code
        == 200
    )
    result = client.get("/frame/playlist", headers=headers)
    assert result.status_code == 200
    assert [item["id"] for item in result.json["slides"]] == [second, first]


def test_frame_playlist_conditional_delivery_rechecks_changes_and_revocation(web):
    app, owner, hid, cid, fid, headers = web
    aid = upload(app, headers)
    client = app.test_client()
    assert (
        client.patch(
            f"/api/v1/households/{hid}/frames/{fid}",
            headers=auth(owner),
            json={"collection_ids": [cid]},
        ).status_code
        == 200
    )
    enrollment = client.post(
        f"/api/v1/households/{hid}/frames/{fid}/enrollments",
        headers=auth(owner),
        json={},
    ).json["enrollment_token"]
    display = app.test_client()
    assert (
        display.post("/display/enroll", data={"token": enrollment}).status_code == 303
    )
    first = display.get("/frame/playlist")
    assert first.status_code == 200 and first.headers["ETag"]
    conditional = {"If-None-Match": first.headers["ETag"]}
    same = display.get("/frame/playlist", headers=conditional)
    assert same.status_code == 304 and same.data == b""
    assert "no-store" in same.headers["Cache-Control"]
    assert (
        client.patch(
            "/api/gallery/" + aid, headers=headers, json={"mat_color": "#123456"}
        ).status_code
        == 200
    )
    changed = display.get("/frame/playlist", headers=conditional)
    assert (
        changed.status_code == 200 and changed.headers["ETag"] != first.headers["ETag"]
    )
    from release_backend import models as m
    from sqlalchemy import update

    with app.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.frame_credentials)
            .where(m.frame_credentials.c.frame_id == fid)
            .values(revoked=True)
        )
    denied = display.get(
        "/frame/playlist", headers={"If-None-Match": changed.headers["ETag"]}
    )
    assert denied.status_code == 401 and not denied.headers.get("ETag")
