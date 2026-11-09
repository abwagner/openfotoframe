"""Verified entitlement state never substitutes for collection/frame authority."""

from dataclasses import replace
import pytest
from sqlalchemy import insert, select, func

from release_backend.store import Store
from release_backend import models as m
from release_billing import BillingService, BillingError, VerifiedBillingEvent
from release_billing import models as b

PLANS = [{"id": "fixture", "name": "Test plan", "storage_bytes": 1000, "asset_limit": 5, "frame_limit": 2}]


@pytest.fixture
def billing():
    store = Store("sqlite:///:memory:")
    store.create_schema()
    with store.transaction() as db:
        db.execute(insert(m.households), [{"id": "a", "name": "A", "settings": {}, "created_at": 1},
                                         {"id": "b", "name": "B", "settings": {}, "created_at": 1}])
        db.execute(insert(b.checkout_requests).values(id="checkout-a", household_id="a", provider="fixture",
            plan_id="fixture", request_key="request-fixture-a", created_at=1))
    yield store, BillingService(PLANS, clock=lambda: 100)
    store.engine.dispose()


def event(**kwargs):
    return replace(VerifiedBillingEvent("fixture", "event-1", "a", "subscription-1", "fixture",
                    "active", 1000, 1, "checkout-a"), **kwargs)


def test_event_replay_is_idempotent_and_never_adds_memberships_or_grants(billing):
    store, service = billing
    with store.transaction() as db:
        assert service.apply_event(db, event(), b"signed-body")["applied"]
        assert service.apply_event(db, event(), b"signed-body")["replayed"]
        assert db.execute(select(func.count()).select_from(b.events)).scalar_one() == 1
        for table in (m.memberships, m.collection_grants, m.frame_grants):
            assert db.execute(select(func.count()).select_from(table)).scalar_one() == 0
        assert service.entitlement(db, "a", authorize=lambda: None)["active"]


def test_unverified_data_wrong_checkout_and_cross_household_subscription_fail(billing):
    store, service = billing
    with store.transaction() as db:
        with pytest.raises(BillingError, match="verified"):
            service.apply_event(db, {"paid": True}, b"client-paid-flag")
        with pytest.raises(BillingError, match="checkout"):
            service.apply_event(db, event(checkout_reference="made-up"), b"signed")
        service.apply_event(db, event(), b"signed")
        with pytest.raises(BillingError, match="another household"):
            service.apply_event(db, event(event_id="event-other", household_id="b"), b"signed-other")


def test_event_identity_and_out_of_order_state_cannot_restore_expired_access(billing):
    store, service = billing
    with store.transaction() as db:
        service.apply_event(db, event(), b"active")
        service.apply_event(db, event(event_id="expired", revision=3, status="expired", valid_until=99), b"expired")
        result = service.apply_event(db, event(event_id="old-active", revision=2), b"old")
        assert not result["applied"]
        assert not service.entitlement(db, "a", authorize=lambda: None)["active"]
        with pytest.raises(BillingError, match="different content"):
            service.apply_event(db, event(), b"forged-replay-body")


def test_replaced_subscription_never_reactivates_from_delayed_old_events(billing):
    store, service = billing
    with store.transaction() as db:
        service.apply_event(db, event(), b"original")
        service.apply_event(db, event(event_id="original-ended", revision=2, status="expired", valid_until=99), b"ended")
        db.execute(insert(b.checkout_requests).values(id="checkout-new", household_id="a", provider="fixture",
            plan_id="fixture", request_key="request-fixture-new", created_at=2))
        service.apply_event(db, event(event_id="new-sub", subscription_id="subscription-2", checkout_reference="checkout-new"), b"new-sub")
        result = service.apply_event(db, event(event_id="delayed", revision=99), b"delayed-old")
        assert not result["applied"]
        assert db.execute(select(b.subscriptions.c.subscription_id)).scalar_one() == "subscription-2"


def test_new_channel_cannot_replace_active_subscription(billing):
    store, service = billing
    with store.transaction() as db:
        service.apply_event(db, event(), b"original")
        db.execute(insert(b.checkout_requests).values(id="checkout-new", household_id="a", provider="fixture",
            plan_id="fixture", request_key="request-fixture-new", created_at=2))
        result = service.apply_event(db, event(event_id="new-sub", subscription_id="subscription-2",
            checkout_reference="checkout-new"), b"new-sub")
        assert not result["applied"]
        assert db.execute(select(b.subscriptions.c.subscription_id)).scalar_one() == "subscription-1"


