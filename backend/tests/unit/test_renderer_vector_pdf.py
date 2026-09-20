"""Full synthetic glyph validation precedes vector parsing in these fixtures."""
import hashlib
from copy import deepcopy

import pytest
from pypdf.generic import ArrayObject, FloatObject, NameObject, NumberObject

from app.services import renderer_vector_pdf as vectors
from tests.unit.test_renderer_glyph_pdf import fixture_pdf, validate


def text_and_path():
    return b"BT /F1 12 Tf <010203> Tj ET 0.5 w 10 20 m 30 20 l S"


def inspect(pdf):
    glyphs = validate(pdf)
    return vectors.inspect_vectors(pdf, hashlib.sha256(pdf).hexdigest(), glyphs)


def test_all_pages_have_explicit_boxes_units_and_same_content_identity():
    pdf = fixture_pdf([text_and_path(), b"BT /F1 12 Tf <01> Tj ET"])
    result = inspect(pdf)
    assert result["pageCount"] == 2 and result["segmentCount"] == 1
    assert [page["renderedPage"] for page in result["pages"]] == [1, 2]
    assert result["pages"][1]["strokes"]["segments"] == []
    for page in result["pages"]:
        assert page["mediaBox"] == page["cropBox"] == page["effectiveBox"] == [0, 0, 200, 300]
        assert page["rotation"] == 0 and page["userUnit"] == 1
    assert result["productionQualified"] is False and result["visibilityVerified"] is False


def test_nonzero_origins_and_page_rotation_are_recorded_not_silently_normalized_away():
    def mutate(writer, pages, font):
        pages[0][NameObject("/MediaBox")] = ArrayObject([
            NumberObject(v) for v in [-10, -20, 190, 280]])
        pages[0][NameObject("/CropBox")] = ArrayObject([
            NumberObject(v) for v in [0, 0, 100, 200]])
        pages[0][NameObject("/UserUnit")] = NumberObject(2)
        pages[0][NameObject("/Rotate")] = NumberObject(-90)

    result = inspect(fixture_pdf([text_and_path()], mutate=mutate))
    page = result["pages"][0]
    assert page["mediaBox"] == [-10, -20, 190, 280]
    assert page["effectiveBox"] == [0, 0, 100, 200]
    assert page["rotation"] == 270 and page["userUnit"] == 2
    assert page["strokes"]["segments"][0]["from"] == [10, 20]


@pytest.mark.parametrize("change", ["input", "pdf_sha", "page_count", "content", "operations"])
def test_glyph_and_vector_input_linkage_cannot_be_reused_for_other_content(change):
    pdf = fixture_pdf([text_and_path()])
    sha = hashlib.sha256(pdf).hexdigest()
    glyphs = deepcopy(validate(pdf))
    if change == "input":
        pdf += b"changed"
    elif change == "pdf_sha":
        glyphs["pdfSha256"] = "0" * 64
    elif change == "page_count":
        glyphs["pageCount"] += 1
    elif change == "content":
        glyphs["pages"][0]["contentSha256"] = "0" * 64
    else:
        glyphs["pages"][0]["contentOperations"] += 1
    with pytest.raises(ValueError):
        vectors.inspect_vectors(pdf, sha, glyphs)


@pytest.mark.parametrize("key,value", [
    ("/Rotate", NumberObject(45)), ("/Rotate", FloatObject(90.5)),
    ("/UserUnit", NumberObject(0)), ("/UserUnit", NumberObject(75001)),
    ("/UserUnit", NumberObject(100)),
    ("/CropBox", ArrayObject([NumberObject(v) for v in [400, 400, 500, 500]])),
])
def test_invalid_or_oversized_page_geometry_is_not_a_coordinate_measurement(key, value):
    def mutate(writer, pages, font):
        pages[0][NameObject(key)] = value

    with pytest.raises(ValueError):
        inspect(fixture_pdf([text_and_path()], mutate=mutate))


def test_unsupported_graphics_are_not_a_partial_stroke_inventory():
    # Glyph validation may accept graphics operators irrelevant to text; the
    # narrower stroke decoder must reject ones it does not interpret itself.
    pdf = fixture_pdf([text_and_path() + b" /DeviceRGB CS"])
    with pytest.raises(ValueError, match="Unsupported PDF stroke"):
        inspect(pdf)


