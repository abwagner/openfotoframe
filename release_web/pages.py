"""First-party browser access and destination-confirming invitation landing."""

import hmac
import secrets
import json
from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)
from .compat import DEFAULT_SETTINGS, api, payload, records, identifier

pages = Blueprint(
    "release_pages",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/release-static",
)


def csrf():
    if session.get("release_csrf"):
        return session["release_csrf"]
    session.setdefault("release_login_csrf", secrets.token_urlsafe(32))
    return session["release_login_csrf"]


def check_csrf():
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
    if not supplied or not hmac.compare_digest(csrf(), supplied):
        abort(403, description="Refresh this page before continuing.")


@pages.app_context_processor
def template_helpers():
    return {
        "csrf_token": csrf,
        "app_scheme": current_app.config.get("APP_LINK_SCHEME", "openfotoframe"),
    }


def portal(template="portal.html", **context):
    return render_template(
        "release/" + template,
        deployment_mode=current_app.config["RELEASE_MODE"],
        **context,
    )


@pages.get("/")
def home():
    return redirect(
        "/workspace" if request.cookies.get("release_session") else "/login"
    )


def finish_web_login(data):
    bearer = {"Authorization": "Bearer " + data["access_token"]}
    bridge = payload(
        api(
            "/api/v1/auth/web-bridge",
            "POST",
            {"path": "/workspace"},
            extra_headers=bearer,
        )
    )
    pending = {
        key: session[key]
        for key in ("release_pending_invite", "release_pending_authorization")
        if key in session
    }
    redeemed = api(
        "/api/v1/auth/web-bridge/redeem",
        "POST",
        {"bridge_token": bridge["bridge_token"]},
        authenticated=False,
        copy_session=True,
    )
    session.update(pending)
    if "release_pending_authorization" in pending:
        redeemed.headers["Location"] = "/authorize/" + identifier(
            pending["release_pending_authorization"]
        )
    elif "release_pending_invite" in pending:
        redeemed.headers["Location"] = "/accept"
    return redeemed


@pages.route("/login", methods=["GET", "POST"])
def login():
    error = None
    status = 200
    stage = session.get("release_login_stage", "password")
    if request.method == "POST":
        check_csrf()
        if request.form.get("action") in ("passkey", "passkey_register") and stage in (
            "mfa",
            "enroll_mfa",
        ):
            try:
                credential = json.loads(request.form.get("credential", ""))
            except (ValueError, TypeError):
                abort(400, description="The passkey response was invalid.")
            enrolling = request.form.get("action") == "passkey_register"
            endpoint = (
                "/api/v1/auth/passkeys/"
                + ("register" if enrolling else "authenticate")
                + "/verify"
            )
            key = "registration_token" if enrolling else "passkey_challenge_token"
            response = api(
                endpoint,
                "POST",
                {
                    key: session.get("release_passkey_challenge"),
                    "credential": credential,
                },
                authenticated=False,
            )
        elif stage == "enroll_mfa":
            if request.form.get("action") == "enroll_start":
                result = payload(
                    api(
                        "/api/v1/auth/mfa/enroll/start",
                        "POST",
                        {"challenge_token": session.get("release_login_challenge")},
                        authenticated=False,
                    )
                )
                session["release_mfa_setup"] = result["setup_token"]
                return portal(
                    "login.html",
                    stage=stage,
                    error=None,
                    passkey="passkey" in session.get("release_mfa_methods", []),
                    enrollment=result,
                )
            response = api(
                "/api/v1/auth/mfa/enroll/complete",
                "POST",
                {
                    "setup_token": session.get("release_mfa_setup"),
                    "code": request.form.get("code", ""),
                },
                authenticated=False,
            )
        elif stage == "mfa":
            response = api(
                "/api/v1/auth/mfa",
                "POST",
                {
                    "challenge_token": session.get("release_login_challenge"),
                    "code": request.form.get("code", ""),
                },
                authenticated=False,
            )
        elif stage == "change_password":
            response = api(
                "/api/v1/auth/change-password",
                "POST",
                {
                    "challenge_token": session.get("release_login_challenge"),
                    "password": request.form.get("password", ""),
                },
                authenticated=False,
            )
        else:
            response = api(
                "/api/v1/auth/login",
                "POST",
                {
                    "email": request.form.get("email", ""),
                    "password": request.form.get("password", ""),
                    "device_name": "Web browser",
                },
                authenticated=False,
            )
        data = response.get_json(silent=True) or {}
        if response.status_code >= 400:
            value = data.get("error", {})
            error = (
                value.get("message", "Sign-in failed.")
                if isinstance(value, dict)
                else str(value)
            )
            status = response.status_code
        elif (
            data.get("mfa_required")
            or data.get("password_change_required")
            or data.get("mfa_enrollment_required")
        ):
            stage = (
                "mfa"
                if data.get("mfa_required")
                else "enroll_mfa"
                if data.get("mfa_enrollment_required")
                else "change_password"
            )
            session["release_login_stage"] = stage
            session["release_login_challenge"] = data["challenge_token"]
            session["release_mfa_methods"] = data.get("methods", [])
        else:
            redeemed = finish_web_login(data)
            if data.get("recovery_codes"):
                destination = redeemed.headers.pop("Location", "/workspace")
                redeemed.status_code = 200
                redeemed.set_data(
                    portal(
                        "recovery_codes.html",
                        codes=data["recovery_codes"],
                        destination=destination,
                    )
                )
            return redeemed
        if data.get("recovery_codes"):
            return portal(
                "recovery_codes.html",
                codes=data["recovery_codes"],
                destination="/login",
            )
    return portal(
        "login.html",
        stage=stage,
        error=error,
        passkey="passkey" in session.get("release_mfa_methods", []),
    ), status


