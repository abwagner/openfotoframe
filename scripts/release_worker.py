#!/usr/bin/env python3
"""Bounded durable export/purge dispatch, or an exact authorized export replay."""

import argparse
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


HEARTBEAT = Path("/tmp/openfotoframe-release-worker.heartbeat")
# The queue allows a claimed job 600 seconds before another worker can take it.
ACTIVE_LEASE_SECONDS = 600


def write_heartbeat(phase, path=HEARTBEAT):
    temp = path.with_suffix(".new")
    try:
        temp.write_text(phase + "\n", encoding="ascii")
        temp.replace(path)
    except OSError:
        # A monitor filesystem failure must never change the outcome of a job.
        pass


def clear_heartbeat():
    try:
        HEARTBEAT.unlink(missing_ok=True)
    except OSError:
        pass


def heartbeat_ready(poll_seconds, path=HEARTBEAT):
    try:
        with path.open(encoding="ascii") as marker:
            phase = marker.read(16).strip()
            age = time.time() - os.fstat(marker.fileno()).st_mtime
    except (OSError, UnicodeError):
        return False
    limits = {"poll": 3 * poll_seconds + 10, "claimed": ACTIVE_LEASE_SECONDS}
    return phase in limits and 0 <= age <= limits[phase]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-heartbeat", action="store_true")
    mode.add_argument(
        "--once", action="store_true", help="Claim at most one due job, then exit"
    )
    mode.add_argument(
        "--supervise",
        action="store_true",
        help="Poll bounded work until SIGTERM/SIGINT",
    )
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--job")
    parser.add_argument("--household")
    parser.add_argument("--user")
    args = parser.parse_args(argv)
    if not 1 <= args.poll_seconds <= 60:
        parser.error("Poll interval must be 1 to 60 seconds")
    explicit = any((args.job, args.household, args.user))
    if explicit and (args.once or args.supervise or args.check_heartbeat):
        parser.error("Explicit job scope cannot be combined with dispatcher modes")
    if explicit and (
        not all((args.job, args.household, args.user))
        or any(
            not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)
            for value in (args.job, args.household, args.user)
            if value
        )
    ):
        parser.error("Use all three opaque job/household/user identifiers")
    if args.check_heartbeat:
        return 0 if heartbeat_ready(args.poll_seconds) else 1
    if not explicit and not (args.once or args.supervise):
        parser.error("Choose --once, --supervise or an exact job scope")
    app = dispatch_store = None
    try:
        if args.supervise:
            clear_heartbeat()
        from release_app import create_app
        from release_backend.store import Store

        app = create_app()
        if explicit:
            from release_backend.jobs import process_export

            completed = process_export(app, args.job, args.household, args.user)
            print(
                "Export job completed."
                if completed
                else "No authorized queued export completed."
            )
            return 0 if completed else 1
        from release_workers import Dispatcher, run_once

        app_store = app.extensions["release_store"]
        if app_store.engine.dialect.name == "postgresql":
            if not os.environ.get("DISPATCH_DATABASE_URL"):
                raise ValueError("Configure the separate dispatcher identity")
            dispatch_store = Store(os.environ["DISPATCH_DATABASE_URL"])
        else:
            dispatch_store = app_store
        dispatcher = Dispatcher(dispatch_store)
        stop = threading.Event()
        if args.supervise:
            signal.signal(signal.SIGTERM, lambda *a: stop.set())
            signal.signal(signal.SIGINT, lambda *a: stop.set())
        def mark_claimed():
            write_heartbeat("claimed")

        while True:
            run_once(app, dispatcher, on_claim=mark_claimed if args.supervise else None)
            if args.supervise:
                write_heartbeat("poll")
            if args.once or stop.is_set():
                break
            # Bound dispatch throughput even with a persistent queue; errors and
            # idle loops never spin, and signals interrupt the next poll wait.
            stop.wait(args.poll_seconds)
            if stop.is_set():
                break
        print("Worker pass finished." if args.once else "Worker stopped.")
        return 0
    except Exception:
        print(
            "Worker could not complete this pass. Review private operator configuration.",
            file=sys.stderr,
        )
        return 1
    finally:
        if args.supervise:
            clear_heartbeat()
        if app:
            app.extensions["release_store"].engine.dispose()
        if dispatch_store and (
            not app or dispatch_store is not app.extensions["release_store"]
        ):
            dispatch_store.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
