import hashlib
import io
from copy import deepcopy

import pytest
from PIL import Image

from app.services import reference_stroke_differential as differential
from app.services.renderer_stroke_probe_pixels import DIFFERENTIAL_POLICY, pack_delta
from tests.unit.test_reference_vector_measurement import paths, sample

sample = sample


def fixture(sample, *, missing=(), outside=False, reverse=False):
    page, vector, _ = deepcopy(sample)
    line = vector["strokes"]["segments"][0]
    line.update(dash=[0.1, 0.1], width=0.3325)
    if reverse:
        line["from"], line["to"] = line["to"], line["from"]
    vector["strokes"]["segments"] = [line]
    with Image.new("RGB", (150, 150), "white") as source:
        for y in range(30, 80):
            if y not in missing:
                source.putpixel((20, y), (30, 30, 30))
        stream = io.BytesIO()
        source.save(stream, format="PNG")
        png, rgb = stream.getvalue(), source.tobytes()
    with Image.new("RGB", (150, 150), "white") as suppressed:
        if outside:
            suppressed.putpixel((100, 100), (0, 0, 0))
        delta = pack_delta(rgb, suppressed.tobytes(), 150, 150)
    page["referenceImage"]["sha256"] = hashlib.sha256(png).hexdigest()
    mutation = {
        "policy": "wsl-single-stroke-suppression-v1", "sourcePdfSha256": "b" * 64,
        "probePdfSha256": "c" * 64, "sourceContentSha256": vector["contentSha256"],
        "probeContentSha256": "d" * 64, "renderedPage": 1, "operation": line["operation"],
        "originalOperator": "S", "replacementOperator": "n", "changedDecodedBytes": 1,
        "byteOffset": 20, "segmentCount": 1,
        "visibilityVerified": False, "productionQualified": False,
    }
    report = {
        "policy": DIFFERENTIAL_POLICY, "sourcePdfSha256": "b" * 64, "dpi": 150,
        "rasterizer": "poppler-pdftoppm", "visibilityVerified": False,
        "productionQualified": False,
        "probes": [{"renderedPage": 1, "operation": line["operation"],
                    "status": "MEASURED", "mutation": mutation, "delta": delta}],
    }
    return page, vector, png, report


@pytest.mark.parametrize("reverse", [False, True])
def test_fine_dash_footprints_use_exact_causal_delta_without_qualifying(sample, reverse):
    page, vector, png, report = fixture(sample, reverse=reverse)
    before = deepcopy((page, vector, png, report))
    differential.validate_manifest(report, {"pages": [vector]}, "b" * 64)
    observed = differential.observe_page(page, vector, png, report["probes"])
    assert len(observed) == 1
    assert observed[0]["status"] == "OBSERVED"
    assert observed[0]["paintFootprints"] == observed[0]["changedFootprints"] > 40
    assert observed[0]["segmentIndex"] == 0
    assert not observed[0]["visibilityVerified"] and not observed[0]["productionQualified"]
    assert (page, vector, png, report) == before


@pytest.mark.parametrize("missing", [(45,), tuple(range(30, 80))])
def test_partial_and_fully_occluded_strokes_are_not_observed(sample, missing):
    page, vector, png, report = fixture(sample, missing=missing)
    observed = differential.observe_page(page, vector, png, report["probes"])[0]
    assert observed["status"] == "UNVERIFIED"
    assert observed["changedFootprints"] < observed["paintFootprints"]


def test_any_changed_pixel_outside_operation_is_rejected(sample):
    page, vector, png, report = fixture(sample, outside=True)
    with pytest.raises(ValueError, match="outside"):
        differential.observe_page(page, vector, png, report["probes"])


@pytest.mark.parametrize("key,value", [
    ("sourcePdfSha256", "f" * 64), ("policy", "other"), ("dpi", 300),
    ("rasterizer", "other"), ("visibilityVerified", True), ("productionQualified", True),
])
def test_outer_manifest_is_bound_to_source_and_policy(sample, key, value):
    _, vector, _, report = fixture(sample)
    report[key] = value
    with pytest.raises(ValueError, match="source evidence"):
        differential.validate_manifest(report, {"pages": [vector]}, "b" * 64)


