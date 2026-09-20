import hashlib
from copy import deepcopy

import pytest

from app.services import reference_vector_measurement as measurement
from app.services.renderer_pdf_strokes import inventory


def paths(edges):
    operations = []
    for start, end in edges:
        operations += [([start[0] * 0.48, 72 - start[1] * 0.48], b"m"),
                       ([end[0] * 0.48, 72 - end[1] * 0.48], b"l")]
    return inventory([*operations, ([], b"S")])


@pytest.fixture
def sample():
    image = b"already-validated-raster-bytes"
    field = {"cellId": "arbitrary-cell", "sourceRange": "B2:C4",
             "rectangle": {"x": 20, "y": 30, "width": 40, "height": 50},
             "bindings": [{"mappingId": "arbitrary-mapping", "targetPath": "customFields.x"}]}
    page = {"page": 7, "width": 150, "height": 150, "fields": [field],
            "referenceImage": {"sha256": hashlib.sha256(image).hexdigest()}}
    vector = {"effectiveBox": [0, 0, 72, 72], "userUnit": 1, "rotation": 0,
              "renderedPage": 1, "contentSha256": "a" * 64,
              "strokes": paths([((20, 30), (20, 80)), ((60, 30), (60, 80)),
                                ((20, 30), (60, 30)), ((20, 80), (60, 80))])}
    return page, vector, image


def test_four_edges_preserve_identity_and_do_not_claim_visibility_or_mutate_inputs(sample):
    before = deepcopy(sample)
    result = measurement.measure_fields(*sample)
    assert sample == before
    assert result["page"] == 7 and result["renderedPage"] == 1
    assert result["fieldCount"] == 1 and result["maxObservedErrorPx"] == 0
    field = result["fields"][0]
    assert field["status"] == "OBSERVED_WITHIN_DIAGNOSTIC_BUDGET"
    assert field["cellId"] == sample[0]["fields"][0]["cellId"]
    assert field["bindings"] == sample[0]["fields"][0]["bindings"]
    assert [edge["errorPx"] for edge in field["edges"]] == [0, 0, 0, 0]
    assert not any(result[key] for key in (
        "thresholdQualified", "visibilityVerified", "productionQualified"))
    field["bindings"][0]["mappingId"] = "changed-returned-copy"
    assert sample == before


@pytest.mark.parametrize(("rotation", "expected"), [
    (0, (25, 150)), (90, (50, 25)), (180, (75, 50)), (270, (150, 75)),
])
def test_origin_rotation_and_user_unit_are_applied_without_fitting(rotation, expected):
    vector = {"effectiveBox": [10, 20, 34, 68], "rotation": rotation, "userUnit": 2}
    assert measurement.raster_point([16, 32], vector, 150) == pytest.approx(expected)


@pytest.mark.parametrize("shift", [-20, -3, 3, 20])
def test_displaced_rectangles_cannot_be_certified_or_fitted_back(sample, shift):
    sample[0]["fields"][0]["rectangle"]["x"] += shift
    result = measurement.measure_fields(*sample)
    field = result["fields"][0]
    assert field["status"] != "OBSERVED_WITHIN_DIAGNOSTIC_BUDGET"
    if abs(shift) == 3:
        assert field["status"] == "EXCEEDS_DIAGNOSTIC_BUDGET"
        assert field["edges"][0]["errorPx"] == -shift


def test_multiple_matching_borders_are_ambiguous_not_nearest_selected(sample):
    sample[1]["strokes"]["segments"] += paths([((21, 30), (21, 80))])["segments"]
    edge = measurement.measure_fields(*sample)["fields"][0]["edges"][0]
    assert edge == {"status": "AMBIGUOUS", "errorPx": None, "candidateCount": 2}


def test_subpixel_rounding_cannot_hide_an_exceeded_diagnostic_threshold(sample):
    line = sample[1]["strokes"]["segments"][0]
    for key in ("from", "to"):
        line[key][0] += 2.0000001 * 0.48
    result = measurement.measure_fields(*sample)
    assert result["fields"][0]["status"] == "EXCEEDS_DIAGNOSTIC_BUDGET"


def test_small_field_needs_its_own_border_without_global_anchor_count(sample):
    sample[0]["fields"][0]["rectangle"] = {"x": 20, "y": 30, "width": 8, "height": 8}
    sample[1]["strokes"] = paths([((20, 30), (20, 38)), ((28, 30), (28, 38)),
                                 ((20, 30), (28, 30)), ((20, 38), (28, 38))])
    result = measurement.measure_fields(*sample)
    assert result["fields"][0]["status"] == "OBSERVED_WITHIN_DIAGNOSTIC_BUDGET"
    assert result["productionQualified"] is False


def test_path_coverage_is_union_not_sum_and_dashes_are_explicit(sample):
    sample[1]["strokes"]["segments"] = paths([((20, 30), (20, 55)),
                                               ((20, 30), (20, 55))])["segments"]
    assert measurement.measure_fields(*sample)["fields"][0]["edges"][0]["status"] == "UNOBSERVED"
    sample[1]["strokes"]["segments"] += paths([((20, 55), (20, 80))])["segments"]
    sample[1]["strokes"]["segments"][0]["dash"] = [3, 2]
    result = measurement.measure_fields(*sample)
    edge = result["fields"][0]["edges"][0]
    assert edge["pathCoverage"] == 1 and edge["hasDashes"] is True
    assert result["visibilityVerified"] is False


@pytest.mark.parametrize("reason", ["width", "clippingUnverified", "sharesPathWithCurve"])
def test_hairline_clipping_or_curve_shared_path_is_not_edge_evidence(sample, reason):
    sample[1]["strokes"]["segments"][0][reason] = 0 if reason == "width" else True
    result = measurement.measure_fields(*sample)
    assert result["excludedSegments"] == 1
    assert result["fields"][0]["edges"][0]["status"] == "UNOBSERVED"


def test_explicit_unbound_fields_are_not_ocr_targets(sample):
    sample[0]["fields"].append({"cellId": "heading", "bindings": []})
    assert measurement.measure_fields(*sample)["fieldCount"] == 1


@pytest.mark.parametrize("change", [
    "image", "size", "page", "policy", "missing_bindings", "binding_type",
    "mapping_duplicate", "cell_duplicate", "source", "rectangle", "nonfinite", "field_type",
])
def test_malformed_or_changed_input_does_not_return_partial_measurement(sample, change):
    page, vector, image = sample
    field = page["fields"][0]
    if change == "image":
        image += b"changed"
    elif change == "size":
        page["width"] += 1
    elif change == "page":
        page["page"] = True
    elif change == "policy":
        vector["strokes"]["policy"] = "older-protocol"
    elif change == "missing_bindings":
        field.pop("bindings")
    elif change == "binding_type":
        field["bindings"] = [None]
    elif change == "mapping_duplicate":
        field["bindings"] *= 2
    elif change == "cell_duplicate":
        page["fields"].append(deepcopy(field))
    elif change == "source":
        field["sourceRange"] = None
    elif change == "rectangle":
        field["rectangle"]["width"] = 200
    elif change == "nonfinite":
        field["rectangle"]["x"] = float("nan")
    else:
        page["fields"] = [None]
    with pytest.raises(ValueError):
        measurement.measure_fields(page, vector, image)


def test_span_budget_fails_without_partial_result(sample, monkeypatch):
    monkeypatch.setattr(measurement, "MAX_SPAN_CHECKS", 1)
    with pytest.raises(ValueError, match="span budget"):
        measurement.measure_fields(*sample)
