"""Verified, replay-safe entitlements; application permissions remain authoritative."""

from dataclasses import dataclass
import hashlib
import time
from sqlalchemy import select, update, insert, func, case, text
from . import models as b


class BillingError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedBillingEvent:
    """Only a trusted server-side provider verifier constructs this object.

    A verifier must validate its provider's signature/receipt, audience, environment,
    product mapping and subscription state before returning this normalized result.
    The HTTP client cannot submit this structure as an entitlement or paid flag.
    """
    provider: str
    event_id: str
    household_id: str
    subscription_id: str
    plan_id: str
    status: str
    valid_until: int
    revision: int
    checkout_reference: str | None = None


def configured_plans(config) -> dict:
    plans = config.get("BILLING_PLANS") or []
    if not isinstance(plans, list):
        raise BillingError("BILLING_PLANS must be a configured list")
    result = {}
    products = {"stripe_price_id": set(), "apple_product_id": set()}
    for value in plans:
        if (not isinstance(value, dict) or not isinstance(value.get("id"), str)
                or not isinstance(value.get("name"), str) or not value["id"] or not value["name"]):
            raise BillingError("Plans require an id and name")
        plan = dict(value)
        for key, seen in products.items():
            if key in plan:
                product = plan[key]
                if not isinstance(product, str) or not product or product in seen:
                    raise BillingError("Provider product mappings must be nonempty and unique")
                seen.add(product)
        if plan["id"] in result:
            raise BillingError("Plan IDs must be unique")
        for key in ("storage_bytes", "asset_limit", "frame_limit"):
            limit = plan.get(key)
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise BillingError("Each plan must explicitly configure positive storage, asset and frame limits")
        result[plan["id"]] = plan
    return result


