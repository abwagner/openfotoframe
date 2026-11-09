#!/usr/bin/env python3
"""Offline additive worker schema migration 002; no traffic/worker activation."""

import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dispatch-role", action="store_true")
    parser.add_argument("--app-role", default="openfotoframe_app")
    parser.add_argument("--dispatch-role", default="openfotoframe_dispatch")
    args = parser.parse_args(argv)
    store = None
    try:
        from release_backend.store import Store
        from release_workers.schema import upgrade

        store = Store(os.environ["OPENFOTOFRAME_SCHEMA_DATABASE_URL"])
        upgrade(
            store,
            install_dispatch_role=args.install_dispatch_role,
            app_role=args.app_role,
            dispatch_role=args.dispatch_role,
        )
        print("Worker schema version 002 prepared. No worker or traffic was started.")
        return 0
    except Exception:
        print(
            "Offline worker migration failed. Review the private schema-owner configuration.",
            file=sys.stderr,
        )
        return 1
    finally:
        if store:
            store.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