@pytest.mark.parametrize("key,value", [
    ("sourceContentSha256", "f" * 64), ("sourcePdfSha256", "e" * 64),
    ("renderedPage", True), ("operation", False), ("originalOperator", "B"),
    ("replacementOperator", "S"), ("changedDecodedBytes", True),
    ("changedDecodedBytes", 2), ("byteOffset", -1), ("byteOffset", True),
    ("segmentCount", 2), ("probePdfSha256", ""), ("probeContentSha256", "f" * 63),
    ("visibilityVerified", True), ("productionQualified", True),
])
def test_mutation_must_be_one_owned_pure_stroke(sample, key, value):
    _, vector, _, report = fixture(sample)
    report["probes"][0]["mutation"][key] = value
    with pytest.raises(ValueError, match="single-operation"):
        differential.validate_manifest(report, {"pages": [vector]}, "b" * 64)


@pytest.mark.parametrize("change", ["missing", "duplicate", "wrong-page", "wrong-op"])
def test_complete_candidate_set_is_required(sample, change):
    _, vector, _, report = fixture(sample)
    if change == "missing":
        report["probes"] = []
    elif change == "duplicate":
        report["probes"] *= 2
    else:
        report["probes"][0]["renderedPage" if change == "wrong-page" else "operation"] += 1
    with pytest.raises(ValueError, match="operation set"):
        differential.validate_manifest(report, {"pages": [vector]}, "b" * 64)


def test_other_page_and_explicitly_unsupported_probe_cannot_rescue(sample):
    page, vector, png, report = fixture(sample)
    report["probes"][0].update(status="UNSUPPORTED", reason="COMBINED_FILL")
    differential.validate_manifest(report, {"pages": [vector]}, "b" * 64)
    assert differential.observe_page(page, vector, png, report["probes"]) == []
    report["probes"][0].update(status="MEASURED", renderedPage=2)
    assert differential.observe_page(page, vector, png, report["probes"]) == []


@pytest.mark.parametrize("change", ["png", "dimensions", "pixels"])
def test_exact_reference_raster_identity_is_required(sample, change):
    page, vector, png, report = fixture(sample)
    if change == "png":
        png += b"tampered"
    elif change == "dimensions":
        report["probes"][0]["delta"]["width"] -= 1
    else:
        report["probes"][0]["delta"]["sourcePixelSha256"] = "0" * 64
    with pytest.raises(ValueError):
        differential.observe_page(page, vector, png, report["probes"])


@pytest.mark.parametrize("begin,end,expected", [
    (0, 0, False), (1, 0, False), (0, 1, True), (3, 4, False),
    (3, 5, False), (4, 6, True), (-1, 0, False), (-1, 1, True),
    (2.9999999999999, 3, True), (0, 5, True), (5, 6, True),
])
def test_pixel_footprints_preserve_phase_wrap_and_positive_subpixel_area(begin, end, expected):
    assert differential.painted_interval([3, 5], [True, False], begin, end) is expected


@pytest.mark.parametrize("dash,expected", [([], True), ([0, 2], False), ([2, 0], True)])
def test_zero_dash_elements_preserve_alternation(dash, expected):
    ends, painted = differential.dash_intervals({"dash": dash})
    assert differential.painted_interval(ends, painted, 0, 1) is expected
    assert differential.painted_interval(ends, painted, 0, 0) is False


def test_solid_black_overpaint_is_not_causal_evidence(sample):
    page, vector, _, report = fixture(sample)
    with Image.new("RGB", (150, 150), "black") as image:
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        png, rgb = stream.getvalue(), image.tobytes()
    page["referenceImage"]["sha256"] = hashlib.sha256(png).hexdigest()
    report["probes"][0]["delta"] = pack_delta(rgb, rgb, 150, 150)
    observed = differential.observe_page(page, vector, png, report["probes"])[0]
    assert observed["status"] == "UNVERIFIED" and observed["changedFootprints"] == 0


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_page_rotation_uses_the_same_forward_transform(sample, rotation):
    page, vector, png, report = fixture(sample)
    vector["rotation"] = rotation
    # Rotate the pixels using exact integer indices, independently of the
    # production vector projection. The PDF rotation is clockwise.
    with Image.open(io.BytesIO(png)) as source:
        transpose = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
                     270: Image.Transpose.ROTATE_90}[rotation]
        rotated = source.transpose(transpose)
        stream = io.BytesIO()
        rotated.save(stream, format="PNG")
        png, rgb = stream.getvalue(), rotated.tobytes()
    page["referenceImage"]["sha256"] = hashlib.sha256(png).hexdigest()
    report["probes"][0]["delta"] = pack_delta(rgb, b"\xff" * len(rgb), 150, 150)
    observed = differential.observe_page(page, vector, png, report["probes"])[0]
    assert observed["status"] == "OBSERVED"
    assert observed["paintFootprints"] == observed["changedFootprints"]


