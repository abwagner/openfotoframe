"""Translate the existing editors onto the authorized versioned API.

These routes never load the legacy JSON store or trust a legacy login cookie.
The versioned handler still resolves the principal and checks resource grants.
An in-process request context reuses the release transaction; it does not make
an HTTP call, create another database transaction, or bypass endpoint policy.
"""

import io
import re

from flask import Blueprint, abort, current_app, g, jsonify, request, session
from datetime import datetime, timezone
from werkzeug.datastructures import MultiDict
from werkzeug.test import EnvironBuilder
from werkzeug.exceptions import HTTPException

blueprint = Blueprint("release_compat", __name__)
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEFAULT_SETTINGS = {
    "mat_color": "#ffffff",
    "mat_finish": "eggshell",
    "bevel_width": 4,
    "border_effect": "bevel",
    "bevel_lit_intensity": 50,
    "bevel_lit_v": 50,
    "bevel_lit_h": 50,
    "slideshow_interval": 60,
    "transition_duration": 1,
    "fit_mode": "contain",
    "auto_mat_color": True,
    "shuffle": False,
    "image_order": [],
    "target_aspect_ratio": "16:9",
}


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        abort(400, description="Select a valid household, collection, and frame.")
    return value


def scope(kind):
    value = request.headers.get("X-OpenFotoFrame-" + kind.title())
    if not value:
        value = session.get("release_" + kind + "_id")
    if not value:
        abort(409, description=f"Select a {kind} before opening this screen.")
    return identifier(value)


def collection_path(suffix=""):
    return f"/api/v1/households/{scope('household')}/collections/{scope('collection')}{suffix}"


def frame_path(suffix=""):
    frame = (
        request.args.get("display")
        or request.headers.get("X-OpenFotoFrame-Frame")
        or session.get("release_frame_id")
    )
    if not frame:
        abort(409, description="Select a frame before using display controls.")
    return f"/api/v1/households/{scope('household')}/frames/{identifier(frame)}{suffix}"


def api(
    path,
    method="GET",
    body=None,
    upload=None,
    extra_headers=None,
    authenticated=True,
    copy_session=False,
):
    """Invoke a v1 route inside the current release transaction and identity."""
    headers = {
        name: request.headers[name]
        for name in (
            "Authorization",
            "Cookie",
            "X-CSRFToken",
            "X-CSRF-Token",
            "Origin",
            "Sec-Fetch-Site",
        )
        if name in request.headers
    }
    if "X-CSRFToken" not in headers and request.form.get("csrf_token"):
        headers["X-CSRFToken"] = request.form["csrf_token"]
    headers.update(extra_headers or {})
    kwargs = {
        "path": path,
        "method": method,
        "base_url": request.host_url,
        "headers": headers,
    }
    if upload is not None:
        kwargs["data"] = MultiDict(
            [("file", (io.BytesIO(upload.read()), upload.filename, upload.mimetype))]
        )
    elif body is not None:
        kwargs["json"] = body
    builder = EnvironBuilder(**kwargs)
    outer_session = session._get_current_object()
    g.release_nested_dispatch = getattr(g, "release_nested_dispatch", 0) + 1
    try:
        with current_app.request_context(builder.get_environ()):
            try:
                if authenticated:
                    from release_backend.security import require_auth

                    require_auth()
                endpoint, values = current_app.url_map.bind_to_environ(
                    request.environ
                ).match()
                result = current_app.view_functions[endpoint](**values)
            except HTTPException as error:
                result = current_app.handle_user_exception(error)
            response = current_app.make_response(result)
            if copy_session:
                outer_session.clear()
                outer_session.update(dict(session))
            return response
    finally:
        builder.close()
        g.release_nested_dispatch -= 1


def payload(response):
    if response.status_code >= 400:
        # Raising preserves the outer request's rollback/error policy.
        data = response.get_json(silent=True) or {}
        error = data.get("error", "Request failed.")
        abort(
            response.status_code,
            description=error.get("message", "Request failed.")
            if isinstance(error, dict)
            else str(error),
        )
    return response.get_json(silent=True) or {}


def records(data, key):
    if isinstance(data, list):
        return data
    return data.get(key, [])


def photo(record):
    meta = dict(record.get("metadata") or {})
    created = meta.get("uploaded_at", record.get("created_at"))
    if isinstance(created, (int, float)):
        created = datetime.fromtimestamp(created, timezone.utc).isoformat()
    meta.update(
        filename=record["id"],
        size=record.get("byte_size", 0),
        uploaded_at=created,
        uploaded_by=meta.get("uploaded_by", record.get("uploaded_by", "")),
    )
    meta.setdefault("title", record.get("filename", ""))
    meta.setdefault("enabled", True)
    return meta


