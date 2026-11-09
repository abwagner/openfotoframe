"""Scoped content operations. Metadata IDs, paths and references never confer access."""

import hashlib
import hmac
import io
import json
import math
from pathlib import Path
from flask import Blueprint, current_app, g, jsonify, request, send_file, session
from sqlalchemy import select, and_, update, delete
from PIL import Image, UnidentifiedImageError, ImageOps
import imagehash
from . import models as m
from .security import (
    require_auth,
    fail,
    json_body,
    now,
    uid,
    secret,
    digest,
    bounded_name,
    household,
    collection,
    frame,
    audit,
    set_scope,
)

bp = Blueprint("release_content", __name__, url_prefix="/api/v1")
ASSET_FIELDS = {
    "title",
    "scale",
    "mat_color",
    "mat_finish",
    "bevel_width",
    "border_effect",
    "crop",
    "enabled",
    "no_mat",
    "rotation",
    "bevel_lit_intensity",
    "bevel_lit_v",
    "bevel_lit_h",
}
SETTING_FIELDS = ASSET_FIELDS | {
    "interval",
    "shuffle",
    "auto_pair_portraits",
    "background_color",
    "transition",
    "transition_duration",
    "slide_duration",
    "default_scale",
    "mat_width",
    "group_gap",
    "display_order",
    "slideshow_interval",
    "fit_mode",
    "auto_mat_color",
    "image_order",
    "target_aspect_ratio",
}
GROUP_FIELDS = ASSET_FIELDS | {"scales", "layout", "gap", "title"}


def validate_metadata(values, allowed=ASSET_FIELDS):
    if not isinstance(values, dict) or set(values) - allowed:
        fail("invalid_fields", "Unsupported metadata fields.")

    def finite_json(value):
        if isinstance(value, float) and not math.isfinite(value):
            return False
        if isinstance(value, dict):
            return all(finite_json(item) for item in value.values())
        if isinstance(value, list):
            return all(finite_json(item) for item in value)
        return True

    if not finite_json(values):
        fail("invalid_metadata", "Numeric settings must be finite.")
    for key, choices in {
        "mat_finish": {"flat", "eggshell", "linen", "suede", "silk"},
        "border_effect": {"bevel", "bevel-lit", "shadow", "none"},
        "fit_mode": {"contain", "cover"},
    }.items():
        if (
            key in values
            and values[key] is not None
            and (not isinstance(values[key], str) or values[key] not in choices)
        ):
            fail("invalid_metadata", "Unsupported visual setting.")
    for key in (
        "no_mat",
        "enabled",
        "shuffle",
        "auto_pair_portraits",
        "auto_mat_color",
    ):
        if key in values and not isinstance(values[key], bool):
            fail("invalid_metadata", "Boolean settings must be true or false.")
    if len(json.dumps(values)) > 20000:
        fail("invalid_metadata", "Metadata is too large.")
    if "scale" in values and (
        isinstance(values["scale"], bool)
        or not isinstance(values["scale"], (int, float))
        or not 0.1 <= values["scale"] <= 3
    ):
        fail("invalid_scale", "Scale must be between 0.1 and 3.")
    if "title" in values and (
        not isinstance(values["title"], str) or len(values["title"]) > 500
    ):
        fail("invalid_title", "Title must be at most 500 characters.")
    if "enabled" in values and not isinstance(values["enabled"], bool):
        fail("invalid_enabled", "Enabled must be a boolean.")
    if "crop" in values and values["crop"] is not None:
        crop = values["crop"]
        if (
            not isinstance(crop, dict)
            or set(crop) != {"x", "y", "w", "h"}
            or any(
                isinstance(v, bool) or not isinstance(v, (int, float))
                for v in crop.values()
            )
        ):
            fail("invalid_crop", "Crop must contain normalized x, y, w and h.")
        if not (
            0 <= crop["x"] < 1
            and 0 <= crop["y"] < 1
            and 0 < crop["w"] <= 1
            and 0 < crop["h"] <= 1
            and crop["x"] + crop["w"] <= 1.000001
            and crop["y"] + crop["h"] <= 1.000001
        ):
            fail("invalid_crop", "Crop extends beyond the image.")
    for key in ("mat_color", "background_color"):
        if key in values and values[key] is not None:
            color = values[key]
            if (
                not isinstance(color, str)
                or len(color) != 7
                or color[0] != "#"
                or any(c not in "0123456789abcdefABCDEF" for c in color[1:])
            ):
                fail("invalid_color", "Use a six-digit hexadecimal color.")
    from .validation import check_visual_metadata

    return check_visual_metadata(values)


def asset_json(row):
    return {
        "id": row["id"],
        "collection_id": row["collection_id"],
        "filename": row["filename"],
        "metadata": row["metadata"],
        "title": row["metadata"].get("title", ""),
        "byte_size": row["byte_size"],
        "mime_type": row["mime_type"],
        "uploaded_by": row["uploaded_by"],
        "created_at": row["created_at"],
        "media_url": f"/api/v1/households/{row['household_id']}/assets/{row['id']}/media",
        "thumbnail_url": f"/api/v1/households/{row['household_id']}/assets/{row['id']}/thumbnail",
    }


