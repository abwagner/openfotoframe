#!/usr/bin/env python3
"""Offline dry-run/apply/verify tool. This never deploys or activates the release app."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_migration import MigrationError, plan_legacy_import


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Consistent backup containing data/ and uploads/")
    parser.add_argument("--migration-id", required=True, help="Stable operator-chosen identifier for this snapshot")
    parser.add_argument("--owner", required=True, help="Existing legacy administrator username to become owner")
    parser.add_argument("--email-map", required=True, help="Private JSON file mapping every old username to a unique login email")
    parser.add_argument("--media-root", help="New private media directory, outside the source snapshot")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--acknowledge-write-pause", action="store_true")
    args = parser.parse_args(argv)
    try:
        mapping = json.loads(Path(args.email_map).read_text())
        plan = plan_legacy_import(args.source, migration_id=args.migration_id,
                                  owner_username=args.owner, email_map=mapping)
        result = {"dry_run": True, **plan.summary()}
        if args.apply or args.verify:
            if not args.media_root or not os.environ.get("OPENFOTOFRAME_DATABASE_URL"):
                parser.error("Apply/verify requires --media-root and OPENFOTOFRAME_DATABASE_URL")
            if args.apply and not args.acknowledge_write_pause:
                parser.error("Apply requires an acknowledged write-paused consistent snapshot")
            from sqlalchemy.engine import make_url
            from release_backend.store import Store
            from release_migration.importer import import_legacy, verify_import
            url = make_url(os.environ["OPENFOTOFRAME_DATABASE_URL"])
            if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
                if Path(url.database).resolve().is_relative_to(plan.source_root):
                    raise MigrationError("Destination database must be outside the source snapshot")
            store = Store(str(url.render_as_string(hide_password=False)))
            try:
                if args.apply:
                    # Schema initialization is a distinct operator step for hosted
                    # PostgreSQL. SQLite new installations can initialize locally.
                    if store.engine.dialect.name == "sqlite":
                        store.create_schema()
                    result = import_legacy(plan, store, media_root=args.media_root,
                        mfa_encryption_key=os.environ.get("OPENFOTOFRAME_MFA_ENCRYPTION_KEY"))
                else:
                    result = verify_import(plan, store, media_root=args.media_root)
            finally:
                store.engine.dispose()
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except Exception:
        # Paths, URLs, identities and source rows may contain operational secrets.
        # Do not print exception text or SQL parameters into a shared terminal log.
        print("Migration failed validation. Check the private snapshot/configuration; no release mode was activated.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
