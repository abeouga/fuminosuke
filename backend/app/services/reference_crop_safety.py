"""Physical clearance budget; context padding is never assumed safe tolerance."""

from __future__ import annotations

import math
from typing import Any

POLICY = "clearance-bounded-physical-v1"
PHYSICAL_MM = 0.1
RASTER_PX = 1.0


def clearance(evidence: dict[str, float], error_px: float, dpi: int) -> dict[str, Any]:
    keys = [
        "widthMm",
        "heightMm",
        "contentInsetMm",
        "neighborGapMm",
        "borderStrokeMm",
        "inkStrokeMm",
        "paddingMm",
    ]
    if (
        dpi not in {96, 150, 300}
        or any(k not in evidence for k in keys)
        or any(not math.isfinite(evidence[k]) or evidence[k] < 0 for k in keys)
        or not math.isfinite(error_px)
        or error_px < 0
    ):
        return {"status": "UNVERIFIED", "code": "CROP_CLEARANCE_UNVERIFIED"}
    half_stroke = (evidence["borderStrokeMm"] + evidence["inkStrokeMm"]) / 2
    components = {
        "contentRetentionMm": evidence["contentInsetMm"] - half_stroke,
        "neighborExclusionMm": evidence["neighborGapMm"] - evidence["paddingMm"] - half_stroke,
        "fieldExtentMm": min(evidence["widthMm"], evidence["heightMm"]) / 2 - half_stroke,
    }
    available = min(components.values())
    # Keep the pre-existing 2 px geometry cap. Never widen it to make a case pass.
    tolerance = min(PHYSICAL_MM + RASTER_PX * 25.4 / dpi, 2 * 25.4 / dpi)
    error = error_px * 25.4 / dpi
    passed = available > tolerance and error <= tolerance
    return {
        "status": "PASS" if passed else "FAIL",
        "policy": POLICY,
        "components": components,
        "clearanceMm": available,
        "toleranceMm": tolerance,
        "measuredErrorMm": error,
        "dpi": dpi,
        "thresholdQualified": False,
        "code": None if passed else "CROP_CLEARANCE_INSUFFICIENT",
    }


def qualify_crop(
    page: dict[str, Any], measurement: dict[str, Any], dpi: int = 150
) -> dict[str, Any]:
    # Current templates do not contain independently measured ink envelopes.
    # Do not fabricate them from blank cells or from region.padding.
    fields = [
        {
            "cellId": f["cellId"],
            "sourceRange": f["sourceRange"],
            "status": "UNVERIFIED",
            "code": "CROP_CLEARANCE_UNVERIFIED",
        }
        for f in page["fields"]
    ]
    return {
        "qualified": False,
        "status": "UNVERIFIED",
        "policy": POLICY,
        "dpi": dpi,
        "geometryStatus": measurement["status"],
        "fields": fields,
        "codes": ["CROP_CLEARANCE_UNVERIFIED"],
    }
