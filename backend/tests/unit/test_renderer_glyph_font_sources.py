import io

import pytest
from fontTools.ttLib import TTCollection, TTFont

from app.services.renderer_glyph_font_sources import find_source_faces
from app.services.renderer_glyph_fonts import (
    GlyphValidationError,
    compare_font_codes,
    load_source_faces,
)
from tests.unit.test_renderer_glyph_fonts import cmap, embedded, font_bytes


def test_candidate_discovery_uses_postscript_names_and_not_filename(tmp_path):
    (tmp_path / "unrelated-file.blob").write_bytes(font_bytes())
    (tmp_path / "metadata.txt").write_bytes(b"not a font")
    found = find_source_faces(tmp_path, {"Unrelated-Form-Face"})
    assert len(found) == 1
    assert found[0].file == str(tmp_path / "unrelated-file.blob")
    assert found[0].postscript_name == "Unrelated-Form-Face"
    report = compare_font_codes(
        embedded(),
        "/ABCDEF+Unrelated-Form-Face",
        cmap(),
        [1, 2, 3],
        load_source_faces([found[0].source]),
    )
    assert report["usedFontCodes"] == 3


def test_name_match_alone_is_not_glyph_verification(tmp_path):
    (tmp_path / "same-name.ttf").write_bytes(font_bytes(altered=True))
    found = find_source_faces(tmp_path, {"Unrelated-Form-Face"})
    with pytest.raises(GlyphValidationError, match="glyph"):
        compare_font_codes(
            embedded(),
            "/ABCDEF+Unrelated-Form-Face",
            cmap(),
            [1],
            load_source_faces([found[0].source]),
        )


def test_collection_lookup_preserves_nonzero_face_identity(tmp_path):
    collection = TTCollection()
    collection.fonts = [
        TTFont(io.BytesIO(font_bytes("Other-Face"))),
        TTFont(io.BytesIO(font_bytes())),
    ]
    output = io.BytesIO()
    collection.save(output)
    (tmp_path / "multiple.ttc").write_bytes(output.getvalue())
    found = find_source_faces(tmp_path, {"Unrelated-Form-Face"})
    assert len(found) == 1 and found[0].source.collection_index == 1


def test_duplicate_bytes_are_deduplicated_but_different_same_name_is_ambiguous(tmp_path):
    content = font_bytes()
    (tmp_path / "first.ttf").write_bytes(content)
    (tmp_path / "second.ttf").write_bytes(content)
    assert len(find_source_faces(tmp_path, {"Unrelated-Form-Face"})) == 1
    (tmp_path / "third.ttf").write_bytes(font_bytes(altered=True))
    with pytest.raises(GlyphValidationError, match="ambiguous"):
        find_source_faces(tmp_path, {"Unrelated-Form-Face"})


@pytest.mark.parametrize("names", [set(), {"../outside"}, {"wrong-face"}])
def test_missing_or_invalid_names_do_not_trigger_a_fuzzy_fallback(tmp_path, names):
    (tmp_path / "font.ttf").write_bytes(font_bytes())
    with pytest.raises(GlyphValidationError):
        find_source_faces(tmp_path, names)


def test_nested_directory_is_rejected_before_candidate_search(tmp_path):
    (tmp_path / "font.ttf").write_bytes(font_bytes())
    (tmp_path / "nested").mkdir()
    with pytest.raises(RuntimeError, match="font directory"):
        find_source_faces(tmp_path, {"Unrelated-Form-Face"})


def test_collection_face_count_is_bounded_before_font_parse(tmp_path):
    (tmp_path / "over-limit.ttc").write_bytes(b"ttcf\x00\x01\x00\x00" + (257).to_bytes(4, "big"))
    with pytest.raises(GlyphValidationError, match="face count"):
        find_source_faces(tmp_path, {"Unrelated-Form-Face"})


def test_explicit_aliases_share_one_physical_face_without_name_fuzziness(tmp_path):
    font = TTFont(io.BytesIO(font_bytes()))
    font["name"].setName("Exact-Alternate-Name", 6, 3, 1, 0x0411)
    output = io.BytesIO()
    font.save(output)
    (tmp_path / "aliases.ttf").write_bytes(output.getvalue())
    names = {"Unrelated-Form-Face", "Exact-Alternate-Name"}
    found = find_source_faces(tmp_path, names)
    assert {item.postscript_name for item in found} == names
    assert len({(item.source.sha256, item.source.collection_index) for item in found}) == 1
    catalog = load_source_faces([item.source for item in found])
    assert set(catalog) == names
    assert catalog["Unrelated-Form-Face"] is catalog["Exact-Alternate-Name"]
    for name in names:
        result = compare_font_codes(embedded(), "/ABCDEF+" + name, cmap(), [1], catalog)
        assert result["usedFontCodes"] == 1
    with pytest.raises(GlyphValidationError, match="missing"):
        find_source_faces(tmp_path, {"Exact-Alternate"})