def test_permission_check_precedes_billing_lookup_and_quota_failures(billing):
    store, service = billing
    def denied():
        raise PermissionError("No collection access")
    with store.transaction() as db:
        with pytest.raises(PermissionError, match="collection"):
            service.entitlement(db, "a", authorize=denied)
        with pytest.raises(PermissionError, match="collection"):
            service.check_quota(db, "a", authorize=denied, bytes_to_add=100000)
        with pytest.raises(BillingError, match="active"):
            service.check_quota(db, "a", authorize=lambda: None, bytes_to_add=1)
        service.apply_event(db, event(), b"active")
        assert service.check_quota(db, "a", authorize=lambda: None, bytes_to_add=1000)["active"]
        with pytest.raises(BillingError, match="limit"):
            service.check_quota(db, "a", authorize=lambda: None, bytes_to_add=1001)


def test_expiry_does_not_delete_content_and_unconfigured_plans_fail_closed(billing):
    store, service = billing
    with store.transaction() as db:
        service.apply_event(db, event(valid_until=99), b"expired")
        assert not service.entitlement(db, "a", authorize=lambda: None)["active"]
        assert db.execute(select(func.count()).select_from(m.households)).scalar_one() == 2
        assert not BillingService([], clock=lambda: 100).entitlement(db, "a", authorize=lambda: None)["active"]
    with pytest.raises(BillingError, match="positive"):
        BillingService([{"id": "unconfigured", "name": "No invented limits"}])


def test_soft_deleted_photos_still_consume_storage_quota(billing):
    store, service = billing
    with store.transaction() as db:
        service.apply_event(db, event(), b"active")
        db.execute(insert(m.collections).values(id="collection-a", household_id="a", name="Photos", settings={}))
        db.execute(insert(m.assets).values(id="deleted-photo", household_id="a", collection_id="collection-a",
            filename="retained.jpg", storage_key="a/retained", byte_size=900, mime_type="image/jpeg",
            metadata={}, deleted=True, created_at=1))
        assert service.check_quota(db, "a", authorize=lambda: None, bytes_to_add=100)["active"]
        with pytest.raises(BillingError, match="limit"):
            service.check_quota(db, "a", authorize=lambda: None, bytes_to_add=101)


@pytest.fixture
def billing_app(tmp_path):
    from release_backend import create_app
    from release_backend.security import digest
    app = create_app({"TESTING": True, "INIT_SCHEMA": True, "RELEASE_MODE": "hosted",
        "DATABASE_URL": "sqlite:///:memory:", "MEDIA_ROOT": str(tmp_path / "media"),
        "PUBLIC_ORIGIN": "https://frame.example.test", "SECRET_KEY": "synthetic-application-key-for-tests-only",
        "CLOCK": lambda: 100})
    store = app.extensions["release_store"]
    with store.transaction() as db:
        for identity in ("owner", "member"):
            db.execute(insert(m.users).values(id=identity, email=identity + "@example.test", username=identity,
                email_verified=True, password_hash="not-used-by-seeded-session", mfa={},
                must_change_password=False, disabled=False, created_at=1))
            db.execute(insert(m.sessions).values(id="session-" + identity, user_id=identity,
                device_name="Fixture", access_hash=digest(identity + "-token"), access_expires=1000,
                refresh_hash=digest(identity + "-refresh"), refresh_expires=1000,
                revoked=False, authenticated_at=100, created_at=1))
        db.execute(insert(m.households).values(id="a", name="A", settings={}, created_at=1))
        db.execute(insert(m.memberships), [
            {"id": "membership-owner", "household_id": "a", "user_id": "owner", "role": "owner", "active": True},
            {"id": "membership-member", "household_id": "a", "user_id": "member", "role": "member", "active": True}])
    yield app
    store.engine.dispose()


def test_http_billing_is_disabled_until_provider_is_configured_and_owner_only(billing_app):
    client = billing_app.test_client()
    assert client.get("/api/v1/plans").get_json() == {"plans": []}
    owner = {"Authorization": "Bearer owner-token", "Idempotency-Key": "fixture-request-0001"}
    member = {**owner, "Authorization": "Bearer member-token"}
    assert client.post("/api/v1/households/a/billing/checkout", json={"plan_id": "fixture"}, headers=member).status_code == 403
    assert client.post("/api/v1/households/a/billing/checkout", json={"plan_id": "fixture"}, headers=owner).status_code == 503
    assert client.post("/api/v1/billing/webhooks/unconfigured", data=b"paid=true").status_code == 404


