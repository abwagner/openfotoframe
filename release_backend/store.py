"""Transactional storage shared by self-hosted and hosted release applications."""

from contextlib import contextmanager
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool
from .models import metadata


class Store:
    def __init__(self, database_url):
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
        options = {"pool_pre_ping": True}
        if database_url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in database_url:
                options["poolclass"] = StaticPool
        self.engine = create_engine(database_url, **options)
        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def configure(db, _):
                db.isolation_level = None
                db.execute("PRAGMA foreign_keys=ON")

            @event.listens_for(self.engine, "begin")
            def begin(connection):
                # All policy checks and writes share a reservation, preventing
                # grant revocation or single-use-token races in SQLite.
                connection.exec_driver_sql("BEGIN IMMEDIATE")

    def create_schema(self):
        metadata.create_all(self.engine)

    def set_scope(self, db, user_id=None, household_id=None, frame_id=None):
        if self.engine.dialect.name == "postgresql":
            for key, value in [
                ("user_id", user_id),
                ("household_id", household_id),
                ("frame_id", frame_id),
            ]:
                db.execute(
                    text("SELECT set_config(:key, :value, true)"),
                    {"key": "app." + key, "value": value or ""},
                )

    def validate_application_role(self):
        """Refuse cloud startup with a role that silently bypasses tenant RLS."""
        if self.engine.dialect.name != "postgresql":
            raise ValueError("The hosted application role must use PostgreSQL")
        from .models import TENANT_TABLES, households

        tables = [households, *TENANT_TABLES]
        try:
            from release_billing.models import TENANT_TABLES as billing_tables
        except ImportError:
            billing_tables = []
        tables.extend(billing_tables)
        with self.engine.connect() as db:
            role = (
                db.execute(
                    text(
                        "SELECT oid,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                )
                .mappings()
                .one()
            )
            if role["rolsuper"] or role["rolbypassrls"]:
                raise ValueError(
                    "Hosted HTTP roles must not be superusers or BYPASSRLS"
                )
            for table in tables:
                row = (
                    db.execute(
                        text(
                            "SELECT relowner,relrowsecurity,pg_has_role(current_user,relowner,'MEMBER') AS owns_via_role FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='public' AND relname=:name"
                        ),
                        {"name": table.name},
                    )
                    .mappings()
                    .first()
                )
                if (
                    not row
                    or not row["relrowsecurity"]
                    or row["relowner"] == role["oid"]
                    or row["owns_via_role"]
                ):
                    raise ValueError(
                        "Hosted tenant tables require RLS and a separate schema owner"
                    )

    @contextmanager
    def transaction(self, user_id=None, household_id=None, frame_id=None):
        with self.engine.begin() as db:
            self.set_scope(db, user_id, household_id, frame_id)
            yield db