def test_nonuniform_scale_and_shear_use_normal_projection(sample):
    _, vector, _, _ = fixture(sample)
    segment = vector["strokes"]["segments"][0]
    segment["strokeMatrix"] = [2, 3, 0, 1, 0, 0]
    projection = differential._projection(segment, vector)
    assert projection["width"] == pytest.approx(.3325 * 2 * 150 / 72)
    assert projection["sourcePerPixel"] == pytest.approx(.48)


@pytest.mark.parametrize("change", ["diagonal", "boundary", "too-wide", "budget"])
def test_unverifiable_geometry_does_not_gain_differential_approval(sample, monkeypatch, change):
    page, vector, png, report = fixture(sample)
    segment = vector["strokes"]["segments"][0]
    if change == "diagonal":
        segment["to"][0] += 1
    elif change == "boundary":
        segment["from"][1] += 50
    elif change == "too-wide":
        segment["width"] = 20
    else:
        monkeypatch.setattr(differential, "MAX_SAMPLES", 1)
    with pytest.raises(ValueError):
        differential.observe_page(page, vector, png, report["probes"])


def test_only_fresh_complete_differential_supplements_unsampleable_paint(sample):
    page, vector, png, report = fixture(sample)
    values = differential.observe_page(page, vector, png, report["probes"])
    segment = {"segmentIndex": 0, "status": "UNVERIFIABLE",
               "reason": "NO_INTERIOR_PAINT_SAMPLES", "inkCoverage": None}
    before = deepcopy(segment)
    assert differential.raster_ink_observed(segment, values)
    assert segment == before  # Retain the original diagnostic, including its reason.
    assert not differential.raster_ink_observed(segment, [])
    assert not differential.raster_ink_observed(segment, values * 2)
    values[0]["segmentIndex"] = 1
    assert not differential.raster_ink_observed(segment, values)


@pytest.mark.parametrize("status,reason,coverage", [
    ("SAMPLED", None, 0), ("SAMPLED", None, .99),
    ("UNVERIFIABLE", "UNSUPPORTED_DASH_CAP", None),
    ("UNVERIFIABLE", "REFERENCE_EDGE_CLIPPED", None),
])
def test_differential_does_not_override_failed_ink_or_unsupported_paths(
    sample, status, reason, coverage,
):
    page, vector, png, report = fixture(sample)
    values = differential.observe_page(page, vector, png, report["probes"])
    segment = {"segmentIndex": 0, "status": status, "reason": reason, "inkCoverage": coverage}
    assert not differential.raster_ink_observed(segment, values)


def test_observed_existing_ink_does_not_require_new_differential():
    assert differential.raster_ink_observed({"status": "SAMPLED", "inkCoverage": 1}, [])


@pytest.mark.parametrize("missing", [(45,), tuple(range(30, 80))])
def test_incomplete_differential_cannot_supplement_a_tiny_dash(sample, missing):
    page, vector, png, report = fixture(sample, missing=missing)
    values = differential.observe_page(page, vector, png, report["probes"])
    segment = {"segmentIndex": 0, "status": "UNVERIFIABLE",
               "reason": "NO_INTERIOR_PAINT_SAMPLES", "inkCoverage": None}
    assert not differential.raster_ink_observed(segment, values)