def test_http_checkout_uses_server_plan_and_validated_host_with_idempotency(billing_app):
    class Provider:
        calls = []
        def create_checkout(self, **kwargs):
            self.calls.append(kwargs)
            return "https://checkout.example.test/pay/fixture"
    provider = Provider()
    billing_app.config.update(BILLING_PLANS=[{**PLANS[0], "provider": "fixture"}],
        BILLING_PROVIDERS={"fixture": provider}, BILLING_CHECKOUT_HOSTS=["checkout.example.test"])
    client = billing_app.test_client()
    headers = {"Authorization": "Bearer owner-token", "Idempotency-Key": "fixture-request-0001"}
    response = client.post("/api/v1/households/a/billing/checkout", json={"plan_id": "fixture", "paid": True, "price": 0}, headers=headers)
    assert response.status_code == 200
    assert response.get_json()["checkout_url"] == "https://checkout.example.test/pay/fixture"
    assert client.post("/api/v1/households/a/billing/checkout", json={"plan_id": "fixture"}, headers=headers).status_code == 200
    assert len(provider.calls) == 1
    assert provider.calls[0]["household_id"] == "a"
    assert provider.calls[0]["plan"]["storage_bytes"] == 1000
    assert "paid" not in provider.calls[0]["plan"]
    billing_app.config["BILLING_CHECKOUT_HOSTS"] = ["other.example.test"]
    response = client.post("/api/v1/households/a/billing/checkout", json={"plan_id": "fixture"},
        headers={**headers, "Idempotency-Key": "fixture-request-0002"})
    assert response.status_code == 503


def test_apple_intent_restore_is_owner_bound_and_replays_verified_state(billing_app):
    class Apple:
        def verify_transaction(self, signed, *, household_id, app_account_token, checkout_reference):
            if signed not in {"fixture-valid-jws", "fixture-older-jws"}:
                raise BillingError("unsigned or invalid transaction")
            return VerifiedBillingEvent("apple", "transaction:fixture:801", household_id,
                "original-apple-fixture", "fixture", "active", 1000, 801, checkout_reference)
    billing_app.config.update(BILLING_PLANS=[{**PLANS[0], "apple_product_id": "fixture.subscription"}],
        BILLING_PROVIDERS={"apple": Apple()})
    store = billing_app.extensions["release_store"]
    # Requests already hold a SQLite transaction: the test handler intentionally
    # uses the request connection exactly as the production SQLite adapter does.
    from flask import g
    billing_app.config["BILLING_EVENT_HANDLER"] = lambda event, body: BillingService(
        billing_app.config["BILLING_PLANS"], clock=lambda: 100).apply_event(g.release_db, event, body)
    client = billing_app.test_client()
    owner = {"Authorization": "Bearer owner-token", "Idempotency-Key": "apple-request-000001"}
    member = {**owner, "Authorization": "Bearer member-token"}
    route = "/api/v1/households/a/billing/apple/"
    assert client.get(route + "account", headers=owner).status_code == 404
    assert client.get(route + "account", headers=member).status_code == 403
    assert client.post(route + "purchase-intent", json={"plan_id": "fixture"}, headers=member).status_code == 403
    intent = client.post(route + "purchase-intent", json={"plan_id": "fixture"}, headers=owner)
    assert intent.status_code == 200
    data = intent.get_json()
    assert client.get(route + "account", headers=owner).get_json() == {
        "app_account_token": data["app_account_token"], "product_ids": ["fixture.subscription"]}
    assert client.post(route + "purchase-intent", json={"plan_id": "fixture"}, headers=owner).get_json() == data
    assert client.post(route + "transactions", json={"signed_transaction": "paid=true"}, headers=owner).status_code == 400
    payload = {"signed_transaction": "fixture-valid-jws", "checkout_reference": data["checkout_reference"]}
    assert client.post(route + "transactions", json=payload, headers=owner).get_json()["active"]
    assert client.get(route + "account", headers=owner).get_json()["app_account_token"] == data["app_account_token"]
    # Different historical JWS reconciles to exactly the same verified state.
    payload["signed_transaction"] = "fixture-older-jws"
    payload.pop("checkout_reference")
    assert client.post(route + "transactions", json=payload, headers=owner).get_json()["active"]
    assert client.post(route + "purchase-intent", json={"plan_id": "fixture"},
        headers={**owner, "Idempotency-Key": "apple-request-000002"}).status_code == 409
    with store.transaction() as db:
        assert db.execute(select(func.count()).select_from(b.events)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(b.apple_accounts)).scalar_one() == 1
