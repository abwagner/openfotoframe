"""Official Stripe and Apple SDK adapters. No browser-return or unsigned paid flag.

All provider identifiers, signing keys, roots, product mappings and destinations
are supplied by operator configuration. Constructors reject development-only
Apple JWS environments that deliberately skip signature verification.
"""

from __future__ import annotations

import json
import time
import uuid
from urllib.parse import urlsplit

from .service import BillingError, VerifiedBillingEvent


class IgnoredBillingEvent(BillingError):
    """A valid provider message that does not change a subscription entitlement."""


def _value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _https(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise BillingError("Billing return destinations require HTTPS")
    return url


class StripeProvider:
    name = "stripe"

    def __init__(self, *, secret_key, webhook_secret, success_url, cancel_url,
                 plans, live_mode=False, client=None):
        import stripe
        if not secret_key or not webhook_secret:
            raise BillingError("Stripe requires server API and webhook signing secrets")
        self.sdk = stripe
        self.client = client or stripe.StripeClient(secret_key, max_network_retries=2)
        self.webhook_secret = webhook_secret
        self.success_url, self.cancel_url = _https(success_url), _https(cancel_url)
        self.live_mode = bool(live_mode)
        self.plans = {p["stripe_price_id"]: p for p in plans if p.get("stripe_price_id")}
        if not self.plans:
            raise BillingError("Configure Stripe recurring price mappings explicitly")

    def create_checkout(self, *, household_id, plan, reference):
        price = plan.get("stripe_price_id")
        if price not in self.plans:
            raise BillingError("This plan is not configured for Stripe")
        metadata = {"off_checkout_ref": reference, "off_household_id": household_id,
                    "off_plan_id": plan["id"]}
        try:
            result = self.client.v1.checkout.sessions.create({
                "mode": "subscription", "line_items": [{"price": price, "quantity": 1}],
                "client_reference_id": reference, "metadata": metadata,
                "subscription_data": {"metadata": metadata},
                "success_url": self.success_url, "cancel_url": self.cancel_url,
            }, options={"idempotency_key": "off-checkout-" + reference})
            url = _value(result, "url")
            if not isinstance(url, str) or urlsplit(_https(url)).hostname != "checkout.stripe.com":
                raise BillingError("Stripe returned an unsupported checkout destination")
            return url
        except BillingError:
            raise
        except Exception as exc:
            raise BillingError("Stripe checkout could not be prepared") from exc

    def verify_event(self, payload, headers):
        """Verify exact raw bytes, then retrieve current subscription server-side.

        Event timestamps order state within a subscription; negative states have
        conservative precedence for same-second events. Returning from Checkout
        never grants access. Stale event bodies are not trusted as current state.
        """
        try:
            event = self.sdk.Webhook.construct_event(payload, headers.get("Stripe-Signature", ""),
                                                     self.webhook_secret, tolerance=300)
            if bool(_value(event, "livemode")) != self.live_mode:
                raise BillingError("Stripe environment mismatch")
            kind, obj = _value(event, "type"), _value(_value(event, "data", {}), "object", {})
            if kind in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
                if _value(obj, "mode") != "subscription":
                    raise IgnoredBillingEvent("Not a subscription checkout")
                subscription_id = _value(obj, "subscription")
            elif kind in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
                subscription_id = _value(obj, "id")
            elif kind in {"invoice.paid", "invoice.payment_failed", "invoice.payment_action_required"}:
                subscription_id = _value(obj, "subscription")
                if not subscription_id:
                    parent = _value(obj, "parent", {})
                    subscription_id = _value(_value(parent, "subscription_details", {}), "subscription")
            else:
                raise IgnoredBillingEvent("No subscription entitlement change")
            if not isinstance(subscription_id, str) or not subscription_id.startswith("sub_"):
                raise BillingError("Missing Stripe subscription identity")
            subscription = self.client.v1.subscriptions.retrieve(subscription_id,
                params={"expand": ["latest_invoice"]})
            if bool(_value(subscription, "livemode")) != self.live_mode:
                raise BillingError("Stripe subscription environment mismatch")
            items = _value(_value(subscription, "items", {}), "data", [])
            if len(items) != 1:
                raise BillingError("Only one configured household subscription item is supported")
            price_id = _value(_value(items[0], "price", {}), "id")
            plan = self.plans.get(price_id)
            metadata = _value(subscription, "metadata", {})
            if not plan or _value(metadata, "off_plan_id") != plan["id"]:
                raise BillingError("Stripe product mapping mismatch")
            household_id, reference = _value(metadata, "off_household_id"), _value(metadata, "off_checkout_ref")
            if not household_id or not reference:
                raise BillingError("Subscription lacks server checkout binding")
            status = _value(subscription, "status")
            end = _value(items[0], "current_period_end") or _value(subscription, "current_period_end")
            if not isinstance(end, int):
                raise BillingError("Missing verified subscription period")
            invoice = _value(subscription, "latest_invoice", {})
            # Do not invent trial entitlement or grant an initial unpaid invoice.
            if status == "active" and _value(invoice, "status") == "paid":
                normalized, priority = "active", 1
            elif status in {"canceled", "incomplete_expired", "unpaid"}:
                normalized, priority = "cancelled", 4
                end = min(end, int(time.time()))
            else:
                normalized, priority = "past_due", 3
            created = _value(event, "created")
            if not isinstance(created, int):
                raise BillingError("Missing verified Stripe event timestamp")
            return VerifiedBillingEvent("stripe", _value(event, "id"), household_id,
                subscription_id, plan["id"], normalized, end, created * 8 + priority, reference)
        except (BillingError, IgnoredBillingEvent):
            raise
        except Exception as exc:
            raise BillingError("Stripe signature or subscription verification failed") from exc


class AppleProvider:
    name = "apple"

    def __init__(self, *, root_certificates, bundle_id, app_apple_id, environment,
                 signing_key, key_id, issuer_id, plans, verifier=None, client=None,
                 binding_resolver=None):
        from appstoreserverlibrary.models.Environment import Environment
        from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier
        from appstoreserverlibrary.api_client import AppStoreServerAPIClient
        choices = {"production": Environment.PRODUCTION, "sandbox": Environment.SANDBOX}
        if environment not in choices or not bundle_id or not root_certificates:
            raise BillingError("Configure Apple audience, trusted roots and a signed production/sandbox environment")
        if environment == "production" and not app_apple_id:
            raise BillingError("Production Apple verification requires the numeric App Store app ID")
        if not signing_key or not key_id or not issuer_id:
            raise BillingError("Apple current-status verification requires an App Store Server API key")
        self.environment, self.bundle_id = choices[environment], bundle_id
        self.app_apple_id = app_apple_id
        # Online certificate/revocation checks are mandatory. Xcode/LocalTesting
        # environments, whose library path skips signatures, are never accepted.
        self.verifier = verifier or SignedDataVerifier(root_certificates, True,
                                                       self.environment, bundle_id, app_apple_id)
        self.client = client or AppStoreServerAPIClient(signing_key, key_id, issuer_id,
                                                        bundle_id, self.environment)
        self.plans = {p["apple_product_id"]: p for p in plans if p.get("apple_product_id")}
        self.binding_resolver = binding_resolver
        if not self.plans:
            raise BillingError("Configure Apple auto-renewable product mappings explicitly")

    def _transaction(self, signed):
        from appstoreserverlibrary.models.Type import Type
        tx = self.verifier.verify_and_decode_signed_transaction(signed)
        if (_value(tx, "bundleId") != self.bundle_id or _value(tx, "environment") != self.environment
                or _value(tx, "type") != Type.AUTO_RENEWABLE_SUBSCRIPTION
                or _value(tx, "productId") not in self.plans
                or _value(tx, "quantity") != 1 or _value(tx, "inAppOwnershipType").value != "PURCHASED"):
            raise BillingError("Apple transaction audience, ownership or product mismatch")
        try:
            uuid.UUID(str(_value(tx, "appAccountToken")))
        except (TypeError, ValueError) as exc:
            raise BillingError("A household-bound appAccountToken is required") from exc
        if not _value(tx, "originalTransactionId") or not _value(tx, "transactionId"):
            raise BillingError("Missing verified Apple transaction identity")
        return tx

    def verify_transaction(self, signed, *, household_id, app_account_token,
                           checkout_reference=None, notification_id=None, notification_date=0):
        """Verify device JWS and latest server state before purchase or restore."""
        from appstoreserverlibrary.models.Status import Status
        try:
            supplied = self._transaction(signed)
            original = _value(supplied, "originalTransactionId")
            account_token = str(uuid.UUID(str(app_account_token)))
            if str(uuid.UUID(str(_value(supplied, "appAccountToken")))) != account_token:
                raise BillingError("Apple purchase belongs to another household")
            response = self.client.get_all_subscription_statuses(original)
            if (_value(response, "environment") != self.environment or _value(response, "bundleId") != self.bundle_id
                    or (self.environment.value == "Production" and _value(response, "appAppleId") != self.app_apple_id)):
                raise BillingError("Apple subscription response audience mismatch")
            matches = []
            for group in _value(response, "data", []) or []:
                for state in _value(group, "lastTransactions", []) or []:
                    candidate = self._transaction(_value(state, "signedTransactionInfo"))
                    if (_value(candidate, "originalTransactionId") == original
                            and str(uuid.UUID(str(_value(candidate, "appAccountToken")))) == account_token):
                        matches.append((candidate, _value(state, "status")))
            if not matches:
                raise BillingError("Apple did not confirm the current subscription")
            tx, status = max(matches, key=lambda pair: (_value(pair[0], "purchaseDate", 0) or 0,
                                                       _value(pair[0], "signedDate", 0) or 0))
            expires, signed_date = _value(tx, "expiresDate"), _value(tx, "signedDate")
            if not isinstance(expires, int) or not isinstance(signed_date, int):
                raise BillingError("Missing signed Apple subscription timestamps")
            revoked = _value(tx, "revocationDate") is not None or status == Status.REVOKED or bool(_value(tx, "isUpgraded"))
            if revoked:
                normalized, priority = "cancelled", 4
            elif status == Status.ACTIVE and expires > int(time.time() * 1000):
                normalized, priority = "active", 1
            else:
                # Billing retry/grace is not silently promoted to paid access.
                normalized, priority = "expired", 3
            plan = self.plans[_value(tx, "productId")]
            revision = max(signed_date, notification_date or 0) * 8 + priority
            event_id = notification_id or ("transaction:" + str(_value(tx, "transactionId")) + ":" + str(revision))
            return VerifiedBillingEvent("apple", event_id, household_id, original,
                plan["id"], normalized, expires // 1000 if not revoked else min(expires // 1000, int(time.time())),
                revision, checkout_reference)
        except BillingError:
            raise
        except Exception as exc:
            raise BillingError("Apple signature or current subscription verification failed") from exc

    def verify_event(self, payload, headers):
        try:
            body = json.loads(payload)
            if not isinstance(body, dict) or not isinstance(body.get("signedPayload"), str):
                raise BillingError("A signed App Store notification is required")
            notification = self.verifier.verify_and_decode_notification(body["signedPayload"])
            data = _value(notification, "data")
            if not data or not _value(data, "signedTransactionInfo"):
                raise IgnoredBillingEvent("No subscription transaction in this verified notification")
            # Verify the nested transaction independently; the outer notification
            # alone does not authorize trusting its nested claims without a check.
            tx = self._transaction(_value(data, "signedTransactionInfo"))
            plan = self.plans[_value(tx, "productId")]
            if not self.binding_resolver:
                raise BillingError("Apple household binding lookup is unavailable")
            binding = self.binding_resolver(str(uuid.UUID(str(_value(tx, "appAccountToken")))),
                                             plan["id"], _value(tx, "originalTransactionId"))
            return self.verify_transaction(_value(data, "signedTransactionInfo"),
                household_id=binding["household_id"], app_account_token=_value(tx, "appAccountToken"),
                checkout_reference=binding.get("checkout_reference"),
                notification_id=_value(notification, "notificationUUID"),
                notification_date=_value(notification, "signedDate") or 0)
        except (BillingError, IgnoredBillingEvent):
            raise
        except Exception as exc:
            raise BillingError("Apple notification verification failed") from exc
