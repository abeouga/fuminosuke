"""Trusted, synthetic PDF fixtures; never parse a user's PDF in the test host."""

import hashlib
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from app.services.renderer_glyph_fonts import GlyphValidationError
from app.services.renderer_glyph_pdf import validate_pdf_glyphs
from tests.unit.test_renderer_glyph_fonts import cmap, embedded, face, font_bytes


def fixture_pdf(instructions=None, mutate=None, bad_second=False):
    writer = PdfWriter()

    def stream(data):
        value = DecodedStreamObject()
        value.set_data(data)
        return writer._add_object(value)

    def font_object(mapping):
        descriptor = DictionaryObject({
            NameObject("/Type"): NameObject("/FontDescriptor"),
            NameObject("/FontName"): NameObject("/ABCDEF+Unrelated-Form-Face"),
            NameObject("/Flags"): NumberObject(4),
            NameObject("/FontFile2"): stream(embedded()),
        })
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/TrueType"),
            NameObject("/BaseFont"): NameObject("/ABCDEF+Unrelated-Form-Face"),
            NameObject("/FontDescriptor"): writer._add_object(descriptor),
            NameObject("/FirstChar"): NumberObject(1), NameObject("/LastChar"): NumberObject(3),
            NameObject("/Widths"): ArrayObject([NumberObject(600) for _ in range(3)]),
            NameObject("/ToUnicode"): stream(mapping),
        })
        return writer._add_object(font)

    reference = font_object(cmap())
    pages = []
    for number, content in enumerate(instructions or [b"BT /F1 12 Tf <010203> Tj ET"]):
        page = writer.add_blank_page(width=200, height=300)
        selected = font_object(cmap({1: "二"})) if bad_second and number == 1 else reference
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): selected}),
        })
        page[NameObject("/Contents")] = stream(content)
        pages.append(page)
    if mutate:
        mutate(writer, pages, reference.get_object())
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def validate(pdf):
    return validate_pdf_glyphs(pdf, hashlib.sha256(pdf).hexdigest(), [face(font_bytes())])


def test_all_pages_and_all_supported_text_showing_operators_are_inventoried():
    pdf = fixture_pdf([
        b"q BT /F1 12 Tf <01> Tj ET Q",
        b"BT /F1 12 Tf [<02> 12 <03>] TJ ET",
        b"BT /F1 12 Tf <01> ' ET",
        b'BT /F1 12 Tf 0 0 <02> " ET',
    ])
    result = validate(pdf)
    assert result["pageCount"] == 4
    assert [p["renderedPage"] for p in result["pages"]] == [1, 2, 3, 4]
    assert [p["textInstructionGlyphs"] for p in result["pages"]] == [1, 2, 1, 1]
    assert result["textInstructionGlyphs"] == 5
    assert result["distinctFontCodes"] == 3
    assert len(result["fonts"]) == 1
    assert result["fonts"][0]["usedFontCodes"] == 3
    assert result["productionQualified"] is False
    assert result["visibilityAndLayoutVerified"] is False


def test_wrong_glyph_mapping_on_later_page_is_not_hidden_by_first_page_success():
    pdf = fixture_pdf([b"BT /F1 12 Tf <01> Tj ET"] * 2, bad_second=True)
    with pytest.raises(GlyphValidationError, match="glyph"):
        validate(pdf)


def test_expected_pdf_identity_is_checked_before_pdf_parser():
    with pytest.raises(GlyphValidationError, match="identity"):
        validate_pdf_glyphs(b"not a PDF", "0" * 64, [face(font_bytes())])


