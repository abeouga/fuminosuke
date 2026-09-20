import io
from copy import deepcopy

import pytest
from PIL import Image

from app.services import prepared_reference_geometry as geometry
from app.services.renderer_stroke_probe_pixels import DIFFERENTIAL_POLICY, pack_delta
from tests.unit.test_prepared_reference_geometry import reference, sample

reference = reference
sample = sample


def attach(sample):
    prepared, _, evidence = sample
    vector = evidence.original_vectors["vectors"]["pages"][0]
    for segment in vector["strokes"]["segments"]:
        segment.update(dash=[.1, .1], width=.3325)
    with Image.open(io.BytesIO(prepared.images[0])) as image:
        rgb = image.convert("RGB").tobytes()
    pdf_sha = prepared.report["pdfSha256"]
    operation = vector["strokes"]["segments"][0]["operation"]
    evidence.original_vectors["strokeProbes"] = {
        "policy": DIFFERENTIAL_POLICY, "sourcePdfSha256": pdf_sha, "dpi": 150,
        "rasterizer": "poppler-pdftoppm", "visibilityVerified": False,
        "productionQualified": False,
        "probes": [{"renderedPage": 1, "operation": operation, "status": "MEASURED",
                    "mutation": {
                        "policy": "wsl-single-stroke-suppression-v1", "sourcePdfSha256": pdf_sha,
                        "probePdfSha256": "c" * 64, "sourceContentSha256": vector["contentSha256"],
                        "probeContentSha256": "d" * 64, "renderedPage": 1, "operation": operation,
                        "originalOperator": "S", "replacementOperator": "n", "byteOffset": 20,
                        "changedDecodedBytes": 1, "segmentCount": 4,
                        "visibilityVerified": False, "productionQualified": False,
                    }, "delta": pack_delta(rgb, b"\xff" * len(rgb), 150, 150)}],
    }
    return sample


def test_complete_causal_pixels_supplement_missing_interiors_without_qualifying(sample):
    sample = attach(sample)
    before = deepcopy(sample)
    observed = geometry.assess_geometry(*sample)[0]
    assert sample == before
    assert observed["codes"] == [] and observed["status"] == "MEASURED"
    assert len(observed["strokeDifferential"]) == 4
    assert all(s["status"] == "OBSERVED" for s in observed["strokeDifferential"])
    assert all(s["reason"] == "NO_INTERIOR_PAINT_SAMPLES"
               for s in observed["rasterObservation"]["segments"])
    assert not any(observed[key] for key in
                   ("visibilityVerified", "productionQualified", "thresholdQualified"))


def test_saved_geometry_keeps_whole_segment_observations_and_recomputes_field_scope(sample):
    sample = attach(sample)
    before = deepcopy(sample)
    observed = geometry.assess_geometry(*sample)[0]
    scope = observed["fieldRasterObservation"]
    assert scope["policy"] == "wsl-bound-field-edge-ink-v1"
    assert scope["allRequiredInkObserved"] is True
    assert scope["observations"] == []  # Whole-segment evidence already suffices in this fixture.
    assert scope["visibilityVerified"] is scope["productionQualified"] is False
    assert all(s["reason"] == "NO_INTERIOR_PAINT_SAMPLES"
               for s in observed["rasterObservation"]["segments"])
    assert sample == before


@pytest.mark.parametrize("change", ["source", "content", "pixels", "count", "policy"])
def test_stored_differential_is_recomputed_and_tampering_is_rejected(sample, change):
    sample = attach(sample)
    report = sample[2].original_vectors["strokeProbes"]
    if change == "source":
        report["sourcePdfSha256"] = "0" * 64
    elif change == "content":
        report["probes"][0]["mutation"]["sourceContentSha256"] = "0" * 64
    elif change == "pixels":
        report["probes"][0]["delta"]["sourcePixelSha256"] = "0" * 64
    elif change == "count":
        report["probes"] = []
    else:
        report["visibilityVerified"] = True
    with pytest.raises(ValueError):
        geometry.assess_geometry(*sample)


def test_missing_differential_does_not_retrofit_success(sample):
    sample = attach(sample)
    sample[2].original_vectors.pop("strokeProbes")
    observed = geometry.assess_geometry(*sample)[0]
    assert observed["status"] == "FAIL"
    assert "REFERENCE_RASTER_INK_UNVERIFIED" in observed["codes"]


def test_zero_delta_does_not_rescue_an_occluded_stroke(sample):
    sample = attach(sample)
    with Image.open(io.BytesIO(sample[0].images[0])) as image:
        rgb = image.convert("RGB").tobytes()
    sample[2].original_vectors["strokeProbes"]["probes"][0]["delta"] = pack_delta(
        rgb, rgb, 150, 150)
    observed = geometry.assess_geometry(*sample)[0]
    assert observed["status"] == "FAIL"
    assert "REFERENCE_RASTER_INK_UNVERIFIED" in observed["codes"]
