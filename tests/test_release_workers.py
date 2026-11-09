"""Durable leases/retries/expiry use synthetic jobs and temporary media only."""
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select, update
from release_backend import models as m
from release_workers import Dispatcher, run_once
from release_workers.schema import upgrade
from tests.test_release_backend import release, bootstrap, auth, collection


def fixture_queue(app):
    owner = bootstrap(app)
    hid = owner['households'][0]['id']
    cid = collection(app, owner, hid)
    job = app.test_client().post(f'/api/v1/households/{hid}/collections/{cid}/exports',
        headers=auth(owner), json={}).get_json()['id']
    store = app.extensions['release_store']
    upgrade(store)
    return store, job, hid, cid, Dispatcher(store, clock=lambda: app.clock[0])


def row(store, jid):
    with store.transaction() as db:
        return dict(db.execute(select(m.jobs).where(m.jobs.c.id == jid)).mappings().one())


def test_exclusive_claim_crash_reclaim_and_stale_finish(release):
    store, jid, hid, cid, dispatcher = fixture_queue(release)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: dispatcher.claim(), range(2)))
    first = next(c for c in claims if c)
    assert sum(c is not None for c in claims) == 1
    assert dispatcher.claim() is None
    release.clock[0] += 601
    second = dispatcher.claim()
    assert second['id'] == jid and second['lease_token'] != first['lease_token']
    assert not dispatcher.finish(first, True)
    assert dispatcher.finish(second, False)
    assert row(store, jid)['dispatch_next_at'] > release.clock[0]


def test_transient_export_failure_retries_bounded_and_then_stops(release, monkeypatch):
    store, jid, hid, cid, dispatcher = fixture_queue(release)
    storage = release.extensions['release_storage']
    original = storage.write
    monkeypatch.setattr(storage, 'write', lambda *a, **kw: (_ for _ in ()).throw(OSError('private provider failure')))
    assert run_once(release, dispatcher)
    state = row(store, jid)
    assert state['state'] == 'queued' and state['dispatch_attempts'] == 1
    assert dispatcher.claim() is None
    for attempt in (2, 3):
        release.clock[0] = state['dispatch_next_at']
        assert run_once(release, dispatcher)
        state = row(store, jid)
        assert state['dispatch_attempts'] == attempt
    assert state['state'] == 'failed' and state['error_code'] == 'export_failed'
    release.clock[0] += 4000
    assert dispatcher.claim() is None
    monkeypatch.setattr(storage, 'write', original)


def test_expiry_cleanup_only_export_artifact_is_idempotent_and_retries(release, monkeypatch):
    store, jid, hid, cid, dispatcher = fixture_queue(release)
    assert run_once(release, dispatcher)
    assert row(store, jid)['state'] == 'complete'
    storage = release.extensions['release_storage']
    storage.write(f'{hid}/photos/keep-original', b'original bytes')
    release.clock[0] += 86401
    original = storage.delete
    monkeypatch.setattr(storage, 'delete', lambda *a: (_ for _ in ()).throw(OSError('transient storage error')))
    assert run_once(release, dispatcher)
    state = row(store, jid)
    assert state['state'] == 'complete'
    monkeypatch.setattr(storage, 'delete', original)
    release.clock[0] = state['dispatch_next_at']
    assert run_once(release, dispatcher)
    with store.transaction() as db:
        assert not db.execute(select(m.jobs.c.id).where(m.jobs.c.id == jid)).first()
    assert storage.read(f'{hid}/photos/keep-original') == b'original bytes'
    assert dispatcher.claim() is None


def test_revoked_requester_never_exports_content_and_upgrade_is_idempotent(release):
    store, jid, hid, cid, dispatcher = fixture_queue(release)
    assert upgrade(store) == {'worker_schema_version': 2}
    with store.transaction() as db:
        db.execute(update(m.users).where(m.users.c.email == 'owner@example.test').values(disabled=True))
    assert run_once(release, dispatcher)
    state = row(store, jid)
    assert state['state'] == 'failed' and state['error_code'] == 'permission_revoked'
    assert not state['result_key']
    assert dispatcher.claim() is None


