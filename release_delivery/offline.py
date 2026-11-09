"""Offline operator recovery for an existing self-hosted household account."""
import hashlib
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit, quote
from sqlalchemy import select, insert, update
from release_backend import models as m


def issue_recovery(store, *, user_id, household_id, public_origin, output_path, clock=time.time):
    if store.engine.dialect.name != "sqlite":
        raise ValueError("Offline recovery is limited to self-hosted SQLite")
    origin = public_origin.rstrip("/")
    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise ValueError("Use the configured server root origin")
    output = Path(output_path).absolute()
    checkout = Path(__file__).resolve().parents[1]
    if output.resolve().is_relative_to(checkout):
        raise ValueError("Recovery output must be outside the public checkout")
    parent = output.parent
    if parent.is_symlink() or parent.resolve() != parent or not parent.is_dir() or parent.stat().st_mode & 0o077:
        raise ValueError("Recovery output requires an existing private directory with mode 0700")
    token = secrets.token_urlsafe(32)
    created_file = False
    try:
        with store.transaction() as db:
            user = db.execute(select(m.users).where(m.users.c.id == user_id)).mappings().first()
            member = db.execute(select(m.memberships).where((m.memberships.c.user_id == user_id) &
                (m.memberships.c.household_id == household_id) & m.memberships.c.active.is_(True))).first()
            if not user or user["disabled"] or not member:
                raise ValueError("An existing enabled household member is required")
            # A fresh operator request invalidates previous unused reset links.
            db.execute(update(m.challenges).where((m.challenges.c.user_id == user_id) &
                (m.challenges.c.purpose == "recovery")).values(consumed=True))
            db.execute(insert(m.challenges).values(id=secrets.token_hex(16),
                token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user_id,
                purpose="recovery", data={"offline_operator": True}, expires_at=int(clock()) + 1800,
                attempts=0, consumed=False))
            db.execute(insert(m.audit_events).values(id=secrets.token_hex(16), household_id=household_id,
                actor_id=None, action="offline_recovery_issued", resource_id=user_id, created_at=int(clock())))
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            created_file = True
            with os.fdopen(fd, "w") as file:
                file.write(origin + "/recover#token=" + quote(token, safe="") + "\n")
                file.flush()
                os.fsync(file.fileno())
    except Exception:
        if created_file:
            output.unlink(missing_ok=True)
        raise
    return {"issued": True, "expires_in": 1800}
