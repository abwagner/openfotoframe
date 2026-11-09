#!/usr/bin/env python3
"""Explicit offline schema setup. Never run automatically from HTTP startup."""

import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-postgres-roles", action="store_true",
                        help="Also create the fixed least-privilege staging roles; requires cluster role administration")
    args = parser.parse_args(argv)
    url = os.environ.get("OPENFOTOFRAME_SCHEMA_DATABASE_URL")
    if not url:
        parser.error("OPENFOTOFRAME_SCHEMA_DATABASE_URL is required; use a separate schema migration identity")
    from release_backend.store import Store
    from release_billing import models  # Register shared metadata before create_all.
    from sqlalchemy import text
    store = Store(url)
    try:
        if store.engine.dialect.name == "postgresql":
            with store.engine.connect() as conn:
                current = conn.execute(text("SELECT current_user")).scalar_one()
                if current in {"openfotoframe_app", "openfotoframe_billing"}:
                    parser.error("The HTTP/billing role must never own the schema or security functions")
        store.create_schema()
        if store.engine.dialect.name == "postgresql":
            directory = Path(__file__).resolve().parents[1] / "deploy" / "release"
            files = [directory / "postgres-rls.sql"]
            if args.install_postgres_roles:
                files.append(directory / "postgres-roles.sql")
            raw = store.engine.raw_connection()
            try:
                for path in files:
                    with raw.cursor() as cursor:
                        cursor.execute(path.read_text())
                    raw.commit()
            finally:
                raw.close()
        print("Release schema prepared. No application traffic or legacy data was switched.")
        return 0
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
