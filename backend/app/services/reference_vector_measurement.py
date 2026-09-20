"""Direct field-edge observations, not a renderer/visibility/crop certificate.

The vector page must come from the fixed vector bridge's validated result. This
module never parses PDF, fits coordinates, reassigns IDs, or changes a layout.
Geometric path coverage is explicitly not painted/raster coverage (notably for
dashes). All production and visibility qualification flags remain false.
"""
from __future__ import annotations

import hashlib
import math
from bisect import bisect_left, bisect_right
from copy import deepcopy
from typing import Any

from app.services.renderer_pdf_strokes import POLICY as STROKE_POLICY
from app.services.renderer_pdf_strokes import numbers

POLICY = "wsl-direct-field-edge-observation-v1"
THRESHOLD_PX = 2.0  # Same diagnostic cap; not a qualified physical/crop tolerance.
SEARCH_PX = 8.0
MIN_PATH_COVERAGE = 0.85
MAX_SPAN_CHECKS = 2_000_000


def raster_point(point: list[float], vector_page: dict[str, Any], dpi: int) -> tuple[float, float]:
    x, y = numbers(point, 2)
    left, bottom, right, top = numbers(vector_page["effectiveBox"], 4)
    unit = numbers([vector_page["userUnit"]], 1)[0]
    rotation = vector_page["rotation"]
    if (dpi not in {96, 150, 300} or not 0 < unit <= 75000 or right <= left or top <= bottom
            or type(rotation) is not int or rotation not in {0, 90, 180, 270}):
        raise ValueError("Invalid field observation page transform")
    u, v, width, height = x - left, y - bottom, right - left, top - bottom
    transformed = {0: (u, height - v), 90: (v, u),
                   180: (width - u, v), 270: (height - v, width - u)}[rotation]
    result = tuple(value * unit * dpi / 72 for value in transformed)
    numbers(list(result), 2)
    return result[0], result[1]


