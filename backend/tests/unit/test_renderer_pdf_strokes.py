"""Operator fixtures only: no assertion of PDF rendering or visible ink."""
import math

import pytest

from app.services import renderer_pdf_strokes as strokes


def path():
    return [([10, 20], b"m"), ([30, 20], b"l"), ([], b"S")]


def test_stroked_line_preserves_default_page_coordinates_and_uncertainty():
    result = strokes.inventory(path())
    line = result["segments"][0]
    assert line["from"] == [10, 20] and line["to"] == [30, 20]
    assert line["width"] == 1 and line["operation"] == 3
    assert result["visibilityVerified"] is False and result["productionQualified"] is False
    assert result["coordinateSpace"] == "default-page-points"


def test_matrix_order_and_nested_restore_are_not_coordinate_fitting():
    ops = [([1, 0, 0, 1, 100, 200], b"cm"), ([], b"q"),
           ([2, 0, 0, 3, 0, 0], b"cm"), *path(), ([], b"Q"), *path()]
    first, second = strokes.inventory(ops)["segments"]
    assert first["from"] == [120, 260] and first["to"] == [160, 260]
    assert second["from"] == [110, 220] and second["to"] == [130, 220]
    assert first["strokeMatrix"] == [2, 0, 0, 3, 100, 200]


def test_rotation_retains_line_and_does_not_project_it_to_an_axis():
    line = strokes.inventory([([0, 1, -1, 0, 0, 0], b"cm"), *path()])["segments"][0]
    assert line["from"] == [-20, 10] and line["to"] == [-20, 30]
    diagonal = strokes.inventory([([1, 1, 0, 1, 0, 0], b"cm"), *path()])["segments"][0]
    assert diagonal["from"] == [10, 30] and diagonal["to"] == [30, 50]


@pytest.mark.parametrize("operator", [b"S", b"s", b"B", b"B*", b"b", b"b*"])
def test_rectangle_stroke_has_exactly_four_edges(operator):
    result = strokes.inventory([([10, 20, -8, 6], b"re"), ([], operator)])
    assert [(v["from"], v["to"]) for v in result["segments"]] == [
        ([10, 20], [2, 20]), ([2, 20], [2, 26]),
        ([2, 26], [10, 26]), ([10, 26], [10, 20]),
    ]


@pytest.mark.parametrize("operator", [b"f", b"F", b"f*", b"n"])
def test_filled_or_discarded_path_is_not_stroke_evidence(operator):
    result = strokes.inventory([([0, 0, 100, 200], b"re"), ([], operator)])
    assert result["segments"] == []


def test_clip_records_are_not_claimed_as_visible_borders_and_restore_is_scoped():
    result = strokes.inventory([
        ([], b"q"), ([0, 0, 100, 200], b"re"), ([], b"W"), ([], b"n"),
        *path(), ([], b"Q"), *path(),
    ])
    assert len(result["segments"]) == 2
    assert result["segments"][0]["clippingUnverified"] is True
    assert result["segments"][1]["clippingUnverified"] is False


def test_curve_is_not_replaced_by_a_straight_chord():
    result = strokes.inventory([
        ([0, 0], b"m"), ([10, 0], b"l"), ([11, 1, 19, 2, 20, 10], b"c"),
        ([30, 10], b"l"), ([], b"S"),
    ])
    assert result["curvedStrokePaths"] == 1
    assert len(result["segments"]) == 2
    assert result["segments"][1]["from"] == [20, 10]
    assert all(line["sharesPathWithCurve"] is True for line in result["segments"])


def test_line_style_is_recorded_without_assuming_dashes_or_hairlines_are_solid():
    result = strokes.inventory([
        ([0], b"w"), ([2], b"J"), ([1], b"j"), ([4], b"M"),
        ([[3, 2], 1], b"d"), ([0.1, 0.2, 0.3], b"RG"), *path(),
    ])
    line = result["segments"][0]
    assert line["width"] == 0 and line["dash"] == [3, 2] and line["dashPhase"] == 1
    assert line["lineCap"] == 2 and line["lineJoin"] == 1 and line["miterLimit"] == 4
    assert line["colorSpace"] == "DeviceRGB" and line["color"] == [0.1, 0.2, 0.3]


