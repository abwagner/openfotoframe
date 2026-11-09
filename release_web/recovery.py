"""First-party email links: explicit CSRF-protected completion, no GET redemption."""

from flask import Blueprint, request, render_template, current_app
from .pages import check_csrf
from .compat import api

recovery_pages = Blueprint(
    "release_recovery",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/recovery-static",
)


def page(kind):
    error = None
    completed = False
    requested = False
    status = 200
    token = ""
    if request.method == "POST":
        check_csrf()
        action = request.form.get("action", "complete")
        if action == "request":
            response = api(
                "/api/v1/auth/"
                + ("verification/request" if kind == "verify" else "recovery/request"),
                "POST",
                {} if kind == "verify" else {"email": request.form.get("email", "")},
                authenticated=kind == "verify",
            )
            requested = response.status_code < 400
        else:
            token = request.form.get("token", "")
            if not token or len(token) > 200:
                error, status = (
                    "Open the original email link on this device before continuing.",
                    400,
                )
                response = None
            else:
                body = {"token": token}
                if kind == "recover":
                    body.update(
                        password=request.form.get("password", ""),
                        code=request.form.get("code", ""),
                    )
                response = api(
                    "/api/v1/auth/"
                    + (
                        "verification/complete"
                        if kind == "verify"
                        else "recovery/complete"
                    ),
                    "POST",
                    body,
                    authenticated=kind == "verify",
                )
                completed = response.status_code < 400
        if response is not None and response.status_code >= 400:
            status = response.status_code
            data = response.get_json(silent=True) or {}
            value = data.get("error", {})
            error = (
                value.get("message", "This request could not be completed.")
                if isinstance(value, dict)
                else "This request could not be completed."
            )
    return render_template(
        "release/email_link.html",
        kind=kind,
        token="" if completed else token,
        error=error,
        completed=completed,
        requested=requested,
        deployment_mode=current_app.config["RELEASE_MODE"],
    ), status


@recovery_pages.route("/verify", methods=["GET", "POST"])
def verify():
    return page("verify")


@recovery_pages.route("/recover", methods=["GET", "POST"])
def recover():
    return page("recover")