def measure_fields(page: dict[str, Any], vector_page: dict[str, Any], image: bytes,
                   *, dpi: int = 150) -> dict[str, Any]:
    if (hashlib.sha256(image).hexdigest() != page["referenceImage"]["sha256"]
            or not image or any(type(page.get(key)) is not int or page[key] <= 0
                                for key in ("width", "height", "page"))
            or page["width"] * page["height"] > 20_000_000):
        raise ValueError("Field observation reference image changed")
    # renderedPage is the PDF ordinal, not the saved page's logical identity.
    # The caller binds that ordinal to the ordered print-plan page explicitly.
    left, bottom, right, top = numbers(vector_page["effectiveBox"], 4)
    corners = [raster_point([x, y], vector_page, dpi)
               for x, y in ((left, bottom), (right, top))]
    size = [math.ceil(abs(corners[1][axis] - corners[0][axis])) for axis in (0, 1)]
    if size != [page["width"], page["height"]]:
        raise ValueError("Field observation page dimensions differ")
    strokes = vector_page["strokes"]
    lines = strokes["segments"]
    if (strokes.get("policy") != STROKE_POLICY or not isinstance(lines, list)
            or len(lines) > 100_000 or not isinstance(page.get("fields"), list)
            or len(page["fields"]) > 20_000):
        raise ValueError("Invalid field observation inventory")
    grouped: dict[str, dict[float, list[tuple[float, float, int]]]] = {"x": {}, "y": {}}
    excluded = 0
    for index, line in enumerate(lines):
        if (line["width"] <= 0 or line["clippingUnverified"]
                or line["sharesPathWithCurve"]):
            excluded += 1
            continue
        start, end = (raster_point(line[key], vector_page, dpi) for key in ("from", "to"))
        dx, dy = abs(end[0] - start[0]), abs(end[1] - start[1])
        if dx <= 1e-6 and dy > 1e-6:
            axis, position, span = "x", (start[0] + end[0]) / 2, sorted([start[1], end[1]])
        elif dy <= 1e-6 and dx > 1e-6:
            axis, position, span = "y", (start[1] + end[1]) / 2, sorted([start[0], end[0]])
        else:
            excluded += 1
            continue
        grouped[axis].setdefault(position, []).append((span[0], span[1], index))
    positions = {axis: sorted(values) for axis, values in grouped.items()}
    checks = 0

    def edge(axis: str, predicted: float, start: float, end: float,
             search: float) -> dict[str, Any]:
        nonlocal checks
        candidates = []
        keys = positions[axis]
        for position in keys[bisect_left(keys, predicted - search):
                             bisect_right(keys, predicted + search)]:
            spans = grouped[axis][position]
            checks += len(spans)
            if checks > MAX_SPAN_CHECKS:
                raise ValueError("Field observation span budget exceeded")
            selected = [(max(start, low), min(end, high), index)
                        for low, high, index in spans if high > start and low < end]
            coverage, last = 0.0, start
            for low, high, _ in sorted(selected):
                if high > max(low, last):
                    coverage += high - max(low, last)
                    last = high
            ratio = coverage / (end - start)
            if ratio >= MIN_PATH_COVERAGE:
                candidates.append({"positionPx": position, "errorPx": position - predicted,
                                   "pathCoverage": ratio,
                                   "segmentIndices": [item[2] for item in selected],
                                   "hasDashes": any(lines[item[2]]["dash"] for item in selected)})
        if len(candidates) != 1:
            return {"status": "UNOBSERVED" if not candidates else "AMBIGUOUS",
                    "errorPx": None, "candidateCount": len(candidates)}
        return {"status": "OBSERVED", **candidates[0]}

    fields = []
    ids, mappings = set(), set()
    for field in page["fields"]:
        if not isinstance(field, dict):
            raise ValueError("Invalid field observation field")
        bindings = field.get("bindings")
        if bindings == []:
            continue  # Explicitly unbound heading/decoration, not an OCR field.
        if not isinstance(bindings, list) or not bindings:
            raise ValueError("Field observation requires explicit bindings")
        identity = field["cellId"]
        if (not isinstance(identity, str) or not identity or identity in ids
                or not isinstance(field.get("sourceRange"), str) or not field["sourceRange"]):
            raise ValueError("Ambiguous field observation identity")
        ids.add(identity)
        for binding in bindings:
            if not isinstance(binding, dict):
                raise ValueError("Invalid field observation binding")
            mapping = binding.get("mappingId")
            if not isinstance(mapping, str) or not mapping or mapping in mappings:
                raise ValueError("Ambiguous field observation mapping")
            mappings.add(mapping)
        rectangle = field["rectangle"]
        x, y, width, height = numbers([rectangle[key] for key in ("x", "y", "width", "height")], 4)
        if (min(width, height) <= 0 or min(x, y) < 0
                or x + width > page["width"] or y + height > page["height"]):
            raise ValueError("Field observation rectangle is outside its saved page")
        search = min(SEARCH_PX, width / 4, height / 4)
        edges = [edge("x", x, y, y + height, search),
                 edge("x", x + width, y, y + height, search),
                 edge("y", y, x, x + width, search),
                 edge("y", y + height, x, x + width, search)]
        complete = all(item["status"] == "OBSERVED" for item in edges)
        exceeded = any(item["errorPx"] is not None and abs(item["errorPx"]) > THRESHOLD_PX
                       for item in edges)
        fields.append({"cellId": identity, "sourceRange": field["sourceRange"],
                       "bindings": deepcopy(bindings), "edges": edges,
                       "status": "EXCEEDS_DIAGNOSTIC_BUDGET" if exceeded else (
                           "OBSERVED_WITHIN_DIAGNOSTIC_BUDGET" if complete else "UNVERIFIABLE")})
    errors = [abs(edge["errorPx"]) for field in fields for edge in field["edges"]
              if edge["errorPx"] is not None]
    return {"policy": POLICY, "page": page["page"], "renderedPage": vector_page["renderedPage"],
            "referenceImageSha256": page["referenceImage"]["sha256"],
            "contentSha256": vector_page["contentSha256"], "dpi": dpi,
            "fieldCount": len(fields), "fields": fields,
            "edgeOrder": ["left", "right", "top", "bottom"],
            "maxObservedErrorPx": max(errors, default=None), "excludedSegments": excluded,
            "thresholdPx": THRESHOLD_PX, "thresholdQualified": False,
            "visibilityVerified": False, "productionQualified": False}
