"""Execute-only dispatcher and real PostgreSQL lease contracts on disposable DB."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import secrets
import time
import uuid
import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError
from release_backend import models as m
from release_backend.store import Store
from release_workers import Dispatcher, run_once
from release_workers.schema import upgrade
from tests.test_release_postgres import postgres_scope


@pytest.fixture
def worker_pg(postgres_scope):
    scope = postgres_scope
    owner = scope['owner']
    upgrade(owner, app_role=scope['app_role'])
    role = 'off_dispatch_test_' + uuid.uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    from psycopg import sql
    with owner.transaction() as db:
        cursor = db.connection.driver_connection.cursor()
        try:
            content = (Path(__file__).resolve().parents[1] / 'deploy/release/postgres-dispatch-role.sql').read_text()
            cursor.execute(content.replace('openfotoframe_dispatch', role))
            cursor.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
        finally:
            cursor.close()
    store = Store(owner.engine.url.set(username=role, password=password).render_as_string(hide_password=False))
    try:
        yield scope, store
    finally:
        store.engine.dispose()
        with owner.transaction() as db:
            db.exec_driver_sql('DROP OWNED BY "' + role + '"')
            db.exec_driver_sql('DROP ROLE "' + role + '"')


def test_postgres_dispatcher_has_no_content_or_queue_select_and_cannot_upgrade(worker_pg):
    scope, store = worker_pg
    Dispatcher(store)
    for table in ('assets', 'users', 'jobs', 'media_purges'):
        with pytest.raises(DBAPIError):
            with store.transaction() as db:
                db.exec_driver_sql('SELECT * FROM public.' + table)
    with pytest.raises(ValueError, match='schema owner'):
        upgrade(scope['application'])
    with pytest.raises(ValueError):
        Dispatcher(scope['owner'])
    with scope['owner'].transaction() as db:
        db.exec_driver_sql('REVOKE EXECUTE ON FUNCTION public.release_storage_usage(text) FROM "' + scope['app_role'] + '"')
    assert upgrade(scope['owner'], app_role=scope['app_role']) == {'worker_schema_version': 2}
    with scope['application'].transaction() as db:
        assert db.exec_driver_sql("SELECT has_function_privilege(current_user,'public.release_storage_usage(text)','EXECUTE')").scalar_one()


def test_postgres_skip_locked_claim_and_app_role_process_then_expiry_cleanup(worker_pg):
    scope, store = worker_pg
    owner = scope['owner']; app = scope['app']
    jid = uuid.uuid4().hex
    cid = uuid.uuid4().hex
    with owner.transaction() as db:
        db.execute(insert(m.collections).values(id=cid, household_id='a', name='Worker fixture', settings={}))
        db.execute(insert(m.jobs).values(id=jid, household_id='a', requested_by='owner-a',
            collection_id=cid, kind='collection_export', state='queued', created_at=int(time.time())))
    dispatcher = Dispatcher(store)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: dispatcher.claim(), range(2)))
    first = next(c for c in claims if c)
    assert sum(c is not None for c in claims) == 1 and first['id'] == jid
    assert not dispatcher.finish(first | {'lease_token': '0'*64}, True)
    # Simulate a process crash: metadata reservation survives until its lease ends.
    with owner.transaction() as db:
        db.execute(update(m.jobs).where(m.jobs.c.id == jid).values(dispatch_until=0))
    assert run_once(app, dispatcher)
    with owner.transaction() as db:
        assert db.execute(select(m.jobs.c.state).where(m.jobs.c.id == jid)).scalar_one() == 'complete'
        db.execute(update(m.jobs).where(m.jobs.c.id == jid).values(created_at=int(time.time())-86401,
            dispatch_until=0, dispatch_next_at=0))
    assert run_once(app, dispatcher)
    with owner.transaction() as db:
        assert not db.execute(select(m.jobs.c.id).where(m.jobs.c.id == jid)).first()


def test_postgres_upgrade_failure_is_atomic_for_old_schema(worker_pg):
    from sqlalchemy import event
    scope, store = worker_pg
    owner = scope['owner']
    fields = ('dispatch_attempts','dispatch_token','dispatch_until','dispatch_next_at')
    with owner.transaction() as db:
        m.media_purges.drop(db)
        for field in fields:
            db.exec_driver_sql('ALTER TABLE jobs DROP COLUMN ' + field)
    def fail_version(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(('UPDATE server_metadata', 'INSERT INTO server_metadata')):
            raise RuntimeError('synthetic final migration failure')
    event.listen(owner.engine, 'before_cursor_execute', fail_version)
    try:
        with pytest.raises(RuntimeError):
            upgrade(owner)
    finally:
        event.remove(owner.engine, 'before_cursor_execute', fail_version)
    with owner.transaction() as db:
        assert db.exec_driver_sql("SELECT to_regclass('public.media_purges')").scalar_one() is None
        columns = set(db.exec_driver_sql("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='jobs'").scalars())
        assert not set(fields) & columns
    upgrade(owner)
    with owner.transaction() as db:
        db.exec_driver_sql('GRANT SELECT,INSERT,UPDATE,DELETE ON public.media_purges TO "' + scope['app_role'] + '"')


def test_postgres_job_delete_waits_for_worker_and_removes_new_archive(worker_pg, monkeypatch):
    import threading
    from concurrent.futures import TimeoutError
    scope, store = worker_pg
    owner, app = scope['owner'], scope['app']
    jid, cid = uuid.uuid4().hex, uuid.uuid4().hex
    with owner.transaction() as db:
        db.execute(insert(m.collections).values(id=cid, household_id='a', name='Delete race fixture', settings={}))
        db.execute(insert(m.jobs).values(id=jid, household_id='a', requested_by='owner-a',
            collection_id=cid, kind='collection_export', state='queued', created_at=int(time.time())))
    storage = app.extensions['release_storage']
    original = storage.write
    writing, resume = threading.Event(), threading.Event()
    def paused_write(key, data):
        writing.set()
        assert resume.wait(5)
        return original(key, data)
    monkeypatch.setattr(storage, 'write', paused_write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        worker = executor.submit(run_once, app, Dispatcher(store))
        assert writing.wait(5)
        deletion = executor.submit(lambda: app.test_client().delete('/api/v1/households/a/jobs/' + jid,
            headers={'Authorization': 'Bearer owner-a-token'}))
        try:
            with pytest.raises(TimeoutError):
                deletion.result(timeout=0.1)
        finally:
            resume.set()
        assert worker.result(timeout=5)
        assert deletion.result(timeout=5).status_code == 200
    with owner.transaction() as db:
        assert not db.execute(select(m.jobs.c.id).where(m.jobs.c.id == jid)).first()
    with pytest.raises(FileNotFoundError):
        storage.read(f'a/jobs/{jid}/export.zip')
