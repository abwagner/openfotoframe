"""Authorized display rendering without public media or shared display secrets."""

import hashlib
import io
from pathlib import Path
import re
import tempfile
from flask import Blueprint, abort, jsonify, request, session, redirect, render_template
from PIL import Image, ImageOps
from render_display import render_single_slide, render_group_slide, parse_aspect_ratio
from .compat import api, payload, scope, identifier, frame_path, collection_path, records, DEFAULT_SETTINGS

display_pages = Blueprint("release_display", __name__)


def frame_browser():
    return bool(
        request.cookies.get("release_frame")
        or request.headers.get("Authorization", "").startswith("Frame ")
    )


def playlist():
    if frame_browser():
        result = payload(api("/api/v1/display/playlist", authenticated=False))
        if (
            request.args.get("display")
            and request.args["display"] != result["frame_id"]
        ):
            abort(404)
        return result
    detail = payload(api(frame_path()))
    frame = detail.get("frame", detail)
    hid = scope("household")
    assets, groups, settings = [], [], {}
    for cid in frame.get("collection_ids", []):
        cid = identifier(cid)
        result = api(f"/api/v1/households/{hid}/collections/{cid}/assets")
        if result.status_code in (403, 404):
            abort(
                403,
                description="This frame contains collections that are not shared with you. You can still use your permitted frame controls.",
            )
        values = payload(result)
        assets.extend(
            item
            for item in values.get("assets", [])
            if item.get("metadata", {}).get("enabled", True)
        )
        groups.extend(values.get("groups", []))
        config = payload(api(f"/api/v1/households/{hid}/collections/{cid}/settings"))
        settings[cid] = config.get("settings", config)
    return {
        "frame_id": frame["id"],
        "assets": assets,
        "groups": groups,
        "collection_settings": settings,
        "settings": frame.get("settings", {}),
        "state": frame.get("state", {}),
    }


def effective_settings(data, cid=None):
    return (
        DEFAULT_SETTINGS
        | data.get("collection_settings", {}).get(cid, {})
        | data.get("settings", {})
    )


def slides(data):
    assets = {item["id"]: item for item in data.get("assets", [])}
    result, grouped = [], set()
    for item in data.get("groups", []):
        ids = [aid for aid in item.get("asset_ids", []) if aid in assets]
        if len(ids) >= 2:
            result.append(
                {
                    "id": item["id"],
                    "asset_ids": ids,
                    "metadata": item.get("metadata", {}),
                    "collection_id": item.get(
                        "collection_id", assets[ids[0]]["collection_id"]
                    ),
                }
            )
            grouped.update(ids)
    pending = None
    for aid, item in assets.items():
        if aid in grouped:
            continue
        meta = dict(item.get("metadata", {}))
        meta.update(meta.get("display_overrides", {}).get(data["frame_id"], {}))
        config = effective_settings(data, item["collection_id"])
        crop = meta.get("crop") or {"w": 1, "h": 1}
        w, h = meta.get("width") or 0, meta.get("height") or 0
        if int(meta.get("rotation", 0)) % 180:
            w, h = h, w
        w, h = w * crop.get("w", 1), h * crop.get("h", 1)
        portrait = w > 0 and h > 0 and w < h
        if portrait and config.get("auto_pair_portraits", True):
            if pending and pending["collection_id"] == item["collection_id"]:
                ids = [pending["id"], aid]
                pair_id = (
                    "pair_" + hashlib.sha256("|".join(ids).encode()).hexdigest()[:32]
                )
                result.append(
                    {
                        "id": pair_id,
                        "asset_ids": ids,
                        "metadata": {},
                        "collection_id": item["collection_id"],
                    }
                )
                pending = None
                continue
            if pending:
                result.append(
                    {
                        "id": pending["id"],
                        "asset_ids": [pending["id"]],
                        "metadata": {},
                        "collection_id": pending["collection_id"],
                    }
                )
            pending = item
            continue
        result.append(
            {
                "id": aid,
                "asset_ids": [aid],
                "metadata": {},
                "collection_id": item["collection_id"],
            }
        )
    if pending:
        result.append(
            {
                "id": pending["id"],
                "asset_ids": [pending["id"]],
                "metadata": {},
                "collection_id": pending["collection_id"],
            }
        )
    order = (
        data.get("settings", {}).get("display_order")
        or data.get("settings", {}).get("image_order")
        or []
    )
    if not order:
        # The gallery reorder API persists collection order, independently of a
        # frame's optional presentation override.
        for cid, values in data.get("collection_settings", {}).items():
            collection_order = (
                values.get("display_order") or values.get("image_order") or []
            )
            order.extend(
                aid
                for aid in collection_order
                if aid in assets and assets[aid]["collection_id"] == cid
            )
    if order:
        ranks = {aid: n for n, aid in enumerate(order)}
        result.sort(
            key=lambda item: min(
                (ranks.get(aid, len(ranks)) for aid in item["asset_ids"]),
                default=len(ranks),
            )
        )
    if data.get("settings", {}).get("shuffle"):
        # Stable order for a given day/frame; polling cannot reshuffle the list.
        from datetime import datetime, timezone

        seed = datetime.now(timezone.utc).date().isoformat() + data["frame_id"]
        result.sort(
            key=lambda item: hashlib.sha256((seed + item["id"]).encode()).hexdigest()
        )
    return result


