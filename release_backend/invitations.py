"""Explicit-grant invitations and device pairing; secrets are hash-only at rest."""

import hmac
from flask import Blueprint, g, jsonify
from sqlalchemy import select, and_, update, delete, text
from . import models as m
from .security import (
    require_auth,
    fail,
    json_body,
    now,
    uid,
    secret,
    digest,
    rate_limit,
    email,
    household,
    recent_auth,
    audit,
    set_scope,
)
from .auth import issue_session, requires_mfa, allowed_mfa_methods

bp = Blueprint("release_invitations", __name__, url_prefix="/api/v1")


def lookup_invitation(token):
    if not isinstance(token, str) or len(token) > 200:
        fail("invalid_invitation", "This invitation is unavailable or expired.", 404)
    db = g.release_db
    # PG lookup SECURITY DEFINER function returns only the matching hashed secret;
    # selected scope is then installed before normal RLS-protected reads.
    if db.dialect.name == "postgresql":
        row = (
            db.execute(
                text("SELECT * FROM release_lookup_invitation(:token_hash)"),
                {"token_hash": digest(token)},
            )
            .mappings()
            .first()
        )
    else:
        row = (
            db.execute(
                select(m.invitations)
                .where(m.invitations.c.token_hash == digest(token))
                .with_for_update()
            )
            .mappings()
            .first()
        )
    if not row or row["revoked"] or row["expires_at"] <= now():
        fail("invalid_invitation", "This invitation is unavailable or expired.", 404)
    set_scope(row["household_id"])
    # Acceptance must recheck that the issuer still has current authority.
    issuer = (
        db.execute(
            select(m.memberships)
            .join(m.users, m.users.c.id == m.memberships.c.user_id)
            .where(
                and_(
                    m.users.c.disabled.is_(False),
                    m.memberships.c.household_id == row["household_id"],
                    m.memberships.c.user_id == row["issuer_id"],
                    m.memberships.c.active.is_(True),
                    m.memberships.c.role.in_(("owner", "admin")),
                )
            )
        )
        .mappings()
        .first()
    )
    # An unauthenticated invite recipient cannot read arbitrary memberships under
    # RLS. A narrowly scoped DB function performs this check in PostgreSQL.
    if db.dialect.name == "postgresql":
        valid = db.execute(
            text("SELECT release_invitation_issuer_active(:token_hash)"),
            {"token_hash": digest(token)},
        ).scalar()
    else:
        valid = issuer is not None
    if not valid:
        fail("invalid_invitation", "This invitation is unavailable or expired.", 404)
    return dict(row)


def validate_grants(hid, grants):
    if not isinstance(grants, dict) or set(grants) - {"collections", "frames"}:
        fail("invalid_grants", "Provide explicit collection and frame grants.")
    output = {"collections": [], "frames": []}
    for kind, table, roles in [
        ("collections", m.collections, ("viewer", "contributor", "editor")),
        ("frames", m.frames, ("viewer", "controller")),
    ]:
        values = grants.get(kind, [])
        if not isinstance(values, list) or len(values) > 200:
            fail("invalid_grants", "Too many grants.")
        seen = set()
        for item in values:
            if (
                not isinstance(item, dict)
                or set(item) != {"id", "role"}
                or item["role"] not in roles
                or not isinstance(item["id"], str)
                or item["id"] in seen
            ):
                fail("invalid_grants", "Invalid or duplicate resource grant.")
            if not g.release_db.execute(
                select(table.c.id).where(
                    and_(table.c.id == item["id"], table.c.household_id == hid)
                )
            ).first():
                fail("not_found", "Not found.", 404)
            output[kind].append({"id": item["id"], "role": item["role"]})
            seen.add(item["id"])
    return output