def group(record):
    meta = dict(record.get("metadata") or {})
    meta["images"] = record.get("asset_ids", record.get("images", []))
    return meta


@blueprint.get("/api/gallery")
def gallery():
    assets = records(payload(api(collection_path("/assets"))), "assets")
    groups = records(payload(api(collection_path("/groups"))), "groups")
    live = {item["id"] for item in assets}
    group_data = {}
    for item in groups:
        data = group(item)
        data["images"] = [value for value in data["images"] if value in live]
        if len(data["images"]) >= 2:
            group_data[item["id"]] = data
    return jsonify(images=[photo(item) for item in assets], groups=group_data)


@blueprint.route("/api/gallery/<asset_id>", methods=["PATCH", "DELETE"])
def asset_update(asset_id):
    body = {"metadata": request.get_json()} if request.method == "PATCH" else None
    result = api(
        collection_path("/assets/" + identifier(asset_id)), request.method, body
    )
    payload(result)
    return jsonify(success=True)


@blueprint.patch("/api/gallery/<asset_id>/displays/<frame_id>")
def asset_display_override(asset_id, frame_id):
    from release_backend.security import require_auth, frame
    from release_backend.content import asset_record, validate_metadata
    from release_backend import models as m
    from sqlalchemy import update, and_

    require_auth()
    hid, cid, aid, fid = (
        scope("household"),
        scope("collection"),
        identifier(asset_id),
        identifier(frame_id),
    )
    record = asset_record(hid, cid, aid, "contributor_edit")
    frame(hid, fid)
    values = validate_metadata(request.get_json() or {})
    metadata = dict(record["metadata"])
    overrides = dict(metadata.get("display_overrides", {}))
    overrides[fid] = dict(overrides.get(fid, {}), **values)
    metadata["display_overrides"] = overrides
    g.release_db.execute(
        update(m.assets)
        .where(
            and_(
                m.assets.c.household_id == hid,
                m.assets.c.collection_id == cid,
                m.assets.c.id == aid,
            )
        )
        .values(metadata=metadata)
    )
    return jsonify(success=True)


@blueprint.post("/api/gallery/bulk")
def bulk():
    data = request.get_json() or {}
    action = data.get("action")
    if action not in ("enable", "disable", "delete"):
        abort(400, description="Choose enable, disable, or delete.")
    ids = data.get("filenames", [])
    # The v1 endpoint validates the entire batch before writing anything.
    payload(
        api(
            collection_path("/assets/bulk"),
            "POST",
            {
                "asset_ids": ids,
                "metadata": {"enabled": action == "enable"}
                if action != "delete"
                else {},
            },
        )
    )
    if action == "delete":
        for aid in ids:
            payload(api(collection_path("/assets/" + identifier(aid)), "DELETE"))
    return jsonify(success=True, count=len(ids))


@blueprint.route("/api/groups", methods=["GET", "POST"])
def groups():
    if request.method == "GET":
        items = records(payload(api(collection_path("/groups"))), "groups")
        return jsonify(groups={item["id"]: group(item) for item in items})
    data = request.get_json() or {}
    created = payload(
        api(
            collection_path("/groups"),
            "POST",
            {
                "asset_ids": data.get("images", []),
                "metadata": {k: v for k, v in data.items() if k != "images"},
            },
        )
    )
    return jsonify(
        success=True, group_id=created.get("id", created.get("group", {}).get("id"))
    )


@blueprint.route("/api/groups/<group_id>", methods=["PATCH", "DELETE"])
def group_update(group_id):
    data = request.get_json(silent=True) or {}
    body = {"metadata": {k: v for k, v in data.items() if k != "images"}}
    if "images" in data:
        body["asset_ids"] = data["images"]
    payload(
        api(
            collection_path("/groups/" + identifier(group_id)),
            request.method,
            body if request.method == "PATCH" else None,
        )
    )
    return jsonify(success=True)


@blueprint.route("/api/settings", methods=["GET", "POST"])
def settings():
    result = payload(
        api(
            collection_path("/settings"),
            "PATCH" if request.method == "POST" else "GET",
            request.get_json(silent=True) if request.method == "POST" else None,
        )
    )
    settings = result.get("settings", result)
    if "display_order" in settings:
        settings = dict(settings, image_order=settings["display_order"])
    return jsonify(
        DEFAULT_SETTINGS
        | {key: value for key, value in settings.items() if key in DEFAULT_SETTINGS}
    )


