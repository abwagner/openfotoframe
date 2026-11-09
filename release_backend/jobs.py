"""Durable bounded collection exports and validated, remapped collection restores."""

import io
import json
import zipfile
import hashlib
from flask import Blueprint, current_app, g, jsonify, request, send_file
from sqlalchemy import select, and_, update, delete, func
from PIL import Image, ImageOps, UnidentifiedImageError
from . import models as m
from .security import (
    require_auth,
    fail,
    now,
    uid,
    household,
    collection,
    audit,
    rate_limit,
)
from .content import group_json, validate_metadata, ASSET_FIELDS, quota

bp = Blueprint("release_jobs", __name__, url_prefix="/api/v1")


@bp.get("/households/<hid>/collections/<cid>/exports")
@require_auth
def list_exports(hid, cid):
    collection(hid, cid)
    rows = (
        g.release_db.execute(
            select(m.jobs)
            .where(
                and_(
                    m.jobs.c.household_id == hid,
                    m.jobs.c.collection_id == cid,
                    m.jobs.c.requested_by == g.release_user["id"],
                    m.jobs.c.kind == "collection_export",
                )
            )
            .order_by(m.jobs.c.created_at.desc(), m.jobs.c.id)
            .limit(50)
        )
        .mappings()
        .all()
    )
    return jsonify(
        jobs=[
            {
                "id": row["id"],
                "state": row["state"],
                "error_code": row["error_code"],
                "created_at": row["created_at"],
                "expires_at": row["created_at"] + 86400,
                "download_path": f"/api/v1/households/{hid}/jobs/{row['id']}/download"
                if row["state"] == "complete" and row["created_at"] > now() - 86400
                else None,
            }
            for row in rows
        ]
    )


@bp.post("/households/<hid>/collections/<cid>/exports")
@require_auth
def create_export(hid, cid):
    collection(hid, cid)
    rate_limit("export", g.release_user["id"], limit=5, seconds=3600)
    active = g.release_db.execute(
        select(func.count())
        .select_from(m.jobs)
        .where(
            and_(
                m.jobs.c.household_id == hid,
                m.jobs.c.requested_by == g.release_user["id"],
                m.jobs.c.state.in_(("queued", "complete")),
            )
        )
    ).scalar()
    if active >= 5:
        fail("export_limit", "Remove an earlier export before creating another.", 409)
    g.release_commit_on_error = False
    jid = uid()
    g.release_db.execute(
        m.jobs.insert().values(
            id=jid,
            household_id=hid,
            requested_by=g.release_user["id"],
            collection_id=cid,
            kind="collection_export",
            state="queued",
            created_at=now(),
        )
    )
    audit(hid, "export.queue", jid)
    return jsonify(id=jid, state="queued"), 202


def job_access(hid, jid, lock=False):
    household(hid)
    query = select(m.jobs).where(
                and_(
                    m.jobs.c.household_id == hid,
                    m.jobs.c.id == jid,
                    m.jobs.c.requested_by == g.release_user["id"],
                )
            )
    if lock:
        query = query.with_for_update()
    row = (
        g.release_db.execute(query)
        .mappings()
        .first()
    )
    if not row:
        fail("not_found", "Not found.", 404)
    collection(hid, row["collection_id"])
    return dict(row)


@bp.get("/households/<hid>/jobs/<jid>")
@require_auth
def get_job(hid, jid):
    row = job_access(hid, jid)
    return jsonify(
        id=jid,
        state=row["state"],
        error_code=row["error_code"],
        download_path=f"/api/v1/households/{hid}/jobs/{jid}/download"
        if row["state"] == "complete"
        else None,
    )


@bp.get("/households/<hid>/jobs/<jid>/download")
@require_auth
def download_job(hid, jid):
    row = job_access(hid, jid, lock=True)
    if row["created_at"] <= now() - 86400:
        fail("export_expired", "This export has expired.", 410)
    if row["state"] != "complete":
        fail("job_pending", "The export is not available.", 409)
    try:
        data = current_app.extensions["release_storage"].read(row["result_key"])
    except FileNotFoundError:
        fail("not_found", "The export is no longer available.", 404)
    return send_file(
        io.BytesIO(data),
        mimetype="application/zip",
        as_attachment=True,
        download_name="OpenFotoFrame-export.zip",
        conditional=False,
    )


@bp.delete("/households/<hid>/jobs/<jid>")
@require_auth
def delete_job(hid, jid):
    row = job_access(hid, jid, lock=True)
    if row["result_key"]:
        current_app.extensions["release_storage"].delete(row["result_key"])
    g.release_db.execute(
        delete(m.jobs).where(
            and_(
                m.jobs.c.id == jid,
                m.jobs.c.household_id == hid,
                m.jobs.c.requested_by == g.release_user["id"],
            )
        )
    )
    return jsonify(ok=True)


