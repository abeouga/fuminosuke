"""Same field/vector/PNG across the raw and rounded-pixel observations."""
from copy import deepcopy

import pytest

from app.services import prepared_reference_geometry as geometry
from tests.unit.test_prepared_reference_geometry import (
    EDGES,
    bind_images,
    image_bytes,
    reference,
    sample,
    vector_report,
)
from tests.unit.test_renderer_splash_calibration import evidence, pixels

reference, sample = reference, sample


def attach(sample, *, ink=True):
    prepared, plan, report = sample
    edges = list(EDGES[1:])
    if ink:
        edges.append(((21, 30), (21, 80)))
    prepared.images = [image_bytes(edges)]
    bind_images(prepared, plan)
    report.original_vectors = vector_report(prepared)
    strokes = report.original_vectors["vectors"]["pages"][0]["strokes"]
    line = strokes["segments"][0]
    line.update(width=.2 * .48, operation=32)
    line["from"][0] = line["to"][0] = 20.62 * .48
    strokes["paintOperations"] = 2
    report.original_vectors["splashCalibration"] = evidence(pixels())
    return sample


def test_calibrated_samples_supplement_only_the_wrong_pixel_observation(sample):
    sample = attach(sample)
    before = deepcopy(sample)
    result = geometry.assess_geometry(*sample)[0]
    assert sample == before
    assert result["status"] == "MEASURED" and result["codes"] == []
    assert result["rasterObservation"]["segments"][0]["inkCoverage"] == 0
    adjusted = result["splashRasterObservation"]
    assert adjusted["calibration"]["status"] == "OBSERVED"
    assert adjusted["segments"][0]["normalPixels"] == [21]
    assert adjusted["segments"][0]["inkCoverage"] == 1
    assert result["fieldRasterObservation"]["adjustedSegmentIndices"] == [0]
    assert .61 < result["maxErrorPx"] < .63  # No geometry fitted to the raster.
    assert result["fields"][0]["bindings"] == before[1]["pages"][0]["fields"][0]["bindings"]
    assert result["visibilityVerified"] is result["productionQualified"] is False


@pytest.mark.parametrize("failure", ["missing", "mismatch", "ink"])
def test_calibration_or_real_ink_failure_keeps_geometry_unverified(sample, failure):
    sample = attach(sample, ink=failure != "ink")
    if failure == "missing":
        sample[2].original_vectors.pop("splashCalibration")
    elif failure == "mismatch":
        sample[2].original_vectors["splashCalibration"] = evidence(pixels(shift=1))
    result = geometry.assess_geometry(*sample)[0]
    assert result["status"] == "FAIL"
    assert "REFERENCE_RASTER_INK_UNVERIFIED" in result["codes"]
    assert result["fieldRasterObservation"]["adjustedSegmentIndices"] == []


def test_changed_returned_summary_is_not_used_when_recomputing(sample):
    sample = attach(sample, ink=False)
    result = geometry.assess_geometry(*sample)[0]
    expected = deepcopy(result)
    result["splashRasterObservation"]["segments"][0]["inkCoverage"] = 1
    result["fieldRasterObservation"]["allRequiredInkObserved"] = True
    assert geometry.assess_geometry(*sample)[0] == expected
