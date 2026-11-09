"""Presentation-only frame settings, separate from authority and provider config."""

import math
from artwork_catalog import validate_content
from .security import fail


def check_visual_metadata(values):
    """Validate values before a renderer, browser, or worker interprets them."""
    enums = {
        "mat_finish": ("flat", "eggshell", "linen", "suede", "silk"),
        "border_effect": ("bevel", "bevel-lit", "shadow", "none"),
        "fit_mode": ("contain", "cover"),
        "transition": ("fade", "none", "slide"),
    }
    for key, choices in enums.items():
        if key in values and values[key] is not None and values[key] not in choices:
            fail(
                "invalid_metadata", "Choose a supported " + key.replace("_", " ") + "."
            )
    for key in ("shuffle", "auto_pair_portraits", "auto_mat_color", "no_mat"):
        if key in values and type(values[key]) is not bool:
            fail("invalid_metadata", "Presentation switches must be booleans.")
    numeric = {
        "bevel_width": (0, 200),
        "bevel_lit_intensity": (0, 100),
        "bevel_lit_v": (0, 100),
        "bevel_lit_h": (0, 100),
        "transition_duration": (0, 60),
        "default_scale": (0.1, 3),
        "mat_width": (0, 500),
        "group_gap": (0, 500),
        "gap": (0, 500),
        "slideshow_interval": (1, 86400),
        "interval": (1, 86400),
        "slide_duration": (1, 86400),
    }
    for key, (low, high) in numeric.items():
        if key in values and values[key] is not None:
            value = values[key]
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                fail(
                    "invalid_metadata",
                    "Presentation values must be finite and within range.",
                )
    if "rotation" in values and (
        type(values["rotation"]) is not int
        or values["rotation"] not in (0, 90, 180, 270)
    ):
        fail("invalid_metadata", "Rotation must be 0, 90, 180, or 270 degrees.")
    if "scales" in values:
        scales = values["scales"]
        if (
            not isinstance(scales, dict)
            or len(scales) > 200
            or any(
                not isinstance(key, str)
                or len(key) > 64
                or type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0.1 <= value <= 3
                for key, value in scales.items()
            )
        ):
            fail("invalid_metadata", "Group scales must be bounded photo scales.")
    for key in ("image_order", "display_order"):
        if key in values and (
            not isinstance(values[key], list)
            or len(values[key]) > 10000
            or any(not isinstance(item, str) or len(item) > 64 for item in values[key])
        ):
            fail("invalid_metadata", "Provide a bounded list of photo IDs.")
    if "target_aspect_ratio" in values:
        ratio = values["target_aspect_ratio"]
        if not isinstance(ratio, str) or len(ratio) > 30:
            fail("invalid_metadata", "Choose a valid display aspect ratio.")
        parts = ratio.split(":")
        try:
            numbers = [float(item) for item in parts]
        except ValueError:
            numbers = []
        if len(numbers) != 2 or any(
            not math.isfinite(item) or not 0 < item <= 100 for item in numbers
        ):
            fail("invalid_metadata", "Choose a valid display aspect ratio.")
    return values


def validate_frame_settings(values, previous=None):
    from .content import SETTING_FIELDS, validate_metadata

    allowed = SETTING_FIELDS | {"width", "height", "active", "synchronized", "content"}
    if not isinstance(values, dict) or set(values) - allowed:
        fail("invalid_settings", "Unsupported frame settings.")
    result = dict(previous or {})
    result.update(
        validate_metadata(
            {k: v for k, v in values.items() if k in SETTING_FIELDS}, SETTING_FIELDS
        )
    )
    for key in ("width", "height"):
        if key in values:
            value = values[key]
            if type(value) is not int or not 100 <= value <= 10000:
                fail(
                    "invalid_dimensions",
                    "Frame dimensions must be between 100 and 10000 pixels.",
                )
            result[key] = value
    for key in ("active", "synchronized"):
        if key in values:
            if type(values[key]) is not bool:
                fail("invalid_settings", "Frame switches must be booleans.")
            result[key] = values[key]
    if "content" in values:
        try:
            result["content"] = validate_content(
                values["content"], result.get("content")
            )
        except ValueError as error:
            fail("invalid_content", str(error))
    for key in ("slideshow_interval", "interval", "slide_duration"):
        if key in result and (
            type(result[key]) not in (int, float)
            or not math.isfinite(result[key])
            or not 1 <= result[key] <= 86400
        ):
            fail(
                "invalid_settings",
                "Choose a slideshow interval between 1 and 86400 seconds.",
            )
    return result
