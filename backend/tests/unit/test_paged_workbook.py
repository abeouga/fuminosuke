import copy
import hashlib
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Border, Side

from app.core.errors import AppError
from app.services import print_xml
from app.services.paged_workbook import ADAPTER, render_workbook
from app.services.preserving_print_workbook import MAIN
from app.services.spreadsheet_template_output_service import SpreadsheetTemplateOutputService
from tests.unit.test_preserving_print_workbook import advanced, rewrite

S = "{" + MAIN + "}"


def binding(coordinate, source_range=None, *, cell_id=None, parts=None):
    return {
        "cellId": cell_id or f"xlsx_{coordinate}",
        "anchorCell": coordinate,
        "sourceRange": source_range or f"{coordinate}:{coordinate}",
        "sourceParts": parts or [],
        "pageId": "arbitrary-page",
        "mappingId": "arbitrary-mapping",
        "targetPath": "customFields.any",
    }


def saved(content, bindings, merges=None, *, sheet="帳票", schema=1):
    return {
        "outputAdapter": ADAPTER,
        "sourceWorkbook": {"sha256": hashlib.sha256(content).hexdigest()},
        "spreadsheetOutput": {
            "schemaVersion": schema,
            "sourceSheetName": sheet,
            "cells": [{"id": b["cellId"]} for b in bindings],
            "cellMerges": merges or [],
        },
    }


def workbook_bytes(book):
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize("prefixed", [False, True])
def test_every_unmodified_part_and_element_is_preserved(prefixed):
    content, _ = advanced(prefixed=prefixed)
    bindings = [binding("C4"), binding("C18"), binding("B3", "B3:C3")]
    snapshot = saved(content, bindings)
    values = ["00123 & <任意>\r\n全角　空白", None, "=SUM(A1:A2)"]
    readings = [{"binding": b, "value": v} for b, v in zip(bindings, values, strict=True)]
    original = copy.deepcopy((snapshot, readings))
    output = render_workbook(content, snapshot, readings)
    assert output == render_workbook(content, snapshot, list(reversed(readings)))
    assert (snapshot, readings) == original
    with ZipFile(BytesIO(content)) as before, ZipFile(BytesIO(output)) as after:
        assert before.namelist() == after.namelist()
        for name in before.namelist():
            if name != "xl/worksheets/sheet1.xml":
                assert before.read(name) == after.read(name)
        a, b = before.read("xl/worksheets/sheet1.xml"), after.read("xl/worksheets/sheet1.xml")
        old, new = print_xml.parse(a), print_xml.parse(b)
        for child in old.children:
            if child.name.endswith("}sheetData"):
                continue
            equivalent = next(n for n in new.children if n.name == child.name)
            assert a[child.start : child.end] == b[equivalent.start : equivalent.end]
        table_old = next(n for n in old.children if n.name.endswith("}sheetData"))
        table_new = next(n for n in new.children if n.name.endswith("}sheetData"))
        new_cells = {c.attrs["r"]: c for row in table_new.children for c in row.children}
        for row in table_old.children:
            for c in row.children:
                if c.attrs["r"] in {"C4", "C18", "B3"}:
                    continue
                equivalent = new_cells[c.attrs["r"]]
                assert a[c.start : c.end] == b[equivalent.start : equivalent.end]
    book = load_workbook(BytesIO(output))
    assert book.sheetnames == ["帳票", "Management"]
    assert book["Management"].sheet_state == "hidden"
    assert [book["帳票"][b["anchorCell"]].value for b in bindings] == values
    assert book["帳票"]["B3"].data_type == "s"
    assert {str(r) for r in book["帳票"].merged_cells.ranges} == {"B3:C3"}


@pytest.mark.parametrize("empty", [False, True])
def test_missing_rows_and_cells_are_inserted_in_order_without_dropping_metadata(empty):
    book = Workbook()
    book.active.title = "帳票"
    if not empty:
        book.active["A1"] = "untouched"
        book.active["D2"] = "right"
        book.active["A4"] = "below"
    content = workbook_bytes(book)
    bindings = [binding(c) for c in ("C2", "B2", "D5", "B3", "A7")]
    snapshot = saved(content, bindings)
    output = render_workbook(
        content, snapshot, [{"binding": b, "value": b["anchorCell"]} for b in bindings]
    )
    loaded = load_workbook(BytesIO(output), read_only=True)
    assert [loaded.active[b["anchorCell"]].value for b in bindings] == [
        b["anchorCell"] for b in bindings
    ]
    assert loaded.active.max_row == 7 and loaded.active.max_column == 4
    with ZipFile(BytesIO(output)) as archive:
        table = ET.fromstring(archive.read("xl/worksheets/sheet1.xml")).find(S + "sheetData")
        assert [int(r.get("r")) for r in table] == sorted(int(r.get("r")) for r in table)
        row2 = table.find(S + "row[@r='2']")
        assert [c.get("r") for c in row2] == (["B2", "C2"] if empty else ["B2", "C2", "D2"])


