"""Independent raster border measurements; never fits or repairs coordinates."""

from __future__ import annotations

import io
from typing import Any

import cv2
import numpy as np
from PIL import Image

# Provisional raster uncertainty budget, NOT an empirically qualified tolerance.
# Two endpoint roundings (0.5px each) plus a 1px antialias edge footprint.
# Production remains closed until calibration across real fixtures is complete.
RASTER_EDGE_BUDGET_PX = 2.0
MIN_EDGE_COVERAGE = 0.85
MIN_ANCHORS = 5
EDGE_SEARCH_PX = 8


def measure_page(page: dict[str, Any], content: bytes) -> dict[str, Any]:
    with Image.open(io.BytesIO(content)) as image:
        actual_size = image.size
        gray = np.asarray(image.convert("L"))
    if actual_size != (page["width"], page["height"]):
        return {"page": page["page"], "status": "FAIL", "code": "PAGE_GEOMETRY_MISMATCH",
                "expectedSize": [page["width"], page["height"]], "actualSize": list(actual_size)}
    ink = (gray < 128).astype(np.uint8)
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, 9), np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((9, 1), np.uint8))

    def edge(axis: str, predicted: int, start: int, end: int) -> float | None:
        matrix = vertical if axis == "x" else horizontal.T
        # Restrict matching to less than half a cell span; never seek a remote
        # border and infer a scale correction from the match.
        start, end = max(0, start + 3), min(matrix.shape[0], end - 3)
        if end - start < 9:
            return None
        candidates = []
        for position in range(max(0, predicted - EDGE_SEARCH_PX),
                              min(matrix.shape[1], predicted + EDGE_SEARCH_PX + 1)):
            if float(matrix[start:end, position].mean()) >= MIN_EDGE_COVERAGE:
                candidates.append(position)
        groups: list[list[int]] = []
        for position in candidates:
            if not groups or position != groups[-1][-1] + 1:
                groups.append([])
            groups[-1].append(position)
        # Ambiguous parallel borders cannot provide a physical cell identity.
        return sum(groups[0]) / len(groups[0]) if len(groups) == 1 else None

    measured: dict[str, dict[str, Any]] = {}
    for cell in page["cells"]:
        if not all(cell["borders"].get(k) for k in ("left", "right", "top", "bottom")):
            continue
        r = cell["rectangle"]
        x, y, w, h = (r[k] for k in ("x", "y", "width", "height"))
        if min(w, h) <= EDGE_SEARCH_PX * 2:
            continue
        positions = [edge("x", x, y, y + h), edge("x", x + w, y, y + h),
                     edge("y", y, x, x + w), edge("y", y + h, x, x + w)]
        if any(p is None for p in positions):
            continue
        errors = [float(p) - expected for p, expected in
                  zip(positions, [x, x + w, y, y + h], strict=True) if p is not None]
        measured[cell["sourceRange"]] = {"sourceRange": cell["sourceRange"],
                                          "rectangle": r, "edgeErrors": errors}
    cells = list(measured.values())
    failures: list[str] = []
    # Five spatially distinct anchors, including the center of the entire ruled
    # area. A cluster near one corner cannot establish page-wide correctness.
    anchors: list[str] = []
    if len(cells) >= MIN_ANCHORS:
        centers = [(c["rectangle"]["x"] + c["rectangle"]["width"] / 2,
                    c["rectangle"]["y"] + c["rectangle"]["height"] / 2) for c in cells]
        all_rects = [c["rectangle"] for c in page["cells"]
                     if all(c["borders"].get(k) for k in ("left", "right", "top", "bottom"))]
        left = min(r["x"] + r["width"] / 2 for r in all_rects)
        right = max(r["x"] + r["width"] / 2 for r in all_rects)
        top = min(r["y"] + r["height"] / 2 for r in all_rects)
        bottom = max(r["y"] + r["height"] / 2 for r in all_rects)
        for tx, ty in [(left, top), (right, top), ((left + right) / 2, (top + bottom) / 2),
                       (left, bottom), (right, bottom)]:
            i = min(range(len(cells)), key=lambda i: (centers[i][0] - tx) ** 2
                    + (centers[i][1] - ty) ** 2)
            if (abs(centers[i][0] - tx) > max(1, (right - left) / 4)
                    or abs(centers[i][1] - ty) > max(1, (bottom - top) / 4)):
                failures.append("REFERENCE_ANCHORS_INSUFFICIENT")
            anchors.append(cells[i]["sourceRange"])
    if len(set(anchors)) < MIN_ANCHORS:
        failures.append("REFERENCE_ANCHORS_INSUFFICIENT")
    fields = []
    for field in page["fields"]:
        match = measured.get(field["sourceRange"])
        if match is None:
            failures.append("REFERENCE_ANCHORS_INSUFFICIENT")
            fields.append({"cellId": field["cellId"], "sourceRange": field["sourceRange"],
                           "status": "UNVERIFIABLE"})
            continue
        # Field rectangles are checked independently of decorative cell rectangles.
        r, f = match["rectangle"], field["rectangle"]
        actual = [r["x"], r["x"] + r["width"], r["y"], r["y"] + r["height"]]
        expected = [f["x"], f["x"] + f["width"], f["y"], f["y"] + f["height"]]
        errors = [a + e - p for a, e, p in
                  zip(actual, match["edgeErrors"], expected, strict=True)]
        fields.append({"cellId": field["cellId"], "sourceRange": field["sourceRange"],
                       "edgeErrors": errors, "status": "MEASURED"})
    errors = [abs(e) for c in [*cells, *fields] for e in c.get("edgeErrors", [])]
    maximum = max(errors, default=0)
    if maximum > RASTER_EDGE_BUDGET_PX:
        failures.append("MAX_ERROR_EXCEEDED")
    if not page["fields"]:
        failures.append("REFERENCE_ANCHORS_INSUFFICIENT")
    return {"page": page["page"], "range": page["range"],
            "status": "FAIL" if failures else "PASS", "codes": sorted(set(failures)),
            "anchorRanges": anchors, "measuredCellCount": len(cells),
            "anchors": [measured[r] for r in dict.fromkeys(anchors)],
            "fieldCount": len(fields), "fields": fields,
            "maxErrorPx": maximum, "meanErrorPx": sum(errors) / len(errors) if errors else None,
            "maxErrorNormalized": maximum / max(actual_size),
            "edgeOrder": ["left", "right", "top", "bottom"],
            "thresholdPx": RASTER_EDGE_BUDGET_PX, "thresholdQualified": False}
