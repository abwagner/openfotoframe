"""Real PostgreSQL/RLS contracts; require an explicitly disposable local cluster.

Set RELEASE_TEST_POSTGRES_DISPOSABLE=1 and RELEASE_TEST_POSTGRES_ADMIN_URL to a
local temporary PostgreSQL instance. Tests create/drop a uniquely named database
and uniquely named least-privilege roles; they never use the normal app DSN.
"""

import os
from pathlib import Path
import secrets
import time
import uuid

import bcrypt
import pytest
from sqlalchemy import create_engine, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from release_backend import create_app, models as m
from release_backend.store import Store
from release_backend.security import digest
from release_billing import models as b


@pytest.fixture(scope="module")
def postgres_scope(tmp_path_factory):
    raw_url = os.environ.get("RELEASE_TEST_POSTGRES_ADMIN_URL")
    if not raw_url or os.environ.get("RELEASE_TEST_POSTGRES_DISPOSABLE") != "1":
        pytest.skip(
            "Requires an explicitly disposable local PostgreSQL cluster; SQLite is not RLS verification"
        )
    url = make_url(raw_url)
    if url.host not in {
        "localhost",
        "127.0.0.1",
        "::1",
    } or not url.drivername.startswith("postgresql"):
        pytest.fail("RLS tests refuse a non-local database destination")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    suffix = uuid.uuid4().hex[:16]
    database_name, app_role, billing_role = (
        "openfotoframe_test_" + suffix,
        "off_app_test_" + suffix,
        "off_billing_test_" + suffix,
    )
    administrative = create_engine(url, isolation_level="AUTOCOMMIT")
    owner = application = billing_store = None
    try:
        with administrative.connect() as conn:
            conn.exec_driver_sql('CREATE DATABASE "' + database_name + '"')
        database_url = url.set(database=database_name)
        owner = Store(database_url.render_as_string(hide_password=False))
        owner.create_schema()
        directory = Path(__file__).resolve().parents[1] / "deploy" / "release"
        raw = owner.engine.raw_connection()
        try:
            for name in ("postgres-rls.sql", "postgres-roles.sql"):
                sql = (
                    (directory / name)
                    .read_text()
                    .replace("openfotoframe_app", app_role)
                    .replace("openfotoframe_billing", billing_role)
                )
                with raw.cursor() as cursor:
                    cursor.execute(sql)
                raw.commit()
            from psycopg import sql

            password = secrets.token_urlsafe(32)
            billing_password = secrets.token_urlsafe(32)
            with raw.cursor() as cursor:
                cursor.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(app_role), sql.Literal(password)
                    )
                )
                cursor.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(billing_role), sql.Literal(billing_password)
                    )
                )
            raw.commit()
        finally:
            raw.close()
        application_url = database_url.set(username=app_role, password=password)
        application = Store(application_url.render_as_string(hide_password=False))
        billing_store = Store(
            database_url.set(
                username=billing_role, password=billing_password
            ).render_as_string(hide_password=False)
        )
        now = int(time.time())
        with owner.transaction() as conn:
            for identity in ("owner-a", "owner-b", "reader", "outsider", "invitee"):
                conn.execute(
                    insert(m.users).values(
                        id=identity,
                        email=identity + "@example.test",
                        username=identity,
                        password_hash=bcrypt.hashpw(
                            b"synthetic-password", bcrypt.gensalt()
                        ).decode(),
                        email_verified=True,
                        mfa={},
                        disabled=False,
                        must_change_password=False,
                        created_at=now,
                    )
                )
                conn.execute(
                    insert(m.sessions).values(
                        id="session-" + identity,
                        user_id=identity,
                        device_name="RLS test",
                        access_hash=digest(identity + "-token"),
                        access_expires=now + 3600,
                        refresh_hash=digest(identity + "-refresh"),
                        refresh_expires=now + 7200,
                        revoked=False,
                        authenticated_at=now,
                        created_at=now,
                    )
                )
            conn.execute(
                insert(m.households),
                [
                    {"id": "a", "name": "A", "settings": {}, "created_at": now},
                    {"id": "b", "name": "B", "settings": {}, "created_at": now},
                ],
            )
            for identity, hid, role in (
                ("owner-a", "a", "owner"),
                ("owner-b", "b", "owner"),
                ("reader", "a", "member"),
                ("reader", "b", "member"),
            ):
                conn.execute(
                    insert(m.memberships).values(
                        id=identity + "-" + hid,
                        household_id=hid,
                        user_id=identity,
                        role=role,
                        active=True,
                    )
                )
            for cid, hid in (("a-visible", "a"), ("a-hidden", "a"), ("b-visible", "b")):
                conn.execute(
                    insert(m.collections).values(
                        id=cid, household_id=hid, name=cid, settings={}
                    )
                )
                conn.execute(
                    insert(m.assets).values(
                        id="asset-" + cid,
                        household_id=hid,
                        collection_id=cid,
                        filename=cid + ".jpg",
                        storage_key=hid + "/" + cid + "/original",
                        byte_size=100,
                        mime_type="image/jpeg",
                        metadata={},
                        deleted=False,
                        created_at=now,
                    )
                )
            for cid, hid in (("a-visible", "a"), ("b-visible", "b")):
                conn.execute(
                    insert(m.collection_grants).values(
                        id="grant-" + cid,
                        household_id=hid,
                        membership_id="reader-" + hid,
                        collection_id=cid,
                        role="contributor",
                    )
                )
            conn.execute(
                insert(m.frames).values(
                    id="frame-a",
                    household_id="a",
                    name="A frame",
                    settings={},
                    state={},
                    schedules=[],
                )
            )
            conn.execute(
                insert(m.frame_collections).values(
                    household_id="a", frame_id="frame-a", collection_id="a-visible"
                )
            )
            conn.execute(
                insert(m.frame_credentials).values(
                    id="credential-a",
                    household_id="a",
                    frame_id="frame-a",
                    token_hash=digest("synthetic-frame-secret"),
                    revoked=False,
                )
            )
            conn.execute(
                insert(b.subscriptions).values(
                    household_id="a",
                    provider="fixture",
                    subscription_id="subscription-a",
                    plan_id="fixture",
                    status="active",
                    valid_until=now + 3600,
                    revision=1,
                    updated_at=now,
                )
            )
        app = create_app(
            {
                "TESTING": True,
                "RELEASE_MODE": "hosted",
                "DATABASE_URL": application_url.render_as_string(hide_password=False),
                "MEDIA_ROOT": str(tmp_path_factory.mktemp("postgres-media")),
                "PUBLIC_ORIGIN": "https://frame.example.test",
                "SECRET_KEY": "synthetic-application-key-for-postgres-tests",
                "SERVER_ID": "isolated-postgres-server",
            }
        )
        yield {
            "owner": owner,
            "application": application,
            "billing": billing_store,
            "app": app,
            "app_role": app_role,
            "now": now,
        }
        app.extensions["release_store"].engine.dispose()
    finally:
        if application:
            application.engine.dispose()
        if billing_store:
            billing_store.engine.dispose()
        if owner:
            owner.engine.dispose()
        with administrative.connect() as conn:
            conn.exec_driver_sql(
                'DROP DATABASE IF EXISTS "' + database_name + '" WITH (FORCE)'
            )
            conn.exec_driver_sql('DROP ROLE IF EXISTS "' + app_role + '"')
            conn.exec_driver_sql('DROP ROLE IF EXISTS "' + billing_role + '"')
        administrative.dispose()


