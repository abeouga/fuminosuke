"""Supplemental model samples never replace existing vector/raster results."""
import hashlib
import io
from copy import deepcopy

import pytest
from PIL import Image, ImageDraw

from app.services import reference_splash_raster as splash
from tests.unit.test_reference_splash_raster import vector_line
from tests.unit.test_renderer_splash_calibration import evidence, pixels


def sample(*, vertical=True, reverse=False, painted=21, background="white"):
    line, vector = vector_line(20.7, vertical=vertical, reverse=reverse)
    with Image.new("RGB", (150, 150), background) as image:
        if painted is not None:
            endpoints = (painted, 20, painted, 80) if vertical else (20, painted, 80, painted)
            ImageDraw.Draw(image).line(endpoints, fill="red", width=1)
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")
    content = encoded.getvalue()
    sha = hashlib.sha256(content).hexdigest()
    page = {"width": 150, "height": 150, "referenceImage": {"sha256": sha}}
    observation = {"referenceImageSha256": sha,
        "fields": [{"edges": [{"segmentIndices": [0]}]}],
        "rasterObservation": {"segments": [{"segmentIndex": 0, "inkCoverage": 0}]}}
    return page, vector, content, observation, evidence(pixels())


@pytest.mark.parametrize("vertical,reverse", [(True, False), (True, True),
                                            (False, False), (False, True)])
def test_only_predicted_pixel_is_observed_and_original_diagnostics_are_unchanged(vertical, reverse):
    args = sample(vertical=vertical, reverse=reverse)
    before = deepcopy(args)
    result = splash.observe_adjusted_ink(*args)
    assert args == before
    line = result["segments"][0]
    assert line["normalPixels"] == [21] and line["endpointGuardPx"] == 2
    assert line["paintSamples"] == result["sampleCount"] == 56
    assert line["inkCoverage"] == line["contrastCoverage"] == 1
    assert all(result[k] is False for k in ("visibilityVerified", "productionQualified",
        "objectIdentityVerified", "thresholdQualified"))


@pytest.mark.parametrize("painted", [None, 19, 20, 22, 23])
def test_missing_or_displaced_ink_is_not_found_by_searching_nearby_pixels(painted):
    result = splash.observe_adjusted_ink(*sample(painted=painted))
    assert result["segments"][0]["inkCoverage"] == 0


def test_dark_background_is_not_claimed_as_contrast_or_object_identity():
    result = splash.observe_adjusted_ink(*sample(painted=None, background="black"))
    assert result["segments"][0]["inkCoverage"] == 1
    assert result["segments"][0]["contrastCoverage"] == 0
    assert result["objectIdentityVerified"] is False


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_nonmatching_raw_calibration_never_enables_model(missing):
    args = list(sample())
    args[-1] = None if missing else evidence(pixels(shift=1))
    result = splash.observe_adjusted_ink(*args)
    assert result["segments"] == [] and result["sampleCount"] == 0


@pytest.mark.parametrize("change", ["image", "observation", "dimensions", "calibration"])
def test_changed_inputs_are_rejected(change):
    args = list(sample())
    if change == "image":
        args[0]["referenceImage"]["sha256"] = "0" * 64
    elif change == "observation":
        args[3]["referenceImageSha256"] = "0" * 64
    elif change == "dimensions":
        args[0]["width"] += 1
    else:
        args[4]["raster"]["probePixelSha256"] = "0" * 64
    with pytest.raises(ValueError):
        splash.observe_adjusted_ink(*args)


def test_shared_edges_are_sampled_once_and_sample_budget_is_enforced(monkeypatch):
    args = list(sample())
    args[3]["fields"] *= 100
    assert splash.observe_adjusted_ink(*args)["sampleCount"] == 56
    monkeypatch.setattr(splash, "MAX_SAMPLES", 55)
    with pytest.raises(ValueError, match="sample budget"):
        splash.observe_adjusted_ink(*args)


def test_unsupported_compound_path_has_no_adjusted_observation():
    args = list(sample())
    args[1]["strokes"]["segments"] *= 2
    assert splash.observe_adjusted_ink(*args)["segments"] == []