@display_pages.route("/display/enroll", methods=["GET", "POST"])
def enroll():
    if request.method == "POST":
        token = request.form.get("token", "")
        if not token or len(token) > 200:
            abort(400, description="Enter a current frame setup code.")
        result = api(
            "/api/v1/display/enroll", "POST", {"token": token}, authenticated=False
        )
        payload(result)
        result.status_code = 303
        result.headers["Location"] = "/display"
        return result
    from .pages import portal

    return portal("enroll_frame.html")


@display_pages.get("/display")
def display():
    if (
        not frame_browser()
        and not request.cookies.get("release_session")
        and not request.headers.get("Authorization")
    ):
        return redirect("/display/enroll")
    data = playlist()
    csrf = (
        request.cookies.get("release_frame_csrf", "")
        if frame_browser()
        else session.get("release_csrf", "")
    )
    return render_template(
        "release/display.html", frame=data, frame_csrf=csrf, physical=frame_browser()
    )


@display_pages.get("/frame/playlist")
def frame_playlist():
    data = playlist()
    presentation = slides(data)
    suffix = "?display=" + data["frame_id"]
    response = {key: data[key] for key in ("frame_id", "settings", "state")}
    import json

    assets = {item["id"]: item for item in data.get("assets", [])}
    response["slides"] = []
    for item in presentation:
        revision = hashlib.sha256(
            json.dumps(
                [
                    item,
                    effective_settings(data, item.get("collection_id")),
                    [assets[aid]["metadata"] for aid in item["asset_ids"]],
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:20]
        response["slides"].append(
            {
                "id": item["id"],
                "snapshot_url": "/frame/snapshots/"
                + item["id"]
                + suffix
                + "&revision="
                + revision,
            }
        )
    content = data.get("settings", {}).get("content", {})
    if content.get("mode") == "art":
        from artwork_catalog import resolve_art, catalog, validate_content

        try:
            response["art"] = resolve_art(validate_content(content))
        except ValueError:
            abort(400, description="The artwork configuration is invalid.")
        response["artwork"] = next(
            item for item in catalog() if item["id"] == response["art"]["artwork_id"]
        )
    result = jsonify(response)
    # Reauthorize and rebuild before evaluating the validator: a prior tag is
    # never authority to retrieve a revoked frame or collection.
    tag = hashlib.sha256(result.get_data()).hexdigest()
    result.set_etag(tag)
    if request.if_none_match.contains(tag):
        result.status_code = 304
        result.set_data(b"")
    return result


@display_pages.post("/frame/control")
def frame_control():
    if frame_browser():
        return api(
            "/api/v1/display/control",
            "POST",
            request.get_json() or {},
            authenticated=False,
        )
    return api(frame_path("/control"), "POST", request.get_json() or {})


def gallery_presentation(display_id=None):
    """Render only the selected, authorized collection, even on multi-collection frames."""
    cid = scope("collection")
    assets = records(payload(api(collection_path("/assets"))), "assets")
    groups = records(payload(api(collection_path("/groups"))), "groups")
    config = payload(api(collection_path("/settings")))
    fid = display_id or request.headers.get("X-OpenFotoFrame-Frame") or session.get("release_frame_id")
    frame = {}
    if fid and fid != "collection":
        frame = payload(api(f"/api/v1/households/{scope('household')}/frames/{identifier(fid)}"))
        frame = frame.get("frame", frame)
        if cid not in frame.get("collection_ids", []):
            assets, groups = [], []
    fid = frame.get("id", "collection")
    settings = DEFAULT_SETTINGS | config.get("settings", config) | frame.get("settings", {})
    width, height = parse_aspect_ratio(settings.get("target_aspect_ratio", "16:9"))
    profile = {"id": fid, "name": frame.get("name", "Collection preview"),
               "width": settings.get("width", width), "height": settings.get("height", height)}
    data = {"frame_id": fid, "assets": [a for a in assets if a.get("metadata", {}).get("enabled", True)],
            "groups": groups, "collection_settings": {cid: config.get("settings", config)},
            "settings": frame.get("settings", {})}
    return data, profile


@display_pages.get("/api/gallery/display-slides")
@display_pages.get("/api/gallery/display-slides/<display_id>")
def gallery_display_slides(display_id=None):
    data, profile = gallery_presentation(display_id)
    manual = {g["id"] for g in data["groups"]}
    items = [{"id": s["id"], "filenames": s["asset_ids"],
              "group_id": s["id"] if s["id"] in manual else None,
              "automatic": len(s["asset_ids"]) > 1 and s["id"] not in manual,
              "thumbnail_path": f"/api/gallery/display-thumbnails/{profile['id']}/{s['id']}"}
             for s in slides(data)]
    response = jsonify(slides=items, profile=profile)
    response.headers["Cache-Control"] = "no-store, private"
    return response


@display_pages.get("/api/gallery/display-thumbnails/<display_id>/<slide_id>")
def gallery_display_thumbnail(display_id, slide_id):
    identifier(display_id)
    identifier(slide_id)
    data, _ = gallery_presentation(display_id)
    slide = next((s for s in slides(data) if s["id"] == slide_id), None)
    if slide is None:
        abort(404)
    response = render(data, slide, thumbnail=True)
    response.headers["Cache-Control"] = "no-store, private"
    return response


def render(data, slide, thumbnail=False):
    from release_backend.security import rate_limit

    rate_limit("render", data["frame_id"], limit=120)
    lookup = {item["id"]: item for item in data["assets"]}
    settings = effective_settings(data, slide.get("collection_id"))
    width, height = settings.get("width", 1920), settings.get("height", 1080)
    if "width" not in settings and "height" not in settings:
        width, height = parse_aspect_ratio(settings.get("target_aspect_ratio", "16:9"))
    if (
        not isinstance(width, int)
        or not isinstance(height, int)
        or not 100 <= width <= 10000
        or not 100 <= height <= 10000
    ):
        width, height = 1920, 1080
    factor = min(1, 4096 / max(width, height))
    size = (max(1, int(width * factor)), max(1, int(height * factor)))
    with tempfile.TemporaryDirectory(prefix="openfotoframe-render-") as directory:
        photos = []
        for aid in slide["asset_ids"]:
            item = lookup[aid]
            meta = dict(item.get("metadata") or {})
            override = meta.get("display_overrides", {}).get(data["frame_id"], {})
            meta.update(override)
            group_metadata = slide.get("metadata", {})
            if aid in group_metadata.get("scales", {}):
                meta["scale"] = group_metadata["scales"][aid]
            if group_metadata.get("mat_finish"):
                meta["mat_finish"] = group_metadata["mat_finish"]
            meta["filename"] = aid
            response = api(item["media_url"], authenticated=not frame_browser())
            if response.status_code >= 400:
                payload(response)
            response.direct_passthrough = False
            try:
                (Path(directory) / aid).write_bytes(response.get_data())
            finally:
                response.close()
            if len(slide["asset_ids"]) > 1:
                # The shared group renderer expects already-oriented pixels.
                path = Path(directory) / aid
                with Image.open(path) as source:
                    oriented = ImageOps.exif_transpose(source)
                    rotation = int(meta.get("rotation", 0)) % 360
                    if rotation:
                        oriented = oriented.rotate(-rotation, expand=True)
                    meta["width"], meta["height"] = oriented.size
                    oriented.save(path, format="PNG")
                meta["rotation"] = 0
            photos.append(meta)
        if len(photos) == 1:
            image = render_single_slide(photos[0], settings, Path(directory), size)
        else:
            image = render_group_slide(
                dict(slide.get("metadata", {}), images=photos),
                settings,
                Path(directory),
                size,
            )
        output = io.BytesIO()
        image = image.convert("RGB")
        if thumbnail:
            # Downsample the completed display so bevel/mat geometry stays identical.
            image.thumbnail((400, 400), Image.Resampling.LANCZOS)
        image.save(output, format="JPEG", quality=88)
    from flask import Response

    return Response(output.getvalue(), mimetype="image/jpeg")


@display_pages.get("/frame/snapshots/<slide_id>")
def frame_snapshot(slide_id):
    identifier(slide_id)
    data = playlist()
    slide = next((item for item in slides(data) if item["id"] == slide_id), None)
    if not slide:
        abort(404)
    return render(data, slide)


@display_pages.get("/snapshots/<name>")
def photo_snapshot(name):
    # Existing photo editors request an opaque photo ID plus an optional frame ID.
    match = re.fullmatch(
        r"([A-Za-z0-9_-]{1,64})(?:\.([A-Za-z0-9_-]{1,64}))?\.display\.(?:png|jpg)", name
    )
    if not match:
        abort(404)
    aid, fid = match.groups()
    hid, cid = scope("household"), scope("collection")
    item = payload(
        api(f"/api/v1/households/{hid}/collections/{cid}/assets/{identifier(aid)}")
    )
    item = item.get("asset", item)
    config = payload(api(f"/api/v1/households/{hid}/collections/{cid}/settings"))
    frame = {}
    if fid:
        result = payload(api(f"/api/v1/households/{hid}/frames/{identifier(fid)}"))
        frame = result.get("frame", result)
    data = {
        "frame_id": fid or cid,
        "assets": [item],
        "settings": frame.get("settings", {}),
        "collection_settings": {cid: config.get("settings", config)},
    }
    return render(data, {"asset_ids": [aid], "collection_id": cid})