def test_postgres_actual_application_role_is_not_owner_or_bypass_and_missing_scope_denies(
    postgres_scope,
):
    store = postgres_scope["application"]
    store.validate_application_role()
    with pytest.raises(ValueError):
        postgres_scope["owner"].validate_application_role()
    with store.transaction() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
            )
            .mappings()
            .one()
        )
        assert row["current_user"] == postgres_scope["app_role"]
        assert not row["rolsuper"] and not row["rolbypassrls"]
        assert not conn.execute(select(m.assets)).first()
        owned = conn.execute(
            text(
                "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user"
            )
        ).scalar_one()
        assert owned == 0
        tables = conn.execute(
            text(
                "SELECT relname,relrowsecurity FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='public' AND relkind='r'"
            )
        ).all()
        flags = dict(tables)
        for table in m.TENANT_TABLES + b.TENANT_TABLES:
            assert flags[table.name], table.name


def test_postgres_multi_household_and_collection_scope_does_not_survive_pool_reuse(
    postgres_scope,
):
    store = postgres_scope["application"]
    with store.transaction(user_id="reader", household_id="a") as conn:
        assert conn.execute(select(m.assets.c.id)).scalars().all() == [
            "asset-a-visible"
        ]
        assert set(conn.execute(select(m.memberships.c.household_id)).scalars()) == {
            "a",
            "b",
        }
    with store.transaction() as conn:
        assert not conn.execute(select(m.assets)).first()
    with store.transaction(user_id="reader", household_id="b") as conn:
        assert conn.execute(select(m.assets.c.id)).scalars().all() == [
            "asset-b-visible"
        ]
    with store.transaction(user_id="outsider", household_id="a") as conn:
        assert not conn.execute(select(m.assets)).first()