@pages.post("/login/passkey/options")
def login_passkey_options():
    check_csrf()
    stage = session.get("release_login_stage")
    if stage not in ("mfa", "enroll_mfa"):
        abort(409)
    registering = stage == "enroll_mfa"
    result = payload(
        api(
            "/api/v1/auth/passkeys/"
            + ("register" if registering else "authenticate")
            + "/options",
            "POST",
            {"challenge_token": session.get("release_login_challenge")},
            authenticated=False,
        )
    )
    session["release_passkey_challenge"] = result[
        "registration_token" if registering else "passkey_challenge_token"
    ]
    return jsonify(result["options"])


@pages.route("/authorize/<request_id>", methods=["GET", "POST"])
def authorize(request_id):
    request_id = identifier(request_id)
    details = payload(
        api("/api/v1/auth/authorization-requests/" + request_id, authenticated=False)
    )
    if not request.cookies.get("release_session"):
        session["release_pending_authorization"] = request_id
        return portal("authorize.html", details=details, signed_in=False)
    identity = payload(api("/api/v1/auth/me"))
    if request.method == "POST":
        check_csrf()
        result = payload(
            api(
                "/api/v1/auth/authorization-requests/" + request_id + "/approve",
                "POST",
                {},
            )
        )
        session.pop("release_pending_authorization", None)
        callback = result["redirect_uri"]
        allowed = current_app.config.get(
            "AUTH_REDIRECT_URIS", ["openfotoframe://auth/callback"]
        )
        if "?" not in callback or callback.split("?", 1)[0] not in allowed:
            abort(500, description="The sign-in callback was invalid.")
        return redirect(callback, code=303)
    return portal("authorize.html", details=details, identity=identity, signed_in=True)


@pages.route("/logout", methods=["GET", "POST"])
def logout():
    if request.method == "GET":
        return portal("logout.html")
    check_csrf()
    response = api("/api/v1/auth/logout", "POST", {})
    if response.status_code >= 400:
        return response
    session.clear()
    response = redirect("/login", code=303)
    response.delete_cookie("release_session", path="/")
    response.delete_cookie("release_csrf", path="/")
    return response


