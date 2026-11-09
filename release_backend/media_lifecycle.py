"""Explicit trash restoration and restart-safe permanent media deletion."""

from flask import Blueprint, g, jsonify, request
from sqlalchemy import and_, delete, select, update
from . import models as m
from .content import asset_json, quota
from .security import (
    APIError,
    audit,
    collection,
    fail,
    household,
    json_body,
    now,
    rate_limit,
    recent_auth,
    require_auth,
    uid,
)

bp = Blueprint("release_media_lifecycle", __name__, url_prefix="/api/v1")


def selected_asset(hid, cid, aid):
    household(hid, manage=True)
    collection(hid, cid)
    row = (
        g.release_db.execute(
            select(m.assets)
            .where(
                and_(
                    m.assets.c.household_id == hid,
                    m.assets.c.collection_id == cid,
                    m.assets.c.id == aid,
                )
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if not row or not row["deleted"]:
        fail("not_found", "No removed photo was found.", 404)
    return dict(row)


def purge_receipt(row):
    return {
        key: row[key]
        for key in (
            "id",
            "asset_id",
            "state",
            "error_code",
            "created_at",
            "completed_at",
        )
    }


@bp.get("/households/<hid>/collections/<cid>/trash")
@require_auth
def trash(hid, cid):
    household(hid, manage=True)
    collection(hid, cid)
    after = request.args.get("after", "")
    if after and (len(after) > 64 or not after.isalnum()):
        fail("invalid_cursor", "Use the next-page cursor returned by this server.")
    rows = (
        g.release_db.execute(
            select(m.assets)
            .where(
                and_(
                    m.assets.c.household_id == hid,
                    m.assets.c.collection_id == cid,
                    m.assets.c.deleted.is_(True),
                    m.assets.c.id > after,
                )
            )
            .order_by(m.assets.c.id)
            .limit(101)
        )
        .mappings()
        .all()
    )
    purges = {
        row["asset_id"]: purge_receipt(row)
        for row in g.release_db.execute(
            select(m.media_purges).where(
                and_(
                    m.media_purges.c.household_id == hid,
                    m.media_purges.c.collection_id == cid,
                    m.media_purges.c.asset_id.in_([row["id"] for row in rows[:100]]),
                )
            )
        ).mappings()
    }
    return jsonify(
        assets=[
            dict(asset_json(row), purge=purges.get(row["id"])) for row in rows[:100]
        ],
        next_cursor=rows[99]["id"] if len(rows) > 100 else None,
    )


@bp.post("/households/<hid>/collections/<cid>/assets/<aid>/restore")
@require_auth
def restore(hid, cid, aid):
    row = selected_asset(hid, cid, aid)
    existing = g.release_db.execute(
        select(m.media_purges.c.id).where(
            and_(m.media_purges.c.household_id == hid, m.media_purges.c.asset_id == aid)
        )
    ).first()
    if existing:
        fail(
            "purge_requested",
            "Permanent deletion has already been requested for this photo.",
            409,
        )
    # Original bytes already count toward the allowance. Only the active-photo
    # count changes; the same verified entitlement checks apply as content writes.
    quota(hid, assets_to_add=1)
    g.release_db.execute(
        update(m.assets)
        .where(and_(m.assets.c.household_id == hid, m.assets.c.id == aid))
        .values(deleted=False)
    )
    audit(hid, "asset.restore", aid)
    row["deleted"] = False
    return jsonify(asset=asset_json(row))


@bp.post("/households/<hid>/collections/<cid>/assets/<aid>/purge")
@require_auth
def request_purge(hid, cid, aid):
    row = selected_asset(hid, cid, aid)
    recent_auth()
    body = json_body()
    if (
        set(body) != {"confirm_permanent_delete"}
        or body["confirm_permanent_delete"] is not True
    ):
        fail("confirmation_required", "Confirm permanent deletion explicitly.")
    rate_limit("media_purge", g.release_user["id"], limit=120, seconds=3600)
    previous = (
        g.release_db.execute(
            select(m.media_purges)
            .where(
                and_(
                    m.media_purges.c.household_id == hid,
                    m.media_purges.c.asset_id == aid,
                )
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if previous:
        result = dict(previous)
        if result["state"] == "failed":
            values = dict(
                state="queued",
                error_code=None,
                completed_at=None,
                requested_by=g.release_user["id"],
                dispatch_attempts=0,
                dispatch_token=None,
                dispatch_until=0,
                dispatch_next_at=0,
            )
            g.release_db.execute(
                update(m.media_purges)
                .where(m.media_purges.c.id == result["id"])
                .values(**values)
            )
            result.update(values)
            audit(hid, "asset.purge_retry", aid)
        return jsonify(purge=purge_receipt(result)), 202
    result = dict(
        id=uid(),
        household_id=hid,
        collection_id=cid,
        asset_id=aid,
        requested_by=g.release_user["id"],
        storage_key=row["storage_key"],
        thumbnail_key=row["thumbnail_key"],
        state="queued",
        error_code=None,
        created_at=now(),
        completed_at=None,
    )
    g.release_db.execute(m.media_purges.insert().values(**result))
    audit(hid, "asset.purge_request", aid)
    return jsonify(purge=purge_receipt(result)), 202


def process_media_purge(app, job_id, household_id, user_id, dispatch_token=None):
    """Delete only captured private keys for a still-authorized removed asset.

    The transaction stays locked while idempotent object deletions run. If the
    process dies after deleting one object, the persisted request is retried.
    Provider failures propagate to the dispatcher's bounded retry policy; quota
    is released only after both deletions and the metadata transaction succeed.
    """
    store = app.extensions["release_store"]
    storage = app.extensions["release_storage"]
    with (
        app.app_context(),
        store.transaction(user_id=user_id, household_id=household_id) as db,
    ):
        g.release_db = db
        g.release_user = None
        initial = (
            db.execute(
                select(m.media_purges).where(
                    and_(
                        m.media_purges.c.id == job_id,
                        m.media_purges.c.household_id == household_id,
                        m.media_purges.c.requested_by == user_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if not initial:
            return False
        user = (
            db.execute(select(m.users).where(m.users.c.id == user_id))
            .mappings()
            .first()
        )
        try:
            if not user or user["disabled"]:
                return False
            g.release_user = dict(user)
            # Membership-before-asset-before-job matches HTTP mutations and
            # prevents a worker/restore deadlock for the same administrator.
            household(household_id, manage=True)
        except APIError:
            # PostgreSQL may hide the request after revocation. Only the
            # bounded dispatcher can settle that queue receipt, never bypass RLS.
            return False
        # Asset-before-job lock order matches the request/restore routes.
        asset = (
            db.execute(
                select(m.assets)
                .where(
                    and_(
                        m.assets.c.id == initial["asset_id"],
                        m.assets.c.household_id == household_id,
                    )
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        job = (
            db.execute(
                select(m.media_purges)
                .where(
                    and_(
                        m.media_purges.c.id == job_id,
                        m.media_purges.c.household_id == household_id,
                        m.media_purges.c.requested_by == user_id,
                    )
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if not job or job["state"] != "queued":
            return False
        if dispatch_token is not None:
            if (
                job["dispatch_token"] != dispatch_token
                or job["dispatch_until"] <= now()
            ):
                return False
        elif job["dispatch_token"] and job["dispatch_until"] > now():
            return False
        # Native uploads and offline legacy imports use these two generated
        # layouts. A corrupted receipt/asset must not redirect deletion to an
        # unrelated household, collection or asset, even if both rows agree.
        base = f"{household_id}/{job['collection_id']}/{job['asset_id']}"
        imported = f"{household_id}/{job['asset_id']}"
        valid_originals = {base + "/original", imported + "/original"}
        valid_thumbnails = {None, base + "/thumbnail.jpg", imported + "/thumbnail"}
        try:
            if (
                job["storage_key"] not in valid_originals
                or job["thumbnail_key"] not in valid_thumbnails
                or not asset
                or not asset["deleted"]
                or asset["collection_id"] != job["collection_id"]
                or asset["storage_key"] != job["storage_key"]
                or asset["thumbnail_key"] != job["thumbnail_key"]
            ):
                fail(
                    "purge_conflict",
                    "The removed photo no longer matches this deletion request.",
                    409,
                )
        except APIError as error:
            db.execute(
                update(m.media_purges)
                .where(m.media_purges.c.id == job_id)
                .values(state="failed", error_code=error.error_code, completed_at=now())
            )
            return True
        for key in (job["storage_key"], job["thumbnail_key"]):
            if key:
                storage.delete(key)
        db.execute(
            delete(m.assets).where(
                and_(
                    m.assets.c.household_id == household_id,
                    m.assets.c.id == job["asset_id"],
                )
            )
        )
        db.execute(
            update(m.media_purges)
            .where(m.media_purges.c.id == job_id)
            .values(state="complete", error_code=None, completed_at=now())
        )
        audit(household_id, "asset.purge_complete", job["asset_id"])
        return True