def test_postgres_api_uses_the_actual_application_role_for_member_reads(postgres_scope):
    client = postgres_scope["app"].test_client()
    headers = {"Authorization": "Bearer reader-token"}
    assert client.get("/api/v1/households", headers=headers).status_code == 200
    response = client.get(
        "/api/v1/households/a/collections/a-visible/assets", headers=headers
    )
    assert response.status_code == 200
    assert "asset-a-visible" in response.get_data(as_text=True)
    assert (
        client.get(
            "/api/v1/households/a/collections/a-hidden/assets", headers=headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/v1/households/a/collections/a-visible/assets",
            headers={"Authorization": "Bearer outsider-token"},
        ).status_code
        == 404
    )


def test_postgres_scope_rejects_cross_collection_writes_and_member_promotion(
    postgres_scope,
):
    store = postgres_scope["application"]
    with pytest.raises(DBAPIError):
        with store.transaction(user_id="reader", household_id="a") as conn:
            conn.execute(
                insert(m.assets).values(
                    id="foreign-upload",
                    household_id="a",
                    collection_id="a-hidden",
                    filename="hidden.jpg",
                    storage_key="a/foreign",
                    byte_size=1,
                    mime_type="image/jpeg",
                    uploaded_by="reader",
                    metadata={},
                    deleted=False,
                    created_at=1,
                )
            )
    with store.transaction(user_id="reader", household_id="a") as conn:
        changed = conn.execute(
            update(m.memberships)
            .where(m.memberships.c.id == "reader-a")
            .values(role="owner")
        )
        assert changed.rowcount == 0


def test_postgres_frame_auth_and_assignment_are_separate_from_user_authority(
    postgres_scope,
):
    store = postgres_scope["application"]
    with store.transaction() as conn:
        assert not conn.execute(select(m.frame_credentials)).first()
        assert not conn.execute(
            text("SELECT * FROM public.release_auth_frame(:hash)"),
            {"hash": digest("wrong")},
        ).first()
        credential = (
            conn.execute(
                text("SELECT * FROM public.release_auth_frame(:hash)"),
                {"hash": digest("synthetic-frame-secret")},
            )
            .mappings()
            .one()
        )
        assert credential["frame_id"] == "frame-a"
    with store.transaction(household_id="a", frame_id="frame-a") as conn:
        assert conn.execute(select(m.assets.c.id)).scalars().all() == [
            "asset-a-visible"
        ]
        assert not conn.execute(select(m.memberships)).first()


def test_postgres_quota_uses_hidden_and_retained_bytes_without_exposing_assets(
    postgres_scope,
):
    from release_billing import BillingService, BillingError

    service = BillingService(
        [
            {
                "id": "fixture",
                "name": "Fixture",
                "storage_bytes": 250,
                "asset_limit": 10,
                "frame_limit": 10,
            }
        ]
    )
    with postgres_scope["application"].transaction(
        user_id="reader", household_id="a"
    ) as conn:
        assert len(conn.execute(select(m.assets)).all()) == 1
        assert not conn.execute(
            select(b.subscriptions)
        ).first()  # owner-only billing endpoint
        assert service.check_quota(conn, "a", authorize=lambda: None, bytes_to_add=50)[
            "active"
        ]
        with pytest.raises(BillingError, match="limit"):
            service.check_quota(conn, "a", authorize=lambda: None, bytes_to_add=51)


