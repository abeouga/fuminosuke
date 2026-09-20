from xml.etree import ElementTree as ET

import pytest

from app.services import renderer_wsl_worker as worker

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def styles(*fonts):
    root = ET.Element("{" + NS + "}styleSheet")
    collection = ET.SubElement(root, "{" + NS + "}fonts")
    for name, bold, italic in fonts:
        font = ET.SubElement(collection, "{" + NS + "}font")
        ET.SubElement(font, "{" + NS + "}name", val=name)
        for tag, value in (("b", bold), ("i", italic)):
            if value is not None:
                ET.SubElement(font, "{" + NS + "}" + tag, **({} if value == "" else {"val": value}))
    return root


def test_same_family_preserves_four_faces_and_deduplicates_identical_requests():
    assert worker.declared_font_requests(styles(
        ("Calibri", None, None), ("Calibri", "", None),
        ("Calibri", None, "true"), ("Calibri", "1", "1"),
        ("Calibri", "true", "true"), ("Calibri", "0", "false"),
    )) == [
        ("Calibri", "bold", "italic"), ("Calibri", "bold", "roman"),
        ("Calibri", "regular", "italic"), ("Calibri", "regular", "roman"),
    ]


@pytest.mark.parametrize("family,escaped", [
    ("Example-Family", r"Example\-Family"),
    ("Example:Family", r"Example\:Family"),
    ("Example,Family", r"Example\,Family"),
    (r"Example\Family", r"Example\\Family"),
    ("\uff2d\uff33 ゴシック", "\uff2d\uff33 ゴシック"),
])
def test_family_is_a_literal_not_an_additional_fontconfig_property(family, escaped):
    assert worker.font_pattern(family, "bold", "italic") == escaped + ":weight=bold:slant=italic"


@pytest.mark.parametrize("family,bold,italic", [
    ("", None, None), ("x" * 257, None, None), ("bad\tname", None, None),
    ("Calibri", "yes", None), ("Calibri", None, "2"),
])
def test_invalid_font_declarations_are_not_silently_normalized(family, bold, italic):
    with pytest.raises(RuntimeError):
        worker.declared_font_requests(styles((family, bold, italic)))


def test_missing_or_duplicate_name_is_rejected():
    for duplicate in (False, True):
        root = styles(("Calibri", None, None))
        font = root.find("{" + NS + "}fonts")[0]
        if duplicate:
            ET.SubElement(font, "{" + NS + "}name", val="Arial")
        else:
            font.remove(font[0])
        with pytest.raises(RuntimeError):
            worker.declared_font_requests(root)


def test_native_resolution_budget_counts_styles_not_just_families():
    root = styles(*[(f"Font{i}", "true" if i % 2 else None, None) for i in range(129)])
    with pytest.raises(RuntimeError):
        worker.declared_font_requests(root)