def asset_record(hid, cid, aid, action="read", allow_deleted=False):
    _, role = collection(hid, cid, "upload" if action == "contributor_edit" else action)
    row = (
        g.release_db.execute(
            select(m.assets).where(
                and_(
                    m.assets.c.household_id == hid,
                    m.assets.c.collection_id == cid,
                    m.assets.c.id == aid,
                )
            )
        )
        .mappings()
        .first()
    )
    if not row or (row["deleted"] and not allow_deleted):
        fail("not_found", "Not found.", 404)
    if (
        action == "contributor_edit"
        and role == "contributor"
        and row["uploaded_by"] != g.release_user["id"]
    ):
        fail("forbidden", "You can only edit your own uploads.", 403)
    return dict(row)


@bp.get("/households")
@require_auth
def households():
    rows = (
        g.release_db.execute(
            select(m.households.c.id, m.households.c.name, m.memberships.c.role)
            .join(m.memberships, m.households.c.id == m.memberships.c.household_id)
            .where(
                and_(
                    m.memberships.c.user_id == g.release_user["id"],
                    m.memberships.c.active.is_(True),
                )
            )
        )
        .mappings()
        .all()
    )
    return jsonify(households=[dict(row) for row in rows])


@bp.post("/households")
@require_auth
def create_household():
    body = json_body()
    if current_app.config["RELEASE_MODE"] == "self_hosted":
        fail(
            "single_household",
            "Self-hosted setup creates its household during bootstrap.",
            409,
        )
    if not g.release_user["email_verified"]:
        fail(
            "verification_required",
            "Verify your email before creating a household.",
            403,
        )
    # Creating an unpaid container is not subscription activation. Billing verifies
    # entitlement before enabling hosted uploads; invitations use the owner's plan.
    hid = uid()
    name = bounded_name(body.get("name"), "My household")
    if g.release_db.dialect.name == "postgresql":
        from sqlalchemy import text

        g.release_db.execute(
            text("SELECT release_create_household(:hid,:name,:mid,:cid)"),
            {"hid": hid, "name": name, "mid": uid(), "cid": uid()},
        )
    else:
        g.release_db.execute(
            m.households.insert().values(
                id=hid, name=name, settings={}, created_at=now()
            )
        )
        set_scope(hid)
        g.release_db.execute(
            m.memberships.insert().values(
                id=uid(),
                household_id=hid,
                user_id=g.release_user["id"],
                role="owner",
                active=True,
            )
        )
        g.release_db.execute(
            m.collections.insert().values(
                id=uid(), household_id=hid, name="Photos", settings={}
            )
        )
    return jsonify(id=hid, name=name, role="owner", subscription_required=True), 201


@bp.get("/households/<hid>/collections")
@require_auth
def list_collections(hid):
    membership = household(hid)
    query = select(m.collections).where(m.collections.c.household_id == hid)
    if membership["role"] == "member":
        query = query.join(
            m.collection_grants,
            and_(
                m.collections.c.id == m.collection_grants.c.collection_id,
                m.collections.c.household_id == m.collection_grants.c.household_id,
            ),
        ).where(m.collection_grants.c.membership_id == membership["id"])
    values = []
    for row in g.release_db.execute(query).mappings().all():
        _, role = collection(hid, row["id"])
        values.append({"id": row["id"], "name": row["name"], "permission": role})
    return jsonify(collections=values)


@bp.post("/households/<hid>/collections")
@require_auth
def create_collection(hid):
    household(hid, manage=True)
    body = json_body()
    cid = uid()
    name = bounded_name(body.get("name"))
    g.release_db.execute(
        m.collections.insert().values(id=cid, household_id=hid, name=name, settings={})
    )
    audit(hid, "collection.create", cid)
    return jsonify(id=cid, name=name), 201


@bp.get("/households/<hid>/collections/<cid>")
@require_auth
def get_collection(hid, cid):
    row, role = collection(hid, cid)
    return jsonify(id=cid, name=row["name"], permission=role)


@bp.patch("/households/<hid>/collections/<cid>")
@require_auth
def update_collection(hid, cid):
    household(hid, manage=True)
    collection(hid, cid)
    body = json_body()
    name = bounded_name(body.get("name"))
    g.release_db.execute(
        update(m.collections)
        .where(and_(m.collections.c.id == cid, m.collections.c.household_id == hid))
        .values(name=name)
    )
    return jsonify(id=cid, name=name)


@bp.route("/households/<hid>/collections/<cid>/settings", methods=["GET", "PATCH"])
@require_auth
def settings(hid, cid):
    row, _ = collection(hid, cid, "read" if request.method == "GET" else "edit")
    if request.method == "PATCH":
        values = validate_metadata(json_body(), SETTING_FIELDS)
        row["settings"] = dict(row["settings"], **values)
        g.release_db.execute(
            update(m.collections)
            .where(and_(m.collections.c.id == cid, m.collections.c.household_id == hid))
            .values(settings=row["settings"])
        )
    return jsonify(settings=row["settings"])


@bp.get("/households/<hid>/collections/<cid>/assets")
@require_auth
def list_assets(hid, cid):
    collection(hid, cid)
    rows = (
        g.release_db.execute(
            select(m.assets)
            .where(
                and_(
                    m.assets.c.household_id == hid,
                    m.assets.c.collection_id == cid,
                    m.assets.c.deleted.is_(False),
                )
            )
            .order_by(m.assets.c.created_at, m.assets.c.id)
        )
        .mappings()
        .all()
    )
    groups = (
        g.release_db.execute(
            select(m.groups).where(
                and_(m.groups.c.household_id == hid, m.groups.c.collection_id == cid)
            )
        )
        .mappings()
        .all()
    )
    return jsonify(
        assets=[asset_json(row) for row in rows],
        groups=[group_json(row) for row in groups],
    )