def process_export(app, job_id, household_id, user_id, dispatch_token=None):
    """Run a persisted job using its exact scope, never a client-provided payload.

    An operator/worker obtains these IDs from the durable queue. API credentials
    cannot call this function over HTTP. The worker must use an application role
    with RLS, not a schema-owner or BYPASSRLS connection. Permission revocation is
    checked in the same transaction that assembles the result and marks completion.
    """
    store = app.extensions["release_store"]
    storage = app.extensions["release_storage"]
    with (
        app.app_context(),
        store.transaction(user_id=user_id, household_id=household_id) as db,
    ):
        g.release_db = db
        g.release_user = None
        user = (
            db.execute(select(m.users).where(m.users.c.id == user_id))
            .mappings()
            .first()
        )
        row = (
            db.execute(
                select(m.jobs)
                .where(
                    and_(
                        m.jobs.c.id == job_id,
                        m.jobs.c.household_id == household_id,
                        m.jobs.c.requested_by == user_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if not row or row["state"] != "queued":
            return False
        try:
            if not user or user["disabled"]:
                fail(
                    "permission_revoked", "The requesting account is unavailable.", 403
                )
            g.release_user = dict(user)
            # HTTP mutations acquire membership before job. Match that order so
            # worker completion and deletion cannot deadlock or orphan a newly
            # produced archive after a stale queued-row read.
            household(household_id)
            row = db.execute(select(m.jobs).where(and_(m.jobs.c.id == job_id,
                m.jobs.c.household_id == household_id, m.jobs.c.requested_by == user_id))
                .with_for_update()).mappings().first()
            if not row or row['state'] != 'queued':
                return False
            if dispatch_token is not None:
                if row['dispatch_token'] != dispatch_token or row['dispatch_until'] <= now():
                    return False
            elif row['dispatch_token'] and row['dispatch_until'] > now():
                return False
            if row["created_at"] <= now() - 86400:
                fail("export_expired", "This export has expired.", 410)
            selected, _ = collection(household_id, row["collection_id"])
            assets = (
                db.execute(
                    select(m.assets).where(
                        and_(
                            m.assets.c.household_id == household_id,
                            m.assets.c.collection_id == row["collection_id"],
                            m.assets.c.deleted.is_(False),
                        )
                    )
                )
                .mappings()
                .all()
            )
            maximum = min(
                app.config.get("MAX_EXPORT_BYTES", 200 * 1024 * 1024),
                app.config["MAX_UPLOAD_BYTES"],
            )
            if sum(a["byte_size"] for a in assets) > maximum:
                fail(
                    "export_too_large",
                    "This export exceeds the configured job limit.",
                    413,
                )
            groups = (
                db.execute(
                    select(m.groups).where(
                        and_(
                            m.groups.c.household_id == household_id,
                            m.groups.c.collection_id == row["collection_id"],
                        )
                    )
                )
                .mappings()
                .all()
            )
            manifest = {
                "format": "openfotoframe-collection",
                "version": 1,
                "collection": {
                    "name": selected["name"],
                    "settings": selected["settings"],
                },
                "assets": [
                    {
                        "id": a["id"],
                        "filename": a["filename"],
                        "metadata": a["metadata"],
                        "sha256": a["sha256"],
                    }
                    for a in assets
                ],
                "groups": [group_json(group) for group in groups],
            }
            output = io.BytesIO()
            with zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_STORED
            ) as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                for asset in assets:
                    archive.writestr(
                        "media/" + asset["id"], storage.read(asset["storage_key"])
                    )
            # Include ZIP headers and the manifest in the same byte budget used
            # by restore, rather than producing an archive this server rejects.
            if output.tell() > maximum:
                fail(
                    "export_too_large",
                    "This collection exceeds the restore archive limit.",
                    413,
                )
            # Recheck the grant immediately before making the result available.
            collection(household_id, row["collection_id"])
            key = f"{household_id}/jobs/{job_id}/export.zip"
            # An interrupted previous attempt can leave this deterministic
            # private artifact after its metadata transaction rolled back. The
            # revalidated queued-job lock makes replacing only this key safe.
            storage.delete(key)
            storage.write(key, output.getvalue())
            db.execute(
                update(m.jobs)
                .where(m.jobs.c.id == job_id)
                .values(state="complete", result_key=key, completed_at=now())
            )
            return True
        except Exception as error:
            # Persist only a generic code; never copy filenames, passwords, storage
            # provider errors, or invitation credentials into client-visible jobs.
            db.execute(
                update(m.jobs)
                .where(m.jobs.c.id == job_id)
                .values(
                    state="failed",
                    error_code=getattr(error, "error_code", "export_failed"),
                    completed_at=now(),
                )
            )
            return False


@bp.post("/households/<hid>/collections/<cid>/restore")
@require_auth
def restore_collection(hid, cid):
    household(hid, manage=True)
    collection(hid, cid, "edit")
    file = request.files.get("file")
    if not file:
        fail("file_required", "Choose an export archive.")
    raw = file.read(current_app.config["MAX_UPLOAD_BYTES"] + 1)
    if len(raw) > current_app.config["MAX_UPLOAD_BYTES"]:
        fail("too_large", "This archive exceeds the upload limit.", 413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if (
            len(infos) > 10002
            or len(set(names)) != len(names)
            or sum(item.file_size for item in infos)
            > current_app.config.get("MAX_EXPORT_BYTES", 200 * 1024 * 1024)
        ):
            fail("invalid_archive", "This archive exceeds restore limits.")
        if any(
            name.startswith("/")
            or "\\" in name
            or any(part in ("", "..", ".") for part in name.split("/"))
            for name in names
        ):
            fail("invalid_archive", "This archive contains an unsafe path.")
        if any((item.external_attr >> 16) & 0o170000 == 0o120000 for item in infos):
            fail("invalid_archive", "Symbolic links cannot be restored.")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            manifest.get("format") != "openfotoframe-collection"
            or manifest.get("version") != 1
        ):
            fail("invalid_archive", "Unsupported export format.")
        assets = manifest["assets"]
        groups = manifest.get("groups", [])
        if (
            not isinstance(assets, list)
            or not isinstance(groups, list)
            or len(assets) > 10000
        ):
            fail("invalid_archive", "Invalid export records.")
        oldids = [item["id"] for item in assets]
        if any(
            not isinstance(value, str) or len(value) > 64 for value in oldids
        ) or len(set(oldids)) != len(oldids):
            fail("invalid_archive", "Invalid photo identifiers.")
        if set(names) != {"manifest.json", *("media/" + value for value in oldids)}:
            fail("invalid_archive", "Unexpected archive entries.")
        prepared = []
        for asset in assets:
            data = archive.read("media/" + asset["id"])
            if (
                len(data) > current_app.config["MAX_UPLOAD_BYTES"]
                or hashlib.sha256(data).hexdigest() != asset["sha256"]
            ):
                fail("invalid_archive", "A photo failed integrity verification.")
            image = Image.open(io.BytesIO(data))
            if image.width * image.height > 80_000_000 or image.format not in (
                "JPEG",
                "PNG",
                "WEBP",
                "GIF",
            ):
                fail("invalid_archive", "Unsupported image.")
            image.load()
            mime = Image.MIME[image.format]
            image = ImageOps.exif_transpose(image)
            thumb = image.convert("RGB")
            thumb.thumbnail((400, 400))
            out = io.BytesIO()
            thumb.save(out, format="JPEG")
            metadata = validate_metadata(
                {k: v for k, v in asset["metadata"].items() if k in ASSET_FIELDS}
            )
            import imagehash

            metadata.update(
                width=image.width,
                height=image.height,
                phash=str(imagehash.phash(image)),
            )
            prepared.append((asset, data, mime, out.getvalue(), metadata))
        for group in groups:
            members = group["asset_ids"]
            if (
                not isinstance(members, list)
                or not 2 <= len(members) <= 20
                or any(aid not in oldids for aid in members)
                or len(set(members)) != len(members)
            ):
                fail("invalid_archive", "A group contains an invalid photo.")
        quota(hid, sum(len(item[1]) for item in prepared), assets_to_add=len(prepared))
    except (
        zipfile.BadZipFile,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ):
        fail("invalid_archive", "This archive could not be validated.")
    mapping = {old: uid() for old in oldids}
    storage = current_app.extensions["release_storage"]
    for old, data, mime, thumbnail, metadata in prepared:
        aid = mapping[old["id"]]
        key = f"{hid}/{cid}/{aid}/original"
        thumb_key = f"{hid}/{cid}/{aid}/thumbnail.jpg"
        storage.write(key, data)
        g.release_created_media.append(key)
        storage.write(thumb_key, thumbnail)
        g.release_created_media.append(thumb_key)
        g.release_db.execute(
            m.assets.insert().values(
                id=aid,
                household_id=hid,
                collection_id=cid,
                filename=_filename(old.get("filename", "photo")),
                storage_key=key,
                thumbnail_key=thumb_key,
                sha256=hashlib.sha256(data).hexdigest(),
                byte_size=len(data),
                mime_type=mime,
                uploaded_by=g.release_user["id"],
                metadata=metadata,
                deleted=False,
                created_at=now(),
            )
        )
    from .content import GROUP_FIELDS

    for old in groups:
        gid = uid()
        metadata = validate_metadata(
            {k: v for k, v in old.get("metadata", {}).items() if k in GROUP_FIELDS},
            GROUP_FIELDS,
        )
        g.release_db.execute(
            m.groups.insert().values(
                id=gid,
                household_id=hid,
                collection_id=cid,
                name=str(old.get("name", ""))[:200],
                metadata=metadata,
            )
        )
        for index, aid in enumerate(old["asset_ids"]):
            g.release_db.execute(
                m.group_assets.insert().values(
                    household_id=hid,
                    collection_id=cid,
                    group_id=gid,
                    asset_id=mapping[aid],
                    position=index,
                )
            )
    audit(hid, "collection.restore", cid)
    return jsonify(ok=True, asset_count=len(mapping), group_count=len(groups)), 201


def _filename(value):
    from pathlib import PurePosixPath

    return PurePosixPath(str(value)).name[:255] or "photo"