@pytest.mark.parametrize("source_merged", [False, True])
def test_selected_merge_has_exact_coverage_preserves_styles_and_removes_only_approved_literal(
    source_merged,
):
    book = Workbook()
    sheet = book.active
    sheet.title = "帳票"
    side = Side(style="thin", color="000000")
    for row in sheet.iter_rows(min_row=2, max_row=3, min_col=3, max_col=6):
        for cell in row:
            cell.border = Border(left=side, right=side, top=side, bottom=side)
    coordinates = ("C2", "E2") if source_merged else ("C2", "D2")
    if source_merged:
        sheet.merge_cells("C2:D3")
        sheet.merge_cells("E2:F3")
    sheet[coordinates[1]] = " 単位 "
    content = workbook_bytes(book)
    source_ranges = {"C2": "C2:D3", "E2": "E2:F3"} if source_merged else None
    merge = {
        "id": "merge-any",
        "parentCellId": "manual-merge",
        "excelRange": "C2:F3" if source_merged else "C2:D2",
        "anchorCell": "C2",
        "parts": [
            {
                "cell": {"id": f"xlsx_{c}", "text": "単位" if i else ""},
                "partType": "literal" if i else "input",
                "literal": "単位" if i else None,
            }
            for i, c in enumerate(coordinates)
        ],
    }
    if source_ranges:
        merge["sourceRanges"] = source_ranges
    parts = [
        {"cellId": f"xlsx_{c}", "sourceRange": source_ranges[c] if source_ranges else f"{c}:{c}"}
        for c in coordinates
    ]
    dest = binding("C2", merge["excelRange"], cell_id="manual-merge", parts=parts)
    snapshot = saved(content, [dest], [merge], schema=2 if source_merged else 1)
    output = render_workbook(content, snapshot, [{"binding": dest, "value": "結合後の任意値"}])
    expected = load_workbook(BytesIO(content)).active
    helper = SpreadsheetTemplateOutputService
    helper._apply_merges(expected, helper._merge_plans(snapshot["spreadsheetOutput"], expected))
    actual = load_workbook(BytesIO(output)).active
    assert actual["C2"].value == "結合後の任意値"
    assert actual[coordinates[1]].value is None
    assert {str(r) for r in actual.merged_cells.ranges} == {merge["excelRange"]}
    for row in expected.iter_rows(min_row=2, max_row=3, min_col=3, max_col=6):
        for cell in row:
            assert copy.copy(actual[cell.coordinate].border) == copy.copy(cell.border)
    with ZipFile(BytesIO(content)) as before, ZipFile(BytesIO(output)) as after:
        assert before.read("xl/styles.xml") == after.read("xl/styles.xml")
    bad = copy.deepcopy(snapshot)
    bad["spreadsheetOutput"]["cellMerges"][0]["parts"][1]["literal"] = "別の文字"
    with pytest.raises(AppError) as error:
        render_workbook(content, bad, [{"binding": dest, "value": "値"}])
    assert error.value.code == "SPREADSHEET_OUTPUT_MERGE_CONFLICT"


@pytest.mark.parametrize(
    "mutation", ["range", "anchor", "parts", "unknown", "adapter", "sha", "conflict", "control"]
)
def test_invalid_source_or_ownership_is_rejected(mutation):
    content, _ = advanced()
    dest = binding("C4")
    snapshot = saved(content, [dest])
    readings = [{"binding": dest, "value": "value"}]
    expected = "PAGE_OUTPUT_MAPPING_INVALID"
    if mutation == "range":
        dest["sourceRange"] = "D4:D4"
    elif mutation == "anchor":
        dest["anchorCell"] = "D4"
    elif mutation == "parts":
        dest["sourceParts"] = [{"cellId": "wrong", "sourceRange": "C4:C4"}]
    elif mutation == "unknown":
        dest["cellId"] = "manual-unknown"
    elif mutation == "adapter":
        snapshot.pop("outputAdapter")
        expected = "PAGE_OUTPUT_ADAPTER_REQUIRED"
    elif mutation == "sha":
        snapshot["sourceWorkbook"]["sha256"] = "0" * 64
        expected = "PAGE_OUTPUT_SOURCE_CHANGED"
    elif mutation == "conflict":
        readings.append({"binding": copy.deepcopy(dest), "value": "different"})
        expected = "PAGE_OUTPUT_CELL_CONFLICT"
    else:
        readings[0]["value"] = "not\x00text"
        expected = "PAGE_OUTPUT_TEXT_INVALID"
    with pytest.raises(AppError) as error:
        render_workbook(content, snapshot, readings)
    assert error.value.code == expected


def test_literal_escape_sequences_and_blank_values_are_not_reinterpreted():
    content, _ = advanced()
    dest = binding("C4")
    snapshot = saved(content, [dest])
    output = render_workbook(content, snapshot, [{"binding": dest, "value": "_x0041_ _x005F_"}])
    with ZipFile(BytesIO(output)) as archive:
        cell = ET.fromstring(archive.read("xl/worksheets/sheet1.xml")).find(f".//{S}c[@r='C4']")
        assert cell.get("t") == "inlineStr"
        assert cell.find(f"{S}is/{S}t").text == "_x005F_x0041_ _x005F_x005F_"
        assert cell.find(S + "f") is None
    blank = render_workbook(content, snapshot, [{"binding": dest, "value": None}])
    assert load_workbook(BytesIO(blank)).active["C4"].value is None


def test_formula_in_unedited_sheet_is_still_rejected():
    content, _ = advanced()
    content = rewrite(
        content,
        {
            "xl/worksheets/sheet2.xml": (
                f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1"><f>1+1</f>'
                "</c></row></sheetData></worksheet>"
            ).encode()
        },
    )
    dest = binding("C4")
    with pytest.raises(AppError) as error:
        render_workbook(content, saved(content, [dest]), [{"binding": dest, "value": "value"}])
    assert error.value.code == "UNSUPPORTED_XLSX_FEATURE"
