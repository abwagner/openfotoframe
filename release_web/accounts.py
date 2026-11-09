"""Browser account and selected-household administration through the v1 policy layer."""

import secrets
from urllib.parse import urlencode
from flask import Blueprint, abort, current_app, redirect, request, session
from werkzeug.exceptions import HTTPException
from .compat import api, payload, identifier
from .pages import portal, check_csrf, finish_web_login

account_pages = Blueprint("release_accounts", __name__)


@account_pages.errorhandler(HTTPException)
def account_error(error):
    if error.response is not None:
        return error.response
    return portal("account_error.html", message=error.description), error.code


def principal():
    response = api("/api/v1/auth/me")
    if response.status_code == 401:
        abort(redirect("/login", 303))
    return payload(response)


def signed_out():
    session.clear()
    response = redirect('/login', 303)
    response.delete_cookie('release_session', path='/')
    response.delete_cookie('release_csrf', path='/')
    return response


def grants_from_form(collections, frames):
    # Resource IDs and role values are revalidated by the authoritative API.
    return {
        kind: [
            {"id": item["id"], "role": request.form.get(kind + "_" + item["id"])}
            for item in values
            if request.form.get(kind + "_" + item["id"])
        ]
        for kind, values in [("collections", collections), ("frames", frames)]
    }


@account_pages.route("/account", methods=["GET", "POST"])
def account():
    identity = principal()
    message = None
    if request.method == "POST":
        check_csrf()
        action = request.form.get("action")
        if action == "revoke":
            sid = identifier(request.form.get("session_id"))
            payload(api("/api/v1/auth/sessions/" + sid, "DELETE"))
            if sid == identity["session_id"]:
                return redirect("/login", 303)
            message = "Device sign-in revoked."
        elif action == "verify":
            payload(api("/api/v1/auth/verification/request", "POST", {}))
            message = "Check your email for the verification link."
        else:
            abort(400)
    devices = payload(api("/api/v1/auth/sessions"))["sessions"]
    return portal("account.html", identity=identity, devices=devices, message=message)


@account_pages.route("/change-password", methods=["GET", "POST"])
def password():
    identity = principal()
    if request.method == "POST":
        check_csrf()
        payload(
            api(
                "/api/v1/auth/password",
                "POST",
                {
                    key: request.form.get(key, "")
                    for key in ("current_password", "password", "code")
                },
            )
        )
        session.clear()
        return redirect("/login", 303)
    return portal("account_password.html", identity=identity)


@account_pages.route("/mfa", methods=["GET", "POST"])
def mfa():
    identity = principal()
    enrollment = None
    if request.method == "POST":
        check_csrf()
        if request.form.get("action") == "start":
            enrollment = payload(api("/api/v1/auth/mfa/enroll/start", "POST", {}))
            # Only the opaque setup token is retained; the plaintext secret is rendered once.
            session["release_account_mfa_setup"] = enrollment["setup_token"]
        elif request.form.get("action") == "complete":
            result = payload(
                api(
                    "/api/v1/auth/mfa/enroll/complete",
                    "POST",
                    {
                        "setup_token": session.get("release_account_mfa_setup"),
                        "code": request.form.get("code", ""),
                    },
                )
            )
            session.pop("release_account_mfa_setup", None)
            response = finish_web_login(result)
            response.headers.pop("Location", None)
            response.status_code = 200
            response.set_data(
                portal(
                    "recovery_codes.html",
                    codes=result["recovery_codes"],
                    destination="/account",
                )
            )
            return response
        else:
            abort(400)
    return portal("account_mfa.html", identity=identity, enrollment=enrollment)