def image_input():
    file = request.files.get("file") or request.files.get("photo")
    if not file:
        fail("file_required", "Choose a photo.")
    maximum = current_app.config["MAX_UPLOAD_BYTES"]
    data = file.read(maximum + 1)
    if len(data) > maximum:
        fail("too_large", "This photo exceeds the upload limit.", 413)
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > 80_000_000:
            fail("too_large", "This photo has too many pixels.", 413)
        image.load()
        if image.format not in ("JPEG", "PNG", "WEBP", "GIF"):
            fail("unsupported_image", "Use a JPEG, PNG, WebP or GIF image.", 415)
        mime = Image.MIME[image.format]
        image = ImageOps.exif_transpose(image)
        thumbnail = image.convert("RGB")
        thumbnail.thumbnail((400, 400))
        out = io.BytesIO()
        thumbnail.save(out, format="JPEG")
        return file, data, image, mime, out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        fail("invalid_image", "This file is not a supported photo.", 415)


def media_path(key):
    root = Path(current_app.config["MEDIA_ROOT"]).resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        fail("not_found", "Not found.", 404)
    return path


def quota(hid, size=0, assets_to_add=0, frames_to_add=0):
    if current_app.config["RELEASE_MODE"] != "hosted":
        return
    from release_billing import BillingService, BillingError

    plans = current_app.config.get("BILLING_PLANS") or []
    if not plans:
        fail(
            "billing_unavailable", "Hosted subscription plans are not configured.", 503
        )
    try:
        BillingService(plans, clock=lambda: now()).check_quota(
            g.release_db,
            hid,
            authorize=lambda: household(hid),
            bytes_to_add=size,
            assets_to_add=assets_to_add,
            frames_to_add=frames_to_add,
        )
    except BillingError as error:
        fail(error.code, str(error), error.status)


@bp.post("/households/<hid>/collections/<cid>/assets")
@require_auth
def upload_asset(hid, cid):
    collection(hid, cid, "upload")
    file, data, image, mime, thumbnail = image_input()
    quota(hid, len(data), assets_to_add=1)
    aid = uid()
    key = f"{hid}/{cid}/{aid}/original"
    thumb_key = f"{hid}/{cid}/{aid}/thumbnail.jpg"
    storage = current_app.extensions["release_storage"]
    storage.write(key, data)
    g.release_created_media.append(key)
    storage.write(thumb_key, thumbnail)
    g.release_created_media.append(thumb_key)
    row = {
        "id": aid,
        "household_id": hid,
        "collection_id": cid,
        "filename": Path(file.filename or "photo").name[:255],
        "storage_key": key,
        "thumbnail_key": thumb_key,
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "mime_type": mime,
        "uploaded_by": g.release_user["id"],
        "metadata": {
            "title": "",
            "width": image.width,
            "height": image.height,
            "enabled": True,
            "phash": str(imagehash.phash(image)),
        },
        "deleted": False,
        "created_at": now(),
    }
    g.release_db.execute(m.assets.insert().values(**row))
    audit(hid, "asset.upload", aid)
    return jsonify(asset=asset_json(row)), 201


@bp.post("/households/<hid>/collections/<cid>/duplicates")
@require_auth
def duplicates(hid, cid):
    collection(hid, cid, "upload")
    _, data, image, _, _ = image_input()
    sha = hashlib.sha256(data).hexdigest()
    perceptual = imagehash.phash(image)
    rows = (
        g.release_db.execute(
            select(m.assets.c.id, m.assets.c.sha256, m.assets.c.metadata).where(
                and_(
                    m.assets.c.household_id == hid,
                    m.assets.c.collection_id == cid,
                    m.assets.c.deleted.is_(False),
                )
            )
        )
        .mappings()
        .all()
    )
    duplicates = []
    similar = []
    for row in rows:
        if row["sha256"] == sha:
            duplicates.append(row["id"])
            continue
        saved = row["metadata"].get("phash")
        if saved:
            try:
                distance = int(perceptual - imagehash.hex_to_hash(saved))
            except (ValueError, TypeError):
                continue
            if distance < 10:
                similar.append({"id": row["id"], "distance": distance})
    return jsonify(
        duplicates=duplicates,
        similar=similar,
        low_resolution=image.width < 1920 or image.height < 1080,
        width=image.width,
        height=image.height,
        phash=str(perceptual),
    )


@bp.route(
    "/households/<hid>/collections/<cid>/assets/<aid>",
    methods=["GET", "PATCH", "DELETE"],
)
@require_auth
def asset(hid, cid, aid):
    row = asset_record(
        hid, cid, aid, "read" if request.method == "GET" else "contributor_edit"
    )
    if request.method == "PATCH":
        body = json_body()
        values = validate_metadata(body.get("metadata", body))
        row["metadata"] = dict(row["metadata"], **values)
        g.release_db.execute(
            update(m.assets)
            .where(and_(m.assets.c.household_id == hid, m.assets.c.id == aid))
            .values(metadata=row["metadata"])
        )
    elif request.method == "DELETE":
        # Soft removal is scoped; bytes remain private until explicit owner purge.
        g.release_db.execute(
            update(m.assets)
            .where(and_(m.assets.c.household_id == hid, m.assets.c.id == aid))
            .values(deleted=True)
        )
        g.release_db.execute(
            delete(m.group_assets).where(
                and_(
                    m.group_assets.c.household_id == hid,
                    m.group_assets.c.asset_id == aid,
                )
            )
        )
        audit(hid, "asset.remove", aid)
        return jsonify(ok=True)
    return jsonify(asset=asset_json(row))


