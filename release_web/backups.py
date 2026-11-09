"""Selected-collection export/restore UI; every action reuses scoped API policy."""

from flask import (
    Blueprint,
    abort,
    redirect,
    render_template,
    request,
    current_app,
    session,
)
from .compat import api, payload, records, scope, identifier
from .pages import check_csrf

backup_pages = Blueprint("release_backups", __name__, template_folder="templates")


def selected():
    hid, cid = scope("household"), scope("collection")
    base = f"/api/v1/households/{hid}/collections/{cid}"
    # Reject stale/revoked scope before listing or mutating jobs.
    payload(api(base + "/settings"))
    return hid, cid, base


def jobs(base):
    return records(payload(api(base + "/exports")), "jobs")


def own_job(base, jid):
    jid = identifier(jid)
    if not any(item["id"] == jid for item in jobs(base)):
        abort(404)
    return jid


@backup_pages.route("/backup", methods=["GET", "POST"])
def backups():
    if request.method == "POST":
        check_csrf()
    hid, cid, base = selected()
    restored = (
        session.pop("release_restore_result", None) if request.method == "GET" else None
    )
    if restored and (
        restored.get("household") != hid or restored.get("collection") != cid
    ):
        restored = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "export":
            payload(api(base + "/exports", "POST", {}))
            return redirect("/backup", code=303)
        if action == "restore":
            if request.form.get("confirm_restore") != "yes":
                abort(
                    400, description="Confirm restoring into the selected collection."
                )
            uploaded = request.files.get("file")
            if not uploaded or not uploaded.filename:
                abort(
                    400,
                    description="Choose an OpenFotoFrame collection export archive.",
                )
            result = payload(api(base + "/restore", "POST", upload=uploaded))
            session["release_restore_result"] = {
                "household": hid,
                "collection": cid,
                "asset_count": result["asset_count"],
                "group_count": result["group_count"],
            }
            return redirect("/backup", code=303)
        else:
            abort(400, description="Choose an export or restore action.")
    return render_template(
        "release/backups.html",
        household_id=hid,
        collection_id=cid,
        jobs=jobs(base),
        restored=restored,
        deployment_mode=current_app.config["RELEASE_MODE"],
    )


@backup_pages.get("/backup/jobs/<jid>/download")
def download(jid):
    hid, cid, base = selected()
    jid = own_job(base, jid)
    return api(f"/api/v1/households/{hid}/jobs/{jid}/download")


@backup_pages.post("/backup/jobs/<jid>/delete")
def remove(jid):
    check_csrf()
    hid, cid, base = selected()
    jid = own_job(base, jid)
    payload(api(f"/api/v1/households/{hid}/jobs/{jid}", "DELETE", {}))
    return redirect("/backup", code=303)
