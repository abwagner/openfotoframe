"""Transactional import into an empty household; never activates application mode."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import mimetypes
import os
from pathlib import Path
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken
import bcrypt
from sqlalchemy import insert, select

from .plan import MigrationError, MigrationPlan, stable_id


VISUAL_SETTINGS = {"mat_color", "mat_finish", "bevel_width", "border_effect",
    "bevel_lit_intensity", "bevel_lit_v", "bevel_lit_h", "slideshow_interval",
    "transition_duration", "fit_mode", "auto_mat_color", "shuffle", "image_order",
    "target_aspect_ratio"}
DISPLAY_SETTINGS = VISUAL_SETTINGS | {"id", "name", "active", "width", "height", "synchronized", "content"}


def _copy_immutable(source: Path, target: Path, expected: str, root: Path) -> None:
    """Copy to generated private keys and verify the bytes actually copied.

    A crash can leave unreferenced immutable files. A retry adopts matching bytes;
    it never overwrites a different file. The HTTP server must not expose root.
    """
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise MigrationError("Unsafe destination media root")
    cursor = target.parent
    while cursor != root:
        if cursor.is_symlink():
            raise MigrationError("Unsafe destination media directory")
        cursor = cursor.parent
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file() or _digest(target) != expected:
            raise MigrationError("Destination media key already contains different bytes")
        return
    temporary = target.with_name(target.name + ".import-" + secrets.token_hex(8))
    digest = hashlib.sha256()
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(source_fd, "rb") as incoming, temporary.open("xb") as outgoing:
            temporary.chmod(0o600)
            while chunk := incoming.read(1024 * 1024):
                digest.update(chunk)
                outgoing.write(chunk)
        if digest.hexdigest() != expected:
            raise MigrationError("Backup media changed after planning; take a consistent snapshot")
        # The generated key is never shared with another migration. A simultaneous
        # identical migration may install only identical bytes under that key.
        if target.exists():
            if target.is_symlink() or _digest(target) != expected:
                raise MigrationError("Concurrent destination media conflict")
        else:
            temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _mfa(plan: MigrationPlan, legacy: dict, new_key: bytes | str | None) -> dict:
    mfa = deepcopy(legacy.get("mfa") or {})
    if mfa.get("totp_secret"):
        if not new_key or not plan.mfa_key:
            raise MigrationError("Both source and destination MFA keys are required")
        try:
            raw = Fernet(plan.mfa_key).decrypt(mfa["totp_secret"].encode())
            key = new_key.encode() if isinstance(new_key, str) else new_key
            mfa["totp_secret"] = Fernet(key).encrypt(raw).decode()
        except (ValueError, InvalidToken) as exc:
            raise MigrationError("Cannot preserve legacy authenticator material") from exc
    return mfa


def _known_default_administrator_password(user: dict) -> bool:
    legacy = user["legacy"]
    if user["role"] not in {"owner", "admin"}:
        return False
    stored = legacy["password_hash"]
    if stored.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(b"password", stored.encode())
        except ValueError as exc:
            raise MigrationError("Invalid legacy password hash") from exc
    candidate = hashlib.sha256((legacy.get("salt", "") + "password").encode()).hexdigest()
    return secrets.compare_digest(candidate, stored)


def import_legacy(plan: MigrationPlan, store, *, media_root: str | Path,
                  mfa_encryption_key: bytes | str | None = None,
                  household_name: str = "Imported household") -> dict:
    """Import all metadata in one DB transaction and record the completed manifest.

    Use a schema-owner/offline migration connection, not the hosted HTTP role.
    Reusing migration_id with different source bytes fails. Existing destination
    identities are never merged by matching email or old username. The caller must
    retain the write-paused source snapshot until validation and rollback rehearsal.
    """
    from release_backend import models as m

    root = Path(media_root).absolute()
    if root.resolve().is_relative_to(plan.source_root):
        raise MigrationError("Destination media must be outside the source snapshot")
    # A forced web password-change challenge still authenticates a known secret
    # and lets an attacker claim ownership. Block the entire import before any
    # writes; only an offline operator can replace this source credential.
    if any(_known_default_administrator_password(user) for user in plan.users):
        raise MigrationError("Activation blocked: replace known default administrator credentials offline before import")
    now = int(time.time())
    with store.transaction() as conn:
        prior = conn.execute(select(m.migrations).where(m.migrations.c.migration_id == plan.migration_id)).mappings().first()
        if prior:
            if prior["manifest"].get("fingerprint") != plan.fingerprint:
                raise MigrationError("Migration ID already belongs to a different snapshot")
            return {**prior["manifest"], "already_imported": True}
        # A completely different migration cannot silently absorb an existing
        # account merely because an operator selected the same login identifier.
        identifiers = [u["email"] for u in plan.users]
        usernames = [u["username"] for u in plan.users]
        collision = conn.execute(select(m.users.c.id).where(
            m.users.c.email.in_(identifiers) | m.users.c.username.in_(usernames))).first()
        if collision:
            raise MigrationError("Destination identity collision; explicit account reconciliation is required")
        if conn.execute(select(m.households.c.id).where(m.households.c.id == plan.household_id)).first():
            raise MigrationError("Destination household already exists without this migration manifest")
        visual_settings = {k: deepcopy(v) for k, v in plan.settings.items() if k in VISUAL_SETTINGS}
        household_settings = {**visual_settings,
            "mfa_mode": deepcopy(plan.settings.get("mfa_mode", "disabled")),
            "mfa_methods": deepcopy(plan.settings.get("mfa_methods", "either"))}
        conn.execute(insert(m.households).values(id=plan.household_id, name=household_name,
                                               settings=household_settings, created_at=now))
        conn.execute(insert(m.collections).values(id=plan.collection_id, household_id=plan.household_id,
                                                name="Legacy shared photos", settings=visual_settings))
        by_name = {u["username"]: u["id"] for u in plan.users}
        memberships = {}
        for user in plan.users:
            legacy = user["legacy"]
            conn.execute(insert(m.users).values(id=user["id"], email=user["email"],
                username=user["username"], email_verified=False,
                password_hash=legacy["password_hash"], legacy_salt=legacy.get("salt"),
                mfa=_mfa(plan, legacy, mfa_encryption_key),
                must_change_password=bool(legacy.get("must_change_password", False)),
                disabled=bool(legacy.get("disabled", False)), created_at=now))
            membership_id = stable_id(plan.migration_id, "membership", user["username"])
            memberships[user["username"]] = membership_id
            conn.execute(insert(m.memberships).values(id=membership_id, household_id=plan.household_id,
                user_id=user["id"], role=user["role"], active=not bool(legacy.get("disabled", False))))
            if user["role"] == "member":
                conn.execute(insert(m.collection_grants).values(
                    id=stable_id(plan.migration_id, "collection-grant", user["username"]),
                    household_id=plan.household_id, membership_id=membership_id,
                    collection_id=plan.collection_id, role="editor"))
        derived_by_path = {f["relative_path"]: f for f in plan.derived_files}
        for asset in plan.assets:
            key = f"{plan.household_id}/{asset['id']}/original"
            _copy_immutable(Path(asset["file"]["path"]), root / key, asset["file"]["sha256"], root)
            thumbnail_key = None
            # Legacy thumbnails may retain the original suffix, or use PNG/JPEG.
            name = asset["filename"]
            candidates = [f"thumbnails/{name}", f"thumbnails/{Path(name).stem}.jpg",
                          f"thumbnails/{Path(name).stem}.png"]
            thumb = next((derived_by_path[p] for p in candidates if p in derived_by_path), None)
            if thumb:
                thumbnail_key = f"{plan.household_id}/{asset['id']}/thumbnail"
                _copy_immutable(Path(thumb["path"]), root / thumbnail_key, thumb["sha256"], root)
            meta = deepcopy(asset["metadata"])
            conn.execute(insert(m.assets).values(id=asset["id"], household_id=plan.household_id,
                collection_id=plan.collection_id, filename=name, storage_key=key,
                thumbnail_key=thumbnail_key, sha256=asset["file"]["sha256"],
                byte_size=asset["file"]["bytes"], mime_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                uploaded_by=by_name.get(meta.get("uploaded_by")), metadata=meta,
                deleted=False, created_at=now))
        for group in plan.groups:
            conn.execute(insert(m.groups).values(id=group["id"], household_id=plan.household_id,
                collection_id=plan.collection_id, name=str(group["metadata"].get("title") or ""),
                metadata=deepcopy(group["metadata"])))
            for position, asset_id in enumerate(group["asset_ids"]):
                conn.execute(insert(m.group_assets).values(household_id=plan.household_id,
                    collection_id=plan.collection_id, group_id=group["id"], asset_id=asset_id, position=position))
        for frame in plan.frames:
            settings = {k: deepcopy(v) for k, v in frame["metadata"].items() if k in DISPLAY_SETTINGS}
            conn.execute(insert(m.frames).values(id=frame["id"], household_id=plan.household_id,
                name=str(settings.get("name") or "Imported frame"), settings=settings,
                state={}, schedules=deepcopy(plan.settings.get("tv_schedules", []))))
            conn.execute(insert(m.frame_collections).values(household_id=plan.household_id,
                frame_id=frame["id"], collection_id=plan.collection_id))
            # Old ordinary users had shared display control; make that legacy
            # grant explicit only for the existing imported frames.
            for user in plan.users:
                if user["role"] == "member":
                    conn.execute(insert(m.frame_grants).values(
                        id=stable_id(plan.migration_id, "frame-grant", user["username"] + ":" + frame["id"]),
                        household_id=plan.household_id, membership_id=memberships[user["username"]],
                        frame_id=frame["id"], role="controller"))
        # Preserve derived renders privately for audit/rollback; they are not
        # activated as playlists and can never bypass the media authorization API.
        for item in plan.derived_files:
            key = f"{plan.household_id}/legacy-derived/{hashlib.sha256(item['relative_path'].encode()).hexdigest()}"
            _copy_immutable(Path(item["path"]), root / key, item["sha256"], root)
        manifest = {**plan.summary(), "completed_at": now,
                    "legacy_visual_settings_preserved": True,
                    "operator_settings_require_review": sorted(set(plan.settings) - VISUAL_SETTINGS - {"displays", "tv_schedules"}),
                    "legacy_mfa_policy": {"mode": plan.settings.get("mfa_mode", "disabled"),
                                          "methods": plan.settings.get("mfa_methods", "either")},
                    "source_sessions_imported": False,
                    "display_credentials_imported": False}
        conn.execute(insert(m.migrations).values(migration_id=plan.migration_id,
                     household_id=plan.household_id, manifest=manifest, created_at=now))
        conn.execute(insert(m.audit_events).values(id=stable_id(plan.migration_id, "audit", "import"),
            household_id=plan.household_id, actor_id=by_name[plan.owner_username],
            action="legacy_import_completed", resource_id=plan.household_id, created_at=now))
    return {**manifest, "already_imported": False}


def verify_import(plan: MigrationPlan, store, *, media_root: str | Path) -> dict:
    """Verify counts, metadata and every original byte against the source plan."""
    from release_backend import models as m
    from sqlalchemy import func
    expected = plan.summary()["counts"]
    root = Path(media_root).absolute()
    with store.transaction() as conn:
        migration = conn.execute(select(m.migrations).where(m.migrations.c.migration_id == plan.migration_id)).mappings().first()
        if not migration or migration["manifest"].get("fingerprint") != plan.fingerprint:
            raise MigrationError("No matching completed migration")
        counts = {}
        for label, table in (("assets", m.assets), ("groups", m.groups), ("frames", m.frames), ("users", m.memberships)):
            counts[label] = conn.execute(select(func.count()).select_from(table).where(table.c.household_id == plan.household_id)).scalar_one()
            if counts[label] != expected[label]:
                raise MigrationError("Imported resource count does not match snapshot")
        imported = {a["id"]: a for a in conn.execute(select(m.assets).where(m.assets.c.household_id == plan.household_id)).mappings()}
        for asset in plan.assets:
            row = imported[asset["id"]]
            path = root / row["storage_key"]
            if row["metadata"] != asset["metadata"] or row["sha256"] != asset["file"]["sha256"]:
                raise MigrationError("Imported asset metadata does not match snapshot")
            if path.is_symlink() or not path.is_file() or _digest(path) != asset["file"]["sha256"]:
                raise MigrationError("Imported original bytes do not match snapshot")
    return {"verified": True, "fingerprint": plan.fingerprint, "counts": counts}