def test_nonstroking_color_and_later_paint_do_not_relabel_existing_strokes():
    result = strokes.inventory([
        ([0.3], b"G"), *path(), ([1, 0, 0], b"rg"),
        ([0, 0, 100, 200], b"re"), ([], b"f"), *path(),
    ])
    assert all(line["color"] == [0.3] for line in result["segments"])
    assert result["paintOperations"] == 3 and result["visibilityVerified"] is False


def test_validated_text_operators_are_counted_but_not_stroke_evidence():
    result = strokes.inventory([([], b"BT"), ([b"text fixture"], b"Tj"), ([], b"ET"), *path()])
    assert len(result["segments"]) == 1 and result["paintOperations"] == 2


@pytest.mark.parametrize("ops", [
    [], [([], b"Q")], [([], b"q")], [([1, 2], b"l")], [([], b"h")],
    [([0, 0], b"m")], [([0, 0], b"m"), ([], b"q")],
    [([0, 0], b"m"), ([], b"BT")], [([], b"W")],
    [([1, 0, 0, 0, 0, 0], b"cm")], [([math.inf], b"w")], [([True], b"w")],
    [([-1], b"w")], [([3], b"J")], [([1.5], b"j")], [([0], b"M")],
    [([10**1000], b"w")], [([math.nan], b"w")],
    [([101], b"i")], [([[0, 0], 0], b"d")], [([[1, -1], 0], b"d")],
    [([[], -1], b"d")], [([2, 0, 0], b"RG")], [(["/Unknown"], b"ri")],
    [(["/Image1"], b"Do")], [(["/State1"], b"gs")], [([], b"unknown")],
    [([0, 0, 1, 1, 2, 2], b"c")],
])
def test_invalid_or_unhandled_operations_do_not_yield_partial_success(ops):
    with pytest.raises(ValueError):
        strokes.inventory(ops)


def test_operation_and_segment_budgets_include_discarded_paths(monkeypatch):
    monkeypatch.setattr(strokes, "MAX_OPERATIONS", 2)
    with pytest.raises(ValueError, match="operation budget"):
        strokes.inventory(path())
    monkeypatch.setattr(strokes, "MAX_OPERATIONS", 100)
    monkeypatch.setattr(strokes, "MAX_SEGMENTS", 3)
    with pytest.raises(ValueError, match="segment budget"):
        strokes.inventory([([0, 0, 1, 1], b"re"), ([], b"n")])


def test_stack_and_transformed_coordinate_budgets():
    with pytest.raises(ValueError, match="stack budget"):
        strokes.inventory([([], b"q")] * 257)
    with pytest.raises(ValueError, match="numeric"):
        strokes.inventory([([1_000_000, 0, 0, 1_000_000, 0, 0], b"cm"), *path()])


def test_dash_phase_distance_is_in_source_units_and_resets_at_each_subpath():
    result = strokes.inventory([
        ([2, 0, 0, 3, 0, 0], b"cm"), ([[3, 2], 1], b"d"),
        ([0, 0], b"m"), ([4, 0], b"l"), ([4, 3], b"l"),
        ([10, 10], b"m"), ([15, 10], b"l"), ([], b"S"),
    ])
    assert [v["subpathDistance"] for v in result["segments"]] == [0, 4, 0]
    assert [v["dashPhase"] for v in result["segments"]] == [1, 1, 1]


def test_curve_length_is_not_guessed_for_a_later_dash_phase():
    result = strokes.inventory([
        ([0, 0], b"m"), ([1, 1, 2, 3, 4, 4], b"c"), ([10, 4], b"l"),
        ([20, 20], b"m"), ([30, 20], b"l"), ([], b"S"),
    ])
    assert [v["subpathDistance"] for v in result["segments"]] == [None, 0]