@bp.post("/households/<hid>/invitations")
@require_auth
def create_invitation(hid):
    household(hid, manage=True)
    recent_auth()
    body = json_body()
    grants = validate_grants(
        hid,
        body.get(
            "grants",
            {
                "collections": [
                    {"id": item["collection_id"], "role": item["role"]}
                    for item in body.get("collection_grants", [])
                ],
                "frames": [
                    {"id": item["frame_id"], "role": item["role"]}
                    for item in body.get("frame_grants", [])
                ],
            },
        ),
    )
    recipient = email(body["recipient_email"]) if body.get("recipient_email") else None
    transferable = body.get("transferable") is True
    if not recipient and not transferable:
        fail(
            "recipient_required",
            "Choose a recipient or explicitly create a transferable invitation.",
        )
    if recipient and transferable:
        fail(
            "invalid_invitation", "A recipient-bound invitation cannot be transferable."
        )
    token = secret()
    iid = uid()
    expires = now() + 86400
    g.release_db.execute(
        m.invitations.insert().values(
            id=iid,
            household_id=hid,
            token_hash=digest(token),
            issuer_id=g.release_user["id"],
            recipient_email=recipient,
            transferable=transferable,
            grants=grants,
            expires_at=expires,
            revoked=False,
            accepted_by=None,
        )
    )
    audit(hid, "invitation.create", iid)
    return jsonify(id=iid, token=token, expires_at=expires, grants=grants), 201


@bp.delete("/households/<hid>/invitations/<iid>")
@require_auth
def revoke_invitation(hid, iid):
    household(hid, manage=True)
    result = g.release_db.execute(
        update(m.invitations)
        .where(and_(m.invitations.c.id == iid, m.invitations.c.household_id == hid))
        .values(revoked=True)
    )
    if not result.rowcount:
        fail("not_found", "Not found.", 404)
    audit(hid, "invitation.revoke", iid)
    return jsonify(ok=True)


@bp.post("/invitations/preview")
def preview_invitation():
    body = json_body()
    rate_limit("invite_preview", limit=20)
    g.release_user = None
    row = lookup_invitation(body.get("token"))
    if row["accepted_by"]:
        fail("invalid_invitation", "This invitation has already been accepted.", 409)
    if g.release_db.dialect.name == "postgresql":
        name = g.release_db.execute(
            text("SELECT release_invitation_household_name(:token_hash)"),
            {"token_hash": digest(body.get("token"))},
        ).scalar()
    else:
        name = g.release_db.execute(
            select(m.households.c.name).where(m.households.c.id == row["household_id"])
        ).scalar()
    # Do not disclose the recipient's full address to a transferable link holder.
    return jsonify(
        household_name=name,
        expires_at=row["expires_at"],
        recipient_required=bool(row["recipient_email"]),
        grants_summary={
            "collections": len(row["grants"]["collections"]),
            "frames": len(row["grants"]["frames"]),
        },
    )