@blueprint.post("/api/reorder")
def reorder():
    data = request.get_json() or {}
    return api(
        collection_path("/reorder"),
        "POST",
        {"asset_ids": data.get("order", data.get("image_order", []))},
    )


@blueprint.post("/api/upload")
def upload():
    files = request.files.getlist("files")
    if not files or any(not item.filename for item in files):
        abort(400, description="Choose photos to upload.")
    uploaded = []
    for item in files:
        result = payload(api(collection_path("/assets"), "POST", upload=item))
        record = result.get("asset", result)
        uploaded.append(record["id"])
    return jsonify(uploaded=uploaded, errors=[])


@blueprint.post("/api/check-duplicates")
def duplicates():
    files = request.files.getlist("files")
    if not files:
        abort(400, description="Choose photos to check.")
    results = {}
    for item in files:
        result = payload(api(collection_path("/duplicates"), "POST", upload=item))
        result["matches"] = [
            {"filename": value, "distance": 0} for value in result.get("duplicates", [])
        ]
        result["matches"] += [
            {"filename": value["id"], "distance": value["distance"]}
            for value in result.get("similar", [])
        ]
        results[item.filename] = result
    return jsonify(results=results, errors=[])


@blueprint.get("/uploads/<asset_id>")
def original(asset_id):
    return api(
        f"/api/v1/households/{scope('household')}/assets/{identifier(asset_id)}/media"
    )


@blueprint.get("/thumbnails/<asset_id>")
def thumbnail(asset_id):
    return api(
        f"/api/v1/households/{scope('household')}/assets/{identifier(asset_id)}/thumbnail"
    )


def display(record):
    settings = record.get("settings") or {}
    return {
        "id": record["id"],
        "name": record.get("name", "Frame"),
        "active": settings.get("active", True),
        "width": settings.get("width", 1920),
        "height": settings.get("height", 1080),
        "synchronized": False,
        "content": settings.get("content", {"mode": "photos"}),
        "collection_ids": record.get("collection_ids", []),
    }


@blueprint.route("/api/displays", methods=["GET", "POST"])
def displays():
    path = f"/api/v1/households/{scope('household')}/frames"
    if request.method == "GET":
        frames = records(payload(api(path)), "frames")
        return jsonify(displays=[display(record) for record in frames])
    data = request.get_json() or {}
    visual = {
        key: value
        for key, value in data.items()
        if key in DEFAULT_SETTINGS
        or key in ("width", "height", "content", "active", "synchronized")
    }
    result = payload(api(path, "POST", {"name": data.get("name"), "settings": visual}))
    frame = result.get("frame", result)
    if visual:
        result = payload(
            api(path + "/" + identifier(frame["id"]), "PATCH", {"settings": visual})
        )
        frame = result.get("frame", result)
    return jsonify(display=display(frame))


@blueprint.route("/api/displays/<frame_id>", methods=["PATCH", "DELETE"])
def display_update(frame_id):
    data = request.get_json(silent=True) or {}
    updates = {}
    if "name" in data:
        updates["name"] = data["name"]
    visual = {
        key: value
        for key, value in data.items()
        if key in DEFAULT_SETTINGS
        or key in ("width", "height", "content", "active", "synchronized")
    }
    if visual:
        updates["settings"] = visual
    if "collection_ids" in data:
        updates["collection_ids"] = data["collection_ids"]
    result = api(
        f"/api/v1/households/{scope('household')}/frames/{identifier(frame_id)}",
        request.method,
        updates if request.method == "PATCH" else None,
    )
    result_data = payload(result)
    return (
        jsonify(display=display(result_data.get("frame", result_data)))
        if request.method == "PATCH"
        else jsonify(success=True)
    )


@blueprint.get("/api/display/state")
def state():
    result = payload(api(frame_path("/state")))
    return jsonify(result.get("state", result))


@blueprint.post("/api/display/control")
def control():
    body = request.get_json() or {}
    if body.get("action") == "prev":
        body["action"] = "previous"
    result = payload(api(frame_path("/control"), "POST", body))
    return jsonify(result.get("state", result))


@blueprint.get("/api/artworks")
def artworks():
    payload(api("/api/v1/auth/me"))
    from artwork_catalog import catalog

    return jsonify(artworks=catalog())
