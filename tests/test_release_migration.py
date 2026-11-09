"""Offline migration contracts use only synthetic snapshots and private tmp paths."""

import hashlib
import json
from pathlib import Path

import bcrypt
from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select, func

from release_backend import models as m
from release_backend.store import Store
from release_migration import MigrationError, plan_legacy_import
from release_migration.importer import import_legacy, verify_import


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / "snapshot"
    (root / "data").mkdir(parents=True)
    (root / "uploads" / "thumbnails").mkdir(parents=True)
    (root / "uploads" / "snapshots").mkdir()
    old_key = Fernet.generate_key()
    (root / "data" / ".mfa_key").write_bytes(old_key)
    users = {
        "admin": {"password_hash": bcrypt.hashpw(b"old-admin-password", bcrypt.gensalt()).decode(),
                  "role": "admin", "must_change_password": False,
                  "mfa": {"totp_secret": Fernet(old_key).encrypt(b"JBSWY3DPEHPK3PXP").decode(),
                          "recovery_code_hashes": [hashlib.sha256(b"recovery").hexdigest()], "passkeys": []}},
        "viewer": {"password_hash": hashlib.sha256(b"saltdemo-password").hexdigest(),
                   "salt": "salt", "role": "user", "must_change_password": True},
    }
    metadata = {"title": "Portrait", "mat_color": "#f5f0e6", "mat_finish": "linen",
                "crop": {"x": .1, "y": .2, "w": .6, "h": .7}, "uploaded_by": "external-attribution"}
    gallery = {"images": {"photo.jpg": metadata}, "groups": {"pair": {"images": ["photo.jpg", "implicit.jpg"], "scales": {"photo.jpg": .8}, "border_effect": "bevel-lit"}}}
    settings = {"mat_color": "#abcdef", "image_order": ["pair"], "shuffle": False,
                "displays": [{"id": "tv", "name": "Kitchen", "width": 1920, "height": 1080}],
                "tv_schedules": [{"on": "07:00", "days": [0, 1, 2]}]}
    for name, value in (("users", users), ("gallery", gallery), ("settings", settings)):
        (root / "data" / (name + ".json")).write_text(json.dumps(value))
    (root / "uploads" / "photo.jpg").write_bytes(b"synthetic-original")
    (root / "uploads" / "implicit.jpg").write_bytes(b"implicit-original")
    (root / "uploads" / "thumbnails" / "photo.jpg").write_bytes(b"synthetic-thumbnail")
    (root / "uploads" / "snapshots" / "pair.display.png").write_bytes(b"synthetic-render")
    return root


def plan(snapshot):
    return plan_legacy_import(snapshot, migration_id="fixture-2026", owner_username="admin",
                               email_map={"admin": "owner@example.test", "viewer": "member@example.test"})


@pytest.fixture
def store():
    value = Store("sqlite:///:memory:")
    value.create_schema()
    yield value
    value.engine.dispose()


def test_dry_run_deterministic_redacted_and_does_not_modify_source(snapshot):
    before = {str(p): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}
    first, second = plan(snapshot), plan(snapshot)
    assert first.fingerprint == second.fingerprint
    assert first.household_id == second.household_id
    assert first.summary()["counts"] == {"users": 2, "assets": 2, "groups": 1, "frames": 1, "derived_files": 2}
    public = json.dumps(first.summary()) + repr(first)
    assert "password_hash" not in public and "recovery_code" not in public and "owner@example" not in public
    assert before == {str(p): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}


