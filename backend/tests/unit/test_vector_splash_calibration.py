"""A vector helper's optional calibration is raw evidence, not an authority flag."""
from copy import deepcopy

import pytest

from app.services.renderer_vector_bridge import validate_vectors
from tests.unit.test_renderer_splash_calibration import evidence, pixels
from tests.unit.test_renderer_vector_bridge import reference, sample

reference, sample = reference, sample


@pytest.mark.parametrize("shift", [0, 1])
def test_well_formed_calibration_can_record_both_match_and_mismatch(sample, shift):
    report, renderer = deepcopy(sample)
    report["splashCalibration"] = evidence(pixels(shift=shift))
    before = deepcopy(report)
    validate_vectors(report, renderer)
    assert report == before  # No conversion of the raw report into a PASS flag.


@pytest.mark.parametrize("change", ["pixels", "fixture", "policy", "qualified", "missing"])
def test_malformed_calibration_cannot_be_silently_accepted_with_valid_vectors(sample, change):
    report, renderer = deepcopy(sample)
    calibration = evidence(pixels())
    report["splashCalibration"] = calibration
    if change == "pixels":
        calibration["raster"]["probePixelSha256"] = "0" * 64
    elif change == "fixture":
        calibration["fixtureSha256"] = "0" * 64
    elif change == "policy":
        calibration["policy"] = "unknown"
    elif change == "qualified":
        calibration["productionQualified"] = True
    else:
        calibration.pop("raster")
    with pytest.raises(ValueError):
        validate_vectors(report, renderer)
