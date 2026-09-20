"""Final-PNG ink observations at known vector paths, not a visibility certificate.

Only the SHA-bound PNG already rendered by the isolated reference process is
decoded here. No PDF/font parsing, coordinate fitting, or field reassignment.
Dark pixels and local contrast cannot prove the identity of an overpainting
object. Keep that distinction, and the unqualified thresholds, in every result.
"""
from __future__ import annotations

import io
import math
from bisect import bisect_right
from typing import Any

from PIL import Image

from app.services.reference_vector_measurement import measure_fields, raster_point
from app.services.renderer_pdf_strokes import numbers

POLICY = "wsl-vector-raster-ink-observation-v1"
MAX_SAMPLES = 2_000_000
MAX_STROKE_PX = 16.0
INK_LEVEL = 128  # Existing raster diagnostic's ink threshold, not calibration.
MIN_CONTRAST = 64  # Diagnostic only; never an ACTIVE/crop acceptance condition.
TRANSITION_GUARD_PX = 0.5


def dash_intervals(line: dict[str, Any]) -> tuple[list[float], list[bool]]:
    """Odd-length arrays alternate across repetitions; zero elements take no space."""
    dash = numbers(line["dash"], len(line["dash"]))
    if not dash:
        return [], []
    if len(dash) > 256 or min(dash) < 0 or sum(dash) <= 0:
        raise ValueError("Invalid raster observation dash pattern")
    if len(dash) % 2:
        dash *= 2
    ends, painted = [], []
    total = 0.0
    for index, length in enumerate(dash):
        total += length
        if length:
            ends.append(total)
            painted.append(index % 2 == 0)
    return ends, painted


def dash_sample(ends: list[float], painted: list[bool], distance: float,
                guard: float) -> bool | None:
    if not ends:
        return True
    phase = distance % ends[-1]
    index = bisect_right(ends, phase)
    begin = ends[index - 1] if index else 0.0
    if min(phase - begin, ends[index] - phase) <= guard:
        return None  # Transition antialiasing cannot be assigned to either side.
    return painted[index]


