import hashlib
import io
from copy import deepcopy

import pytest
from PIL import Image, ImageDraw

from app.services import reference_vector_raster as raster
from tests.unit.test_reference_vector_measurement import sample

sample = sample


def png_sample(sample, paint=None, *, mode="L"):
    page, vector, _ = deepcopy(sample)
    with Image.new(mode, (150, 150), "white") as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 30, 60, 80), outline="black", width=2)
        if paint:
            paint(draw)
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")
    content = encoded.getvalue()
    page["referenceImage"]["sha256"] = hashlib.sha256(content).hexdigest()
    return page, vector, content


def first_segment(result):
    return result["rasterObservation"]["segments"][0]


def test_exact_reference_ink_is_observed_without_changing_geometry_or_qualification(sample):
    values = png_sample(sample)
    before = deepcopy(values)
    result = raster.observe_raster(*values)
    assert values == before
    observation = result["rasterObservation"]
    assert len(observation["segments"]) == 4
    assert all(line["status"] == "SAMPLED" for line in observation["segments"])
    assert all(line["contrastCoverage"] == 1 for line in observation["segments"])
    assert result["fields"][0]["bindings"] == values[0]["fields"][0]["bindings"]
    assert result["fields"][0]["edges"][0]["rasterSegmentIndices"] == [0]
    assert not any(observation[k] for k in (
        "visibilityVerified", "thresholdQualified", "objectIdentityVerified",
        "productionQualified"))


@pytest.mark.parametrize("background,ink", [("white", 0), ("black", 1)])
def test_overpaint_does_not_prove_a_visible_border(sample, background, ink):
    values = png_sample(sample, lambda draw: draw.rectangle((0, 0, 149, 149), fill=background))
    result = raster.observe_raster(*values)
    assert result["fields"][0]["status"] == "OBSERVED_WITHIN_DIAGNOSTIC_BUDGET"
    assert all(line["inkCoverage"] == ink for line in result["rasterObservation"]["segments"])
    assert all(line["contrastCoverage"] == 0 for line in result["rasterObservation"]["segments"])


def test_raster_line_is_not_searched_or_shifted_back_to_the_vector(sample):
    def displaced(draw):
        draw.rectangle((0, 0, 149, 149), fill="white")
        draw.rectangle((26, 30, 66, 80), outline="black", width=2)
    result = raster.observe_raster(*png_sample(sample, displaced))
    assert first_segment(result)["inkCoverage"] == 0


@pytest.mark.parametrize("dash,ends,painted", [
    ([], [], []), ([3, 2], [3, 5], [True, False]),
    ([3], [3, 6], [True, False]), ([0, 2], [2], [False]),
    ([2, 0], [2], [True]),
    ([3, 2, 1], [3, 5, 6, 9, 11, 12], [True, False, True, False, True, False]),
])
def test_odd_and_zero_dash_elements_keep_alternation(dash, ends, painted):
    assert raster.dash_intervals({"dash": dash}) == (ends, painted)


@pytest.mark.parametrize("distance,expected", [
    (0, None), (1, True), (2.5, None), (3, None), (4, False),
    (6, True), (9, False), (11, True),
])
def test_dash_phase_and_cycle_boundaries(distance, expected):
    assert raster.dash_sample([3, 5], [True, False], distance, 0.5) is expected


def test_dashed_pixels_are_checked_only_inside_painted_intervals(sample):
    def dashed(draw):
        draw.rectangle((17, 30, 23, 80), fill="white")
        for y in (30, 50, 70):
            draw.line((20, y, 20, y + 9), fill="black", width=2)
    values = png_sample(sample, dashed)
    values[1]["strokes"]["segments"][0]["dash"] = [4.8, 4.8]  # 10px on/off at 150dpi.
    line = first_segment(raster.observe_raster(*values))
    assert line["paintSamples"] > 0 and line["gapSamples"] > 0
    assert line["inkCoverage"] == line["contrastCoverage"] == 1
    assert line["gapInkSamples"] == 0


def test_subpath_distance_is_not_reset_at_each_segment(sample):
    def dashed(draw):
        draw.rectangle((17, 30, 23, 80), fill="white")
        for y in (40, 60):
            draw.line((20, y, 20, y + 9), fill="black", width=2)
    values = png_sample(sample, dashed)
    line = values[1]["strokes"]["segments"][0]
    line["dash"] = [4.8, 4.8]
    line["subpathDistance"] = 4.8
    observation = first_segment(raster.observe_raster(*values))
    assert observation["inkCoverage"] == 1 and observation["gapInkSamples"] == 0


def test_unresolved_tiny_dashes_are_not_treated_as_a_solid_line(sample):
    values = png_sample(sample)
    values[1]["strokes"]["segments"][0]["dash"] = [0.1, 0.1]
    line = first_segment(raster.observe_raster(*values))
    assert line["status"] == "UNVERIFIABLE"
    assert line["reason"] == "NO_INTERIOR_PAINT_SAMPLES" and line["inkCoverage"] is None


def test_sheared_normal_width_uses_projection_not_vector_length(sample):
    values = png_sample(sample)
    values[1]["strokes"]["segments"][2]["strokeMatrix"] = [1, 0, 3, 2, 0, 0]
    result = raster.observe_raster(*values)
    line = result["rasterObservation"]["segments"][2]
    assert line["strokeWidthPx"] == pytest.approx(2 * 150 / 72)


def test_unsupported_dash_caps_and_transparent_images_are_not_guessed(sample):
    values = png_sample(sample)
    values[1]["strokes"]["segments"][0].update(dash=[3, 2], lineCap=1)
    assert first_segment(raster.observe_raster(*values))["reason"] == "UNSUPPORTED_DASH_CAP"
    with pytest.raises(ValueError, match="opaque reference PNG"):
        raster.observe_raster(*png_sample(sample, mode="RGBA"))


def test_changed_png_sha_and_sample_budget_fail_without_partial_results(sample, monkeypatch):
    page, vector, content = png_sample(sample)
    with pytest.raises(ValueError, match="reference image changed"):
        raster.observe_raster(page, vector, content + b"different")
    monkeypatch.setattr(raster, "MAX_SAMPLES", 1)
    with pytest.raises(ValueError, match="sample budget"):
        raster.observe_raster(page, vector, content)