@pages.route("/workspace", methods=["GET", "POST"])
def workspace():
    if not request.cookies.get("release_session") and not request.headers.get(
        "Authorization"
    ):
        return redirect("/login")
    identity = payload(api("/api/v1/auth/me"))
    households = identity.get("households", [])
    hid = request.values.get("household_id") or session.get("release_household_id")
    collections, frames = [], []
    if hid:
        hid = identifier(hid)
        collections = records(
            payload(api(f"/api/v1/households/{hid}/collections")), "collections"
        )
        frames = records(payload(api(f"/api/v1/households/{hid}/frames")), "frames")
    if request.method == "POST":
        check_csrf()
        cid = request.form.get("collection_id")
        if cid:
            cid = identifier(cid)
            if not any(item["id"] == cid for item in collections):
                abort(404)
            session["release_collection_id"] = cid
        else:
            session.pop("release_collection_id", None)
        session["release_household_id"] = hid
        fid = request.form.get("frame_id")
        if fid:
            fid = identifier(fid)
            if not any(item["id"] == fid for item in frames):
                abort(404)
            session["release_frame_id"] = fid
        else:
            session.pop("release_frame_id", None)
        if not cid and not fid:
            abort(400, description="Choose a collection or frame.")
        return redirect("/upload" if cid else "/controls", code=303)
    return portal(
        identity=identity,
        households=households,
        collections=collections,
        frames=frames,
        selected_household=hid,
    )


@pages.get("/upload")
def upload_page():
    from .compat import collection_path

    settings = payload(api(collection_path("/settings")))
    identity = payload(api("/api/v1/auth/me"))
    hid = session.get("release_household_id") or request.headers.get(
        "X-OpenFotoFrame-Household"
    )
    membership = next(
        (item for item in identity.get("households", []) if item["id"] == hid), {}
    )
    return render_template(
        "upload.html",
        settings=DEFAULT_SETTINGS | settings.get("settings", settings),
        is_admin=membership.get("role") in ("owner", "admin"),
        password_changed=True,
    )


@pages.get("/connect")
def connect():
    # No destination or invitation is read on the server: the fragment is handled
    # entirely on-device, and no remote server is fetched by this landing page.
    return portal("connect.html")


@pages.route("/accept", methods=["GET", "POST"])
def accept():
    if (
        request.method == "POST"
        and request.form.get("token")
        and not request.form.get("confirm")
    ):
        # Link previews cannot reach this POST or consume the invitation.
        token = request.form["token"]
        if len(token) > 200:
            abort(400)
        session["release_pending_invite"] = token
        return redirect("/accept", code=303)
    token = session.get("release_pending_invite")
    if not token:
        abort(400, description="Open or scan your invitation again.")
    preview = payload(
        api(
            "/api/v1/invitations/preview", "POST", {"token": token}, authenticated=False
        )
    )
    if not request.cookies.get("release_session"):
        return portal("accept.html", preview=preview, signed_in=False)
    if request.method == "POST":
        check_csrf()
        payload(api("/api/v1/invitations/accept", "POST", {"token": token}))
        session.pop("release_pending_invite", None)
        return redirect("/workspace", code=303)
    return portal("accept.html", preview=preview, signed_in=True)


@pages.get("/.well-known/apple-app-site-association")
def app_association():
    team = current_app.config.get("APP_LINK_TEAM_ID", "")
    bundle = current_app.config.get("APP_LINK_BUNDLE_ID", "")
    if not team or not bundle:
        abort(404)
    return jsonify(
        applinks={
            "apps": [],
            "details": [{"appID": team + "." + bundle, "paths": ["/connect"]}],
        }
    )


@pages.route("/controls", methods=["GET", "POST"])
def frame_controls():
    from .compat import frame_path

    detail = payload(api(frame_path()))
    frame = detail.get("frame", detail)
    if request.method == "POST":
        check_csrf()
        action = request.form.get("action")
        if action not in ("previous", "next", "pause", "play"):
            abort(400)
        payload(api(frame_path("/control"), "POST", {"action": action}))
        return redirect("/controls", 303)
    state = payload(api(frame_path("/state")))
    return portal("frame_controls.html", frame=frame, state=state.get("state", state))