def accept_invitation(token):
    row = lookup_invitation(token)
    user = g.release_user
    if row["accepted_by"]:
        if row["accepted_by"] == user["id"]:
            return row["household_id"]
        fail("invalid_invitation", "This invitation has already been accepted.", 409)
    if row["recipient_email"]:
        if user["email"] != row["recipient_email"]:
            fail(
                "recipient_mismatch",
                "Sign in with the account this invitation was sent to.",
                403,
            )
        if not user["email_verified"]:
            fail(
                "verification_required",
                "Verify your email address before accepting this invitation.",
                403,
            )
    hid = row["household_id"]
    grants = (
        row["grants"]
        if g.release_db.dialect.name == "postgresql"
        else validate_grants(hid, row["grants"])
    )
    db = g.release_db
    membership = (
        db.execute(
            select(m.memberships)
            .where(
                and_(
                    m.memberships.c.household_id == hid,
                    m.memberships.c.user_id == user["id"],
                )
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    # PostgreSQL acceptance is a narrow atomic SECURITY DEFINER function because
    # an invitation must grant a membership the recipient does not yet possess.
    if db.dialect.name == "postgresql":
        db.execute(
            text("SELECT release_accept_invitation(:token_hash,:uid)"),
            {"token_hash": digest(token), "uid": user["id"]},
        )
        return hid
    if not membership:
        mid = uid()
        db.execute(
            m.memberships.insert().values(
                id=mid, household_id=hid, user_id=user["id"], role="member", active=True
            )
        )
    else:
        mid = membership["id"]
        if not membership["active"]:
            db.execute(
                delete(m.collection_grants).where(
                    m.collection_grants.c.membership_id == mid
                )
            )
            db.execute(
                delete(m.frame_grants).where(m.frame_grants.c.membership_id == mid)
            )
            db.execute(
                update(m.memberships)
                .where(m.memberships.c.id == mid)
                .values(active=True, role="member")
            )
    for kind, table, target in [
        ("collections", m.collection_grants, "collection_id"),
        ("frames", m.frame_grants, "frame_id"),
    ]:
        for grant in grants[kind]:
            previous = (
                db.execute(
                    select(table).where(
                        and_(
                            table.c.membership_id == mid, table.c[target] == grant["id"]
                        )
                    )
                )
                .mappings()
                .first()
            )
            # Never silently downgrade an existing grant when accepting another
            # invitation. Explicit administrator edits handle reductions.
            rank = {"viewer": 0, "contributor": 1, "controller": 1, "editor": 2}
            if previous:
                role = max((previous["role"], grant["role"]), key=rank.get)
                db.execute(
                    update(table).where(table.c.id == previous["id"]).values(role=role)
                )
            else:
                db.execute(
                    table.insert().values(
                        id=uid(),
                        household_id=hid,
                        membership_id=mid,
                        **{target: grant["id"]},
                        role=grant["role"],
                    )
                )
    db.execute(
        update(m.invitations)
        .where(m.invitations.c.id == row["id"])
        .values(accepted_by=user["id"])
    )
    audit(hid, "invitation.accept", row["id"])
    return hid


@bp.post("/invitations/accept")
@require_auth
def accept():
    body = json_body()
    rate_limit("invite_accept", g.release_user["id"], limit=20)
    g.release_commit_on_error = False
    return jsonify(household_id=accept_invitation(body.get("token")))


@bp.post("/pairings")
@require_auth
def create_pairing():
    recent_auth()
    rate_limit("pairing_create", g.release_user["id"], limit=5)
    pid, code = uid(), secret()
    g.release_db.execute(
        m.pairings.insert().values(
            id=pid,
            user_id=g.release_user["id"],
            source_session_id=g.release_session["id"],
            code_hash=digest(code),
            expires_at=now() + 120,
            consumed=False,
        )
    )
    return jsonify(id=pid, code=code, expires_in=120), 201


def active_pairing(pid):
    row = (
        g.release_db.execute(
            select(m.pairings).where(m.pairings.c.id == pid).with_for_update()
        )
        .mappings()
        .first()
    )
    if not row or row["consumed"] or row["expires_at"] <= now():
        fail("invalid_pairing", "This quick-login request has expired.", 404)
    parent = (
        g.release_db.execute(
            select(m.sessions).where(m.sessions.c.id == row["source_session_id"])
        )
        .mappings()
        .first()
    )
    user = (
        g.release_db.execute(select(m.users).where(m.users.c.id == row["user_id"]))
        .mappings()
        .first()
    )
    if (
        not parent
        or parent["revoked"]
        or parent["refresh_expires"] <= now()
        or not user
        or user["disabled"]
        or user["must_change_password"]
        or (
            requires_mfa(user)
            and (
                not parent["mfa_verified"]
                or parent["mfa_method"] not in allowed_mfa_methods(user) | {"recovery"}
            )
        )
    ):
        fail("invalid_pairing", "This quick-login request is no longer available.", 404)
    return dict(row), dict(user)


@bp.post("/pairings/<pid>/challenge")
def request_pairing(pid):
    body = json_body()
    rate_limit("pairing_challenge", pid, limit=5)
    row, _ = active_pairing(pid)
    if not isinstance(body.get("code"), str) or not hmac.compare_digest(
        row["code_hash"], digest(body["code"])
    ):
        fail("invalid_pairing", "This quick-login code is invalid.", 404)
    cid, challenge_secret, confirmation = (
        uid(),
        secret(),
        str(
            int.from_bytes(__import__("secrets").token_bytes(4), "big") % 1000000
        ).zfill(6),
    )
    g.release_db.execute(
        m.pairing_challenges.insert().values(
            id=cid,
            pairing_id=pid,
            secret_hash=digest(challenge_secret),
            confirmation_hash=digest(confirmation),
            confirmation_code=confirmation,
            device_name=str(body.get("device_name", "New device"))[:120],
            approved=False,
            denied=False,
            attempts=0,
        )
    )
    return jsonify(
        challenge_id=cid,
        challenge_secret=challenge_secret,
        confirmation_code=confirmation,
        expires_in=max(0, row["expires_at"] - now()),
    ), 201


@bp.get("/pairings/<pid>")
@require_auth
def inspect_pairing(pid):
    row, _ = active_pairing(pid)
    if row["source_session_id"] != g.release_session["id"]:
        fail("not_found", "Not found.", 404)
    challenges = (
        g.release_db.execute(
            select(
                m.pairing_challenges.c.id,
                m.pairing_challenges.c.confirmation_code,
                m.pairing_challenges.c.device_name,
                m.pairing_challenges.c.approved,
                m.pairing_challenges.c.denied,
            ).where(m.pairing_challenges.c.pairing_id == pid)
        )
        .mappings()
        .all()
    )
    return jsonify(
        id=pid,
        challenges=[dict(item, challenge_id=item["id"]) for item in challenges],
        expires_at=row["expires_at"],
    )


@bp.post("/pairings/<pid>/approve")
@require_auth
def approve_pairing(pid):
    recent_auth()
    body = json_body()
    rate_limit("pairing_approve", pid, limit=5)
    row, _ = active_pairing(pid)
    if row["source_session_id"] != g.release_session["id"]:
        fail("not_found", "Not found.", 404)
    c = (
        g.release_db.execute(
            select(m.pairing_challenges).where(
                and_(
                    m.pairing_challenges.c.id == body.get("challenge_id"),
                    m.pairing_challenges.c.pairing_id == pid,
                )
            )
        )
        .mappings()
        .first()
    )
    if (
        not c
        or c["denied"]
        or not hmac.compare_digest(
            c["confirmation_hash"], digest(str(body.get("confirmation_code", "")))
        )
    ):
        fail("confirmation_mismatch", "The confirmation codes do not match.", 403)
    g.release_db.execute(
        update(m.pairing_challenges)
        .where(m.pairing_challenges.c.id == c["id"])
        .values(approved=True)
    )
    # Approval of one challenge invalidates every other scanner.
    g.release_db.execute(
        update(m.pairing_challenges)
        .where(
            and_(
                m.pairing_challenges.c.pairing_id == pid,
                m.pairing_challenges.c.id != c["id"],
            )
        )
        .values(denied=True)
    )
    return jsonify(ok=True)


@bp.post("/pairings/<pid>/deny")
@require_auth
def deny_pairing(pid):
    row, _ = active_pairing(pid)
    if row["source_session_id"] != g.release_session["id"]:
        fail("not_found", "Not found.", 404)
    g.release_db.execute(
        update(m.pairings).where(m.pairings.c.id == pid).values(consumed=True)
    )
    return jsonify(ok=True)


@bp.post("/pairings/<pid>/exchange")
def exchange_pairing(pid):
    body = json_body()
    rate_limit("pairing_exchange", pid, limit=20)
    row, user = active_pairing(pid)
    c = (
        g.release_db.execute(
            select(m.pairing_challenges)
            .where(
                and_(
                    m.pairing_challenges.c.id == body.get("challenge_id"),
                    m.pairing_challenges.c.pairing_id == pid,
                )
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if (
        not c
        or c["denied"]
        or c["attempts"] >= 5
        or not hmac.compare_digest(
            c["secret_hash"], digest(str(body.get("challenge_secret", "")))
        )
    ):
        if c:
            g.release_db.execute(
                update(m.pairing_challenges)
                .where(m.pairing_challenges.c.id == c["id"])
                .values(attempts=c["attempts"] + 1)
            )
        fail("invalid_pairing", "This quick-login request is invalid.", 403)
    if not c["approved"]:
        fail("approval_pending", "Approve this device from the signed-in source.", 409)
    g.release_db.execute(
        update(m.pairings).where(m.pairings.c.id == pid).values(consumed=True)
    )
    parent = (
        g.release_db.execute(
            select(m.sessions).where(m.sessions.c.id == row["source_session_id"])
        )
        .mappings()
        .first()
    )
    return jsonify(
        issue_session(
            user,
            c["device_name"],
            mfa_verified=parent["mfa_verified"],
            mfa_method=parent["mfa_method"],
        )
    )