@account_pages.route("/admin", methods=["GET", "POST"])
def administration():
    identity = principal()
    hid = (
        request.form.get("household_id")
        or request.args.get("household_id")
        or session.get("release_household_id")
    )
    households = [
        item for item in identity["households"] if item["role"] in ("owner", "admin")
    ]
    if not hid:
        return portal(
            "account_admin.html",
            identity=identity,
            households=households,
            selected=None,
        )
    hid = identifier(hid)
    membership = next((item for item in households if item["id"] == hid), None)
    if not membership:
        abort(404)
    prefix = "/api/v1/households/" + hid
    collections = payload(api(prefix + "/collections"))["collections"]
    frames = payload(api(prefix + "/frames"))["frames"]
    invitation = None
    message = None
    members = payload(api(prefix + "/memberships"))["memberships"]
    if request.method == "POST":
        check_csrf()
        action = request.form.get("action")
        if action == "invite":
            invitation = payload(
                api(
                    prefix + "/invitations",
                    "POST",
                    {
                        "recipient_email": request.form.get("recipient_email") or None,
                        "transferable": request.form.get("transferable") == "yes",
                        "grants": grants_from_form(collections, frames),
                    },
                )
            )
            fragment = urlencode(
                {
                    "v": "1",
                    "purpose": "invite",
                    "mode": current_app.config["RELEASE_MODE"],
                    "server": current_app.config["PUBLIC_ORIGIN"],
                    "token": invitation["token"],
                }
            )
            invitation["link"] = (
                current_app.config["PUBLIC_ORIGIN"] + "/connect#" + fragment
            )
        elif action == "revoke_invitation":
            payload(
                api(
                    prefix
                    + "/invitations/"
                    + identifier(request.form.get("invitation_id")),
                    "DELETE",
                )
            )
            message = "Invitation revoked."
        elif action in ("member", "grants"):
            mid = identifier(request.form.get("membership_id"))
            if action == "member":
                payload(
                    api(
                        prefix + "/memberships/" + mid,
                        "PATCH",
                        {
                            "role": request.form.get("role"),
                            "active": request.form.get("active") == "yes",
                        },
                    )
                )
                if any(
                    item["id"] == mid and item["user_id"] == identity["user"]["id"]
                    for item in members
                ):
                    return redirect("/account", 303)
            else:
                if request.form.get("confirm_replace") != "yes":
                    abort(
                        400,
                        description="Confirm replacing the complete set of resource grants.",
                    )
                payload(
                    api(
                        prefix + "/memberships/" + mid + "/grants",
                        "PUT",
                        grants_from_form(collections, frames),
                    )
                )
            message = "Membership access updated."
        else:
            abort(400)
    if request.method == "POST" and request.form.get("action") == "member":
        members = payload(api(prefix + "/memberships"))["memberships"]
    return portal(
        "account_admin.html",
        identity=identity,
        households=households,
        selected=membership,
        collections=collections,
        frames=frames,
        members=members,
        invitation=invitation,
        message=message,
    )


@account_pages.route("/register", methods=["GET", "POST"])
def registration():
    info = payload(api("/api/v1/server", authenticated=False))
    if request.method == "POST":
        check_csrf()
        body = {
            "email": request.form.get("email"),
            "password": request.form.get("password"),
            "device_name": "Web browser",
        }
        if session.get("release_pending_invite"):
            body["invitation_token"] = session["release_pending_invite"]
        result = payload(
            api("/api/v1/auth/register", "POST", body, authenticated=False)
        )
        if result.get("access_token"):
            return finish_web_login(result)
        if result.get("mfa_enrollment_required"):
            session["release_login_stage"] = "enroll_mfa"
            session["release_login_challenge"] = result["challenge_token"]
            session["release_mfa_methods"] = result.get("methods", [])
            return redirect("/login", 303)
        abort(409, description="Complete the required account setup through sign-in.")
    return portal(
        "account_register.html",
        info=info,
        invited=bool(session.get("release_pending_invite")),
    )


@account_pages.route("/subscribe", methods=["GET", "POST"])
def subscribe():
    identity = principal()
    if current_app.config["RELEASE_MODE"] != "hosted":
        abort(404)
    plans = [
        p
        for p in payload(api("/api/v1/plans", authenticated=False))["plans"]
        if "stripe" in p.get("purchase_channels", [])
    ]
    hid = request.form.get("household_id") or request.args.get("household_id")
    owners = [h for h in identity["households"] if h["role"] == "owner"]
    selected = next((h for h in owners if h["id"] == hid), None)
    checkout_url = None
    if request.method == "POST":
        check_csrf()
        if request.form.get("action") == "create":
            created = payload(
                api("/api/v1/households", "POST", {"name": request.form.get("name")})
            )
            return redirect("/subscribe?household_id=" + created["id"], 303)
        if not selected:
            abort(404)
        plan = next((p for p in plans if p["id"] == request.form.get("plan_id")), None)
        if not plan:
            abort(400)
        # Reuse the server-selected key across retries; a new plan gets a new key.
        checkout = session.get("release_checkout", {})
        if (
            checkout.get("household_id") != selected["id"]
            or checkout.get("plan_id") != plan["id"]
        ):
            checkout = {
                "household_id": selected["id"],
                "plan_id": plan["id"],
                "key": secrets.token_urlsafe(24),
            }
            session["release_checkout"] = checkout
        result = payload(
            api(
                "/api/v1/households/" + selected["id"] + "/billing/checkout",
                "POST",
                {"plan_id": plan["id"]},
                extra_headers={"Idempotency-Key": checkout["key"]},
            )
        )
        checkout_url = result[
            "checkout_url"
        ]  # API enforces configured HTTPS checkout hosts.
    entitlement = (
        payload(api("/api/v1/households/" + selected["id"] + "/billing/entitlement"))
        if selected
        else None
    )
    return portal(
        "account_subscribe.html",
        identity=identity,
        households=owners,
        selected=selected,
        plans=plans,
        checkout_url=checkout_url,
        entitlement=entitlement,
    )
