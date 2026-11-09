"""Permanent deletion must be explicit, scoped and restart-safe."""

import pytest
from sqlalchemy import select, update
from tests.test_release_backend import (
    release,  # noqa: F401 - imported pytest fixture
    bootstrap,
    collection,
    upload,
    auth,
    user,
    invite,
    accept,
)  # noqa: F401
from release_backend import models as m
from release_backend.media_lifecycle import process_media_purge
from release_backend.storage import StorageError


@pytest.fixture
def library(release):  # noqa: F811
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    uploaded = upload(release, owner, hid, cid)
    assert uploaded.status_code == 201
    aid = uploaded.json["asset"]["id"]
    path = f"/api/v1/households/{hid}/collections/{cid}"
    return release, owner, hid, cid, aid, path


def removed(library):
    app, owner, hid, cid, aid, prefix = library
    assert (
        app.test_client()
        .delete(prefix + "/assets/" + aid, headers=auth(owner))
        .status_code
        == 200
    )
    return app, owner, hid, cid, aid, prefix


def enqueue(library):
    app, owner, hid, cid, aid, prefix = removed(library)
    response = app.test_client().post(
        prefix + "/assets/" + aid + "/purge",
        headers=auth(owner),
        json={"confirm_permanent_delete": True},
    )
    assert response.status_code == 202, response.json
    return response.json["purge"]["id"]


def test_delete_retains_bytes_and_restore_requires_admin_current_grant(library):
    app, owner, hid, cid, aid, prefix = removed(library)
    client = app.test_client()
    response = client.get(prefix + "/trash", headers=auth(owner))
    assert response.status_code == 200 and len(response.json["assets"]) == 1
    assert "storage_key" not in response.get_data(as_text=True)
    assert (
        client.get(
            f"/api/v1/households/{hid}/assets/{aid}/media", headers=auth(owner)
        ).status_code
        == 404
    )
    member = user(app)
    assert (
        accept(
            app,
            member,
            invite(app, owner, hid, collections=[{"id": cid, "role": "editor"}]),
        ).status_code
        == 200
    )
    assert client.get(prefix + "/trash", headers=auth(member)).status_code == 403
    assert (
        client.post(
            prefix + "/assets/" + aid + "/restore", headers=auth(member), json={}
        ).status_code
        == 403
    )
    assert (
        client.post(
            prefix + "/assets/" + aid + "/restore", headers=auth(owner), json={}
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/households/{hid}/assets/{aid}/media", headers=auth(owner)
        ).status_code
        == 200
    )
    assert client.get(prefix + "/trash", headers=auth(owner)).json["assets"] == []


def test_purge_confirmation_scope_recent_auth_and_idempotency(library):
    app, owner, hid, cid, aid, prefix = removed(library)
    client = app.test_client()
    endpoint = prefix + "/assets/" + aid + "/purge"
    assert client.post(endpoint, headers=auth(owner), json={}).status_code == 400
    assert (
        client.post(
            endpoint, headers=auth(owner), json={"confirm_permanent_delete": 1}
        ).status_code
        == 400
    )
    assert (
        client.post(
            endpoint.replace(cid, "another-collection"),
            headers=auth(owner),
            json={"confirm_permanent_delete": True},
        ).status_code
        == 404
    )
    app.clock[0] += 301
    assert (
        client.post(
            endpoint, headers=auth(owner), json={"confirm_permanent_delete": True}
        ).json["error"]["code"]
        == "reauthentication_required"
    )
    app.clock[0] -= 301
    first = client.post(
        endpoint, headers=auth(owner), json={"confirm_permanent_delete": True}
    )
    second = client.post(
        endpoint, headers=auth(owner), json={"confirm_permanent_delete": True}
    )
    assert first.status_code == second.status_code == 202
    assert first.json["purge"]["id"] == second.json["purge"]["id"]
    assert (
        client.post(
            prefix + "/assets/" + aid + "/restore", headers=auth(owner), json={}
        ).status_code
        == 409
    )
    with app.extensions["release_store"].transaction() as db:
        assert len(db.execute(select(m.media_purges)).all()) == 1
        assert db.execute(select(m.assets.c.byte_size)).scalar_one() > 0


