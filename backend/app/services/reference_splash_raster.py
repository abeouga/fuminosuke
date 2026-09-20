"""Predict pixel columns/rows for simple Poppler Splash stroke adjustment.

No image search, tolerance enlargement, source geometry rewrite or certificate.
The caller must bind this model to validated Poppler renderer evidence. Complex
paths retain their existing observations rather than guessing their join hints.
"""
from __future__ import annotations

import hashlib
import io
import math
from collections import Counter
from typing import Any

from PIL import Image

from app.services.reference_stroke_differential import _projection
from app.services.reference_vector_raster import INK_LEVEL, MAX_SAMPLES, MIN_CONTRAST
from app.services.renderer_splash_calibration import assess as assess_calibration

POLICY = "poppler-splash-simple-axial-stroke-adjust-v1"


def adjusted_normal_pixels(line: dict[str, Any], vector: dict[str, Any]) -> list[int] | None:
    """Mirror SplashXPath's rounded opposing edges for an independent solid path.

    SplashOutputDev enables stroke adjustment; SplashXPath rounds both edges
    with floor(v + .5), and advances the upper edge by one if they coincide.
    This is restricted to the single-segment butt-cap case whose hints are
    unambiguous. It is a renderer model, not a match against nearby dark pixels.
    """
    count = sum(s["operation"] == line["operation"] for s in vector["strokes"]["segments"])
    return _normal_pixels(line, vector, count)


def _normal_pixels(line: dict[str, Any], vector: dict[str, Any],
                   operation_count: int) -> list[int] | None:
    if (line["dash"] or line["lineCap"] != 0 or line["width"] <= 0
            or line["clippingUnverified"] or line["sharesPathWithCurve"]
            or line["subpathDistance"] != 0
            or operation_count != 1):
        return None
    a, b, c, d, _, _ = line["strokeMatrix"]
    if not ((abs(b) < 1e-12 and abs(c) < 1e-12)
            or (abs(a) < 1e-12 and abs(d) < 1e-12)):
        return None  # Do not assume axial edge hints for a sheared stroke outline.
    try:
        projected = _projection(line, vector)
    except ValueError:
        return None
    center, width = projected["center"], projected["width"]
    if not 0 < width < 1:
        return None  # Only the reproduced subpixel-solid observation gap.
    low = math.floor(center - width / 2 + .5)
    high = math.floor(center + width / 2 + .5)
    if high == low:
        high += 1
    return list(range(low, high))


def observe_adjusted_ink(page: dict[str, Any], vector: dict[str, Any], content: bytes,
                         observation: dict[str, Any], calibration: dict[str, Any] | None,
                         ) -> dict[str, Any]:
    """Supplement raw diagnostics using a freshly rechecked, bound calibration.

    The owning evidence validator must verify the helper's runtime/code/input
    identity before calling. This function never grants visibility or admission.
    It does not change the existing samples or move geometry toward observed ink.
    """
    result: dict[str, Any] = {"policy": POLICY, "segments": [], "sampleCount": 0,
        "calibration": None, "visibilityVerified": False, "productionQualified": False,
        "objectIdentityVerified": False, "thresholdQualified": False,
        "inkThreshold": INK_LEVEL, "contrastThreshold": MIN_CONTRAST}
    if calibration is None:
        return result
    result["calibration"] = assess_calibration(calibration)
    if result["calibration"]["status"] != "OBSERVED":
        return result
    sha = hashlib.sha256(content).hexdigest()
    if (len(content) > 64 * 1024 * 1024 or sha != page["referenceImage"]["sha256"]
            or sha != observation["referenceImageSha256"]):
        raise ValueError("Adjusted raster reference identity changed")
    lines = vector["strokes"]["segments"]
    counts = Counter(line["operation"] for line in lines)
    indices = sorted({index for field in observation["fields"] for edge in field["edges"]
                      for index in edge.get("segmentIndices", [])})
    with Image.open(io.BytesIO(content)) as opened:
        if (opened.format != "PNG" or opened.size != (page["width"], page["height"])
                or opened.mode not in {"L", "RGB"}):
            raise ValueError("Adjusted raster requires the exact opaque reference PNG")
        gray = opened.convert("L")
    try:
        pixels = gray.load()
        for index in indices:
            line = lines[index]
            normals = _normal_pixels(line, vector, counts[line["operation"]])
            if normals is None:
                continue
            projected = _projection(line, vector)
            axis, center, width = (projected[k] for k in ("axis", "center", "width"))
            shoulders = [math.floor(center - width / 2 - 2),
                         math.floor(center + width / 2 + 2)]
            lo, hi = sorted([projected["start"], projected["end"]])
            guard = max(2.0, width / 2 + 1)
            base = {"segmentIndex": index, "normalPixels": normals, "endpointGuardPx": guard}
            if (min(*normals, *shoulders) < 0
                    or max(*normals, *shoulders) >= gray.size[1 - axis]
                    or lo < 0 or hi > gray.size[axis]):
                result["segments"].append({**base, "status": "UNVERIFIABLE",
                                            "reason": "REFERENCE_EDGE_CLIPPED"})
                continue
            first, last = math.ceil(lo + guard - .5), math.floor(hi - guard - .5)
            count = max(0, last - first + 1)
            result["sampleCount"] += count
            if result["sampleCount"] > MAX_SAMPLES:
                raise ValueError("Adjusted raster sample budget exceeded")
            ink = contrast = 0
            for along in range(first, last + 1):
                def value(normal: int, position: int = along, direction: int = axis) -> int:
                    return int(pixels[normal, position] if direction == 1
                               else pixels[position, normal])
                darkest = min(value(n) for n in normals)
                ink += darkest < INK_LEVEL
                contrast += (darkest < INK_LEVEL
                             and min(value(n) for n in shoulders) - darkest >= MIN_CONTRAST)
            result["segments"].append({**base, "status": "SAMPLED" if count else "UNVERIFIABLE",
                "reason": None if count else "NO_INTERIOR_PAINT_SAMPLES",
                "paintSamples": count, "inkSamples": ink, "contrastSamples": contrast,
                "inkCoverage": ink / count if count else None,
                "contrastCoverage": contrast / count if count else None})
        return result
    finally:
        gray.close()
