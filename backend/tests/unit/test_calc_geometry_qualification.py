from copy import deepcopy
from fractions import Fraction

import pytest

from app.domain.calc_print_grid import MODEL
from app.services import calc_geometry_qualification as qualification
from app.services.calc_reference_precision import compare_coordinates
from app.services.prepared_reference_evidence import PreparedReference
from app.services.prepared_reference_geometry import POLICY as EVIDENCE_POLICY
from app.services.print_layout_service import digest


@pytest.fixture
def evidence():
    field = {
        "cellId": "field-a",
        "sourceRange": "B3:C3",
        "startRow": 3,
        "endRow": 3,
        "startCol": 2,
        "endCol": 3,
        "rectangle": {"x": 66, "y": 74, "width": 15, "height": 15},
        "bindings": [{
            "mappingId": "mapping-a",
            "sourceRegionId": "region-a",
            "targetPath": "customFields.any",
        }],
    }
    page = {
        "page": 7,
        "range": "A2:C3",
        "sheetName": "Form",
        "startRow": 2,
        "endRow": 3,
        "startCol": 1,
        "endCol": 3,
        "width": 150,
        "height": 150,
        "referenceImage": {"sha256": "a" * 64},
        "fields": [field],
    }
    plan = {
        "geometryModelVersion": MODEL,
        "rendererVersion": "wsl-print-reference-v1",
        "dpi": 150,
        "unit": "pt",
        "printScalePolicy": "integer-percent-v1",
        "targetPagesWide": 1,
        "sheetName": "Form",
        "commonScale": 1.0,
        "paperSize": "A4",
        "orientation": "portrait",
        "margins": dict.fromkeys(("left", "right", "top", "bottom"), 10),
        "horizontalCentering": False,
        "verticalCentering": False,
        "geometryEvidence": {
            "columns": [3.6] * 3,
            "rows": [7.2] * 3,
            "hiddenColumns": [],
            "hiddenRows": [],
        },
        "pages": [page],
    }
    height = Fraction(8000009, 10000)
    box = [0, 0, 600, float(height)]
    vector = {
        "renderedPage": 1,
        "rotation": 0,
        "userUnit": 1,
        "mediaBox": box,
        "cropBox": box,
        "effectiveBox": box,
    }
    points = [
        Fraction(31918, 1000),
        Fraction(39118, 1000),
        height - Fraction(764482, 1000),
        height - Fraction(757282, 1000),
    ]
    observation = {
        "page": 7,
        "fields": [{
            "cellId": field["cellId"],
            "sourceRange": field["sourceRange"],
            "bindings": deepcopy(field["bindings"]),
            "edges": [
                {"status": "OBSERVED", "positionPx": float(point * 150 / 72)}
                for point in points
            ],
        }],
    }
    precision = compare_coordinates(plan, page, vector, observation)
    measured = {
        "page": 7,
        "range": page["range"],
        "policy": EVIDENCE_POLICY,
        "status": "MEASURED",
        "codes": [],
        "fieldCount": 1,
        "fields": [{
            "cellId": field["cellId"],
            "sourceRange": field["sourceRange"],
            "bindings": deepcopy(field["bindings"]),
            "status": "OBSERVED_WITHIN_DIAGNOSTIC_BUDGET",
            "edges": [
                {"status": "OBSERVED", "errorPx": error, "segmentIndices": [index]}
                for index, error in enumerate((0.49, -0.49, 0.502, -0.502))
            ],
        }],
        "fieldRasterObservation": {"allRequiredInkObserved": True},
        "referenceImageSha256": page["referenceImage"]["sha256"],
        "measurementImageSha256": page["referenceImage"]["sha256"],
        "coordinatePrecision": precision,
    }
    prepared = PreparedReference(b"xlsx", b"pdf", [b"png"], {}, {
        "planSha256": digest(plan), "owned": "graph",
    })
    return prepared, plan, [measured]


def test_calc_geometry_issuer_uses_analytic_axis_bounds(evidence):
    before = deepcopy(evidence)
    result = qualification.issue(*evidence, "version-a", "f" * 64)
    assert evidence == before
    assert result["status"] == "PASS" and result["qualified"] is True
    assert result["fieldCount"] == 1 and result["edgeCount"] == 4
    assert result["representationBoundsPx"]["xTotalPx"] == pytest.approx(0.5010416667)
    assert result["representationBoundsPx"]["yTotalPxExclusive"] == pytest.approx(0.503125)
    assert result["maxObservedRepresentationErrorPx"] == 0.502
    assert all(result["requirements"].values())


@pytest.mark.parametrize("change", ["edge", "coordinate", "ink", "model", "graph", "binding"])
def test_calc_geometry_issuer_fails_closed_for_incomplete_or_changed_evidence(
    evidence, change,
):
    prepared, plan, pages = evidence
    if change == "edge":
        pages[0]["fields"][0]["edges"][0]["status"] = "UNOBSERVED"
    elif change == "coordinate":
        pages[0]["coordinatePrecision"]["status"] = "UNVERIFIED"
    elif change == "ink":
        pages[0]["fieldRasterObservation"]["allRequiredInkObserved"] = False
    elif change == "model":
        plan["geometryModelVersion"] = "wsl-fods-physical-length-v1"
    elif change == "graph":
        prepared.evidence["planSha256"] = "0" * 64
    else:
        pages[0]["fields"][0]["bindings"][0]["targetPath"] = "changed"
    with pytest.raises(ValueError):
        qualification.issue(prepared, plan, pages, "version-a", "f" * 64)


def test_axis_bound_is_not_expanded_to_fit_observed_error(evidence):
    evidence[2][0]["fields"][0]["edges"][0]["errorPx"] = 0.502
    with pytest.raises(ValueError, match="analytic bound"):
        qualification.issue(*evidence, "version-a", "f" * 64)


def test_anchor_shortage_is_non_blocking_only_after_formal_calc_geometry():
    diagnostic = [qualification.ANCHOR_CODE]
    separated = qualification.anchor_diagnostic(diagnostic, True, MODEL)
    assert separated["status"] == "NON_BLOCKING_DIAGNOSTIC"
    assert separated["codes"] == diagnostic and separated["blockingCodes"] == []
    for qualified, model in ((False, MODEL), (True, "wsl-fods-physical-length-v1")):
        result = qualification.anchor_diagnostic(diagnostic, qualified, model)
        assert result["status"] == "BLOCKING"
        assert result["blockingCodes"] == diagnostic