def test_purge_releases_metadata_and_deletes_only_target_objects(library):
    app, owner, hid, cid, aid, prefix = library
    other = upload(app, owner, hid, cid).json["asset"]["id"]
    jid = enqueue(library)
    with app.extensions["release_store"].transaction() as db:
        row = db.execute(select(m.media_purges)).mappings().one()
        keys = [row["storage_key"], row["thumbnail_key"]]
    storage = app.extensions["release_storage"]
    assert all(storage.read(key) for key in keys)
    assert process_media_purge(app, jid, hid, owner["user"]["id"]) is True
    assert process_media_purge(app, jid, hid, owner["user"]["id"]) is False
    for key in keys:
        with pytest.raises(FileNotFoundError):
            storage.read(key)
    with app.extensions["release_store"].transaction() as db:
        assert db.execute(select(m.assets.c.id)).scalar_one() == other
        assert db.execute(select(m.media_purges.c.state)).scalar_one() == "complete"
    assert (
        app.test_client()
        .get(f"/api/v1/households/{hid}/assets/{other}/media", headers=auth(owner))
        .status_code
        == 200
    )


def test_purge_partial_provider_failure_retries_without_releasing_quota(
    library, monkeypatch
):
    app, owner, hid, cid, aid, prefix = library
    jid = enqueue(library)
    storage = app.extensions["release_storage"]
    original_delete = storage.delete
    calls = []

    def interrupted(key):
        calls.append(key)
        if len(calls) == 2:
            raise StorageError("disposable provider outage")
        original_delete(key)

    monkeypatch.setattr(storage, "delete", interrupted)
    with pytest.raises(StorageError):
        process_media_purge(app, jid, hid, owner["user"]["id"])
    with app.extensions["release_store"].transaction() as db:
        assert db.execute(select(m.assets.c.byte_size)).scalar_one() > 0
        assert db.execute(select(m.media_purges.c.state)).scalar_one() == "queued"
    monkeypatch.setattr(storage, "delete", original_delete)
    assert process_media_purge(app, jid, hid, owner["user"]["id"])
    with app.extensions["release_store"].transaction() as db:
        assert db.execute(select(m.assets.c.id)).first() is None


def test_purge_rechecks_disabled_requester_before_deleting(library):
    app, owner, hid, cid, aid, prefix = library
    jid = enqueue(library)
    with app.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.users)
            .where(m.users.c.id == owner["user"]["id"])
            .values(disabled=True)
        )
        key = db.execute(select(m.assets.c.storage_key)).scalar_one()
    assert not process_media_purge(app, jid, hid, owner["user"]["id"])
    assert app.extensions["release_storage"].read(key)
    with app.extensions["release_store"].transaction() as db:
        assert db.execute(select(m.media_purges.c.state)).scalar_one() == "queued"


def test_browser_purge_requires_csrf_and_dispatch_lease_prevents_stale_worker(library):
    app, owner, hid, cid, aid, prefix = library
    jid = enqueue(library)
    client = app.test_client()
    bridge = client.post(
        "/api/v1/auth/web-bridge",
        headers=auth(owner),
        json={"path": "/upload", "household_id": hid, "collection_id": cid},
    )
    assert (
        client.post(
            "/api/v1/auth/web-bridge/redeem",
            data={"bridge_token": bridge.json["bridge_token"]},
        ).status_code
        == 303
    )
    assert (
        client.post(
            prefix + "/assets/" + aid + "/purge",
            json={"confirm_permanent_delete": True},
        ).status_code
        == 403
    )
    with app.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.media_purges)
            .where(m.media_purges.c.id == jid)
            .values(dispatch_token="current-worker", dispatch_until=app.clock[0] + 60)
        )
    assert not process_media_purge(app, jid, hid, owner["user"]["id"])
    assert not process_media_purge(
        app, jid, hid, owner["user"]["id"], dispatch_token="stale-worker"
    )
    assert process_media_purge(
        app, jid, hid, owner["user"]["id"], dispatch_token="current-worker"
    )


