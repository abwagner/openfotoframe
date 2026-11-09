"""Existing schedule editor compatibility with explicit release frame authority."""

from flask import Blueprint, current_app, jsonify, request
from release_backend.security import require_auth, household
from .compat import api, payload, frame_path, scope

frame_settings = Blueprint("release_frame_settings", __name__)


@frame_settings.route("/api/tv-schedules", methods=["GET", "POST"])
def schedules():
    result = api(
        frame_path("/schedules"),
        "PUT" if request.method == "POST" else "GET",
        request.get_json() if request.method == "POST" else None,
    )
    data = payload(result)
    return jsonify(data)


@frame_settings.get("/api/network-info")
def network_info():
    require_auth()
    household(scope("household"), manage=True)
    # Verify the selected frame independently; a supplied header is only a selector.
    selected = payload(api(frame_path()))
    frame = selected.get("frame", selected)
    return jsonify(
        server_url=current_app.config["PUBLIC_ORIGIN"],
        frame_id=frame["id"],
        local_ip=None,
        tailscale_ip=None,
        discovery_enabled=False,
    )