class BillingService:
    def __init__(self, plans, *, clock=time.time):
        self.plans = configured_plans({"BILLING_PLANS": plans})
        self.clock = clock

    def apply_event(self, db, event: VerifiedBillingEvent, raw_payload: bytes) -> dict:
        """Apply inside a provider-authorized service transaction, never user context.

        Existing subscription bindings prevent reassignment to another household.
        The initial binding requires a server-created checkout reference. Replayed
        events are idempotent, mismatched payloads fail, and older revisions cannot
        re-enable an expired/cancelled entitlement. No content is deleted on expiry.
        """
        from release_backend.models import households
        if (not isinstance(event, VerifiedBillingEvent) or not isinstance(event.status, str)
                or event.status not in {"active", "expired", "cancelled", "past_due"}):
            raise BillingError("Invalid verified provider event")
        fields = ((event.provider, 40), (event.event_id, 255), (event.household_id, 64),
                  (event.subscription_id, 255), (event.plan_id, 120))
        if any(not isinstance(value, str) or not value or len(value) > limit for value, limit in fields):
            raise BillingError("Invalid verified provider identity")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in (event.valid_until, event.revision)):
            raise BillingError("Invalid verified provider timestamps/revision")
        if event.plan_id not in self.plans or not event.event_id or not event.subscription_id or event.revision < 0:
            raise BillingError("Unknown plan or invalid event identity")
        digest = hashlib.sha256(raw_payload).hexdigest()
        # Serialize checkout/event/quota updates per household in PostgreSQL.
        if db.dialect.name == "postgresql":
            exists = db.execute(text("SELECT public.release_lock_billing_household(:hid)"),
                                {"hid": event.household_id}).scalar_one()
        else:
            exists = db.execute(select(households.c.id).where(households.c.id == event.household_id).with_for_update()).first()
        if not exists:
            raise BillingError("Unknown billing household")
        prior_event = db.execute(select(b.events).where(
            (b.events.c.provider == event.provider) & (b.events.c.event_id == event.event_id))).mappings().first()
        if prior_event:
            if prior_event["payload_hash"] != digest or prior_event["household_id"] != event.household_id:
                raise BillingError("Provider event ID was reused with different content")
            return {**prior_event["result"], "replayed": True}
        existing = db.execute(select(b.subscriptions).where(b.subscriptions.c.household_id == event.household_id)).mappings().first()
        binding = db.execute(select(b.subscription_bindings).where(
            (b.subscription_bindings.c.provider == event.provider) &
            (b.subscription_bindings.c.subscription_id == event.subscription_id))).mappings().first()
        if binding and binding["household_id"] != event.household_id:
            raise BillingError("Subscription is bound to another household")
        same = existing and existing["provider"] == event.provider and existing["subscription_id"] == event.subscription_id
        if not binding:
            checkout = db.execute(select(b.checkout_requests).where(
                (b.checkout_requests.c.id == event.checkout_reference) &
                (b.checkout_requests.c.household_id == event.household_id) &
                (b.checkout_requests.c.provider == event.provider) &
                (b.checkout_requests.c.plan_id == event.plan_id))).mappings().first()
            if not checkout:
                raise BillingError("Initial subscription requires a matching server checkout")
            db.execute(insert(b.subscription_bindings).values(provider=event.provider,
                subscription_id=event.subscription_id, household_id=event.household_id,
                checkout_reference=checkout["id"], last_revision=event.revision,
                last_status=event.status, last_plan_id=event.plan_id,
                last_valid_until=event.valid_until, created_at=int(self.clock())))
        newer = not binding or event.revision > binding["last_revision"]
        if binding and event.revision == binding["last_revision"]:
            if any(binding[k] != getattr(event, value) for k, value in
                   (("last_status", "status"), ("last_plan_id", "plan_id"), ("last_valid_until", "valid_until"))):
                raise BillingError("Conflicting subscription revision")
        if binding and newer:
            db.execute(update(b.subscription_bindings).where(
                (b.subscription_bindings.c.provider == event.provider) &
                (b.subscription_bindings.c.subscription_id == event.subscription_id)).values(
                    last_revision=event.revision, last_status=event.status,
                    last_plan_id=event.plan_id, last_valid_until=event.valid_until))
        # Do not let another channel or a retired subscription replace an active
        # household subscription. Once that subscription ends, a genuinely newer
        # verified renewal/restore may reactivate its permanent original binding.
        different_active = bool(existing and not same and existing["status"] == "active"
                                and existing["valid_until"] > self.clock())
        applied = newer and not different_active and (not same or event.revision > existing["revision"])
        if same and event.revision == existing["revision"]:
            # Equal revisions with contradictory state are not safely orderable.
            if any(existing[k] != getattr(event, k) for k in ("plan_id", "status", "valid_until")):
                raise BillingError("Conflicting subscription revision")
        if applied:
            values = dict(provider=event.provider, subscription_id=event.subscription_id,
                          plan_id=event.plan_id, status=event.status, valid_until=event.valid_until,
                          revision=event.revision, updated_at=int(self.clock()))
            if existing:
                db.execute(update(b.subscriptions).where(b.subscriptions.c.household_id == event.household_id).values(**values))
            else:
                db.execute(insert(b.subscriptions).values(household_id=event.household_id, **values))
        result = {"accepted": True, "applied": bool(applied), "replayed": False}
        db.execute(insert(b.events).values(provider=event.provider, event_id=event.event_id,
            household_id=event.household_id, payload_hash=digest, result=result, created_at=int(self.clock())))
        return result

    def entitlement(self, db, household_id: str, *, authorize) -> dict:
        authorize()  # Check current membership/role before reading billing state.
        row = db.execute(select(b.subscriptions).where(b.subscriptions.c.household_id == household_id)).mappings().first()
        plan = self.plans.get(row["plan_id"]) if row else None
        active = bool(row and plan and row["status"] == "active" and row["valid_until"] > self.clock())
        return {"active": active, "plan_id": row["plan_id"] if row else None,
                "provider": row["provider"] if row else None,
                "status": row["status"] if row else "unconfigured",
                "valid_until": row["valid_until"] if row else None,
                "limits": {k: plan[k] for k in ("storage_bytes", "asset_limit", "frame_limit")} if active else None}

    def check_quota(self, db, household_id: str, *, authorize, bytes_to_add=0, assets_to_add=0, frames_to_add=0) -> dict:
        from release_backend.models import assets, frames, households
        authorize()
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0
               for v in (bytes_to_add, assets_to_add, frames_to_add)):
            raise BillingError("Quota increments must be nonnegative integers")
        if db.dialect.name == "postgresql":
            # RLS intentionally hides ungranted collections and owner-only billing
            # rows. Bounded definer functions compute household quota atomically;
            # their raw counts are never returned through a member-facing API.
            usage = db.execute(text("SELECT * FROM public.release_quota_usage(:hid)"), {"hid": household_id}).mappings().one()
            row = db.execute(text("SELECT * FROM public.release_quota_entitlement(:hid)"), {"hid": household_id}).mappings().first()
            plan = self.plans.get(row["plan_id"]) if row else None
            active = bool(row and plan and row["status"] == "active" and row["valid_until"] > self.clock())
            entitlement = {"active": active, "plan_id": row["plan_id"] if row else None,
                "status": row["status"] if row else "unconfigured",
                "valid_until": row["valid_until"] if row else None,
                "limits": {k: plan[k] for k in ("storage_bytes", "asset_limit", "frame_limit")} if active else None}
            used_bytes, used_assets, used_frames = usage["total_bytes"], usage["active_assets"], usage["frame_count"]
        else:
            db.execute(select(households.c.id).where(households.c.id == household_id).with_for_update()).first()
            entitlement = self.entitlement(db, household_id, authorize=lambda: None)
            # Soft deletion retains the original bytes until an explicit owner
            # purge; visible-only counting allows upload/delete quota bypass.
            used_bytes, used_assets = db.execute(select(
                func.coalesce(func.sum(assets.c.byte_size), 0),
                func.coalesce(func.sum(case((assets.c.deleted == False, 1), else_=0)), 0)
            ).where(assets.c.household_id == household_id)).one()
            used_frames = db.execute(select(func.count()).select_from(frames).where(frames.c.household_id == household_id)).scalar_one()
        if not entitlement["active"]:
            raise BillingError("An active hosted subscription is required for new content")
        for used, added, key in ((used_bytes, bytes_to_add, "storage_bytes"),
                                 (used_assets, assets_to_add, "asset_limit"),
                                 (used_frames, frames_to_add, "frame_limit")):
            if used + added > entitlement["limits"][key]:
                raise BillingError("The configured subscription limit would be exceeded")
        return entitlement
