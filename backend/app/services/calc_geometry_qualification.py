"""Formal tolerance decision for the fixed Calc-grid reference path.

The decision is limited to logical workbook geometry through the owned 150 dpi
reference raster.  It does not qualify text visibility, scan registration,
crop correspondence for a submitted image, or handwritten-ink clearance.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.domain.calc_pdf_coordinates import POLICY as COORDINATE_POLICY
from app.domain.calc_pdf_coordinates import representation_bounds
from app.domain.calc_print_grid import MODEL
from app.services.prepared_reference_evidence import PreparedReference
from app.services.prepared_reference_geometry import POLICY as GEOMETRY_EVIDENCE_POLICY
from app.services.print_layout_service import digest

POLICY = "wsl-calc-reference-geometry-qualification-v1"
SCOPE = "logical-workbook-to-owned-reference-raster-v1"
ANCHOR_CODE = "REFERENCE_ANCHORS_INSUFFICIENT"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def implementation_fingerprint() -> str:
    directory = Path(__file__).parent
    files = (
        "calc_geometry_qualification.py",
        "calc_reference_precision.py",
        "prepared_reference_geometry.py",
        "reference_vector_measurement.py",
        "reference_vector_raster.py",
        "reference_field_raster.py",
        "reference_splash_raster.py",
        "reference_stroke_differential.py",
        "print_layout_service.py",
    )
    domain = directory.parent / "domain"
    identity = {
        "policy": POLICY,
        "scope": SCOPE,
        "implementation": {
            **{name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
               for name in files},
            "calc_pdf_coordinates.py": hashlib.sha256(
                (domain / "calc_pdf_coordinates.py").read_bytes()
            ).hexdigest(),
            "calc_print_grid.py": hashlib.sha256(
                (domain / "calc_print_grid.py").read_bytes()
            ).hexdigest(),
        },
    }
    return hashlib.sha256(_canonical(identity)).hexdigest()


def _number(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"Invalid {label}")
    return float(value)


def _within_bound(error: float, side: int, bounds: dict[str, Fraction]) -> bool:
    magnitude = Fraction(str(abs(error)))
    if side < 2:
        return magnitude <= bounds["xTotalPx"]
    return magnitude < bounds["yTotalPxExclusive"]


def issue(
    prepared: PreparedReference,
    plan: dict[str, Any],
    pages: list[dict[str, Any]],
    version_id: str,
    qualification_fingerprint: str,
) -> dict[str, Any]:
    """Qualify only complete, independently recomputed Calc evidence."""
    if (
        not isinstance(version_id, str)
        or not version_id
        or not isinstance(qualification_fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", qualification_fingerprint) is None
        or not isinstance(plan, dict)
        or plan.get("geometryModelVersion") != MODEL
        or plan.get("rendererVersion") != "wsl-print-reference-v1"
        or plan.get("dpi") != 150
        or plan.get("unit") != "pt"
        or plan.get("printScalePolicy") != "integer-percent-v1"
        or plan.get("targetPagesWide") != 1
        or prepared.evidence.get("planSha256") != digest(plan)
    ):
        raise ValueError("Unsupported formal Calc geometry path")
    zoom = _number(plan.get("commonScale"), "Calc print scale") * 100
    if abs(zoom - round(zoom)) > 1e-10 or not 10 <= round(zoom) <= 100:
        raise ValueError("Formal Calc geometry requires an integer print scale")
    if not isinstance(pages, list) or len(pages) != len(plan.get("pages", [])):
        raise ValueError("Formal geometry page inventory changed")

    bounds = representation_bounds(150)
    field_ids: set[str] = set()
    mapping_ids: set[str] = set()
    page_results = []
    total_fields = total_edges = 0
    max_error = 0.0
    for expected_page, measured_page in zip(plan["pages"], pages, strict=True):
        expected_fields = [field for field in expected_page["fields"] if field.get("bindings")]
        measured_fields = measured_page.get("fields")
        precision = measured_page.get("coordinatePrecision")
        if (
            measured_page.get("policy") != GEOMETRY_EVIDENCE_POLICY
            or measured_page.get("page") != expected_page["page"]
            or measured_page.get("range") != expected_page["range"]
            or measured_page.get("status") != "MEASURED"
            or measured_page.get("codes") != []
            or measured_page.get("fieldCount") != len(expected_fields)
            or not isinstance(measured_fields, list)
            or len(measured_fields) != len(expected_fields)
            or measured_page.get("referenceImageSha256")
            != expected_page["referenceImage"]["sha256"]
            or measured_page.get("fieldRasterObservation", {}).get(
                "allRequiredInkObserved"
            ) is not True
            or not isinstance(precision, dict)
            or precision.get("policy") != COORDINATE_POLICY
            or precision.get("status") != "MATCH"
            or precision.get("codes") != []
            or precision.get("page") != expected_page["page"]
        ):
            raise ValueError("Formal geometry evidence is incomplete")
        expected_by_id = {field["cellId"]: field for field in expected_fields}
        measured_by_id = {field.get("cellId"): field for field in measured_fields}
        precision_fields = precision.get("fields")
        precision_by_id = (
            {field.get("cellId"): field for field in precision_fields}
            if isinstance(precision_fields, list) else {}
        )
        if (
            len(expected_by_id) != len(expected_fields)
            or len(measured_by_id) != len(measured_fields)
            or len(precision_by_id) != len(expected_fields)
            or set(expected_by_id) != set(measured_by_id)
            or set(expected_by_id) != set(precision_by_id)
        ):
            raise ValueError("Formal geometry field inventory changed")
        page_max = 0.0
        for identity, expected in expected_by_id.items():
            if identity in field_ids:
                raise ValueError("Formal geometry field identity is duplicated")
            field_ids.add(identity)
            bindings = expected.get("bindings")
            if not isinstance(bindings, list) or not bindings:
                raise ValueError("Formal geometry binding is missing")
            for binding in bindings:
                mapping_id = binding.get("mappingId") if isinstance(binding, dict) else None
                if not isinstance(mapping_id, str) or not mapping_id or mapping_id in mapping_ids:
                    raise ValueError("Formal geometry mapping identity is ambiguous")
                mapping_ids.add(mapping_id)
            measured = measured_by_id[identity]
            exact = precision_by_id[identity]
            if (
                measured.get("sourceRange") != expected.get("sourceRange")
                or measured.get("bindings") != bindings
                or measured.get("status") != "OBSERVED_WITHIN_DIAGNOSTIC_BUDGET"
                or exact.get("sourceRange") != expected.get("sourceRange")
                or exact.get("bindings") != bindings
                or exact.get("status") != "MATCH"
                or exact.get("codes") != []
            ):
                raise ValueError("Formal geometry field binding changed")
            rectangle = expected.get("rectangle")
            if not isinstance(rectangle, dict):
                raise ValueError("Formal geometry rectangle is missing")
            x, y, width, height = (
                _number(rectangle.get(key), "field rectangle")
                for key in ("x", "y", "width", "height")
            )
            if (
                min(x, y) < 0
                or min(width, height) <= 0
                or x + width > expected_page["width"]
                or y + height > expected_page["height"]
            ):
                raise ValueError("Formal geometry rectangle leaves its page")
            edges = measured.get("edges")
            exact_edges = exact.get("edges")
            if (
                not isinstance(edges, list)
                or len(edges) != 4
                or not isinstance(exact_edges, list)
                or len(exact_edges) != 4
            ):
                raise ValueError("Formal geometry edge inventory changed")
            for side, (edge, exact_edge) in enumerate(zip(edges, exact_edges, strict=True)):
                error = _number(edge.get("errorPx"), "field edge error")
                segments = edge.get("segmentIndices")
                if (
                    edge.get("status") != "OBSERVED"
                    or not isinstance(segments, list)
                    or not segments
                    or any(type(index) is not int or index < 0 for index in segments)
                    or exact_edge.get("differencePx") is None
                    or not _within_bound(error, side, bounds)
                ):
                    raise ValueError("Formal geometry edge exceeds its analytic bound")
                page_max = max(page_max, abs(error))
                total_edges += 1
            total_fields += 1
        measurement_sha = measured_page.get("measurementImageSha256")
        if measurement_sha != measured_page.get("referenceImageSha256"):
            comparison = measured_page.get("probeComparison")
            if (
                not isinstance(comparison, dict)
                or comparison.get("changedOutsideRedNeighborhood") != 0
            ):
                raise ValueError("Formal geometry measurement copy changed unrelated pixels")
        page_results.append({
            "page": expected_page["page"],
            "range": expected_page["range"],
            "fieldCount": len(expected_fields),
            "edgeCount": len(expected_fields) * 4,
            "maxObservedRepresentationErrorPx": page_max,
            "coordinatePrecision": "MATCH",
            "rasterEvidence": "PASS",
        })
        max_error = max(max_error, page_max)
    if total_fields == 0:
        raise ValueError("Formal geometry has no OCR fields")

    graph_sha = hashlib.sha256(_canonical(prepared.evidence)).hexdigest()
    return {
        "policy": POLICY,
        "scope": SCOPE,
        "status": "PASS",
        "qualified": True,
        "templateVersionId": version_id,
        "qualificationFingerprint": qualification_fingerprint,
        "implementationFingerprint": implementation_fingerprint(),
        "geometryModelVersion": MODEL,
        "preparedEvidenceSha256": graph_sha,
        "planSha256": digest(plan),
        "dpi": 150,
        "integerScalePercent": round(zoom),
        "representationBoundsPx": {
            key: float(value) for key, value in bounds.items()
        },
        "pageCount": len(page_results),
        "fieldCount": total_fields,
        "edgeCount": total_edges,
        "maxObservedRepresentationErrorPx": max_error,
        "pages": page_results,
        "requirements": {
            "sourcePlanAndBindingsRecomputed": True,
            "allRequiredEdgesObserved": True,
            "integerPdfCoordinatesMatched": True,
            "analyticRepresentationBoundsSatisfied": True,
            "ownedVectorAndRasterEvidence": True,
            "pageAndCropRectanglesDefined": True,
        },
        "excludedFromScope": [
            "text-visibility",
            "submitted-scan-registration",
            "submitted-scan-crop-correspondence",
            "submitted-handwriting-ink-clearance",
        ],
        "unresolvedBlockers": [],
    }


def anchor_diagnostic(
    diagnostic_codes: list[str], geometry_qualified: bool, geometry_model: object
) -> dict[str, Any]:
    codes = sorted(set(diagnostic_codes))
    separated = (
        geometry_model == MODEL and geometry_qualified is True and ANCHOR_CODE in codes
    )
    blocking = [code for code in codes if not (separated and code == ANCHOR_CODE)]
    return {
        "policy": "calc-anchor-diagnostic-separation-v1",
        "geometryModelVersion": geometry_model,
        "codes": codes,
        "status": "NON_BLOCKING_DIAGNOSTIC" if separated else (
            "BLOCKING" if ANCHOR_CODE in codes else "NOT_PRESENT"
        ),
        "separated": separated,
        "blockingCodes": blocking,
    }