def test_document_operation_budget_applies_across_pages(monkeypatch):
    pdf = fixture_pdf([text_and_path(), text_and_path()])
    glyphs = validate(pdf)
    monkeypatch.setattr(vectors, "MAX_OPERATIONS", glyphs["pages"][0]["contentOperations"])
    with pytest.raises(ValueError, match="operation count"):
        vectors.inspect_vectors(pdf, hashlib.sha256(pdf).hexdigest(), glyphs)


def program(stream):
    return inspect(fixture_pdf([stream]))["pages"][0]["textProgram"]


def test_text_program_can_compare_borders_without_ignoring_text_placement():
    base = b"BT /F1 12 Tf 1 0 0 1 20 30 Tm <010203> Tj ET"
    first = program(base)
    second = program(base + b" 1 0 0 RG 0.1 w 10 20 m 30 20 l S")
    assert first == second
    assert first["status"] == "RECORDED"
    assert first["visibilityVerified"] is False
    moved = program(base.replace(b"20 30 Tm", b"21 30 Tm"))
    assert moved["programSha256"] != first["programSha256"]


def test_only_empty_graphics_scopes_after_removing_pure_strokes_can_be_elided():
    base = b"BT /F1 12 Tf 1 0 0 1 20 30 Tm <010203> Tj ET"
    stroke = b"1 0 0 RG 0.1 w 10 20 m 30 20 l S"
    assert program(base) == program(b"q q " + stroke + b" Q Q " + base)
    clipped = b"q 0 0 100 100 re W n " + base + b" Q"
    assert program(base)["programSha256"] != program(clipped)["programSha256"]
    transformed = b"q 1 0 0 1 3 0 cm " + base + b" Q"
    assert program(base)["programSha256"] != program(transformed)["programSha256"]


@pytest.mark.parametrize("change", [
    b"1 0 0 1 3 0 cm", b"0.5 g", b"BT /F1 9 Tf <01> Tj ET",
])
def test_text_trace_retains_transforms_fill_and_text(change):
    base = text_and_path()
    assert program(base)["programSha256"] != program(change + b" " + base)["programSha256"]


def test_text_program_retains_clipping_path_geometry_and_winding_rule():
    value = program(b"0 0 100 100 re W n " + text_and_path())
    assert value["status"] == "RECORDED"
    changed = program(b"0 0 101 100 re W n " + text_and_path())
    winding = program(b"0 0 100 100 re W* n " + text_and_path())
    assert value["programSha256"] != changed["programSha256"]
    assert value["programSha256"] != winding["programSha256"]
    assert value["visibilityVerified"] is False


@pytest.mark.parametrize("paint", [b"f", b"f*", b"B", b"B*", b"b", b"b*"])
def test_filled_or_combined_paths_are_not_ignored_as_measurement_borders(paint):
    a = program(b"0 0 10 10 re " + paint + b" " + text_and_path())
    b = program(b"0 0 11 10 re " + paint + b" " + text_and_path())
    assert a["programSha256"] != b["programSha256"]


def test_clipping_path_construction_keeps_its_original_transform_order():
    a = program(b"1 0 0 1 3 0 cm 0 0 100 100 re W n " + text_and_path())
    b = program(b"0 0 100 100 re W n 1 0 0 1 3 0 cm " + text_and_path())
    assert a["programSha256"] != b["programSha256"]


def test_identical_text_bytes_with_other_font_widths_are_not_equivalent_programs():
    base = fixture_pdf([text_and_path()])

    def mutate(writer, pages, font):
        widths = font[NameObject("/Widths")]
        widths[0] = NumberObject(int(widths[0]) + 1)

    changed = fixture_pdf([text_and_path()], mutate=mutate)
    a = inspect(base)["pages"][0]["textProgram"]
    b = inspect(changed)["pages"][0]["textProgram"]
    assert a["programSha256"] == b["programSha256"]
    assert a["fontResourcesSha256"] != b["fontResourcesSha256"]


@pytest.mark.parametrize("state", [b"1 Tc", b"2 Tw", b"90 Tz", b"3 TL", b"4 Ts"])
def test_text_spacing_parameters_are_not_lost_from_transcript(state):
    prefix, suffix = b"BT /F1 12 Tf ", b" <010203> Tj ET"
    assert program(prefix + suffix) != program(prefix + state + suffix)


def test_text_array_numeric_spacing_is_not_confused_with_the_same_glyph_sequence():
    a = program(b"BT /F1 12 Tf [<01> 2 <02>] TJ ET")
    b = program(b"BT /F1 12 Tf [<01> 3 <02>] TJ ET")
    assert a["programSha256"] != b["programSha256"]