def test_transactional_idempotent_import_preserves_credentials_metadata_and_explicit_grants(snapshot, tmp_path, store):
    value = plan(snapshot)
    new_key = Fernet.generate_key()
    media = tmp_path / "private-media"
    result = import_legacy(value, store, media_root=media, mfa_encryption_key=new_key)
    assert result["already_imported"] is False
    assert result["source_sessions_imported"] is False
    assert result["display_credentials_imported"] is False
    assert verify_import(value, store, media_root=media)["verified"]
    repeated = import_legacy(value, store, media_root=media, mfa_encryption_key=new_key)
    assert repeated["already_imported"] is True
    with store.transaction() as db:
        users = db.execute(select(m.users)).mappings().all()
        owner = next(u for u in users if u["username"] == "admin")
        assert bcrypt.checkpw(b"old-admin-password", owner["password_hash"].encode())
        assert Fernet(new_key).decrypt(owner["mfa"]["totp_secret"].encode()) == b"JBSWY3DPEHPK3PXP"
        assert not owner["email_verified"]
        assert next(u for u in users if u["username"] == "viewer")["legacy_salt"] == "salt"
        assert db.execute(select(m.memberships.c.role).where(m.memberships.c.user_id == owner["id"])).scalar_one() == "owner"
        grants = db.execute(select(m.collection_grants)).mappings().all()
        assert len(grants) == 1 and grants[0]["collection_id"] == value.collection_id and grants[0]["role"] == "editor"
        assert db.execute(select(func.count()).select_from(m.frame_grants)).scalar_one() == 1
        asset = db.execute(select(m.assets).where(m.assets.c.filename == "photo.jpg")).mappings().one()
        assert asset["uploaded_by"] is None  # attribution string cannot create identity/grants
        assert asset["metadata"]["uploaded_by"] == "external-attribution"
        assert (media / asset["storage_key"]).read_bytes() == b"synthetic-original"
        assert (media / asset["thumbnail_key"]).read_bytes() == b"synthetic-thumbnail"
        frame = db.execute(select(m.frames)).mappings().one()
        assert frame["schedules"] == value.settings["tv_schedules"]
        assert not db.execute(select(m.sessions)).first()
        assert not db.execute(select(m.frame_credentials)).first()


def test_changed_media_rolls_back_all_metadata_and_can_resume_same_snapshot(snapshot, tmp_path, store):
    value = plan(snapshot)
    original = (snapshot / "uploads" / "photo.jpg").read_bytes()
    (snapshot / "uploads" / "photo.jpg").write_bytes(b"changed-during-migration")
    with pytest.raises(MigrationError, match="changed"):
        import_legacy(value, store, media_root=tmp_path / "media", mfa_encryption_key=Fernet.generate_key())
    with store.transaction() as db:
        assert not db.execute(select(m.households)).first()
        assert not db.execute(select(m.users)).first()
        assert not db.execute(select(m.migrations)).first()
    (snapshot / "uploads" / "photo.jpg").write_bytes(original)
    assert import_legacy(value, store, media_root=tmp_path / "media", mfa_encryption_key=Fernet.generate_key())["already_imported"] is False


def test_collision_and_changed_snapshot_do_not_merge_or_mutate_existing_users(snapshot, tmp_path, store):
    value = plan(snapshot)
    key = Fernet.generate_key()
    import_legacy(value, store, media_root=tmp_path / "media", mfa_encryption_key=key)
    (snapshot / "uploads" / "photo.jpg").write_bytes(b"changed")
    with pytest.raises(MigrationError, match="different snapshot"):
        import_legacy(plan(snapshot), store, media_root=tmp_path / "media", mfa_encryption_key=key)
    other = plan_legacy_import(snapshot, migration_id="another-import", owner_username="admin",
                              email_map={"admin": "owner@example.test", "viewer": "member@example.test"})
    with pytest.raises(MigrationError, match="identity collision"):
        import_legacy(other, store, media_root=tmp_path / "media", mfa_encryption_key=key)


def test_unsafe_paths_missing_groups_and_owner_selection_fail_closed(snapshot):
    with pytest.raises(MigrationError, match="administrator"):
        plan_legacy_import(snapshot, migration_id="x", owner_username="viewer", email_map={})
    gallery = snapshot / "data" / "gallery.json"
    gallery.write_text(json.dumps({"images": {"../outside.jpg": {}}, "groups": {}}))
    with pytest.raises(MigrationError, match="filename"):
        plan(snapshot)
    gallery.write_text(json.dumps({"images": {}, "groups": {"bad": {"images": ["missing.jpg"]}}}))
    with pytest.raises(MigrationError, match="missing"):
        plan(snapshot)


