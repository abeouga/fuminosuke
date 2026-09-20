"""Synthetic fonts only; no workstation font or untrusted PDF is read here."""

import hashlib
import io

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTCollection, TTFont, newTable
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

from app.services.renderer_glyph_fonts import (
    GlyphValidationError,
    SourceFace,
    compare_font_codes,
    glyph_signature,
    load_source_faces,
    parse_to_unicode,
)

HEADER = """/CIDInit /ProcSet findresource begin
12 dict begin begincmap
/CIDSystemInfo << /Registry (Example) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Any-Form-Identity def /CMapType 2 def
1 begincodespacerange <00> <FF> endcodespacerange
"""
FOOTER = "endcmap CMapName currentdict /CMap defineresource pop end end"


def cmap(mapping=None):
    mapping = mapping or {1: "一", 2: "二", 3: "𠮷"}
    body = "\n".join(f"<{code:02X}> <{char.encode('utf-16-be').hex()}>"
                     for code, char in mapping.items())
    return (HEADER + f"{len(mapping)} beginbfchar\n" + body + "\nendbfchar\n" + FOOTER).encode()


def font_bytes(name="Unrelated-Form-Face", embedded=False, altered=False):
    builder = FontBuilder(1000, isTTF=True)
    order = [".notdef", "one", "two", "compound", "space"]
    builder.setupGlyphOrder(order)
    glyphs = {}
    for key, points in {
        ".notdef": [], "space": [],
        "one": [(0, 0), (400 if altered else 500, 0), (250, 700)],
        "two": [(0, 0), (500, 0), (500, 700), (0, 700)],
    }.items():
        pen = TTGlyphPen(None)
        if points:
            pen.moveTo(points[0])
            for point in points[1:]:
                pen.lineTo(point)
            pen.closePath()
        glyphs[key] = pen.glyph()
    pen = TTGlyphPen(glyphs)
    pen.addComponent("one", (1, 0, 0, 1, 20, 30))
    glyphs["compound"] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({key: (600, 0) for key in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupCharacterMap({0x4E00: "one", 0x4E8C: "two", 0x20BB7: "compound", 32: "space"})
    builder.setupNameTable({"familyName": name, "styleName": "Regular", "psName": name})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    if embedded:
        table = newTable("cmap")
        table.tableVersion = 0
        subtable = CmapSubtable.newSubtable(0)
        subtable.platformID, subtable.platEncID, subtable.language = 1, 0, 0
        subtable.cmap = {1: "one", 2: "two", 3: "compound", 4: "space"}
        table.tables = [subtable]
        builder.font["cmap"] = table
    result = io.BytesIO()
    builder.save(result)
    return result.getvalue()


def face(data, index=0):
    return SourceFace(data=data, sha256=hashlib.sha256(data).hexdigest(), collection_index=index)


def embedded():
    return font_bytes("ABCDEF+Unrelated-Form-Face", embedded=True)


def test_arbitrary_face_name_unicode_and_composite_are_compared():
    source = face(font_bytes())
    report = compare_font_codes(embedded(), "/ABCDEF+Unrelated-Form-Face", cmap(), [1, 2, 3],
                                load_source_faces([source]))
    assert report["sourceSha256"] == source.sha256
    assert report["sourceCollectionIndex"] == 0
    assert report["usedFontCodes"] == 3
    assert report["productionQualified"] is False


def test_nonzero_collection_face_is_not_replaced_by_first_face():
    collection = TTCollection()
    collection.fonts = [TTFont(io.BytesIO(font_bytes("Other-Face"))),
                        TTFont(io.BytesIO(font_bytes()))]
    buffer = io.BytesIO()
    collection.save(buffer)
    source = face(buffer.getvalue(), 1)
    report = compare_font_codes(embedded(), "/ABCDEF+Unrelated-Form-Face", cmap(), [3],
                                load_source_faces([source]))
    assert report["sourceCollectionIndex"] == 1
    with pytest.raises(GlyphValidationError, match="source face"):
        compare_font_codes(embedded(), "/ABCDEF+Unrelated-Form-Face", cmap(), [1],
                           load_source_faces([face(buffer.getvalue(), 0)]))


@pytest.mark.parametrize("kind", ["unicode", "outline", "advance", "hint", "lookup"])
def test_changed_unicode_or_glyph_cannot_pass(kind):
    original = font_bytes()
    data, mapping = embedded(), cmap()
    if kind == "unicode":
        mapping = cmap({1: "二"})
    elif kind == "outline":
        data = font_bytes("ABCDEF+Unrelated-Form-Face", embedded=True, altered=True)
    else:
        font = TTFont(io.BytesIO(data))
        if kind == "advance":
            font["hmtx"].metrics["one"] = (601, 0)
        elif kind == "lookup":
            font["cmap"].tables[0].cmap[1] = "two"
        else:
            font["glyf"]["one"].program.fromBytecode([0])
        buffer = io.BytesIO()
        font.save(buffer)
        data = buffer.getvalue()
    with pytest.raises(GlyphValidationError, match="glyph"):
        compare_font_codes(data, "/ABCDEF+Unrelated-Form-Face", mapping, [1],
                           load_source_faces([face(original)]))


def test_source_identity_mismatch_is_rejected_before_parsing():
    with pytest.raises(GlyphValidationError, match="identity"):
        load_source_faces([SourceFace(data=b"not a font", sha256="0" * 64, collection_index=0)])


def test_same_name_different_source_is_ambiguous_but_repeated_same_face_is_not():
    source = face(font_bytes())
    assert len(load_source_faces([source, source])) == 1
    with pytest.raises(GlyphValidationError, match="ambiguous"):
        load_source_faces([source, face(font_bytes(altered=True))])


@pytest.mark.parametrize("index", [-1, 256, True, 1])
def test_invalid_face_index_is_not_silently_ignored_for_ttf(index):
    with pytest.raises(GlyphValidationError, match="index"):
        load_source_faces([face(font_bytes(), index)])


@pytest.mark.parametrize("name", ["/Unrelated-Form-Face", "/abcdef+Unrelated-Form-Face",
                                  "/ABCDE+Unrelated-Form-Face", "/ABCDEF+Missing-Face"])
def test_unproved_subset_identity_is_rejected(name):
    with pytest.raises(GlyphValidationError):
        compare_font_codes(embedded(), name, cmap(), [1], load_source_faces([face(font_bytes())]))


@pytest.mark.parametrize("used", [[], [True], [-1], [256], [5]])
def test_invalid_or_unmapped_used_code_is_rejected(used):
    with pytest.raises(GlyphValidationError):
        compare_font_codes(embedded(), "/ABCDEF+Unrelated-Form-Face", cmap(), used,
                           load_source_faces([face(font_bytes())]))


def test_unicode_parser_supports_astral_scalar_and_multiple_bounded_groups():
    assert parse_to_unicode(cmap()) == {1: 0x4E00, 2: 0x4E8C, 3: 0x20BB7}
    raw = (HEADER + "1 beginbfchar <01> <4e00> endbfchar\n"
           "1 beginbfchar <02> <4e8c> endbfchar\n" + FOOTER).encode()
    assert parse_to_unicode(raw) == {1: 0x4E00, 2: 0x4E8C}


@pytest.mark.parametrize("replacement", [
    "2 beginbfchar <01> <4e00> endbfchar",
    "2 beginbfchar <01> <4e00> <01> <4e8c> endbfchar",
    "1 beginbfchar <01> <D800> endbfchar",
    "1 beginbfchar <01> <00410042> endbfchar",
    "1 beginbfchar <0010> <4e00> endbfchar",
    "1 beginbfchar <01> <4e00> arbitrary endbfchar",
    "1 beginbfrange <01> <02> <4e00> endbfrange",
    "1 beginbfchar <01> <4e00> endbfchar /Parent usecmap",
    "101 beginbfchar <01> <4e00> endbfchar",
], ids=["wrong-count", "duplicate", "surrogate", "shaping", "wide-code", "code-injection",
        "range-unsupported", "parent-cmap", "oversized-group"])
def test_unsupported_or_ambiguous_cmap_is_not_partially_accepted(replacement):
    with pytest.raises(GlyphValidationError):
        parse_to_unicode((HEADER + replacement + "\n" + FOOTER).encode())


def test_cmap_header_and_trailing_program_are_not_ignored():
    for raw in [b"execute " + cmap(), cmap() + b" execute", cmap().replace(b"<FF>", b"<FFFF>")]:
        with pytest.raises(GlyphValidationError):
            parse_to_unicode(raw)


def test_slash_delimited_names_do_not_require_extra_whitespace():
    raw = cmap().replace(b"/CIDInit /ProcSet", b"/CIDInit/ProcSet")
    raw = raw.replace(b"/CMapName /Any", b"/CMapName/Any")
    assert parse_to_unicode(raw) == {1: 0x4E00, 2: 0x4E8C, 3: 0x20BB7}


def test_undelimited_operators_are_not_read_as_separate_operators():
    for raw in [cmap().replace(b"endbfchar\n", b"endbfchar"),
                cmap().replace(b"endcodespacerange\n", b"endcodespacerange")]:
        with pytest.raises(GlyphValidationError):
            parse_to_unicode(raw)


def test_composite_cycle_and_missing_component_are_not_skipped():
    font = TTFont(io.BytesIO(font_bytes()))
    for target in ["compound", "missing"]:
        font["glyf"]["compound"].components[0].glyphName = target
        with pytest.raises(GlyphValidationError, match="component"):
            glyph_signature(font, "compound")


def test_source_unicode_subtables_cannot_disagree():
    font = TTFont(io.BytesIO(font_bytes()))
    candidate = next(table for table in font["cmap"].tables if table.format == 4)
    candidate.cmap = {**candidate.cmap, 0x4E00: "two"}
    output = io.BytesIO()
    font.save(output)
    with pytest.raises(GlyphValidationError, match="ambiguous"):
        load_source_faces([face(output.getvalue())])


def test_changed_global_hint_program_is_not_ignored():
    from fontTools.ttLib.tables.ttProgram import Program

    font = TTFont(io.BytesIO(embedded()))
    font["prep"] = newTable("prep")
    font["prep"].program = Program()
    font["prep"].program.fromBytecode([0])
    output = io.BytesIO()
    font.save(output)
    with pytest.raises(GlyphValidationError, match="global glyph hints"):
        compare_font_codes(output.getvalue(), "/ABCDEF+Unrelated-Form-Face", cmap(), [1],
                           load_source_faces([face(font_bytes())]))


@pytest.mark.parametrize("limit", ["MAX_COMPONENTS", "MAX_POINTS"])
def test_component_expansion_is_bounded(monkeypatch, limit):
    from app.services import renderer_glyph_fonts as subject

    monkeypatch.setattr(subject, limit, 1)
    with pytest.raises(GlyphValidationError, match="component"):
        glyph_signature(TTFont(io.BytesIO(font_bytes())), "compound")


@pytest.mark.parametrize("limit", ["MAX_FONT_BYTES", "MAX_SOURCE_BYTES", "MAX_CMAP_BYTES"])
def test_byte_budgets_are_enforced(monkeypatch, limit):
    from app.services import renderer_glyph_fonts as subject

    monkeypatch.setattr(subject, limit, 1)
    with pytest.raises(GlyphValidationError):
        if limit == "MAX_CMAP_BYTES":
            parse_to_unicode(cmap())
        else:
            load_source_faces([face(font_bytes())])
