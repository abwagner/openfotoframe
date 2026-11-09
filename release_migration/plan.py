"""Validate a consistent legacy backup and prepare a deterministic import plan.

The plan contains password hashes and MFA records and must never be logged or
serialized to a public artifact. ``summary`` is the intentionally redacted view.
No legacy module is imported: doing so would initialize credentials and jobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import uuid


class MigrationError(ValueError):
    pass


def stable_id(migration_id: str, kind: str, legacy_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"openfotoframe:{migration_id}:{kind}:{legacy_id}"))


def _json(path: Path, required: bool = True) -> dict:
    if not path.exists() and not required:
        return {}
    if path.is_symlink() or not path.is_file():
        raise MigrationError(f"Missing or unsafe backup metadata: {path.name}")
    try:
        value = json.loads(path.read_text())
    except (ValueError, UnicodeError) as exc:
        raise MigrationError(f"Invalid JSON metadata: {path.name}") from exc
    if not isinstance(value, dict):
        raise MigrationError(f"Expected JSON object: {path.name}")
    return value


def _filename(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise MigrationError("Unsafe media filename in backup")
    if PurePosixPath(value).name != value or value in {".", ".."} or "\x00" in value:
        raise MigrationError("Unsafe media filename in backup")
    return value


def _file(path: Path, base: Path) -> dict:
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(base.resolve()):
        raise MigrationError("Missing or unsafe media file in backup")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": size}


@dataclass(frozen=True)
class MigrationPlan:
    migration_id: str
    fingerprint: str
    household_id: str
    collection_id: str
    owner_username: str = field(repr=False)
    users: tuple[dict, ...] = field(repr=False)
    assets: tuple[dict, ...] = field(repr=False)
    groups: tuple[dict, ...] = field(repr=False)
    frames: tuple[dict, ...] = field(repr=False)
    settings: dict = field(repr=False)
    derived_files: tuple[dict, ...] = field(repr=False)
    source_root: Path = field(repr=False)
    mfa_key: bytes | None = field(repr=False)

    def summary(self) -> dict:
        """Public-safe counters; deliberately excludes identities and credentials."""
        return {
            "migration_id": self.migration_id,
            "fingerprint": self.fingerprint,
            "household_id": self.household_id,
            "collection_id": self.collection_id,
            "counts": {"users": len(self.users), "assets": len(self.assets),
                       "groups": len(self.groups), "frames": len(self.frames),
                       "derived_files": len(self.derived_files)},
            "original_bytes": sum(a["file"]["bytes"] for a in self.assets),
            "has_legacy_mfa": any(bool(u["legacy"].get("mfa")) for u in self.users),
            "requires_frame_reenrollment": True,
        }


def plan_legacy_import(source: str | Path, *, migration_id: str, owner_username: str,
                       email_map: dict[str, str]) -> MigrationPlan:
    """Read an operator-selected backup; never infer accounts from email matches.

    ``source`` must contain data/ and uploads/ from the same write-paused snapshot.
    Operators explicitly map every old username to a login identifier. No existing
    destination account is merged or updated by this migration.
    """
    root = Path(source).resolve()
    if not migration_id or len(migration_id) > 100:
        raise MigrationError("An explicit migration ID of at most 100 characters is required")
    data, uploads = root / "data", root / "uploads"
    if data.is_symlink() or uploads.is_symlink() or not uploads.is_dir():
        raise MigrationError("Expected safe data/ and uploads/ directories in the backup")
    users = _json(data / "users.json")
    gallery = _json(data / "gallery.json")
    settings = _json(data / "settings.json", required=False)
    if any(not isinstance(u, dict) for u in users.values()):
        raise MigrationError("Invalid legacy user record")
    if owner_username not in users or users[owner_username].get("role") != "admin":
        raise MigrationError("The explicitly selected owner must be an existing legacy administrator")
    if users[owner_username].get("disabled"):
        raise MigrationError("The selected initial owner must be enabled")
    if not isinstance(email_map, dict) or any(not isinstance(v, str) for v in email_map.values()):
        raise MigrationError("Login mapping must be a JSON object of username/email strings")
    if set(email_map) != set(users):
        raise MigrationError("Provide an explicit login mapping for every legacy user, and no others")
    emails = [str(v).strip().lower() for v in email_map.values()]
    if any(not v or "@" not in v or len(v) > 254 for v in emails) or len(set(emails)) != len(emails):
        raise MigrationError("Login mappings must be unique valid email identifiers")
    household_id = stable_id(migration_id, "household", "legacy")
    collection_id = stable_id(migration_id, "collection", "legacy-shared")
    user_records = []
    for username, legacy in sorted(users.items()):
        if not isinstance(legacy, dict) or not isinstance(legacy.get("password_hash"), str):
            raise MigrationError("A legacy user is missing a password hash")
        stored = legacy["password_hash"]
        if not stored.startswith(("$2a$", "$2b$", "$2y$")):
            if not re.fullmatch(r"[a-fA-F0-9]{64}", stored) or not isinstance(legacy.get("salt"), str):
                raise MigrationError("Unsupported legacy password representation requires reconciliation")
        if legacy.get("role", "user") not in {"admin", "user"}:
            raise MigrationError("Unknown legacy role requires explicit operator reconciliation")
        if legacy.get("mfa") is not None and not isinstance(legacy["mfa"], dict):
            raise MigrationError("Invalid legacy MFA record")
        user_records.append({"id": stable_id(migration_id, "user", username),
                             "username": username, "email": email_map[username].strip().lower(),
                             "role": "owner" if username == owner_username else
                             "admin" if legacy.get("role") == "admin" else "member",
                             "legacy": legacy})
    images, groups = gallery.get("images", {}), gallery.get("groups", {})
    if not isinstance(images, dict) or not isinstance(groups, dict):
        raise MigrationError("Invalid gallery images/groups structure")
    records = []
    # The old app discovers media directly from disk; preserve originals that have
    # no explicit overrides in gallery.json as well as the metadata-backed ones.
    allowed = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif"}
    filenames = set(images)
    for path in uploads.iterdir():
        if path.suffix.lower() in allowed:
            filenames.add(path.name)
    for name in sorted(filenames):
        _filename(name)
        meta = images.get(name, {})
        if not isinstance(meta, dict):
            raise MigrationError("Invalid asset metadata")
        records.append({"id": stable_id(migration_id, "asset", name), "filename": name,
                        "metadata": meta, "file": _file(uploads / name, uploads)})
    known = {r["filename"]: r["id"] for r in records}
    group_records = []
    for group_id, group in sorted(groups.items()):
        _filename(group_id)
        if not isinstance(group, dict) or not isinstance(group.get("images"), list):
            raise MigrationError("Invalid group structure")
        if any(not isinstance(name, str) or name not in known for name in group["images"]) or len(set(group["images"])) != len(group["images"]):
            raise MigrationError("Group references missing or duplicate assets")
        group_records.append({"id": stable_id(migration_id, "group", group_id),
                              "legacy_id": group_id, "metadata": group,
                              "asset_ids": [known[name] for name in group["images"]]})
    displays = settings.get("displays") or [{"id": "default", "name": "Main display",
                                              "active": True, "target_aspect_ratio": settings.get("target_aspect_ratio", "16:9")}]
    if not isinstance(displays, list) or any(not isinstance(f, dict) or not isinstance(f.get("id"), str) or not f.get("id") for f in displays):
        raise MigrationError("Invalid display profiles")
    if len({f["id"] for f in displays}) != len(displays):
        raise MigrationError("Duplicate display profile IDs")
    frames = tuple({"id": stable_id(migration_id, "frame", str(f["id"])),
                    "legacy_id": f["id"], "metadata": f} for f in displays)
    derived = []
    for folder in (uploads / "thumbnails", uploads / "snapshots"):
        if folder.is_symlink():
            raise MigrationError("Unsafe derived-media directory")
        if folder.exists():
            for path in sorted(folder.iterdir()):
                if path.is_file() or path.is_symlink():
                    entry = _file(path, uploads)
                    derived.append({**entry, "relative_path": str(path.relative_to(uploads))})
    key_file = data / ".mfa_key"
    mfa_key = None
    if any(u["legacy"].get("mfa", {}).get("totp_secret") for u in user_records):
        if key_file.is_symlink() or not key_file.is_file():
            raise MigrationError("Legacy MFA key is required to preserve enrolled authenticators")
        mfa_key = key_file.read_bytes().strip()
    canonical = {"users": users, "gallery": gallery, "settings": settings,
                 "emails": email_map, "owner": owner_username,
                 "media": [(r["filename"], r["file"]["sha256"], r["file"]["bytes"]) for r in records],
                 "derived": [(r["relative_path"], r["sha256"]) for r in derived],
                 "mfa_key_hash": hashlib.sha256(mfa_key).hexdigest() if mfa_key else None}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return MigrationPlan(migration_id, digest, household_id, collection_id,
                         owner_username, tuple(user_records), tuple(records),
                         tuple(group_records), frames, settings, tuple(derived), root, mfa_key)
