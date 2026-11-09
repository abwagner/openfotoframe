"""Operator-only provider wiring and bounded billing-role transactions."""

from pathlib import Path
from contextlib import contextmanager
import time
from flask import g, has_request_context
from sqlalchemy import select, text

from . import models as b
from .providers import AppleProvider, StripeProvider
from .service import BillingError, BillingService, configured_plans


def configure(app):
    plans = list(configured_plans(app.config).values())
    providers = dict(app.config.get("BILLING_PROVIDERS") or {})
    store = app.extensions["release_store"]
    service_store = None
    if app.config.get("BILLING_DATABASE_URL"):
        from release_backend.store import Store
        service_store = Store(app.config["BILLING_DATABASE_URL"])
        if service_store.engine.dialect.name == "postgresql":
            with service_store.transaction() as db:
                role = db.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")).mappings().one()
                broad = db.execute(text("SELECT has_table_privilege(current_user,'public.assets','SELECT') OR has_table_privilege(current_user,'public.users','SELECT')")).scalar_one()
                if role["rolsuper"] or role["rolbypassrls"] or broad:
                    raise ValueError("Use the bounded billing database role, not an application/schema identity")
        app.extensions["release_billing_store"] = service_store

    @contextmanager
    def billing_connection():
        if store.engine.dialect.name == "sqlite":
            # A request already holds SQLite's write reservation. Never open a
            # nested write transaction while that request transaction is active.
            if has_request_context() and getattr(g, "release_db", None) is not None:
                yield g.release_db
            else:
                with store.transaction() as db:
                    yield db
        elif service_store is not None:
            with service_store.transaction() as db:
                yield db
        else:
            raise BillingError("Configure the bounded billing database connection")

    def process(event, payload):
        with billing_connection() as db:
            return BillingService(plans, clock=app.config.get("CLOCK", time.time)).apply_event(db, event, payload)

    def apple_binding(token, plan_id, subscription_id):
        with billing_connection() as db:
            account = db.execute(select(b.apple_accounts).where(b.apple_accounts.c.app_account_token == token)).mappings().first()
            if not account:
                raise BillingError("Unknown Apple household binding")
            binding = db.execute(select(b.subscription_bindings).where(
                (b.subscription_bindings.c.provider == "apple") &
                (b.subscription_bindings.c.subscription_id == subscription_id))).mappings().first()
            if binding:
                if binding["household_id"] != account["household_id"]:
                    raise BillingError("Apple subscription is already bound to another household")
                return {"household_id": account["household_id"], "checkout_reference": binding["checkout_reference"]}
            checkout = db.execute(select(b.checkout_requests).where(
                (b.checkout_requests.c.household_id == account["household_id"]) &
                (b.checkout_requests.c.provider == "apple") &
                (b.checkout_requests.c.plan_id == plan_id)).order_by(b.checkout_requests.c.created_at.desc())).mappings().first()
            if not checkout:
                raise BillingError("Apple subscription has no matching purchase intent")
            return {"household_id": account["household_id"], "checkout_reference": checkout["id"]}

    stripe_fields = [app.config.get(k) for k in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL")]
    if any(stripe_fields):
        if not all(stripe_fields):
            raise ValueError("Complete Stripe server configuration before enabling checkout")
        providers["stripe"] = StripeProvider(secret_key=stripe_fields[0], webhook_secret=stripe_fields[1],
            success_url=stripe_fields[2], cancel_url=stripe_fields[3], plans=plans,
            live_mode=app.config.get("STRIPE_LIVE_MODE", False))
        app.config["BILLING_CHECKOUT_HOSTS"] = sorted(set(app.config.get("BILLING_CHECKOUT_HOSTS") or []) | {"checkout.stripe.com"})
    apple_keys = ("APPLE_BUNDLE_ID", "APPLE_ENVIRONMENT", "APPLE_ROOT_CERTIFICATES", "APPLE_SIGNING_KEY_PATH", "APPLE_KEY_ID", "APPLE_ISSUER_ID")
    apple_fields = [app.config.get(k) for k in apple_keys]
    if any(apple_fields):
        if not all(apple_fields):
            raise ValueError("Complete Apple signature and server-status configuration before enabling purchases")
        root_paths = app.config["APPLE_ROOT_CERTIFICATES"]
        if not isinstance(root_paths, (tuple, list)) or not root_paths:
            raise ValueError("APPLE_ROOT_CERTIFICATES must list official DER trust-root files")
        providers["apple"] = AppleProvider(root_certificates=[Path(p).read_bytes() for p in root_paths],
            bundle_id=app.config["APPLE_BUNDLE_ID"], app_apple_id=app.config.get("APPLE_APP_ID"),
            environment=app.config["APPLE_ENVIRONMENT"],
            signing_key=Path(app.config["APPLE_SIGNING_KEY_PATH"]).read_bytes(),
            key_id=app.config["APPLE_KEY_ID"], issuer_id=app.config["APPLE_ISSUER_ID"],
            plans=plans, binding_resolver=apple_binding)
    if ("stripe" in providers or "apple" in providers) and store.engine.dialect.name == "postgresql" and service_store is None:
        raise ValueError("Provider integrations require a separately bounded BILLING_DATABASE_URL")
    app.config["BILLING_PROVIDERS"] = providers
    if providers and app.config.get("BILLING_EVENT_HANDLER") is None:
        app.config["BILLING_EVENT_HANDLER"] = process
