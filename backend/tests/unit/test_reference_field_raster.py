from copy import deepcopy

import pytest

from app.services import reference_field_raster as ink
from tests.unit.test_reference_vector_measurement import paths


@pytest.fixture
def evidence():
    line = paths([((0, 30), (100, 30))])["segments"][0]
    line.update(width=.3325, dash=[.1, .9])
    vector = {"effectiveBox": [0, 0, 72, 72], "rotation": 0, "userUnit": 1,
              "strokes": {"segments": [line]}}
    field = {"cellId": "first-field", "rectangle": {"x": 20, "y": 30, "width": 60,
             "height": 20}, "bindings": [{"mappingId": "mapping-first", "targetPath": "a"}]}
    raster = {"segmentIndex": 0, "status": "UNVERIFIABLE",
              "reason": "NO_INTERIOR_PAINT_SAMPLES"}
    difference = {"segmentIndex": 0, "status": "UNVERIFIED", "paintFootprints": 40,
        "changedFootprints": 38, "subpixelComponents": {
            "paintComponents": 46, "changedComponents": 45, "maxPaintComponentPx": .20834,
            "status": "UNVERIFIED", "intersectionCandidates": [
                {"rasterInterval": [5.0, 5.2], "candidateSegmentIndices": []}],
        }}
    observation = {"fields": [{"cellId": field["cellId"], "bindings": deepcopy(field["bindings"]),
        "edges": [{}, {}, {"segmentIndices": [0]}, {}]}],
        "rasterObservation": {"segments": [raster]}, "strokeDifferential": [difference]}
    return {"fields": [field]}, vector, observation


def test_only_missing_contributions_outside_the_field_interior_are_excluded(evidence):
    before = deepcopy(evidence)
    result = ink.observe_field_ink(*evidence)
    assert evidence == before
    assert result["allRequiredInkObserved"] is True
    assert result["visibilityVerified"] is result["productionQualified"] is False
    assert result["observations"][0]["requiredInterval"] == [22, 78]
    assert result["observations"][0]["paintComponents"] > 0
    assert result["observations"][0]["unobservedIntervals"] == []
    assert result["observations"][0]["cellId"] == "first-field"


@pytest.mark.parametrize(("interval", "observed"), [
    ([21.8, 22], True), ([21.8, 22.00001], False), ([40, 40.2], False),
    ([78, 78.2], True), ([77.99999, 78.2], False),
])
def test_existing_endpoint_guard_is_not_enlarged(evidence, interval, observed):
    evidence[2]["strokeDifferential"][0]["subpixelComponents"]["intersectionCandidates"][0][
        "rasterInterval"] = interval
    assert ink.observe_field_ink(*evidence)["allRequiredInkObserved"] is observed


@pytest.mark.parametrize("change", ["sampled_missing", "unsupported", "missing", "duplicate",
                                    "zero", "large", "incomplete", "empty_interior", "no_paint"])
def test_scope_never_rescues_missing_ink_or_missing_causal_evidence(evidence, change):
    page, _, observation = evidence
    difference = observation["strokeDifferential"][0]
    component = difference["subpixelComponents"]
    if change == "sampled_missing":
        observation["rasterObservation"]["segments"][0].update(status="SAMPLED", inkCoverage=0)
    elif change == "unsupported":
        observation["rasterObservation"]["segments"][0]["reason"] = "UNVERIFIED_PATH"
    elif change == "missing":
        observation["strokeDifferential"].clear()
    elif change == "duplicate":
        observation["strokeDifferential"].append(deepcopy(difference))
    elif change == "zero":
        component["changedComponents"] = 0
    elif change == "large":
        component["maxPaintComponentPx"] = 1
    elif change == "incomplete":
        component["paintComponents"] += 1
    elif change == "empty_interior":
        page["fields"][0]["rectangle"]["width"] = 4
    else:
        # A narrow field interior entirely in one dash gap has no observed paint.
        page["fields"][0]["rectangle"].update(x=20.5, width=4.1)
    assert ink.observe_field_ink(*evidence)["allRequiredInkObserved"] is False


def test_shared_line_is_checked_separately_for_each_field(evidence):
    page, _, observation = evidence
    second = deepcopy(page["fields"][0])
    second.update(cellId="second-field", rectangle={"x": 0, "y": 30, "width": 20, "height": 20},
                  bindings=[{"mappingId": "mapping-second", "targetPath": "b"}])
    page["fields"].append(second)
    observed = deepcopy(observation["fields"][0])
    observed.update(cellId=second["cellId"], bindings=deepcopy(second["bindings"]))
    observation["fields"].append(observed)
    result = ink.observe_field_ink(*evidence)
    assert [r["status"] for r in result["observations"]] == ["OBSERVED", "UNVERIFIED"]
    assert result["allRequiredInkObserved"] is False


@pytest.mark.parametrize("vertical", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_field_interval_uses_projected_axis_and_direction(evidence, vertical, reverse):
    page, vector, observation = evidence
    line = vector["strokes"]["segments"][0]
    # Define both directions in PDF coordinates at the existing 150dpi transform.
    line["from"], line["to"] = ([14.4, 72], [14.4, 24]) if vertical else (
        [0, 57.6], [48, 57.6])
    if reverse:
        line["from"], line["to"] = line["to"], line["from"]
    side = 0 if vertical else 2
    observation["fields"][0]["edges"] = [{} for _ in range(4)]
    observation["fields"][0]["edges"][side] = {"segmentIndices": [0]}
    if vertical:
        page["fields"][0]["rectangle"] = {"x": 30, "y": 20, "width": 20, "height": 60}
    result = ink.observe_field_ink(*evidence)
    assert result["allRequiredInkObserved"] is True
    assert result["observations"][0]["requiredInterval"] == [22, 78]
    observation["strokeDifferential"][0]["subpixelComponents"]["intersectionCandidates"][0][
        "rasterInterval"] = [40, 40.2]
    assert ink.observe_field_ink(*evidence)["allRequiredInkObserved"] is False


def test_full_segment_success_does_not_require_supplemental_scope(evidence):
    evidence[2]["rasterObservation"]["segments"][0].update(status="SAMPLED", inkCoverage=1)
    result = ink.observe_field_ink(*evidence)
    assert result["allRequiredInkObserved"] is True
    assert result["observations"] == []


def test_mapping_mismatch_is_not_resolved_by_position(evidence):
    evidence[2]["fields"][0]["bindings"][0]["mappingId"] = "another"
    with pytest.raises(ValueError, match="binding changed"):
        ink.observe_field_ink(*evidence)


def test_field_scope_work_is_bounded(evidence, monkeypatch):
    monkeypatch.setattr(ink, "MAX_SAMPLES", 1)
    with pytest.raises(ValueError, match="sample budget"):
        ink.observe_field_ink(*evidence)