def test_existing_sqlite_upgrade_alters_old_jobs_and_rolls_back_on_failure(tmp_path):
    from release_backend.store import Store
    from sqlalchemy import event
    store = Store('sqlite:///' + str(tmp_path / 'old-rehearsal.sqlite'))
    store.create_schema()
    fields = ('dispatch_attempts', 'dispatch_token', 'dispatch_until', 'dispatch_next_at')
    with store.transaction() as db:
        m.media_purges.drop(db)
        for field in fields:
            db.exec_driver_sql('ALTER TABLE jobs DROP COLUMN ' + field)
    def fail_final_step(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO server_metadata'):
            raise RuntimeError('synthetic final migration failure')
    event.listen(store.engine, 'before_cursor_execute', fail_final_step)
    import pytest
    with pytest.raises(RuntimeError):
        upgrade(store)
    event.remove(store.engine, 'before_cursor_execute', fail_final_step)
    with store.transaction() as db:
        assert not set(fields) & {r[1] for r in db.exec_driver_sql('PRAGMA table_info(jobs)')}
        assert not db.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='media_purges'").first()
    assert upgrade(store) == {'worker_schema_version': 2}
    assert upgrade(store) == {'worker_schema_version': 2}
    with store.transaction() as db:
        assert set(fields) <= {r[1] for r in db.exec_driver_sql('PRAGMA table_info(jobs)')}
    store.engine.dispose()


def test_stale_export_worker_and_manual_replay_cannot_use_active_lease(release):
    from release_backend.jobs import process_export
    store, jid, hid, cid, dispatcher = fixture_queue(release)
    first = dispatcher.claim()
    assert not process_export(release, jid, hid, first['requested_by'])
    release.clock[0] += 601
    current = dispatcher.claim()
    assert not process_export(release, jid, hid, first['requested_by'], dispatch_token=first['lease_token'])
    assert row(store, jid)['state'] == 'queued'
    assert process_export(release, jid, hid, current['requested_by'], dispatch_token=current['lease_token'])


from tests.test_release_media_lifecycle import library, enqueue, removed


def test_only_explicit_media_purges_are_dispatched_and_partial_delete_retries(library, monkeypatch):
    app, owner, hid, cid, aid, prefix = library
    store = app.extensions['release_store']; upgrade(store)
    dispatcher = Dispatcher(store, clock=lambda: app.clock[0])
    # A photo's age or trash state never creates a permanent-deletion task.
    removed(library)
    assert not run_once(app, dispatcher)
    response = app.test_client().post(prefix + '/assets/' + aid + '/purge',
        headers=auth(owner), json={'confirm_permanent_delete': True})
    assert response.status_code == 202
    jid = response.get_json()['purge']['id']
    storage = app.extensions['release_storage']; original = storage.delete
    calls = []
    def transient(key):
        calls.append(key)
        if len(calls) == 2:
            raise OSError('synthetic private provider failure')
        return original(key)
    monkeypatch.setattr(storage, 'delete', transient)
    assert run_once(app, dispatcher)
    with store.transaction() as db:
        state = db.execute(select(m.media_purges).where(m.media_purges.c.id == jid)).mappings().one()
        assert state['state'] == 'queued' and state['dispatch_attempts'] == 1
        assert db.execute(select(m.assets.c.id).where(m.assets.c.id == aid)).first()
        app.clock[0] = state['dispatch_next_at']
    monkeypatch.setattr(storage, 'delete', original)
    assert run_once(app, dispatcher)
    with store.transaction() as db:
        assert db.execute(select(m.media_purges.c.state).where(m.media_purges.c.id == jid)).scalar_one() == 'complete'
        assert not db.execute(select(m.assets.c.id).where(m.assets.c.id == aid)).first()
    assert not run_once(app, dispatcher)


def test_orphan_after_object_write_retries_without_touching_other_media(release, monkeypatch):
    store, jid, hid, cid, dispatcher = fixture_queue(release)
    storage = release.extensions['release_storage']; original = storage.write
    def crash_after_write(key, data):
        original(key, data)
        raise OSError('synthetic crash after immutable object write')
    monkeypatch.setattr(storage, 'write', crash_after_write)
    assert run_once(release, dispatcher)
    assert storage.read(f'{hid}/jobs/{jid}/export.zip')
    state = row(store, jid)
    assert state['state'] == 'queued'
    release.clock[0] = state['dispatch_next_at']
    monkeypatch.setattr(storage, 'write', original)
    assert run_once(release, dispatcher)
    assert row(store, jid)['state'] == 'complete'
