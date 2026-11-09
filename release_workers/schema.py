"""Explicit offline schema version 002; never called by app/worker startup."""

from pathlib import Path
import re
from sqlalchemy import select, insert, update
from release_backend import models as m


def upgrade(
    store,
    *,
    install_dispatch_role=False,
    app_role="openfotoframe_app",
    dispatch_role="openfotoframe_dispatch",
):
    if any(
        not isinstance(role, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role)
        for role in (app_role, dispatch_role)
    ):
        raise ValueError("Use explicit lowercase PostgreSQL role identifiers")
    directory = Path(__file__).resolve().parents[1] / "deploy" / "release"
    with store.transaction() as db:
        if db.dialect.name == "postgresql":
            if not db.exec_driver_sql(
                "SELECT pg_get_userbyid(relowner)=current_user FROM pg_class WHERE oid='public.jobs'::regclass"
            ).scalar_one():
                raise ValueError(
                    "Only the explicit jobs schema owner can upgrade worker schema"
                )
        m.media_purges.create(db, checkfirst=True)
        if db.dialect.name == "sqlite":
            columns = {row[1] for row in db.exec_driver_sql("PRAGMA table_info(jobs)")}
            additions = {
                "dispatch_attempts": "INTEGER NOT NULL DEFAULT 0",
                "dispatch_token": "VARCHAR(64)",
                "dispatch_until": "INTEGER NOT NULL DEFAULT 0",
                "dispatch_next_at": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    db.exec_driver_sql(
                        "ALTER TABLE jobs ADD COLUMN " + name + " " + declaration
                    )
            db.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS jobs_dispatch_queue ON jobs(dispatch_next_at,created_at) WHERE state='queued'"
            )
            db.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS jobs_dispatch_expiry ON jobs(created_at)"
            )
            db.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS media_purges_dispatch_queue ON media_purges(dispatch_next_at,created_at) WHERE state='queued'"
            )
        elif db.dialect.name == "postgresql":
            # PostgreSQL permits multi-statement DDL through the underlying driver.
            cursor = db.connection.driver_connection.cursor()
            try:
                cursor.execute(
                    (directory / "migrations" / "002_worker_dispatch.sql").read_text()
                )
                # The standalone RLS script brackets its own transaction. Keep
                # its statements inside this upgrade's single atomic transaction.
                rls = "\n".join(
                    line
                    for line in (directory / "postgres-rls.sql")
                    .read_text()
                    .splitlines()
                    if line.strip() not in {"BEGIN;", "COMMIT;"}
                )
                cursor.execute(rls)
                cursor.execute(
                    "DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='"
                    + app_role
                    + "') THEN "
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON public.media_purges TO "
                    + app_role
                    + "; "
                    "GRANT EXECUTE ON FUNCTION public.release_storage_usage(text) TO "
                    + app_role
                    + "; "
                    "END IF; END $$;"
                )
                if install_dispatch_role:
                    cursor.execute(
                        (directory / "postgres-dispatch-role.sql")
                        .read_text()
                        .replace("openfotoframe_dispatch", dispatch_role)
                    )
            finally:
                cursor.close()
        else:
            raise ValueError("Only SQLite and PostgreSQL are supported")
        version = db.execute(
            select(m.server_metadata).where(
                m.server_metadata.c.key == "worker_schema_version"
            )
        ).first()
        if version:
            db.execute(
                update(m.server_metadata)
                .where(m.server_metadata.c.key == "worker_schema_version")
                .values(value=2)
            )
        else:
            db.execute(
                insert(m.server_metadata).values(key="worker_schema_version", value=2)
            )
    return {"worker_schema_version": 2}
