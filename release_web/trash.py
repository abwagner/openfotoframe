"""Explicit owner/admin restoration and queued permanent deletion for one collection."""

from urllib.parse import urlencode
from flask import Blueprint, abort, redirect, request, session
from werkzeug.exceptions import HTTPException
from .accounts import principal, account_error
from .compat import api, payload, identifier
from .pages import portal, check_csrf
from .storage import byte_label

trash_pages = Blueprint("release_trash_pages", __name__)
trash_pages.register_error_handler(HTTPException, account_error)


@trash_pages.route("/trash", methods=["GET", "POST"])
def trash():
    identity = principal()
    households = [h for h in identity["households"] if h["role"] in ("owner", "admin")]
    hid = request.values.get("household_id") or session.get("release_household_id")
    cid = request.values.get("collection_id")
    if not cid and hid == session.get("release_household_id"):
        cid = session.get("release_collection_id")
    selected = None
    collections = []
    assets = []
    next_cursor = None
    selected_collection = None
    if hid:
        hid = identifier(hid)
        selected = next((h for h in households if h["id"] == hid), None)
        if not selected:
            abort(403 if any(h["id"] == hid for h in identity["households"]) else 404)
        prefix = "/api/v1/households/" + hid
        collections = payload(api(prefix + "/collections"))["collections"]
        if cid:
            cid = identifier(cid)
            selected_collection = next((c for c in collections if c["id"] == cid), None)
            if not selected_collection:
                abort(404)
            path = prefix + "/collections/" + cid
            if request.method == "POST":
                check_csrf()
                aid = identifier(request.form.get("asset_id"))
                action = request.form.get("action")
                if action == "restore":
                    payload(api(path + "/assets/" + aid + "/restore", "POST", {}))
                elif action == "purge":
                    if request.form.get("confirm_permanent_delete") != "yes":
                        abort(
                            400,
                            description="Confirm permanent removal before continuing.",
                        )
                    payload(
                        api(
                            path + "/assets/" + aid + "/purge",
                            "POST",
                            {"confirm_permanent_delete": True},
                        )
                    )
                else:
                    abort(400)
                return redirect(
                    "/trash?"
                    + urlencode(
                        {"household_id": hid, "collection_id": cid, "result": action}
                    ),
                    303,
                )
            after = request.args.get("after")
            result = payload(
                api(
                    path
                    + "/trash"
                    + ("?" + urlencode({"after": identifier(after)}) if after else "")
                )
            )
            assets, next_cursor = result["assets"], result.get("next_cursor")
    if request.method == "POST":
        abort(
            400,
            description="Select a household and collection before changing deleted photos.",
        )
    return portal(
        "trash.html",
        households=households,
        selected=selected,
        collections=collections,
        collection=selected_collection,
        assets=assets,
        next_cursor=next_cursor,
        bytes_label=byte_label,
        result=request.args.get("result"),
    )