@bp.post("/households/<hid>/collections/<cid>/assets/bulk")
@require_auth
def bulk_assets(hid, cid):
    collection(hid, cid, "edit")
    body = json_body()
    ids = body.get("asset_ids")
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) > 200
        or any(not isinstance(item, str) or len(item) > 64 for item in ids)
        or len(set(ids)) != len(ids)
    ):
        fail("invalid_batch", "Choose at most 200 distinct photos.")
    rows = [asset_record(hid, cid, aid, "edit") for aid in ids]
    values = validate_metadata(body.get("metadata", {}))
    for row in rows:
        g.release_db.execute(
            update(m.assets)
            .where(and_(m.assets.c.household_id == hid, m.assets.c.id == row["id"]))
            .values(metadata=dict(row["metadata"], **values))
        )
    return jsonify(ok=True, count=len(rows))


@bp.post("/households/<hid>/collections/<cid>/reorder")
@require_auth
def reorder(hid, cid):
    row, _ = collection(hid, cid, "edit")
    body = json_body()
    ids = body.get("asset_ids")
    if (
        not isinstance(ids, list)
        or len(ids) > 10000
        or any(not isinstance(item, str) or len(item) > 64 for item in ids)
        or len(set(ids)) != len(ids)
    ):
        fail("invalid_order", "Provide distinct asset IDs.")
    for aid in ids:
        asset_record(hid, cid, aid)
    settings = dict(row["settings"], display_order=ids)
    g.release_db.execute(
        update(m.collections)
        .where(and_(m.collections.c.household_id == hid, m.collections.c.id == cid))
        .values(settings=settings)
    )
    return jsonify(ok=True)


