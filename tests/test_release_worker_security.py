"""Execute-only dispatcher identities must not gain content access through roles."""
import pytest
from sqlalchemy import text
from release_workers import Dispatcher
from tests.test_release_postgres import postgres_scope
from tests.test_release_workers_postgres import worker_pg


def test_dispatch_identity_rejects_noinherit_membership_that_permits_set_role(worker_pg):
    scope, store = worker_pg
    dispatch_role = store.engine.url.username
    # NOINHERIT suppresses immediate SELECT, but SET ROLE would recover all
    # application's content privileges. Startup validation must inspect membership.
    with scope['owner'].transaction() as db:
        db.exec_driver_sql('GRANT "' + scope['app_role'] + '" TO "' + dispatch_role + '"')
    with store.transaction() as db:
        assert not db.execute(text("SELECT has_table_privilege(current_user,'public.assets','SELECT')")).scalar_one()
    with pytest.raises(ValueError, match='dispatcher'):
        Dispatcher(store)


def test_dispatch_identity_rejects_write_only_content_privilege(worker_pg):
    scope, store = worker_pg
    dispatch_role = store.engine.url.username
    with scope['owner'].transaction() as db:
        db.exec_driver_sql('GRANT UPDATE ON public.assets TO "' + dispatch_role + '"')
    with store.transaction() as db:
        assert not db.execute(text("SELECT has_table_privilege(current_user,'public.assets','SELECT')")).scalar_one()
    with pytest.raises(ValueError, match='dispatcher'):
        Dispatcher(store)