def observe_raster(page: dict[str, Any], vector: dict[str, Any], content: bytes,
                   *, dpi: int = 150) -> dict[str, Any]:
    result = measure_fields(page, vector, content, dpi=dpi)
    if len(content) > 64 * 1024 * 1024:
        raise ValueError("Raster observation image budget exceeded")
    with Image.open(io.BytesIO(content)) as opened:
        if (opened.format != "PNG" or opened.size != (page["width"], page["height"])
                or opened.mode not in {"L", "RGB"}):
            raise ValueError("Raster observation requires the exact opaque reference PNG")
        gray = opened.convert("L")
    try:
        pixels = gray.load()
        width, height = gray.size
        used = 0
        cache: dict[int, dict[str, Any]] = {}

        def sample_line(index: int) -> dict[str, Any]:
            nonlocal used
            line = vector["strokes"]["segments"][index]
            base = {"segmentIndex": index, "status": "UNVERIFIABLE"}
            if (line["width"] <= 0 or line["clippingUnverified"]
                    or line["sharesPathWithCurve"] or line["subpathDistance"] is None):
                return {**base, "reason": "UNVERIFIED_PATH"}
            if line["dash"] and line["lineCap"] != 0:
                return {**base, "reason": "UNSUPPORTED_DASH_CAP"}
            start, end = (raster_point(line[key], vector, dpi) for key in ("from", "to"))
            if abs(start[0] - end[0]) <= 1e-6 and abs(start[1] - end[1]) > 1e-6:
                axis, center, along, limit = "x", (start[0] + end[0]) / 2, 1, height
                normal_limit = width
            elif abs(start[1] - end[1]) <= 1e-6 and abs(start[0] - end[0]) > 1e-6:
                axis, center, along, limit = "y", (start[1] + end[1]) / 2, 0, width
                normal_limit = height
            else:
                return {**base, "reason": "NON_AXIAL_PATH"}
            a, b, c, d, _, _ = numbers(line["strokeMatrix"], 6)
            determinant = a * d - b * c
            if abs(determinant) < 1e-12:
                raise ValueError("Degenerate raster observation matrix")
            dx, dy = (line["to"][i] - line["from"][i] for i in (0, 1))
            source_length = math.hypot((d * dx - c * dy) / determinant,
                                       (a * dy - b * dx) / determinant)
            pixel_length = abs(end[along] - start[along])
            source_per_pixel = source_length / pixel_length
            # Normal projection, not length of the sheared normal vector.
            stroke_px = (line["width"] * abs(determinant) * source_length / math.hypot(dx, dy)
                         * vector["userUnit"] * dpi / 72)
            if not 0 < stroke_px <= MAX_STROKE_PX:
                return {**base, "reason": "UNSUPPORTED_STROKE_WIDTH"}
            radius = stroke_px / 2 + 0.75
            normals = list(range(math.ceil(center - radius - 0.5),
                                 math.floor(center + radius - 0.5) + 1))
            shoulders = [math.floor(center - stroke_px / 2 - 2),
                         math.floor(center + stroke_px / 2 + 2)]
            if (not normals or min(*normals, *shoulders) < 0
                    or max(*normals, *shoulders) >= normal_limit):
                return {**base, "reason": "REFERENCE_EDGE_CLIPPED"}
            low, high = sorted([start[along], end[along]])
            if low < 0 or high > limit:
                return {**base, "reason": "REFERENCE_EDGE_CLIPPED"}
            endpoint_guard = max(2.0, stroke_px / 2 + 1)
            first, last = math.ceil(low + endpoint_guard - 0.5), math.floor(
                high - endpoint_guard - 0.5)
            count = max(0, last - first + 1)
            used += count
            if used > MAX_SAMPLES:
                raise ValueError("Raster observation sample budget exceeded")
            ends, painted = dash_intervals(line)
            phase = line["dashPhase"] + line["subpathDistance"]
            stats = {"paintSamples": 0, "inkSamples": 0, "contrastSamples": 0,
                     "gapSamples": 0, "gapInkSamples": 0, "transitionSamples": 0}
            for longitudinal in range(first, last + 1):
                source_distance = abs(longitudinal + 0.5 - start[along]) * source_per_pixel
                paint = dash_sample(ends, painted, phase + source_distance,
                                    TRANSITION_GUARD_PX * source_per_pixel)
                if paint is None:
                    stats["transitionSamples"] += 1
                    continue
                values = [int(pixels[n, longitudinal] if axis == "x" else pixels[longitudinal, n])
                          for n in normals]
                shoulder = min(int(pixels[n, longitudinal] if axis == "x"
                                   else pixels[longitudinal, n]) for n in shoulders)
                darkest = min(values)
                if paint:
                    stats["paintSamples"] += 1
                    stats["inkSamples"] += darkest < INK_LEVEL
                    stats["contrastSamples"] += (darkest < INK_LEVEL
                                                 and shoulder - darkest >= MIN_CONTRAST)
                else:
                    stats["gapSamples"] += 1
                    stats["gapInkSamples"] += darkest < INK_LEVEL
            checked = stats["paintSamples"]
            return {**base, **stats, "strokeWidthPx": stroke_px,
                    "status": "SAMPLED" if checked else "UNVERIFIABLE",
                    "reason": None if checked else "NO_INTERIOR_PAINT_SAMPLES",
                    "inkCoverage": stats["inkSamples"] / checked if checked else None,
                    "contrastCoverage": stats["contrastSamples"] / checked if checked else None}

        for field in result["fields"]:
            for edge in field["edges"]:
                indices = edge.get("segmentIndices", [])
                for index in indices:
                    if index not in cache:
                        cache[index] = sample_line(index)
                # Explicit indices refer to this report's same validated vector page.
                # Never substitute another stroke to rescue a failed observation.
                edge["rasterSegmentIndices"] = list(indices)
        result["rasterObservation"] = {
            "policy": POLICY, "referenceImageSha256": result["referenceImageSha256"],
            "sampleCount": used, "segments": [cache[index] for index in sorted(cache)],
            "inkThreshold": INK_LEVEL, "contrastThreshold": MIN_CONTRAST,
            "transitionGuardPx": TRANSITION_GUARD_PX,
            "thresholdQualified": False, "visibilityVerified": False,
            "objectIdentityVerified": False, "productionQualified": False,
        }
        return result
    finally:
        gray.close()
