"""Compare verified Calc-grid coordinates with owned PDF edge observations.

The caller must validate source/plan/renderer and vector ownership first. This
comparison is not a visibility, environment, crop, or production certificate.
It neither fits observed lines nor modifies saved rectangles or field bindings.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any

from app.domain.calc_pdf_coordinates import POLICY, point_millipoints, representation_bounds
from app.domain.calc_print_grid import MODEL, dimension_grid, frame_origin
from app.domain.print_layout import PAPER_MM
from app.services.renderer_pdf_strokes import numbers

NUMERIC_IDENTITY_PT = 1e-9  # Arithmetic equality; not a geometric tolerance.


def compare_coordinates(plan: dict[str, Any], page: dict[str, Any],
                        vector_page: dict[str, Any], observation: dict[str, Any]
                        ) -> dict[str, Any] | None:
    if plan.get("geometryModelVersion") != MODEL:
        return None  # No retrofit of this model onto historical plans.
    dpi = plan["dpi"]
    bounds = representation_bounds(dpi)
    result: dict[str, Any] = {
        "policy": POLICY, "page": page["page"], "status": "UNVERIFIED", "fields": [],
        "codes": [], "edgeOrder": ["left", "right", "top", "bottom"],
        "representationBoundsPx": {k: float(v) for k, v in bounds.items()},
        "numericIdentityPt": NUMERIC_IDENTITY_PT,
        "productionQualified": False, "visibilityVerified": False,
    }
    box = numbers(vector_page["effectiveBox"], 4)
    if (type(vector_page["rotation"]) is not int or vector_page["rotation"] != 0
            or type(vector_page["userUnit"]) not in (int, float)
            or vector_page["userUnit"] != 1 or box[:2] != [0, 0]
            or vector_page["effectiveBox"] != vector_page["mediaBox"]
            or vector_page["effectiveBox"] != vector_page["cropBox"]):
        result["codes"] = ["PDF_COORDINATE_PATH_UNSUPPORTED"]
        return result
    zoom_value = numbers([plan["commonScale"]], 1)[0] * 100
    zoom = round(zoom_value)
    if abs(zoom_value - zoom) > 1e-10 or not 10 <= zoom <= 100:
        raise ValueError("Coordinate precision requires an integer print scale")
    if (observation["page"] != page["page"]
            or page["sheetName"] != plan["sheetName"]
            or type(page["page"]) is not int):
        raise ValueError("Coordinate precision page identity changed")
    evidence = plan["geometryEvidence"]
    grids = [dimension_grid(evidence[axis], evidence["hidden" + axis.title()])
             for axis in ("columns", "rows")]
    paper = PAPER_MM[plan["paperSize"]]
    if plan["orientation"] == "landscape":
        paper = paper[::-1]
    elif plan["orientation"] != "portrait":
        raise ValueError("Unknown print orientation")
    origins = []
    for axis, grid, prefix, before, after, center in (
        (0, grids[0], "Col", "left", "right", "horizontalCentering"),
        (1, grids[1], "Row", "top", "bottom", "verticalCentering"),
    ):
        start, end = page["start" + prefix], page["end" + prefix]
        if (type(start) is not int or type(end) is not int
                or not 1 <= start <= end <= len(grid["twips"])):
            raise ValueError("Coordinate precision page range changed")
        margin_before, margin_after = numbers(
            [plan["margins"][before], plan["margins"][after]], 2)
        origins.append(frame_origin(
            round(paper[axis] * 1440 / 25.4), round(margin_before * 1440 / 25.4),
            round(margin_after * 1440 / 25.4), sum(grid["twips"][start - 1:end]), zoom,
            centered=plan[center],
        ))
    source = [f for f in page["fields"] if f.get("bindings") != []]
    expected = {f["cellId"]: f for f in source}
    observed = {f["cellId"]: f for f in observation["fields"]}
    if (not expected or len(expected) != len(source)
            or len(observed) != len(observation["fields"]) or set(observed) != set(expected)):
        raise ValueError("Coordinate precision field inventory changed")
    codes: set[str] = set()
    height = Fraction(str(box[3]))
    for identity, field in expected.items():
        measured = observed[identity]
        if (not isinstance(field["bindings"], list) or not field["bindings"]
                or measured["bindings"] != field["bindings"]
                or measured["sourceRange"] != field["sourceRange"]):
            raise ValueError("Coordinate precision field meaning changed")
        mm100 = []
        for axis, grid, prefix in ((0, grids[0], "Col"), (1, grids[1], "Row")):
            start, end = field["start" + prefix], field["end" + prefix]
            page_start, page_end = page["start" + prefix], page["end" + prefix]
            if (type(start) is not int or type(end) is not int
                    or not page_start <= start <= end <= page_end):
                raise ValueError("Coordinate precision field leaves its bound page")
            boundaries = grid["boundariesMm100"]
            for index in (start - 1, end):
                mm100.append(origins[axis] + boundaries[index] - boundaries[page_start - 1])
        continuous = [Fraction(v * zoom * dpi, 100 * 2540) for v in mm100]
        rounded = [round(value) for value in continuous]
        rectangle = field["rectangle"]
        saved = [rectangle[k] for k in ("x", "y", "width", "height")]
        if any(type(v) is not int for v in saved):
            raise ValueError("Coordinate precision requires integer saved rectangles")
        saved_edges = [saved[0], saved[0] + saved[2], saved[1], saved[1] + saved[3]]
        field_codes = set()
        if rounded[1] <= rounded[0] or rounded[3] <= rounded[2]:
            field_codes.add("COLLAPSED_FIELD_EXTENT")
        if saved_edges != rounded:
            field_codes.add("SAVED_EDGE_ROUNDING_MISMATCH")
        if len(measured["edges"]) != 4:
            raise ValueError("Coordinate precision edge inventory changed")
        edges = []
        for index, (coordinate, edge) in enumerate(zip(mm100, measured["edges"], strict=True)):
            encoded = point_millipoints(coordinate, coordinate, zoom, height)[index // 2]
            expected_pt = Fraction(encoded, 1000) if index < 2 else height - Fraction(encoded, 1000)
            expected_px = expected_pt * dpi / 72
            delta = None
            if edge["status"] == "OBSERVED":
                actual = numbers([edge["positionPx"]], 1)[0]
                delta = actual - float(expected_px)
                if abs(delta * 72 / dpi) > NUMERIC_IDENTITY_PT:
                    field_codes.add("PDF_COORDINATE_MISMATCH")
            else:
                field_codes.add("PDF_COORDINATE_UNOBSERVED")
            edges.append({"expectedMillipoint": encoded,
                          "expectedRasterPx": float(expected_px), "differencePx": delta})
        result["fields"].append({"cellId": identity, "sourceRange": field["sourceRange"],
                                 "bindings": deepcopy(field["bindings"]), "edges": edges,
                                 "status": "MATCH" if not field_codes else "UNVERIFIED",
                                 "codes": sorted(field_codes)})
        codes.update(field_codes)
    result["codes"] = sorted(codes)
    result["status"] = "MATCH" if not codes else "UNVERIFIED"
    return result
