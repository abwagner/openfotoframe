"""Frame-scoped schedule configuration, independent of physical power execution."""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import update, and_
from . import models as m
from .security import require_auth, frame, household, json_body, fail, uid, audit

bp = Blueprint("release_schedules", __name__, url_prefix="/api/v1")


def validate_schedules(values):
    if not isinstance(values, list) or len(values) > 32:
        fail("invalid_schedules", "Provide at most 32 TV schedules.")
    result, seen = [], set()
    for value in values:
        if not isinstance(value, dict) or set(value) - {
            "id",
            "on_time",
            "off_time",
            "days",
            "enabled",
            "timezone",
        }:
            fail("invalid_schedules", "Unsupported TV schedule fields.")
        identifier = value.get("id", uid())
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier)
            or identifier in seen
        ):
            fail(
                "invalid_schedules", "Schedule IDs must be distinct valid identifiers."
            )
        seen.add(identifier)
        for field in ("on_time", "off_time"):
            if not isinstance(value.get(field), str) or not re.fullmatch(
                r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value[field]
            ):
                fail(
                    "invalid_schedules",
                    "Schedule times must use HH:MM in 24-hour time.",
                )
        days = value.get("days", [])
        if (
            not isinstance(days, list)
            or len(days) > 7
            or any(type(day) is not int or day not in range(7) for day in days)
            or len(set(days)) != len(days)
        ):
            fail(
                "invalid_schedules",
                "Choose distinct weekdays from 0 (Monday) to 6 (Sunday).",
            )
        enabled = value.get("enabled", True)
        if type(enabled) is not bool:
            fail("invalid_schedules", "Schedule enabled must be true or false.")
        zone = value.get("timezone", current_app.config.get("SCHEDULE_TIMEZONE", "UTC"))
        if not isinstance(zone, str) or len(zone) > 100:
            fail("invalid_schedules", "Choose an IANA timezone.")
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            fail(
                "invalid_schedules",
                "Choose a valid IANA timezone, such as America/New_York.",
            )
        result.append(
            {
                "id": identifier,
                "on_time": value["on_time"],
                "off_time": value["off_time"],
                "days": sorted(days),
                "enabled": enabled,
                "timezone": zone,
            }
        )
    return result


@bp.route("/households/<hid>/frames/<fid>/schedules", methods=["GET", "PUT"])
@require_auth
def schedules(hid, fid):
    row, _ = frame(hid, fid)
    if request.method == "PUT":
        household(hid, manage=True)
        body = json_body()
        if set(body) != {"schedules"}:
            fail("invalid_schedules", "Provide schedules for this frame.")
        values = validate_schedules(body["schedules"])
        g.release_db.execute(
            update(m.frames)
            .where(and_(m.frames.c.household_id == hid, m.frames.c.id == fid))
            .values(schedules=values)
        )
        audit(hid, "frame.schedules.update", fid)
    else:
        values = validate_schedules(row["schedules"])
    # Storing a wall-clock plan does not claim a CEC/Wake-on-LAN device acted on it.
    return jsonify(
        frame_id=fid,
        schedules=values,
        cec_available=False,
        execution_enabled=False,
        execution_status="power_agent_not_configured",
        timezone=current_app.config.get("SCHEDULE_TIMEZONE", "UTC"),
    )
