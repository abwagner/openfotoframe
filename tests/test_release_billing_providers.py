"""Provider SDK boundary tests: real Stripe HMAC, rejected Apple unsigned JWS,
plus deterministic typed App Store status responses for lifecycle/binding tests.
No payment network or production credentials are used.
"""
import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace as NS
import pytest
from release_billing.providers import StripeProvider, AppleProvider
from release_billing.service import BillingError

PLAN = dict(id="fixture", name="Fixture", storage_bytes=100, asset_limit=2,
            frame_limit=1, stripe_price_id="price_fixture", apple_product_id="fixture.subscription")


def stripe_fixture():
    subscription = dict(id="sub_fixture", livemode=False, status="active",
        items={"data": [{"price": {"id": "price_fixture"}, "current_period_end": int(time.time()) + 1000}]},
        latest_invoice={"status": "paid"}, metadata={"off_plan_id": "fixture",
        "off_household_id": "household", "off_checkout_ref": "intent"})
    client = NS(v1=NS(subscriptions=NS(retrieve=lambda *a, **k: subscription)))
    provider = StripeProvider(secret_key="sk_test_fixture", webhook_secret="whsec_fixture",
        success_url="https://fixture.example/success", cancel_url="https://fixture.example/cancel",
        plans=[PLAN], client=client)
    raw = json.dumps(dict(id="evt_fixture", object="event", type="customer.subscription.updated",
        livemode=False, created=int(time.time()), data={"object": {"id": "sub_fixture"}})).encode()
    stamp = int(time.time())
    signature = hmac.new(b"whsec_fixture", str(stamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return provider, subscription, raw, {"Stripe-Signature": f"t={stamp},v1={signature}"}


def test_real_stripe_sdk_verifies_raw_body_signature_and_current_paid_state():
    provider, subscription, raw, headers = stripe_fixture()
    event = provider.verify_event(raw, headers)
    assert (event.household_id, event.checkout_reference, event.status) == ("household", "intent", "active")
    with pytest.raises(BillingError, match="verification"):
        provider.verify_event(raw + b" ", headers)
    subscription["latest_invoice"]["status"] = "open"
    assert provider.verify_event(raw, headers).status == "past_due"
    subscription["status"] = "canceled"
    assert provider.verify_event(raw, headers).status == "cancelled"
    subscription["livemode"] = True
    with pytest.raises(BillingError, match="environment"):
        provider.verify_event(raw, headers)


def apple_fixture():
    from appstoreserverlibrary.models.Environment import Environment
    from appstoreserverlibrary.models.Type import Type
    from appstoreserverlibrary.models.Status import Status
    token = str(uuid.uuid4())
    tx = NS(bundleId="fixture.bundle", environment=Environment.SANDBOX,
        type=Type.AUTO_RENEWABLE_SUBSCRIPTION, productId="fixture.subscription", quantity=1,
        inAppOwnershipType=NS(value="PURCHASED"), appAccountToken=token,
        originalTransactionId="original-fixture", transactionId="transaction-fixture",
        purchaseDate=int(time.time()*1000)-100, signedDate=int(time.time()*1000),
        expiresDate=int(time.time()*1000)+100000, revocationDate=None, isUpgraded=False)
    state = NS(signedTransactionInfo="verified-current", status=Status.ACTIVE)
    verifier = NS(verify_and_decode_signed_transaction=lambda signed: tx)
    response = NS(environment=Environment.SANDBOX, bundleId="fixture.bundle",
        data=[NS(lastTransactions=[state])])
    provider = AppleProvider(root_certificates=[b"injected-verifier-test-only"], bundle_id="fixture.bundle",
        app_apple_id=None, environment="sandbox", signing_key=b"injected-client-test-only", key_id="fixture",
        issuer_id="fixture", plans=[PLAN], verifier=verifier,
        client=NS(get_all_subscription_statuses=lambda original: response))
    return provider, tx, state, token


def test_apple_current_status_binds_household_and_handles_revocation_and_expiry():
    from appstoreserverlibrary.models.Status import Status
    provider, tx, state, token = apple_fixture()
    kwargs = dict(household_id="household", app_account_token=token, checkout_reference="intent")
    active = provider.verify_transaction("device-jws", **kwargs)
    assert active.status == "active" and active.subscription_id == "original-fixture"
    with pytest.raises(BillingError, match="another household"):
        provider.verify_transaction("device-jws", household_id="other", app_account_token=str(uuid.uuid4()))
    state.status = Status.REVOKED
    revoked = provider.verify_transaction("device-jws", **kwargs)
    assert revoked.status == "cancelled" and revoked.revision > active.revision
    state.status = Status.EXPIRED
    tx.expiresDate = int(time.time()*1000)-1000
    assert provider.verify_transaction("device-jws", **kwargs).status == "expired"
    tx.productId = "unconfigured.product"
    with pytest.raises(BillingError, match="product mismatch"):
        provider.verify_transaction("device-jws", **kwargs)


def test_apple_sdk_rejects_unsigned_jws_and_signature_bypassing_environments():
    from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier
    from appstoreserverlibrary.models.Environment import Environment
    verifier = SignedDataVerifier([], True, Environment.SANDBOX, "fixture.bundle", None)
    with pytest.raises(Exception):
        verifier.verify_and_decode_signed_transaction("eyJhbGciOiJub25lIn0.e30.")
    for environment in ("xcode", "localtesting"):
        with pytest.raises(BillingError, match="signed production/sandbox"):
            AppleProvider(root_certificates=[b"fixture"], bundle_id="fixture.bundle", app_apple_id=None,
                environment=environment, signing_key=b"fixture", key_id="fixture", issuer_id="fixture", plans=[PLAN])