@pytest.mark.parametrize("instruction", [
    b"BT /F1 12 Tf <05> Tj ET", b"BT /Missing 12 Tf <01> Tj ET",
    b"BT <01> Tj ET", b"BT /F1 0 Tf <01> Tj ET", b"BT /F1 12 Tf 3 Tr <01> Tj ET",
    b"BT /F1 12 Tf [true] TJ ET", b"BT /F1 12 Tf 12 Tj ET",
    b"BT /F1 12 Tf <01> unsupported ET", b"Q BT /F1 12 Tf <01> Tj ET",
    b"q BT /F1 12 Tf <01> Tj ET", b"BT /F1 12 Tf <01> Tj",
    b"BT BT /F1 12 Tf <01> Tj ET", b"BT /F1 12 Tf <01> Tj ET ET",
    b"/Image Do", b"/Span BMC BT /F1 12 Tf <01> Tj ET EMC",
    b"BT /F1 12 Tf 1 2 Tm <01> Tj ET", b"BT /F1 12 Tf <01> <02> Tj ET",
], ids=["unmapped-code", "missing-font", "no-font", "zero-size", "invisible", "boolean-array",
        "numeric-string", "unknown-op", "extra-Q", "unclosed-q", "unclosed-BT", "nested-BT",
        "extra-ET", "xobject-op", "marked-content", "bad-matrix", "extra-string"])
def test_unsupported_or_malformed_content_cannot_be_partially_accepted(instruction):
    with pytest.raises(GlyphValidationError):
        validate(fixture_pdf([instruction]))


@pytest.mark.parametrize("resource", ["/XObject", "/Pattern", "/ExtGState", "/Properties"])
def test_nested_or_conditional_resources_require_explicit_handling(resource):
    def add(writer, pages, font):
        pages[0]["/Resources"][NameObject(resource)] = DictionaryObject({
            NameObject("/Nested"): NumberObject(1),
        })

    with pytest.raises(GlyphValidationError, match="resources"):
        validate(fixture_pdf(mutate=add))


@pytest.mark.parametrize("key,value", [
    ("/Subtype", NameObject("/Type0")), ("/Encoding", NameObject("/WinAnsiEncoding")),
    ("/FirstChar", NumberObject(-1)), ("/LastChar", NumberObject(255)),
])
def test_unsupported_font_encoding_or_range_is_rejected(key, value):
    def change(writer, pages, font):
        font[NameObject(key)] = value

    with pytest.raises(GlyphValidationError):
        validate(fixture_pdf(mutate=change))


def test_graphics_state_restores_the_selected_font():
    result = validate(fixture_pdf([b"BT /F1 12 Tf ET q BT /F1 16 Tf <01> Tj ET Q BT <02> Tj ET"]))
    assert result["textInstructionGlyphs"] == 2


def test_all_empty_text_is_not_a_glyph_validation_success():
    with pytest.raises(GlyphValidationError, match="no supported text"):
        validate(fixture_pdf([b"BT /F1 12 Tf ET"]))


@pytest.mark.parametrize("limit", [
    "MAX_PDF_BYTES", "MAX_CONTENT_BYTES", "MAX_TOTAL_CONTENT_BYTES", "MAX_EMBEDDED_FONT_BYTES",
    "MAX_GLYPH_OCCURRENCES", "MAX_FONT_OBJECTS", "MAX_OPERATIONS",
])
def test_pdf_resource_budgets_are_enforced(monkeypatch, limit):
    from app.services import renderer_glyph_pdf as subject

    monkeypatch.setattr(subject, limit, 1 if limit != "MAX_FONT_OBJECTS" else 0)
    with pytest.raises(GlyphValidationError):
        validate(fixture_pdf())


@pytest.mark.parametrize("limit,value", [("MAX_GLYPH_OCCURRENCES", 3), ("MAX_OPERATIONS", 5)])
def test_document_budgets_are_shared_across_pages(monkeypatch, limit, value):
    from app.services import renderer_glyph_pdf as subject

    monkeypatch.setattr(subject, limit, value)
    with pytest.raises(GlyphValidationError, match="document"):
        validate(fixture_pdf([b"BT /F1 12 Tf <0102> Tj ET"] * 2))


def test_empty_font_object_does_not_escape_as_unhandled_exception():
    def change(writer, pages, font):
        font.clear()

    with pytest.raises(GlyphValidationError):
        validate(fixture_pdf(mutate=change))