@bp.get("/households/<hid>/assets/<aid>/<variant>")
@require_auth
def media(hid, aid, variant):
    if variant not in ("media", "thumbnail"):
        fail("not_found", "Not found.", 404)
    household(hid)
    row = (
        g.release_db.execute(
            select(m.assets).where(
                and_(
                    m.assets.c.household_id == hid,
                    m.assets.c.id == aid,
                    m.assets.c.deleted.is_(False),
                )
            )
        )
        .mappings()
        .first()
    )
    if not row:
        fail("not_found", "Not found.", 404)
    collection(hid, row["collection_id"])
    key = row["storage_key"] if variant == "media" else row["thumbnail_key"]
    if not key:
        fail("not_found", "Not found.", 404)
    try:
        data = current_app.extensions["release_storage"].read(key)
    except (FileNotFoundError, KeyError):
        fail("not_found", "Not found.", 404)
    response = send_file(
        io.BytesIO(data),
        mimetype=row["mime_type"] if variant == "media" else "image/jpeg",
        download_name=row["filename"],
        conditional=False,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


def group_json(row):
    members = (
        g.release_db.execute(
            select(m.group_assets.c.asset_id)
            .where(
                and_(
                    m.group_assets.c.group_id == row["id"],
                    m.group_assets.c.household_id == row["household_id"],
                )
            )
            .order_by(m.group_assets.c.position)
        )
        .scalars()
        .all()
    )
    return {
        "id": row["id"],
        "collection_id": row["collection_id"],
        "name": row["name"],
        "metadata": row["metadata"],
        "asset_ids": members,
    }


def validate_group_members(hid, cid, values):
    if (
        not isinstance(values, list)
        or len(values) < 2
        or len(values) > 20
        or any(not isinstance(item, str) or len(item) > 64 for item in values)
        or len(set(values)) != len(values)
    ):
        fail("invalid_group", "Choose between 2 and 20 distinct photos.")
    for aid in values:
        asset_record(hid, cid, aid, "edit")
    return values


@bp.route("/households/<hid>/collections/<cid>/groups", methods=["GET", "POST"])
@require_auth
def group_list(hid, cid):
    collection(hid, cid, "read" if request.method == "GET" else "edit")
    if request.method == "GET":
        rows = (
            g.release_db.execute(
                select(m.groups).where(
                    and_(
                        m.groups.c.household_id == hid, m.groups.c.collection_id == cid
                    )
                )
            )
            .mappings()
            .all()
        )
        return jsonify(groups=[group_json(row) for row in rows])
    body = json_body()
    members = validate_group_members(hid, cid, body.get("asset_ids"))
    metadata = validate_metadata(body.get("metadata", {}), GROUP_FIELDS)
    gid = uid()
    row = {
        "id": gid,
        "household_id": hid,
        "collection_id": cid,
        "name": str(body.get("name", ""))[:200],
        "metadata": metadata,
    }
    g.release_db.execute(m.groups.insert().values(**row))
    for index, aid in enumerate(members):
        g.release_db.execute(
            m.group_assets.insert().values(
                household_id=hid,
                collection_id=cid,
                group_id=gid,
                asset_id=aid,
                position=index,
            )
        )
    return jsonify(group=group_json(row)), 201


@bp.route(
    "/households/<hid>/collections/<cid>/groups/<gid>", methods=["PATCH", "DELETE"]
)
@require_auth
def group(hid, cid, gid):
    collection(hid, cid, "edit")
    row = (
        g.release_db.execute(
            select(m.groups).where(
                and_(
                    m.groups.c.household_id == hid,
                    m.groups.c.collection_id == cid,
                    m.groups.c.id == gid,
                )
            )
        )
        .mappings()
        .first()
    )
    if not row:
        fail("not_found", "Not found.", 404)
    if request.method == "DELETE":
        g.release_db.execute(
            delete(m.group_assets).where(
                and_(
                    m.group_assets.c.household_id == hid,
                    m.group_assets.c.group_id == gid,
                )
            )
        )
        g.release_db.execute(
            delete(m.groups).where(
                and_(m.groups.c.household_id == hid, m.groups.c.id == gid)
            )
        )
        return jsonify(ok=True)
    body = json_body()
    metadata = validate_metadata(body.get("metadata", {}), GROUP_FIELDS)
    if "asset_ids" in body:
        members = validate_group_members(hid, cid, body["asset_ids"])
        g.release_db.execute(
            delete(m.group_assets).where(
                and_(
                    m.group_assets.c.household_id == hid,
                    m.group_assets.c.group_id == gid,
                )
            )
        )
        for index, aid in enumerate(members):
            g.release_db.execute(
                m.group_assets.insert().values(
                    household_id=hid,
                    collection_id=cid,
                    group_id=gid,
                    asset_id=aid,
                    position=index,
                )
            )
    row = dict(row)
    row["metadata"] = dict(row["metadata"], **metadata)
    if "name" in body:
        row["name"] = bounded_name(body["name"])
    g.release_db.execute(
        update(m.groups)
        .where(and_(m.groups.c.household_id == hid, m.groups.c.id == gid))
        .values(metadata=row["metadata"], name=row["name"])
    )
    return jsonify(group=group_json(row))


def frame_json(row, role):
    assignments = (
        g.release_db.execute(
            select(m.frame_collections.c.collection_id).where(
                and_(
                    m.frame_collections.c.household_id == row["household_id"],
                    m.frame_collections.c.frame_id == row["id"],
                )
            )
        )
        .scalars()
        .all()
    )
    # Frame control never reveals asset filenames/titles or photo metadata.
    return {
        "id": row["id"],
        "name": row["name"],
        "permission": role,
        "collection_ids": assignments,
        "settings": row["settings"],
        "state": {
            key: value
            for key, value in row["state"].items()
            if key in ("index", "paused", "power")
        },
    }


@bp.route("/households/<hid>/frames", methods=["GET", "POST"])
@require_auth
def frame_list(hid):
    membership = household(hid, manage=request.method == "POST")
    if request.method == "POST":
        body = json_body()
        fid = uid()
        name = bounded_name(body.get("name"))
        quota(hid, frames_to_add=1)
        from .validation import validate_frame_settings

        g.release_db.execute(
            m.frames.insert().values(
                id=fid,
                household_id=hid,
                name=name,
                settings=validate_frame_settings(body.get("settings", {})),
                state={"index": 0, "paused": False},
                schedules=[],
            )
        )
        return jsonify(id=fid, name=name), 201
    query = select(m.frames).where(m.frames.c.household_id == hid)
    if membership["role"] == "member":
        query = query.join(
            m.frame_grants,
            and_(
                m.frames.c.id == m.frame_grants.c.frame_id,
                m.frames.c.household_id == m.frame_grants.c.household_id,
            ),
        ).where(m.frame_grants.c.membership_id == membership["id"])
    rows = g.release_db.execute(query).mappings().all()
    return jsonify(frames=[frame_json(row, frame(hid, row["id"])[1]) for row in rows])


@bp.route("/households/<hid>/frames/<fid>", methods=["GET", "PATCH", "DELETE"])
@require_auth
def frame_detail(hid, fid):
    row, role = frame(hid, fid)
    if request.method == "DELETE":
        household(hid, manage=True)
        for table in (m.frame_credentials, m.frame_grants, m.frame_collections):
            g.release_db.execute(
                delete(table).where(
                    and_(table.c.household_id == hid, table.c.frame_id == fid)
                )
            )
        g.release_db.execute(
            delete(m.frames).where(
                and_(m.frames.c.household_id == hid, m.frames.c.id == fid)
            )
        )
        audit(hid, "frame.delete", fid)
        return jsonify(ok=True)
    if request.method == "PATCH":
        household(hid, manage=True)
        body = json_body()
        if set(body) - {"name", "collection_ids", "settings", "schedules"}:
            fail("invalid_fields", "Unsupported frame fields.")
        values = {}
        if "name" in body:
            values["name"] = bounded_name(body["name"])
        if "settings" in body:
            from .validation import validate_frame_settings

            values["settings"] = validate_frame_settings(
                body["settings"], row["settings"]
            )
        if "schedules" in body:
            from .schedules import validate_schedules

            values["schedules"] = validate_schedules(body["schedules"])
        if "collection_ids" in body:
            ids = body["collection_ids"]
            if (
                not isinstance(ids, list)
                or len(ids) > 200
                or any(not isinstance(item, str) or len(item) > 64 for item in ids)
                or len(set(ids)) != len(ids)
            ):
                fail("invalid_assignments", "Choose distinct collections.")
            for cid in ids:
                collection(hid, cid)
            g.release_db.execute(
                delete(m.frame_collections).where(
                    and_(
                        m.frame_collections.c.household_id == hid,
                        m.frame_collections.c.frame_id == fid,
                    )
                )
            )
            for cid in ids:
                g.release_db.execute(
                    m.frame_collections.insert().values(
                        household_id=hid, frame_id=fid, collection_id=cid
                    )
                )
        if values:
            g.release_db.execute(
                update(m.frames)
                .where(and_(m.frames.c.household_id == hid, m.frames.c.id == fid))
                .values(**values)
            )
        row.update(values)
        audit(hid, "frame.update", fid)
    return jsonify(frame=frame_json(row, role))


@bp.get("/households/<hid>/frames/<fid>/state")
@require_auth
def frame_state(hid, fid):
    row, role = frame(hid, fid)
    return jsonify(state=frame_json(row, role)["state"])


@bp.post("/households/<hid>/frames/<fid>/control")
@require_auth
def frame_control(hid, fid):
    row, _ = frame(hid, fid, "control")
    body = json_body()
    action = body.get("action")
    state = dict(row["state"])
    if action == "pause":
        state["paused"] = True
    elif action == "play":
        state["paused"] = False
    elif action == "toggle_pause":
        state["paused"] = not state.get("paused", False)
    elif action == "next":
        state["index"] = state.get("index", 0) + 1
    elif action == "previous":
        state["index"] = max(0, state.get("index", 0) - 1)
    elif (
        action == "goto"
        and isinstance(body.get("index"), int)
        and not isinstance(body["index"], bool)
        and 0 <= body["index"] < 100000
    ):
        state["index"] = body["index"]
    else:
        fail("invalid_action", "Unsupported playback action.")
    g.release_db.execute(
        update(m.frames)
        .where(and_(m.frames.c.household_id == hid, m.frames.c.id == fid))
        .values(state=state)
    )
    return jsonify(
        state={
            key: value
            for key, value in state.items()
            if key in ("index", "paused", "power")
        }
    )


@bp.post("/households/<hid>/frames/<fid>/credentials")
@require_auth
def create_frame_credential(hid, fid):
    household(hid, manage=True)
    frame(hid, fid)
    token = secret()
    credential_id = uid()
    g.release_db.execute(
        update(m.frame_credentials)
        .where(
            and_(
                m.frame_credentials.c.household_id == hid,
                m.frame_credentials.c.frame_id == fid,
            )
        )
        .values(revoked=True)
    )
    g.release_db.execute(
        m.frame_credentials.insert().values(
            id=credential_id,
            household_id=hid,
            frame_id=fid,
            token_hash=digest(token),
            revoked=False,
        )
    )
    audit(hid, "frame.credential.rotate", fid)
    return jsonify(id=credential_id, frame_token=token), 201


@bp.delete("/households/<hid>/frames/<fid>/credentials")
@require_auth
def revoke_frame_credential(hid, fid):
    household(hid, manage=True)
    frame(hid, fid)
    g.release_db.execute(
        update(m.frame_credentials)
        .where(
            and_(
                m.frame_credentials.c.household_id == hid,
                m.frame_credentials.c.frame_id == fid,
            )
        )
        .values(revoked=True)
    )
    audit(hid, "frame.credential.revoke", fid)
    return jsonify(ok=True)


def display_principal():
    authorization = request.headers.get("Authorization", "")
    browser = False
    if authorization.startswith("Frame "):
        token = authorization[6:]
    elif not authorization:
        token = request.cookies.get("release_frame", "")
        browser = True
    else:
        token = ""
    if not token or len(token) > 200:
        fail("unauthorized", "A frame credential is required.", 401)
    hashed = digest(token)
    db = g.release_db
    if db.dialect.name == "postgresql":
        from sqlalchemy import text

        credential = (
            db.execute(
                text("SELECT * FROM release_auth_frame(:token_hash)"),
                {"token_hash": hashed},
            )
            .mappings()
            .first()
        )
    else:
        credential = (
            db.execute(
                select(m.frame_credentials).where(
                    and_(
                        m.frame_credentials.c.token_hash == hashed,
                        m.frame_credentials.c.revoked.is_(False),
                    )
                )
            )
            .mappings()
            .first()
        )
    if not credential:
        fail("unauthorized", "The frame credential is invalid.", 401)
    if browser and request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf = (
            request.headers.get("X-CSRFToken") or request.form.get("csrf_token") or ""
        )
        if not credential["csrf_hash"] or not hmac.compare_digest(
            credential["csrf_hash"], digest(csrf)
        ):
            fail("csrf_failed", "The display session could not be verified.", 403)
    g.release_user = None
    g.release_frame = dict(credential)
    set_scope(credential["household_id"], credential["frame_id"])
    return dict(credential)


@bp.get("/display/playlist")
def display_playlist():
    credential = display_principal()
    hid = credential["household_id"]
    fid = credential["frame_id"]
    assignments = select(m.frame_collections.c.collection_id).where(
        and_(
            m.frame_collections.c.household_id == hid,
            m.frame_collections.c.frame_id == fid,
        )
    )
    rows = (
        g.release_db.execute(
            select(m.assets).where(
                and_(
                    m.assets.c.household_id == hid,
                    m.assets.c.collection_id.in_(assignments),
                    m.assets.c.deleted.is_(False),
                )
            )
        )
        .mappings()
        .all()
    )
    output = []
    for row in rows:
        if not row["metadata"].get("enabled", True):
            continue
        item = asset_json(row)
        item["media_url"] = f"/api/v1/display/assets/{row['id']}/media"
        item["thumbnail_url"] = f"/api/v1/display/assets/{row['id']}/thumbnail"
        output.append(item)
    active_ids = {item["id"] for item in output}
    groups = []
    for row in g.release_db.execute(
        select(m.groups).where(
            and_(
                m.groups.c.household_id == hid,
                m.groups.c.collection_id.in_(assignments),
            )
        )
    ).mappings():
        item = group_json(row)
        item["asset_ids"] = [aid for aid in item["asset_ids"] if aid in active_ids]
        if len(item["asset_ids"]) >= 2:
            groups.append(item)
    collection_settings = {
        row["id"]: row["settings"]
        for row in g.release_db.execute(
            select(m.collections).where(
                and_(
                    m.collections.c.household_id == hid,
                    m.collections.c.id.in_(assignments),
                )
            )
        ).mappings()
    }
    config = (
        g.release_db.execute(
            select(m.frames).where(
                and_(m.frames.c.household_id == hid, m.frames.c.id == fid)
            )
        )
        .mappings()
        .first()
    )
    return jsonify(
        frame_id=fid,
        assets=output,
        groups=groups,
        collection_settings=collection_settings,
        settings=config["settings"],
        state={
            key: value
            for key, value in config["state"].items()
            if key in ("index", "paused", "power")
        },
    )


@bp.get("/display/assets/<aid>/<variant>")
def display_media(aid, variant):
    credential = display_principal()
    hid = credential["household_id"]
    fid = credential["frame_id"]
    if variant not in ("media", "thumbnail"):
        fail("not_found", "Not found.", 404)
    row = (
        g.release_db.execute(
            select(m.assets)
            .join(
                m.frame_collections,
                and_(
                    m.frame_collections.c.collection_id == m.assets.c.collection_id,
                    m.frame_collections.c.household_id == m.assets.c.household_id,
                ),
            )
            .where(
                and_(
                    m.assets.c.household_id == hid,
                    m.assets.c.id == aid,
                    m.assets.c.deleted.is_(False),
                    m.frame_collections.c.frame_id == fid,
                )
            )
        )
        .mappings()
        .first()
    )
    if not row or not row["metadata"].get("enabled", True):
        fail("not_found", "Not found.", 404)
    try:
        data = current_app.extensions["release_storage"].read(
            row["storage_key"] if variant == "media" else row["thumbnail_key"]
        )
    except (FileNotFoundError, KeyError):
        fail("not_found", "Not found.", 404)
    return send_file(
        io.BytesIO(data),
        mimetype=row["mime_type"] if variant == "media" else "image/jpeg",
        conditional=False,
    )


@bp.get("/households/<hid>/memberships")
@require_auth
def list_memberships(hid):
    household(hid, manage=True)
    rows = (
        g.release_db.execute(
            select(
                m.memberships.c.id,
                m.memberships.c.user_id,
                m.memberships.c.role,
                m.memberships.c.active,
                m.users.c.email,
            )
            .join(m.users, m.users.c.id == m.memberships.c.user_id)
            .where(m.memberships.c.household_id == hid)
        )
        .mappings()
        .all()
    )
    return jsonify(memberships=[dict(row) for row in rows])


@bp.patch("/households/<hid>/memberships/<mid>")
@require_auth
def modify_membership(hid, mid):
    acting = household(hid, manage=True)
    body = json_body()
    db = g.release_db
    target = (
        db.execute(
            select(m.memberships)
            .where(and_(m.memberships.c.household_id == hid, m.memberships.c.id == mid))
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if not target:
        fail("not_found", "Not found.", 404)
    role = body.get("role", target["role"])
    active = body.get("active", target["active"])
    if role not in ("owner", "admin", "member") or not isinstance(active, bool):
        fail("invalid_membership", "Invalid membership.")
    if acting["role"] != "owner" and (role != "member" or target["role"] != "member"):
        fail("forbidden", "Only an owner can manage administrators or owners.", 403)
    if (
        target["role"] == "owner"
        and target["active"]
        and (role != "owner" or not active)
    ):
        owners = db.execute(
            select(m.memberships.c.id)
            .where(
                and_(
                    m.memberships.c.household_id == hid,
                    m.memberships.c.role == "owner",
                    m.memberships.c.active.is_(True),
                )
            )
            .with_for_update()
        ).all()
        if len(owners) <= 1:
            fail("last_owner", "At least one owner must remain.", 409)
    from .security import recent_auth

    recent_auth()
    db.execute(
        update(m.memberships)
        .where(m.memberships.c.id == mid)
        .values(role=role, active=active)
    )
    audit(hid, "membership.update", mid)
    return jsonify(ok=True)


@bp.put("/households/<hid>/memberships/<mid>/grants")
@require_auth
def replace_grants(hid, mid):
    household(hid, manage=True)
    row = (
        g.release_db.execute(
            select(m.memberships).where(
                and_(m.memberships.c.household_id == hid, m.memberships.c.id == mid)
            )
        )
        .mappings()
        .first()
    )
    if not row:
        fail("not_found", "Not found.", 404)
    from .invitations import validate_grants

    grants = validate_grants(hid, json_body())
    for kind, table, column in [
        ("collections", m.collection_grants, "collection_id"),
        ("frames", m.frame_grants, "frame_id"),
    ]:
        g.release_db.execute(
            delete(table).where(
                and_(table.c.household_id == hid, table.c.membership_id == mid)
            )
        )
        for value in grants[kind]:
            g.release_db.execute(
                table.insert().values(
                    id=uid(),
                    household_id=hid,
                    membership_id=mid,
                    **{column: value["id"]},
                    role=value["role"],
                )
            )
    audit(hid, "membership.grants.replace", mid)
    return jsonify(ok=True)


@bp.post("/households/<hid>/frames/<fid>/enrollments")
@require_auth
def frame_enrollment(hid, fid):
    from .security import recent_auth, rate_limit
    from .auth import challenge

    household(hid, manage=True)
    frame(hid, fid)
    recent_auth()
    rate_limit("frame_enrollment", g.release_user["id"], limit=5)
    token = challenge(
        g.release_user["id"],
        "frame_enrollment",
        {"household_id": hid, "frame_id": fid},
        ttl=120,
    )
    audit(hid, "frame.enrollment.create", fid)
    return jsonify(enrollment_token=token, expires_in=120), 201


@bp.post("/display/enroll")
def enroll_display():
    from .security import credential_install_origin

    credential_install_origin()
    from .security import rate_limit
    from .auth import get_challenge

    body = json_body()
    rate_limit("display_enroll", limit=10)
    token = body.get("token") or body.get("enrollment_token")
    pending = get_challenge(token, "frame_enrollment")
    token_value, csrf = secret(), secret()
    credential_id = uid()
    if g.release_db.dialect.name == "postgresql":
        from sqlalchemy import text

        result = (
            g.release_db.execute(
                text(
                    "SELECT * FROM release_enroll_frame(:enrollment_hash,:credential_id,:credential_hash,:csrf_hash)"
                ),
                {
                    "enrollment_hash": digest(token),
                    "credential_id": credential_id,
                    "credential_hash": digest(token_value),
                    "csrf_hash": digest(csrf),
                },
            )
            .mappings()
            .first()
        )
        if not result:
            fail("invalid_enrollment", "This frame setup code is unavailable.", 401)
        hid, fid = result["household_id"], result["frame_id"]
    else:
        issuer = (
            g.release_db.execute(
                select(m.users).where(m.users.c.id == pending["user_id"])
            )
            .mappings()
            .first()
        )
        if not issuer or issuer["disabled"]:
            fail("invalid_enrollment", "This frame setup code is unavailable.", 401)
        g.release_user = dict(issuer)
        hid, fid = pending["data"]["household_id"], pending["data"]["frame_id"]
        household(hid, manage=True)
        frame(hid, fid)
        g.release_db.execute(
            update(m.challenges)
            .where(m.challenges.c.id == pending["id"])
            .values(consumed=True)
        )
        g.release_db.execute(
            update(m.frame_credentials)
            .where(
                and_(
                    m.frame_credentials.c.household_id == hid,
                    m.frame_credentials.c.frame_id == fid,
                )
            )
            .values(revoked=True)
        )
        g.release_db.execute(
            m.frame_credentials.insert().values(
                id=credential_id,
                household_id=hid,
                frame_id=fid,
                token_hash=digest(token_value),
                csrf_hash=digest(csrf),
                revoked=False,
            )
        )
    g.release_user = None
    session["release_frame_csrf"] = csrf
    response = jsonify(ok=True, frame_id=fid, redirect_path="/display")
    secure = current_app.config["PUBLIC_ORIGIN"].startswith("https://")
    response.set_cookie(
        "release_frame",
        token_value,
        httponly=True,
        secure=secure,
        samesite="Strict",
        max_age=365 * 86400,
        path="/",
    )
    response.set_cookie(
        "release_frame_csrf",
        csrf,
        httponly=False,
        secure=secure,
        samesite="Strict",
        max_age=365 * 86400,
        path="/",
    )
    return response


@bp.post("/display/control")
def display_control():
    credential = display_principal()
    body = json_body()
    hid, fid = credential["household_id"], credential["frame_id"]
    row = (
        g.release_db.execute(
            select(m.frames).where(
                and_(m.frames.c.household_id == hid, m.frames.c.id == fid)
            )
        )
        .mappings()
        .first()
    )
    state = dict(row["state"])
    action = body.get("action")
    if action == "pause":
        state["paused"] = True
    elif action == "play":
        state["paused"] = False
    elif action == "toggle_pause":
        state["paused"] = not state.get("paused", False)
    elif action == "next":
        state["index"] = state.get("index", 0) + 1
    elif action == "previous":
        state["index"] = max(0, state.get("index", 0) - 1)
    elif (
        action == "goto"
        and isinstance(body.get("index"), int)
        and not isinstance(body["index"], bool)
        and 0 <= body["index"] < 100000
    ):
        state["index"] = body["index"]
    else:
        fail("invalid_action", "Unsupported playback action.")
    if g.release_db.dialect.name == "postgresql":
        from sqlalchemy import text

        # The display role can operate only its own opaque playback state. It
        # never receives general frame-management UPDATE permission.
        g.release_db.execute(
            text("SELECT release_control_frame(:index,:paused)"),
            {"index": state.get("index", 0), "paused": state.get("paused", False)},
        )
    else:
        g.release_db.execute(
            update(m.frames)
            .where(and_(m.frames.c.household_id == hid, m.frames.c.id == fid))
            .values(state=state)
        )
    return jsonify(
        state={
            key: value
            for key, value in state.items()
            if key in ("index", "paused", "power")
        }
    )
