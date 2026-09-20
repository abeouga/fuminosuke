from copy import deepcopy
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
import pytest
from PIL import Image

from app.services.preserving_print_workbook import MAIN, REL
from scripts.inspect_border_probe import compare_raster, probe_copy

S = "{" + MAIN + "}"


def fixture():
    parts = {
        "xl/workbook.xml": (
            f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>'
            '<sheet name="Form" sheetId="1" r:id="r1"/>'
            "</sheets></workbook>"
        ).encode(),
        "xl/_rels/workbook.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="r1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ).encode(),
        "xl/worksheets/sheet1.xml": (
            f'<worksheet xmlns="{MAIN}"><sheetData><row r="1" ht="20" customHeight="1">'
            '<c r="A1" s="0" t="inlineStr"><is><t>Keep</t></is></c><c r="B1" s="0"/>'
            '</row><row r="2"><c r="A2" s="0"/><c r="B2" s="0"/></row></sheetData>'
            '<mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells>'
            '<pageSetup scale="69"/></worksheet>'
        ).encode(),
        "xl/styles.xml": (
            f'<styleSheet xmlns="{MAIN}"><fonts count="1"><font><sz val="11"/></font></fonts>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/>'
            '</border></borders>'
            '<cellXfs count="1"><xf borderId="0" fontId="0" fillId="0" numFmtId="0">'
            '<alignment wrapText="1"/></xf></cellXfs></styleSheet>'
        ).encode(),
    }
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return result.getvalue(), parts


def selection():
    return [{"cellId": "field-any", "sourceRange": "A1:B1", "sides": ["top", "right"]}]


def test_probe_preserves_all_other_parts_values_dimensions_merges_and_existing_styles():
    original, parts = fixture()
    selected = selection()
    before = deepcopy(selected)
    result, report = probe_copy(original, "Form", selected)
    assert selected == before
    assert report["productionQualified"] is False
    assert report["cellCount"] == 2 and report["addedStyles"] == 2
    with ZipFile(BytesIO(result)) as archive:
        for name in parts.keys() - {"xl/styles.xml", "xl/worksheets/sheet1.xml"}:
            assert archive.read(name) == parts[name]
        old = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
        new = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        for cell in new.iter(S + "c"):
            if cell.attrib["r"] in {"A1", "B1"}:
                assert cell.attrib["s"] != "0"
                cell.attrib["s"] = "0"
        assert ET.tostring(old) == ET.tostring(new)
        old_styles = ET.fromstring(parts["xl/styles.xml"])
        new_styles = ET.fromstring(archive.read("xl/styles.xml"))
        for name in ("borders", "cellXfs"):
            a, b = old_styles.find(S + name), new_styles.find(S + name)
            assert ET.tostring(a[0]) == ET.tostring(b[0])
            assert len(b) == int(b.attrib["count"]) == 3
        assert ET.tostring(old_styles.find(S + "fonts")) == ET.tostring(
            new_styles.find(S + "fonts")
        )
        assert new_styles.find(S + "borders")[1].find(S + "top").attrib["style"] == "hair"
        assert new_styles.find(S + "borders")[2].find(S + "right").find(S + "color").attrib == {
            "rgb": "FFFF0000"
        }


@pytest.mark.parametrize(
    "change", ["missing_cell", "missing_style", "duplicate", "wrong_sheet", "side", "huge"]
)
def test_unsupported_probe_does_not_guess_or_partially_produce_a_workbook(change):
    content, _ = fixture()
    selected = selection()
    sheet = "Form"
    if change == "missing_cell":
        selected[0]["sourceRange"] = "B2:C2"
    elif change == "missing_style":
        # Change only the fixture's explicitly serialized style attribute.
        _, parts = fixture()
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
            b' s="0"', b""
        )
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            for name, data in parts.items():
                archive.writestr(name, data)
        content = buffer.getvalue()
    elif change == "duplicate":
        selected *= 2
    elif change == "wrong_sheet":
        sheet = "Other"
    elif change == "side":
        selected[0]["sides"] = ["diagonal"]
    else:
        selected[0]["sourceRange"] = "A1:XFD1048576"
    with pytest.raises(ValueError):
        probe_copy(content, sheet, selected)


def png(array):
    stream = BytesIO()
    Image.fromarray(array).save(stream, format="PNG")
    return stream.getvalue()


def test_raster_comparison_detects_text_displacement_outside_measurement_lines():
    original = np.full((30, 30, 3), 255, np.uint8)
    original[15:18, 15:18] = 0
    probe = original.copy()
    probe[5, :] = [255, 0, 0]
    unchanged_text = compare_raster(png(original), png(probe))
    assert unchanged_text["changedOutsideRedNeighborhood"] == 0
    assert unchanged_text["originalInkInsideRedNeighborhood"] == 0
    assert unchanged_text["textPlacementVerified"] is False
    probe[15:18, 15:18] = 255
    probe[16:19, 15:18] = 0
    moved = compare_raster(png(original), png(probe))
    assert moved["changedOutsideRedNeighborhood"] == 6
    assert moved["outsideChangeBounds"] == [15, 15, 17, 18]
    assert moved["productionQualified"] is False


def test_ink_overlapped_by_a_probe_is_not_silently_a_preservation_proof():
    original = np.full((30, 30, 3), 255, np.uint8)
    original[5, 10] = 0
    probe = original.copy()
    probe[5, :] = [255, 0, 0]
    result = compare_raster(png(original), png(probe))
    assert result["originalInkInsideRedNeighborhood"] == 1
    assert result["textPlacementVerified"] is False
