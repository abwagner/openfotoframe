"""Administrative household usage; never disclose hidden collections to members."""

from flask import Blueprint, current_app, g, jsonify
from .security import require_auth, household, fail, now
from release_billing import BillingService, BillingError

bp = Blueprint("release_storage_accounting", __name__, url_prefix="/api/v1")


@bp.get("/households/<hid>/storage")
@require_auth
def storage_usage(hid):
    household(hid, manage=True)
    hosted = current_app.config["RELEASE_MODE"] == "hosted"
    try:
        service = BillingService(
            current_app.config.get("BILLING_PLANS") or [] if hosted else [],
            clock=lambda: now(),
        )
        return jsonify(
            service.storage_summary(
                g.release_db,
                hid,
                authorize=lambda: household(hid, manage=True),
                hosted=hosted,
            )
        )
    except BillingError as error:
        fail(
            error.code,
            "Storage accounting configuration requires operator review.",
            error.status,
        )
