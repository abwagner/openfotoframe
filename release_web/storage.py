"""Owner/administrator-only household storage presentation; no aggregate member reads."""

from flask import Blueprint, abort, request, session
from .accounts import principal, account_error
from .compat import api, payload, identifier
from .pages import portal
from werkzeug.exceptions import HTTPException

storage_pages = Blueprint("release_storage_pages", __name__)
storage_pages.register_error_handler(HTTPException, account_error)


def byte_label(value):
    amount = max(0, int(value))
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if amount < 1000 or unit == "PB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{amount} B"
        amount /= 1000


@storage_pages.get("/storage")
def storage():
    identity = principal()
    households = [h for h in identity["households"] if h["role"] in ("owner", "admin")]
    hid = request.args.get("household_id") or session.get("release_household_id")
    selected = None
    result = None
    if hid:
        hid = identifier(hid)
        selected = next((h for h in households if h["id"] == hid), None)
        if not selected:
            known = any(h["id"] == hid for h in identity["households"])
            abort(
                403 if known else 404,
                description="Only household owners and administrators can view aggregate storage.",
            )
        result = payload(api("/api/v1/households/" + hid + "/storage"))
        if result.get("household_id") != hid:
            abort(
                502,
                description="The storage response did not match the selected household.",
            )
    return portal(
        "storage.html",
        identity=identity,
        households=households,
        selected=selected,
        storage=result,
        bytes_label=byte_label,
    )