def test_postgres_invitation_redemption_is_token_bound_atomic_and_grant_scoped(
    postgres_scope,
):
    owner, application = postgres_scope["owner"], postgres_scope["application"]
    with owner.transaction() as conn:
        conn.execute(
            insert(m.invitations).values(
                id="invite-test",
                household_id="a",
                token_hash=digest("synthetic-invitation"),
                issuer_id="owner-a",
                recipient_email="invitee@example.test",
                transferable=False,
                grants={
                    "collections": [{"id": "a-visible", "role": "viewer"}],
                    "frames": [],
                },
                expires_at=postgres_scope["now"] + 3600,
                revoked=False,
            )
        )
    with application.transaction(user_id="invitee") as conn:
        assert not conn.execute(select(m.assets)).first()
        assert (
            conn.execute(
                text(
                    "SELECT household_id FROM public.release_lookup_invitation(:hash)"
                ),
                {"hash": digest("synthetic-invitation")},
            ).scalar_one()
            == "a"
        )
        result = conn.execute(
            text("SELECT public.release_accept_invitation(:hash,:uid)"),
            {"hash": digest("synthetic-invitation"), "uid": "invitee"},
        ).scalar_one()
        assert result == "a"
    with application.transaction(user_id="invitee", household_id="a") as conn:
        assert conn.execute(select(m.assets.c.id)).scalars().all() == [
            "asset-a-visible"
        ]
        assert (
            conn.execute(
                text("SELECT public.release_accept_invitation(:hash,:uid)"),
                {"hash": digest("synthetic-invitation"), "uid": "invitee"},
            ).scalar_one()
            == "a"
        )
    with pytest.raises(DBAPIError):
        with application.transaction(user_id="outsider") as conn:
            conn.execute(
                text("SELECT public.release_accept_invitation(:hash,:uid)"),
                {"hash": digest("synthetic-invitation"), "uid": "outsider"},
            )


def test_postgres_billing_service_role_can_apply_verified_state_but_cannot_read_photos(
    postgres_scope,
):
    from release_billing import BillingService, VerifiedBillingEvent

    service = BillingService(
        [
            {
                "id": "fixture",
                "name": "Fixture",
                "storage_bytes": 10000,
                "asset_limit": 10,
                "frame_limit": 10,
            }
        ]
    )
    with postgres_scope["owner"].transaction() as conn:
        conn.execute(
            insert(b.checkout_requests).values(
                id="billing-checkout-b",
                household_id="b",
                provider="fixture",
                plan_id="fixture",
                request_key="postgres-billing-request",
                created_at=postgres_scope["now"],
            )
        )
    event = VerifiedBillingEvent(
        "fixture",
        "pg-billing-event",
        "b",
        "pg-subscription-b",
        "fixture",
        "active",
        postgres_scope["now"] + 3600,
        1,
        "billing-checkout-b",
    )
    with postgres_scope["billing"].transaction() as conn:
        assert service.apply_event(conn, event, b"verified-fixture-event")["applied"]
    with postgres_scope["billing"].transaction() as conn:
        assert service.apply_event(conn, event, b"verified-fixture-event")["replayed"]
    with pytest.raises(DBAPIError):
        with postgres_scope["billing"].transaction() as conn:
            conn.execute(select(m.assets)).all()