def test_database_failure_after_object_deletes_can_resume_without_double_credit(
    library,
):
    from sqlalchemy import event

    app, owner, hid, cid, aid, prefix = library
    jid = enqueue(library)
    engine = app.extensions["release_store"].engine

    def fail_removal(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("DELETE FROM assets"):
            raise RuntimeError("simulated transaction interruption")

    event.listen(engine, "before_cursor_execute", fail_removal)
    try:
        with pytest.raises(RuntimeError, match="simulated transaction interruption"):
            process_media_purge(app, jid, hid, owner["user"]["id"])
    finally:
        event.remove(engine, "before_cursor_execute", fail_removal)
    with app.extensions["release_store"].transaction() as db:
        assert db.execute(select(m.assets.c.byte_size)).scalar_one() > 0
        assert db.execute(select(m.media_purges.c.state)).scalar_one() == "queued"
    assert process_media_purge(app, jid, hid, owner["user"]["id"])
    with app.extensions["release_store"].transaction() as db:
        assert db.execute(select(m.assets.c.id)).first() is None
        assert db.execute(select(m.media_purges.c.state)).scalar_one() == "complete"


def test_two_workers_do_not_purge_the_same_asset_twice(library):
    from concurrent.futures import ThreadPoolExecutor

    app, owner, hid, cid, aid, prefix = library
    jid = enqueue(library)

    def run():
        return process_media_purge(app, jid, hid, owner["user"]["id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(results) == [False, True]


def test_corrupt_asset_and_receipt_cannot_redirect_purge_to_another_photo(library):
    app, owner, hid, cid, aid, prefix = library
    other = upload(app, owner, hid, cid).json["asset"]["id"]
    jid = enqueue(library)
    with app.extensions["release_store"].transaction() as db:
        other_key = db.execute(
            select(m.assets.c.storage_key).where(m.assets.c.id == other)
        ).scalar_one()
        # Simulate corrupt operational metadata without violating the unique-key
        # constraint; the real other object's bytes remain private and present.
        db.execute(
            update(m.assets)
            .where(m.assets.c.id == other)
            .values(storage_key=other_key + ".moved")
        )
        db.execute(
            update(m.assets).where(m.assets.c.id == aid).values(storage_key=other_key)
        )
        db.execute(
            update(m.media_purges)
            .where(m.media_purges.c.id == jid)
            .values(storage_key=other_key)
        )
    assert process_media_purge(app, jid, hid, owner["user"]["id"])
    assert app.extensions["release_storage"].read(other_key)
    with app.extensions["release_store"].transaction() as db:
        assert (
            db.execute(select(m.media_purges.c.error_code)).scalar_one()
            == "purge_conflict"
        )
        assert len(db.execute(select(m.assets.c.id)).all()) == 2


def test_offline_import_generated_keys_can_be_purged(library):
    app, owner, hid, cid, aid, prefix = library
    storage = app.extensions["release_storage"]
    with app.extensions["release_store"].transaction() as db:
        row = db.execute(select(m.assets).where(m.assets.c.id == aid)).mappings().one()
        old = [row["storage_key"], row["thumbnail_key"]]
        keys = [f"{hid}/{aid}/original", f"{hid}/{aid}/thumbnail"]
        for source, destination in zip(old, keys):
            storage.write(destination, storage.read(source))
            storage.delete(source)
        db.execute(
            update(m.assets)
            .where(m.assets.c.id == aid)
            .values(storage_key=keys[0], thumbnail_key=keys[1])
        )
    jid = enqueue(library)
    assert process_media_purge(app, jid, hid, owner["user"]["id"])
    for key in keys:
        with pytest.raises(FileNotFoundError):
            storage.read(key)
