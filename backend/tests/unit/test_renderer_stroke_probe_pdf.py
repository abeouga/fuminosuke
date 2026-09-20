"""Synthetic PDF mutation checks; no user's PDF is parsed in the host process."""
import hashlib
import io

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import FloatObject, NameObject

from app.services.renderer_stroke_probe_pdf import StrokeProbeDocument, _operator_offsets
from tests.unit.test_renderer_glyph_pdf import fixture_pdf, validate


def document(stream):
    pdf = fixture_pdf([stream, b"BT /F1 12 Tf <02> Tj ET"])
    return StrokeProbeDocument(pdf, validate(pdf))


@pytest.mark.parametrize("operator", [b"S", b"s"])
def test_only_one_exact_operator_byte_changes_and_source_is_immutable(operator):
    stream = (b"% S and s are not operators here\r\n"
              b"BT /F1 12 Tf <010203> Tj ET\n"
              b"0.332500000000 w [0.3325 0.3325] 0 d 10 20 m 80 20 l " + operator + b"\n")
    source = document(stream)
    operation = source.vectors["pages"][0]["strokes"]["segments"][0]["operation"]
    copied, report = source.suppress(1, operation)
    pages = PdfReader(io.BytesIO(copied), strict=True).pages
    assert pages[0].get_contents().get_data() == stream[:-2] + b"n\n"
    assert pages[1].get_contents().get_data() == source.contents[1]
    assert source.reader.pages[0].get_contents().get_data() == stream
    assert source.sha == hashlib.sha256(source.pdf).hexdigest()
    assert report["changedDecodedBytes"] == 1
    assert report["visibilityVerified"] is False and report["productionQualified"] is False
    assert report["originalOperator"] == operator.decode()
    assert validate(copied)["textInstructionGlyphs"] == 4


def test_token_offsets_do_not_select_operators_in_objects_or_comments():
    stream = b"% S\n/SomeS 12 Tf (S \\( s\\)) Tj <5373> Tj [(S) 2 <73>] TJ 1 2 m 3 4 l S"
    offsets = _operator_offsets(stream)
    assert [op for _, op in offsets] == [b"Tf", b"Tj", b"Tj", b"TJ", b"m", b"l", b"S"]
    assert offsets[-1] == (len(stream) - 1, b"S")


@pytest.mark.parametrize("page,operation", [(0, 1), (3, 1), (True, 1), (1, True), (1, 0), (1, 999)])
def test_invalid_operation_identity_is_rejected(page, operation):
    source = document(b"BT /F1 12 Tf <01> Tj ET 1 w 10 20 m 80 20 l S")
    with pytest.raises(ValueError):
        source.suppress(page, operation)


@pytest.mark.parametrize("path", [
    b"10 20 m 80 20 l B", b"10 20 m 80 20 l W S", b"0 w 10 20 m 80 20 l S",
    b"10 20 m 20 30 30 40 80 20 c 90 20 l S",
])
def test_combined_fill_clip_hairline_and_curves_are_not_silent_stroke_edits(path):
    source = document(b"BT /F1 12 Tf <01> Tj ET " + path)
    operation = len(source.reader.pages[0].get_contents().operations)
    with pytest.raises(ValueError):
        source.suppress(1, operation)


def test_suppressing_an_earlier_stroke_preserves_later_paint_operations():
    stream = b"BT /F1 12 Tf <01> Tj ET 1 w 10 20 m 80 20 l S 0 g 0 0 100 100 re f"
    source = document(stream)
    operation = source.vectors["pages"][0]["strokes"]["segments"][0]["operation"]
    copied, _ = source.suppress(1, operation)
    assert PdfReader(io.BytesIO(copied)).pages[0].get_contents().get_data() == stream.replace(
        b"l S", b"l n")


@pytest.mark.parametrize("content", [b"BI", b"10 20", b"% only comment\n10"])
def test_unsupported_token_stream_does_not_return_partial_offsets(content):
    with pytest.raises(ValueError):
        _operator_offsets(content)


def test_measurement_copy_preserves_high_precision_page_values(monkeypatch):
    from pypdf.generic import _base

    initial = document(b"BT /F1 12 Tf <01> Tj ET 1 w 10 20 m 80 20 l S")
    writer = PdfWriter()
    writer.clone_document_from_reader(initial.reader)
    writer.pages[0].mediabox.upper_right = (100.123456789123, 100.987654321987)
    writer.pages[0][NameObject("/UserUnit")] = FloatObject(1.123456789123)
    stream = io.BytesIO()
    # Generate a separate trusted fixture with precision present in Office PDFs.
    # Restore the library default before exercising the production copy writer.
    with monkeypatch.context() as context:
        context.setattr(_base, "FLOAT_WRITE_PRECISION", 18)
        writer.write(stream)
    source = StrokeProbeDocument(stream.getvalue(), validate(stream.getvalue()))
    segment = source.vectors["pages"][0]["strokes"]["segments"][0]
    copied, _ = source.suppress(1, segment["operation"])
    page = PdfReader(io.BytesIO(copied)).pages[0]
    assert list(page.mediabox) == list(source.reader.pages[0].mediabox)
    assert page["/UserUnit"] == source.reader.pages[0]["/UserUnit"]
    assert _base.FLOAT_WRITE_PRECISION == 8