def test_postgres_http_contributor_owns_upload_and_editor_can_save_settings(
    postgres_scope,
):
    import io
    from PIL import Image

    app = postgres_scope["app"]
    app.config["BILLING_PLANS"] = [
        {
            "id": "fixture",
            "name": "Fixture",
            "storage_bytes": 10000,
            "asset_limit": 100,
            "frame_limit": 100,
        }
    ]
    output = io.BytesIO()
    Image.new("RGB", (20, 30), "red").save(output, format="PNG")
    client = app.test_client()
    headers = {"Authorization": "Bearer reader-token"}
    upload = client.post(
        "/api/v1/households/a/collections/a-visible/assets",
        headers=headers,
        data={"file": (io.BytesIO(output.getvalue()), "reader.png")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201, upload.json
    aid = upload.json["asset"]["id"]
    changed = client.patch(
        "/api/v1/households/a/collections/a-visible/assets/" + aid,
        headers=headers,
        json={"title": "My own upload"},
    )
    assert changed.status_code == 200, changed.json
    assert (
        client.patch(
            "/api/v1/households/a/collections/a-visible/assets/asset-a-visible",
            headers=headers,
            json={"title": "Not mine"},
        ).status_code
        == 403
    )
    with postgres_scope["owner"].transaction() as conn:
        conn.execute(
            update(m.collection_grants)
            .where(m.collection_grants.c.id == "grant-a-visible")
            .values(role="editor")
        )
    changed = client.patch(
        "/api/v1/households/a/collections/a-visible/settings",
        headers=headers,
        json={"shuffle": True},
    )
    assert changed.status_code == 200, changed.json
    with postgres_scope["owner"].transaction() as conn:
        assert conn.execute(
            select(m.collections.c.settings).where(m.collections.c.id == "a-visible")
        ).scalar()["shuffle"]
        conn.execute(
            update(m.collection_grants)
            .where(m.collection_grants.c.id == "grant-a-visible")
            .values(role="contributor")
        )
    assert (
        client.delete(
            "/api/v1/households/a/collections/a-visible/assets/" + aid, headers=headers
        ).status_code
        == 200
    )


def test_postgres_http_invitation_preview_and_acceptance_use_secret_bound_helpers(
    postgres_scope,
):
    client = postgres_scope["app"].test_client()
    invitation = client.post(
        "/api/v1/households/a/invitations",
        headers={"Authorization": "Bearer owner-a-token"},
        json={
            "grants": {
                "collections": [{"id": "a-visible", "role": "viewer"}],
                "frames": [],
            },
            "recipient_email": "outsider@example.test",
        },
    )
    assert invitation.status_code == 201, invitation.json
    token = invitation.json["token"]
    preview = client.post("/api/v1/invitations/preview", json={"token": token})
    assert preview.status_code == 200, preview.json
    accepted = client.post(
        "/api/v1/invitations/accept",
        headers={"Authorization": "Bearer outsider-token"},
        json={"token": token},
    )
    assert accepted.status_code == 200, accepted.json
    assert (
        client.get(
            "/api/v1/households/a/collections/a-visible/assets",
            headers={"Authorization": "Bearer outsider-token"},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/households/a/collections/a-hidden/assets",
            headers={"Authorization": "Bearer outsider-token"},
        ).status_code
        == 404
    )


def test_postgres_frame_enrollment_cookie_and_control_use_bounded_helpers(
    postgres_scope,
):
    app = postgres_scope["app"]
    owner = app.test_client()
    issued = owner.post(
        "/api/v1/households/a/frames/frame-a/enrollments",
        headers={"Authorization": "Bearer owner-a-token"},
        json={},
    )
    assert issued.status_code == 201, issued.json
    token = issued.json["enrollment_token"]
    physical = app.test_client()
    rejected = physical.post(
        "/api/v1/display/enroll",
        json={"token": token},
        headers={"Origin": "https://evil.example.test"},
    )
    assert rejected.status_code == 403
    enrollment = physical.post("/api/v1/display/enroll", json={"token": token})
    assert enrollment.status_code == 200, enrollment.json
    assert (
        physical.post("/api/v1/display/enroll", json={"token": token}).status_code
        == 401
    )
    playlist = physical.get("/api/v1/display/playlist")
    assert playlist.status_code == 200, playlist.json
    assert playlist.json["frame_id"] == "frame-a"
    assert physical.get("/api/v1/auth/me").status_code == 401
    assert (
        physical.post("/api/v1/display/control", json={"action": "next"}).status_code
        == 403
    )
    csrf = physical.get_cookie("release_frame_csrf").value
    control = physical.post(
        "/api/v1/display/control",
        json={"action": "next", "frame_id": "frame-b"},
        headers={"X-CSRFToken": csrf},
    )
    assert control.status_code == 200, control.json
    with postgres_scope["owner"].transaction() as db:
        assert (
            db.execute(
                select(m.frames.c.state).where(m.frames.c.id == "frame-a")
            ).scalar()["index"]
            == 1
        )


def test_postgres_frame_schedule_write_uses_admin_scope_and_validates_before_update(
    postgres_scope,
):
    with postgres_scope["owner"].transaction() as db:
        db.execute(
            insert(m.frame_grants).values(
                id="reader-frame-schedule-test",
                household_id="a",
                membership_id="reader-a",
                frame_id="frame-a",
                role="controller",
            )
        )
    client = postgres_scope["app"].test_client()
    path = "/api/v1/households/a/frames/frame-a/schedules"
    plan = {
        "id": "pg-morning",
        "on_time": "07:00",
        "off_time": "23:00",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "enabled": True,
        "timezone": "America/New_York",
    }
    owner = {"Authorization": "Bearer owner-a-token"}
    assert (
        client.put(path, headers=owner, json={"schedules": [plan]}).status_code == 200
    )
    assert client.get(path, headers=owner).json["schedules"] == [plan]
    assert (
        client.put(
            path,
            headers={"Authorization": "Bearer reader-token"},
            json={"schedules": []},
        ).status_code
        == 403
    )
    rejected = client.put(
        path, headers=owner, json={"schedules": [dict(plan, on_time="invalid")]}
    )
    assert rejected.status_code == 400
    assert client.get(path, headers=owner).json["schedules"] == [plan]
    assert (
        client.get(
            "/api/v1/households/b/frames/frame-a/schedules", headers=owner
        ).status_code
        == 404
    )
