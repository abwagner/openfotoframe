#!/usr/bin/env python3
"""Offline self-hosted recovery link; never prints bearer links or resets MFA."""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--household-id", required=True)
    parser.add_argument("--output", required=True, help="New file inside private 0700 directory outside checkout")
    args = parser.parse_args(argv)
    store = None
    try:
        if os.environ.get("RELEASE_MODE") != "self_hosted":
            raise ValueError("Explicit self-hosted operator context required")
        from release_backend.store import Store
        from release_delivery.offline import issue_recovery
        store = Store(os.environ["DATABASE_URL"])
        issue_recovery(store, user_id=args.user_id, household_id=args.household_id,
            public_origin=os.environ["PUBLIC_ORIGIN"], output_path=args.output)
        print("Recovery link written privately; expires in 30 minutes. Enrolled MFA remains required.")
        return 0
    except Exception:
        print("Recovery issuance failed. Check the private operator configuration.", file=sys.stderr)
        return 1
    finally:
        if store:
            store.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