def test_symlinks_and_source_destination_overlap_are_rejected(snapshot, tmp_path, store):
    original = snapshot / "uploads" / "photo.jpg"
    original.unlink()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"not-in-backup")
    original.symlink_to(outside)
    with pytest.raises(MigrationError, match="unsafe"):
        plan(snapshot)
    original.unlink()
    original.write_bytes(b"safe")
    with pytest.raises(MigrationError, match="outside"):
        import_legacy(plan(snapshot), store, media_root=snapshot / "new-media", mfa_encryption_key=Fernet.generate_key())


def test_verification_detects_corrupted_destination_bytes(snapshot, tmp_path, store):
    value = plan(snapshot)
    media = tmp_path / "media"
    import_legacy(value, store, media_root=media, mfa_encryption_key=Fernet.generate_key())
    with store.transaction() as db:
        key = db.execute(select(m.assets.c.storage_key)).scalar()
    (media / key).write_bytes(b"corruption")
    with pytest.raises(MigrationError, match="bytes"):
        verify_import(value, store, media_root=media)


def test_operator_security_and_provider_settings_are_not_exposed_as_tenant_visual_settings(snapshot, tmp_path, store):
    path = snapshot / "data" / "settings.json"
    settings = json.loads(path.read_text())
    settings["backup_token"] = "never-copy-provider-credentials"
    settings["mfa_mode"] = "required_admins"
    settings["mfa_methods"] = "totp_only"
    settings["displays"][0]["provider_token"] = "never-copy-frame-credentials"
    path.write_text(json.dumps(settings))
    result = import_legacy(plan(snapshot), store, media_root=tmp_path / "media", mfa_encryption_key=Fernet.generate_key())
    assert result["legacy_mfa_policy"]["mode"] == "required_admins"
    assert "backup_token" in result["operator_settings_require_review"]
    with store.transaction() as db:
        assert db.execute(select(m.households.c.settings)).scalar_one()["mat_color"] == "#abcdef"
        assert db.execute(select(m.households.c.settings)).scalar_one()["mfa_mode"] == "required_admins"
        assert db.execute(select(m.households.c.settings)).scalar_one()["mfa_methods"] == "totp_only"
        assert db.execute(select(m.collections.c.settings)).scalar_one()["mat_color"] == "#abcdef"
        assert "mfa_mode" not in db.execute(select(m.collections.c.settings)).scalar_one()
        assert "backup_token" not in db.execute(select(m.households.c.settings)).scalar_one()
        assert "provider_token" not in db.execute(select(m.frames.c.settings)).scalar_one()


def test_old_fixed_administrator_password_cannot_be_migrated_as_a_full_session_credential(snapshot, tmp_path, store):
    from release_backend import create_app
    app = create_app({"TESTING": True, "INIT_SCHEMA": True,
        "DATABASE_URL": "sqlite:///:memory:", "MEDIA_ROOT": str(tmp_path / "app-media"),
        "PUBLIC_ORIGIN": "https://fixture.example", "SECRET_KEY": "fixture-private-key-with-at-least-32-characters"})
    store = app.extensions["release_store"]
    path = snapshot / "data" / "users.json"
    users = json.loads(path.read_text())
    users["admin"]["password_hash"] = bcrypt.hashpw(b"password", bcrypt.gensalt()).decode()
    users["admin"].pop("must_change_password", None)
    path.write_text(json.dumps(users))
    with pytest.raises(MigrationError, match="Activation blocked"):
        import_legacy(plan(snapshot), store, media_root=tmp_path / "media", mfa_encryption_key=Fernet.generate_key())
    with store.transaction() as db:
        assert db.execute(select(func.count()).select_from(m.users)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(m.households)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(m.migrations)).scalar_one() == 0
    assert not (tmp_path / "media").exists()
    login = app.test_client().post("/api/v1/auth/login",
        json={"email": "admin", "password": "password"})
    assert login.status_code == 401
    assert "challenge_token" not in login.get_json()
    assert "access_token" not in login.get_json()
    # The immutable source credential remains available for private audit.
    assert bcrypt.checkpw(b"password", json.loads(path.read_text())["admin"]["password_hash"].encode())
    # No verification/activation result can succeed for a rejected import.
    with pytest.raises(MigrationError, match="completed migration"):
        verify_import(plan(snapshot), store, media_root=tmp_path / "media")
    store.engine.dispose()
