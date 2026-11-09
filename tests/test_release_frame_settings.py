"""TV settings remain frame-scoped and do not pretend a power agent executed them."""

import pytest
from tests.test_release_backend import release, auth, user, invite, accept, post, frame
from tests.test_release_web import web


def schedule(**changes):
    return {
        "id": "morning",
        "on_time": "07:00",
        "off_time": "23:00",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "enabled": True,
        "timezone": "America/New_York",
    } | changes


def test_schedule_roundtrip_is_scoped_and_explicitly_not_executing(web):
    app, owner, hid, cid, fid, headers = web
    other = frame(app, owner, hid, "Bedroom")
    client = app.test_client()
    saved = client.post(
        "/api/tv-schedules", headers=headers, json={"schedules": [schedule()]}
    )
    assert saved.status_code == 200, saved.json
    assert saved.json["schedules"] == [schedule()]
    assert (
        saved.json["execution_enabled"] is False
        and saved.json["cec_available"] is False
    )
    assert client.get("/api/tv-schedules", headers=headers).json["schedules"] == [
        schedule()
    ]
    assert (
        client.get(
            "/api/tv-schedules", headers=headers | {"X-OpenFotoFrame-Frame": other}
        ).json["schedules"]
        == []
    )
    assert client.get("/api/tv-schedules", headers=auth(owner)).status_code == 409
    assert (
        client.get(
            "/api/tv-schedules",
            headers={
                "Remote-User": "owner",
                "X-Forwarded-For": "127.0.0.1",
                "X-OpenFotoFrame-Household": hid,
                "X-OpenFotoFrame-Frame": fid,
            },
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    "invalid",
    [
        {"on_time": '07:00" autofocus onfocus=alert(1)'},
        {"off_time": "24:01"},
        {"days": [True]},
        {"days": [0, 0]},
        {"enabled": 1},
        {"timezone": "invalid/no-zone"},
        {"id": "<script>"},
        {"command": "shell command"},
    ],
)
def test_invalid_schedules_fail_atomically_in_compat_and_versioned_api(web, invalid):
    app, owner, hid, cid, fid, headers = web
    client = app.test_client()
    saved = client.post(
        "/api/tv-schedules", headers=headers, json={"schedules": [schedule()]}
    )
    assert saved.status_code == 200
    for path, method in [
        ("/api/tv-schedules", "POST"),
        (f"/api/v1/households/{hid}/frames/{fid}", "PATCH"),
    ]:
        rejected = client.open(
            path,
            method=method,
            headers=headers,
            json={"schedules": [schedule(), schedule(**({"id": "second"} | invalid))]},
        )
        assert rejected.status_code == 400, rejected.json
        assert client.get("/api/tv-schedules", headers=headers).json["schedules"] == [
            schedule()
        ]


def test_frame_controller_can_read_schedule_but_cannot_change_or_probe_server_network(
    web,
):
    app, owner, hid, cid, fid, headers = web
    controller = user(app)
    assert (
        accept(
            app,
            controller,
            invite(app, owner, hid, frames=[{"id": fid, "role": "controller"}]),
        ).status_code
        == 200
    )
    member_headers = headers | auth(controller)
    client = app.test_client()
    assert client.get("/api/tv-schedules", headers=member_headers).status_code == 200
    assert (
        client.post(
            "/api/tv-schedules",
            headers=member_headers,
            json={"schedules": [schedule()]},
        ).status_code
        == 403
    )
    assert client.get("/api/network-info", headers=member_headers).status_code == 403
    info = client.get("/api/network-info", headers=headers)
    assert info.status_code == 200
    assert info.json["server_url"] == app.config["PUBLIC_ORIGIN"]
    assert info.json["local_ip"] is None and info.json["tailscale_ip"] is None
    assert (
        client.get(
            "/api/network-info",
            headers=headers | {"X-OpenFotoFrame-Frame": "missing-frame"},
        ).status_code
        == 404
    )


def test_browser_schedule_mutation_requires_valid_csrf(web):
    app, owner, hid, cid, fid, headers = web
    bridge = post(
        app,
        "/auth/web-bridge",
        owner,
        json={
            "household_id": hid,
            "collection_id": cid,
            "frame_id": fid,
            "path": "/upload",
        },
    ).json
    client = app.test_client()
    assert (
        client.post(
            "/api/v1/auth/web-bridge/redeem",
            json={"bridge_token": bridge["bridge_token"]},
        ).status_code
        == 303
    )
    assert (
        client.post("/api/tv-schedules", json={"schedules": [schedule()]}).status_code
        == 403
    )
    csrf = client.get_cookie("release_csrf").value
    assert (
        client.post(
            "/api/tv-schedules",
            headers={"X-CSRFToken": csrf},
            json={"schedules": [schedule()]},
        ).status_code
        == 200
    )
