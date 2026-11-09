"""Crash-recoverable queue leases; execution retains the ordinary API/RLS identity."""

import secrets
import time
from sqlalchemy import select, update, delete, and_, or_, text
from release_backend import models as m


class Dispatcher:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock
        if store.engine.dialect.name == "postgresql":
            with store.transaction() as db:
                unsafe = db.execute(
                    text("""SELECT r.rolsuper OR r.rolbypassrls OR r.rolcreaterole
                    OR r.rolcreatedb OR r.rolreplication
                    OR EXISTS(SELECT 1 FROM pg_roles parent WHERE parent.oid<>r.oid
                        AND pg_has_role(current_user,parent.oid,'MEMBER'))
                    OR EXISTS(SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
                        WHERE n.nspname='public' AND t.relkind IN ('r','p','v','m','f') AND
                        (has_table_privilege(current_user,t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                        OR has_any_column_privilege(current_user,t.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
                    FROM pg_roles r WHERE r.rolname=current_user""")
                ).scalar_one()
                if unsafe:
                    raise ValueError(
                        "Use the separate execute-only dispatcher database role"
                    )

    def claim(self):
        token, ts = secrets.token_hex(32), int(self.clock())
        with self.store.transaction() as db:
            if db.dialect.name == "postgresql":
                row = (
                    db.execute(
                        text("SELECT * FROM public.release_claim_work(:token)"),
                        {"token": token},
                    )
                    .mappings()
                    .first()
                )
                return dict(row) if row else None
            # BEGIN IMMEDIATE serializes selection/reservation for local SQLite.
            candidates = []
            for kind, table in (("export", m.jobs), ("media_purge", m.media_purges)):
                eligible = table.c.state == "queued"
                if kind == "export":
                    eligible = and_(
                        table.c.kind == "collection_export",
                        or_(table.c.created_at <= ts - 86400, eligible),
                    )
                row = (
                    db.execute(
                        select(table)
                        .where(
                            and_(
                                eligible,
                                table.c.dispatch_until <= ts,
                                table.c.dispatch_next_at <= ts,
                            )
                        )
                        .order_by(table.c.created_at, table.c.id)
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if row:
                    candidates.append((kind, table, row))
            if not candidates:
                return None
            kind, table, row = min(
                candidates,
                key=lambda item: (item[2]["created_at"], item[0], item[2]["id"]),
            )
            db.execute(
                update(table)
                .where(table.c.id == row["id"])
                .values(
                    dispatch_token=token,
                    dispatch_until=ts + 600,
                    dispatch_attempts=min(row["dispatch_attempts"] + 1, 1000),
                )
            )
            return {k: row[k] for k in ("id", "household_id", "requested_by")} | {
                "kind": kind,
                "lease_token": token,
                "action": "cleanup"
                if kind == "export" and row["created_at"] <= ts - 86400
                else "exhausted"
                if row["dispatch_attempts"] >= 3
                else kind,
            }

    def finish(self, job, success):
        ts = int(self.clock())
        with self.store.transaction() as db:
            if db.dialect.name == "postgresql":
                return db.execute(
                    text(
                        "SELECT public.release_finish_work(:kind,:id,:token,:success)"
                    ),
                    {
                        "kind": job["kind"],
                        "id": job["id"],
                        "token": job["lease_token"],
                        "success": bool(success),
                    },
                ).scalar_one()
            table = m.jobs if job["kind"] == "export" else m.media_purges
            row = (
                db.execute(
                    select(table).where(
                        and_(
                            table.c.id == job["id"],
                            table.c.dispatch_token == job["lease_token"],
                        )
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                return False
            if job["kind"] == "export" and row["created_at"] <= ts - 86400 and success:
                db.execute(delete(table).where(table.c.id == row["id"]))
                return True
            values = dict(
                dispatch_token=None,
                dispatch_until=0,
                dispatch_next_at=ts
                + min(3600, 30 * 2 ** min(row["dispatch_attempts"], 7)),
            )
            if not (job["kind"] == "export" and row["created_at"] <= ts - 86400):
                if row["state"] == "queued" and row["dispatch_attempts"] >= 3:
                    values.update(
                        state="failed",
                        error_code="worker_attempts_exhausted",
                        completed_at=ts,
                    )
                elif (
                    job["kind"] == "export"
                    and row["state"] == "failed"
                    and row["error_code"] == "export_failed"
                    and row["dispatch_attempts"] < 3
                ):
                    values.update(state="queued", error_code=None, completed_at=None)
            db.execute(update(table).where(table.c.id == row["id"]).values(**values))
            return True


def run_once(app, dispatcher):
    job = dispatcher.claim()
    if not job:
        return False
    success = False
    try:
        if job["action"] == "cleanup":
            # A generated key is the only permitted deletion. Do not trust a
            # path/result key supplied through a queue row or client payload.
            app.extensions["release_storage"].delete(
                f"{job['household_id']}/jobs/{job['id']}/export.zip"
            )
            success = True
        elif job["action"] == "export":
            from release_backend.jobs import process_export

            success = process_export(
                app,
                job["id"],
                job["household_id"],
                job["requested_by"],
                dispatch_token=job["lease_token"],
            )
        elif job["action"] == "media_purge":
            from release_backend.media_lifecycle import process_media_purge

            success = process_media_purge(
                app,
                job["id"],
                job["household_id"],
                job["requested_by"],
                dispatch_token=job["lease_token"],
            )
        # Exhausted crash attempts become a generic terminal failure in finish.
    except Exception:
        # Do not log storage credentials, filenames or tenant identifiers. A crash
        # before finish leaves a durable lease that another process can reclaim.
        success = False
    dispatcher.finish(job, success)
    return True
