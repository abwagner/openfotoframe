"""Billing HTTP entry points; providers are trusted operator-configured adapters."""

from urllib.parse import urlsplit
from dataclasses import replace, asdict
import hashlib
import hmac
import json
import uuid
import re
from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import select, insert, update
from release_backend.security import require_auth, household, json_body, fail, uid, now
from .service import BillingService, BillingError, configured_plans, VerifiedBillingEvent
from . import models as b
from .providers import IgnoredBillingEvent

blueprint = Blueprint("release_billing", __name__)


def _providers():
    return current_app.config.get("BILLING_PROVIDERS") or {}


def _service():
    return BillingService(current_app.config.get("BILLING_PLANS") or [], clock=lambda: now())


def _idempotency_key():
    key = request.headers.get("Idempotency-Key", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{16,120}", key):
        fail("invalid_request", "Provide a unique Idempotency-Key header of 16 to 120 characters.")
    return key


def _no_active_subscription(hid):
    row = g.release_db.execute(select(b.subscriptions).where(b.subscriptions.c.household_id == hid)).mappings().first()
    if row and row["status"] == "active" and row["valid_until"] > now():
        fail("subscription_exists", "This household already has an active subscription. Manage it with the original purchase provider.", 409)


@blueprint.get("/api/v1/plans")
def plans():
    configured = configured_plans(current_app.config)
    if current_app.config.get("RELEASE_MODE") != "hosted" or not _providers():
        return jsonify(plans=[])
    result = []
    for p in configured.values():
        channels = []
        if p.get("stripe_price_id") and "stripe" in _providers():
            channels.append("stripe")
        if p.get("apple_product_id") and "apple" in _providers():
            channels.append("apple")
        if p.get("provider") in _providers() and not channels:
            channels.append(p["provider"])
        if channels:
            result.append({**{k: p[k] for k in ("id", "name", "description", "price_label", "apple_product_id") if k in p},
                           "purchase_channels": channels})
    return jsonify(plans=result)


@blueprint.get("/api/v1/households/<hid>/billing/entitlement")
@require_auth
def entitlement(hid):
    household(hid, owner=True)
    return jsonify(_service().entitlement(g.release_db, hid, authorize=lambda: None))


@blueprint.post("/api/v1/households/<hid>/billing/checkout")
@require_auth
def checkout(hid):
    household(hid, owner=True)
    body = json_body()
    if not isinstance(body.get("plan_id"), str) or len(body["plan_id"]) > 120:
        fail("invalid_request", "Select a configured plan.")
    plan = configured_plans(current_app.config).get(body.get("plan_id"))
    provider_name = ("stripe" if plan and plan.get("stripe_price_id") else plan.get("provider")) if plan else None
    provider = _providers().get(provider_name) if provider_name != "apple" else None
    if current_app.config.get("RELEASE_MODE") != "hosted" or not provider:
        fail("billing_unavailable", "Paid registration is not configured on this server.", 503)
    key = _idempotency_key()
    prior = g.release_db.execute(select(b.checkout_requests).where(
        (b.checkout_requests.c.household_id == hid) & (b.checkout_requests.c.request_key == key))).mappings().first()
    if prior:
        if prior["plan_id"] != plan["id"] or prior["provider"] != provider_name:
            fail("idempotency_conflict", "This request key belongs to a different plan.", 409)
        if prior["checkout_url"]:
            return jsonify(checkout_url=prior["checkout_url"])
        fail("checkout_pending", "Checkout is being prepared. Retry the same request key.", 409)
    _no_active_subscription(hid)
    # Survives a process/network failure after Stripe created the session but
    # before this transaction committed. Retrying the same key reuses Stripe's
    # idempotency identity rather than creating another purchase opportunity.
    reference = hmac.new(str(current_app.config["SECRET_KEY"]).encode(),
        (hid + "\x00" + provider_name + "\x00" + key).encode(), hashlib.sha256).hexdigest()
    g.release_db.execute(insert(b.checkout_requests).values(id=reference, household_id=hid,
        provider=provider_name, plan_id=plan["id"], request_key=key, created_at=now()))
    # The adapter receives only server-selected identity and product mapping. It
    # must use reference as its provider-side idempotency key on every retry.
    try:
        url = provider.create_checkout(household_id=hid, plan=plan, reference=reference)
    except BillingError:
        fail("billing_unavailable", "Checkout could not be prepared. Retry this request.", 503)
    parsed = urlsplit(url)
    allowed = current_app.config.get("BILLING_CHECKOUT_HOSTS") or []
    if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password:
        fail("billing_unavailable", "The checkout provider returned an unsupported destination.", 503)
    g.release_db.execute(update(b.checkout_requests).where(b.checkout_requests.c.id == reference).values(checkout_url=url))
    return jsonify(checkout_url=url)


@blueprint.post("/api/v1/billing/webhooks/<provider_name>")
def webhook(provider_name):
    provider = _providers().get(provider_name)
    if current_app.config.get("RELEASE_MODE") != "hosted" or provider is None:
        fail("not_found", "Not found.", 404)
    if request.content_length is None or request.content_length > 256 * 1024:
        fail("invalid_request", "A bounded event body is required.", 413)
    payload = request.get_data(cache=False)
    try:
        event = provider.verify_event(payload, request.headers)
        if not isinstance(event, VerifiedBillingEvent) or event.provider != provider_name:
            raise BillingError("Provider identity mismatch")
        # A service transaction is required under PostgreSQL RLS; the provider's
        # verified checkout reference is not an end-user authorization credential.
        # The request's DB connection is already transactional. The factory must
        # supply a bounded billing service-role connection for hosted deployment.
        handler = current_app.config.get("BILLING_EVENT_HANDLER")
        if handler is None:
            fail("billing_unavailable", "Billing event processing is not configured.", 503)
        result = handler(event, payload)
    except IgnoredBillingEvent:
        return jsonify(accepted=True, ignored=True)
    except BillingError:
        fail("invalid_billing_event", "The payment event could not be verified.", 400)
    return jsonify(result)


@blueprint.post("/api/v1/households/<hid>/billing/apple/purchase-intent")
@require_auth
def apple_purchase_intent(hid):
    household(hid, owner=True)
    body = json_body()
    if not isinstance(body.get("plan_id"), str):
        fail("invalid_request", "Select a configured plan.")
    plan = configured_plans(current_app.config).get(body["plan_id"])
    provider = _providers().get("apple")
    if current_app.config.get("RELEASE_MODE") != "hosted" or not provider or not plan or not plan.get("apple_product_id"):
        fail("billing_unavailable", "App Store purchases are not configured on this server.", 503)
    key = _idempotency_key()
    existing = g.release_db.execute(select(b.checkout_requests).where(
        (b.checkout_requests.c.household_id == hid) & (b.checkout_requests.c.request_key == key))).mappings().first()
    if existing and (existing["plan_id"] != plan["id"] or existing["provider"] != "apple"):
        fail("idempotency_conflict", "This request key belongs to another purchase.", 409)
    if not existing:
        _no_active_subscription(hid)
    account = g.release_db.execute(select(b.apple_accounts).where(b.apple_accounts.c.household_id == hid)).mappings().first()
    if not account:
        token = str(uuid.uuid4())
        g.release_db.execute(insert(b.apple_accounts).values(household_id=hid, app_account_token=token, created_at=now()))
    else:
        token = account["app_account_token"]
    reference = existing["id"] if existing else uid()
    if not existing:
        g.release_db.execute(insert(b.checkout_requests).values(id=reference, household_id=hid,
            provider="apple", plan_id=plan["id"], request_key=key, created_at=now()))
    return jsonify(app_account_token=token, product_id=plan["apple_product_id"],
                   plan_id=plan["id"], checkout_reference=reference)


@blueprint.get("/api/v1/households/<hid>/billing/apple/account")
@require_auth
def apple_account(hid):
    """Read an existing restore identity without preparing a new purchase."""
    household(hid, owner=True)
    if current_app.config.get("RELEASE_MODE") != "hosted" or not _providers().get("apple"):
        fail("billing_unavailable", "App Store verification is not configured on this server.", 503)
    account = g.release_db.execute(select(b.apple_accounts).where(
        b.apple_accounts.c.household_id == hid)).mappings().first()
    if not account:
        fail("not_found", "No App Store account is linked to this household.", 404)
    product_ids = sorted(p["apple_product_id"] for p in configured_plans(current_app.config).values()
                         if p.get("apple_product_id"))
    return jsonify(app_account_token=account["app_account_token"], product_ids=product_ids)


@blueprint.post("/api/v1/households/<hid>/billing/apple/transactions")
@require_auth
def apple_transaction(hid):
    household(hid, owner=True)
    body = json_body()
    signed = body.get("signed_transaction")
    provider = _providers().get("apple")
    if current_app.config.get("RELEASE_MODE") != "hosted" or not provider:
        fail("billing_unavailable", "App Store verification is not configured on this server.", 503)
    if not isinstance(signed, str) or len(signed) > 100000:
        fail("invalid_request", "Provide the signed App Store transaction.")
    account = g.release_db.execute(select(b.apple_accounts).where(b.apple_accounts.c.household_id == hid)).mappings().first()
    if not account:
        fail("purchase_intent_required", "Prepare a purchase for this household before restoring it.", 409)
    reference = body.get("checkout_reference")
    if reference is not None and (not isinstance(reference, str) or len(reference) > 64):
        fail("invalid_request", "Invalid purchase reference.")
    try:
        event = provider.verify_transaction(signed, household_id=hid,
            app_account_token=account["app_account_token"], checkout_reference=reference)
        binding = g.release_db.execute(select(b.subscription_bindings).where(
            (b.subscription_bindings.c.provider == "apple") &
            (b.subscription_bindings.c.subscription_id == event.subscription_id))).mappings().first()
        if binding and binding["household_id"] != hid:
            fail("purchase_binding_conflict", "This purchase belongs to another household.", 409)
        if not reference:
            if binding:
                reference = binding["checkout_reference"]
            else:
                checkout = g.release_db.execute(select(b.checkout_requests).where(
                    (b.checkout_requests.c.household_id == hid) & (b.checkout_requests.c.provider == "apple") &
                    (b.checkout_requests.c.plan_id == event.plan_id)).order_by(b.checkout_requests.c.created_at.desc())).mappings().first()
                reference = checkout["id"] if checkout else None
        event = replace(event, checkout_reference=reference)
        handler = current_app.config.get("BILLING_EVENT_HANDLER")
        if handler is None:
            fail("billing_unavailable", "Subscription verification is not configured.", 503)
        # Several historical device JWS values can reconcile to the same latest
        # server transaction. Deduplicate that verified state, not the historical
        # input, so a legitimate restore does not look like mutated event content.
        handler(event, json.dumps(asdict(event), sort_keys=True, separators=(",", ":")).encode())
    except BillingError:
        fail("invalid_purchase", "This App Store purchase could not be verified for this household.", 400)
    return jsonify(_service().entitlement(g.release_db, hid, authorize=lambda: None))