@pytest.mark.parametrize("missing_component", [False, True])
def test_subpixel_components_do_not_require_every_fractional_edge_pixel(sample, missing_component):
    page, vector, _, report = fixture(sample)
    vector["strokes"]["segments"][0]["dash"] = [.3325, .3325]
    # Independently place one contributing pixel at each subpixel dash center.
    # Fractional slivers in adjacent pixels may quantize to no visible change.
    centers = [int(30 + (i * .665 + .3325 / 2) / .48) for i in range(36)]
    with Image.new("RGB", (150, 150), "white") as image:
        for index, y in enumerate(centers):
            if not missing_component or index != 15:
                image.putpixel((20, y), (64, 64, 64))
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")
        png, rgb = encoded.getvalue(), image.tobytes()
    page["referenceImage"]["sha256"] = hashlib.sha256(png).hexdigest()
    report["probes"][0]["delta"] = pack_delta(rgb, b"\xff" * len(rgb), 150, 150)
    values = differential.observe_page(page, vector, png, report["probes"])
    observation = values[0]
    assert observation["status"] == "UNVERIFIED"  # Original footprint diagnostic remains.
    assert observation["paintFootprints"] > observation["changedFootprints"]
    components = observation["subpixelComponents"]
    assert 0 < components["maxPaintComponentPx"] < 1
    assert components["paintComponents"] > 30
    assert components["status"] == ("UNVERIFIED" if missing_component else "OBSERVED")
    segment = {"segmentIndex": 0, "status": "UNVERIFIABLE",
               "reason": "NO_INTERIOR_PAINT_SAMPLES", "inkCoverage": None}
    assert differential.raster_ink_observed(segment, values) is not missing_component


@pytest.mark.parametrize("begin,end,expected", [
    (0, 10, [(0, 3), (5, 8)]), (1, 10, [(5, 8)]), (-5, 3, [(-5, -2), (0, 3)]),
    (3, 5, []), (3, 3, []), (3, 8, [(5, 8)]),
])
def test_complete_paint_components_keep_phase_and_exclude_partial_endpoints(begin, end, expected):
    assert differential.paint_components([3, 5], [True, False], begin, end) == expected


def test_component_iteration_is_bounded_and_long_dashes_keep_old_evidence(sample, monkeypatch):
    with pytest.raises(ValueError, match="component budget"):
        differential.paint_components([.00001, .00002], [True, False], 0, 100)
    page, vector, png, report = fixture(sample)
    vector["strokes"]["segments"][0]["dash"] = [2, 2]
    values = differential.observe_page(page, vector, png, report["probes"])
    assert values[0]["subpixelComponents"] is None


@pytest.mark.parametrize("proof", ["valid", "missing", "wrong-id", "partial-ink", "low-contrast"])
def test_crossing_requires_exact_solid_identity_and_independent_ink_evidence(sample, proof):
    page, vector, png, report = fixture(sample, missing=(45,))
    crossing = paths([((5, 45), (40, 45))])["segments"][0]
    vector["strokes"]["segments"].append(crossing)
    with Image.open(io.BytesIO(png)) as original, Image.new("RGB", (150, 150), "white") as removed:
        for x in range(5, 41):
            original.putpixel((x, 45), (0, 0, 0))
            removed.putpixel((x, 45), (0, 0, 0))
        encoded = io.BytesIO()
        original.save(encoded, format="PNG")
        png = encoded.getvalue()
        delta = pack_delta(original.tobytes(), removed.tobytes(), 150, 150)
    page["referenceImage"]["sha256"] = hashlib.sha256(png).hexdigest()
    report["probes"][0]["delta"] = delta
    values = differential.observe_page(page, vector, png, report["probes"])
    component = values[0]["subpixelComponents"]
    assert component["status"] == "UNVERIFIED"  # No causal claim about hidden contributions.
    assert component["intersectionCandidates"]
    assert all(c["candidateSegmentIndices"] == [1] for c in component["intersectionCandidates"])
    solid = {"segmentIndex": 1, "status": "SAMPLED", "inkCoverage": 1, "contrastCoverage": 1}
    if proof == "wrong-id":
        solid["segmentIndex"] = 2
    elif proof == "partial-ink":
        solid["inkCoverage"] = .99
    elif proof == "low-contrast":
        solid["contrastCoverage"] = .99
    segment = {"segmentIndex": 0, "status": "UNVERIFIABLE",
               "reason": "NO_INTERIOR_PAINT_SAMPLES", "inkCoverage": None}
    samples = [] if proof == "missing" else [solid]
    assert differential.raster_ink_observed(segment, values, samples) is (proof == "valid")
