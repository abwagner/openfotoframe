#!/usr/bin/env python3
"""Execute one persisted export job using its exact stored authorization scope."""

import argparse
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--household", required=True)
    parser.add_argument("--user", required=True)
    args = parser.parse_args(argv)
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) for value in (args.job, args.household, args.user)):
        parser.error("Use opaque database identifiers, not URLs or filesystem paths")
    from release_app import create_app
    from release_backend.jobs import process_export
    app = create_app()
    try:
        completed = process_export(app, args.job, args.household, args.user)
        print("Export job completed." if completed else "No matching authorized queued job was completed.")
        return 0 if completed else 1
    finally:
        app.extensions["release_store"].engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
