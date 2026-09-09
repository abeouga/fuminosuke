from __future__ import annotations

import copy
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from app.core.errors import AppError
from app.domain.print_layout import page_plan
from app.services.office_reference_renderer import S
from app.services.print_layout_source import read_source
from app.services.print_workbook import workbook_copy
from tests.unit.test_print_layout import request, workbook_bytes


def case(breaks):
    content = workbook_bytes(rows=30, manual=9, merge="B3:C3")
    plan = page_plan(read_source(content, "帳票", {"cells": []}), request(break_after_rows=breaks))
    return content, plan


@pytest.mark.parametrize("breaks", [[], [15], [10, 20]])
def test_saved_boundaries_override_original_without_changing_cells(breaks):
    content, plan = case(breaks)
    before = copy.deepcopy(plan)
    output = workbook_copy(content, plan)
    assert plan == before
    book = load_workbook(BytesIO(output))
    sheet = book.active
    assert [b.id for b in sheet.row_breaks.brk] == breaks
    assert all(b.man and b.min == 0 and b.max == 16383 for b in sheet.row_breaks.brk)
    assert sheet.col_breaks.brk == []
    assert str(sheet.print_area) == "'帳票'!$B$2:$D$30"
    assert sheet.page_setup.scale == 100
    assert (
        not sheet.sheet_properties.pageSetUpPr or not sheet.sheet_properties.pageSetUpPr.fitToPage
    )
    assert next(iter(sheet.merged_cells.ranges)).coord == "B3:C3"
    with ZipFile(BytesIO(content)) as original, ZipFile(BytesIO(output)) as generated:
        assert original.namelist() == generated.namelist()
        for name in original.namelist():
            if name not in {"xl/workbook.xml", "xl/worksheets/sheet1.xml"}:
                assert original.read(name) == generated.read(name)
        old = ET.fromstring(original.read("xl/worksheets/sheet1.xml"))
        new = ET.fromstring(generated.read("xl/worksheets/sheet1.xml"))
        for tag in ["sheetData", "sheetFormatPr", "cols", "mergeCells", "sheetViews"]:
            a, b = old.find(S + tag), new.find(S + tag)
            assert (ET.tostring(a) if a is not None else None) == (
                ET.tostring(b) if b is not None else None
            )
    assert [b.id for b in load_workbook(BytesIO(content)).active.row_breaks.brk] == [9]


@pytest.mark.parametrize(
    "mutation", ["gap", "overlap", "column", "range", "sheet", "order", "missing", "wide", "merge"]
)
def test_invalid_saved_pages_are_rejected(mutation):
    content, plan = case([15])
    if mutation == "gap":
        plan["pages"][1]["startRow"] += 1
    elif mutation == "overlap":
        plan["pages"][1]["startRow"] -= 1
    elif mutation == "column":
        plan["pages"][1]["endCol"] += 1
    elif mutation == "range":
        plan["pages"][1]["range"] = "B16:E30"
    elif mutation == "sheet":
        plan["sheetName"] = "missing"
    elif mutation == "order":
        plan["pages"].reverse()
    elif mutation == "missing":
        plan["pages"].pop()
    elif mutation == "wide":
        plan["targetPagesWide"] = 2
    else:
        # A merge spanning the vertical boundary cannot be split.
        content = workbook_bytes(rows=30, merge="B15:B16")
        from hashlib import sha256

        plan["sourceSha256"] = sha256(content).hexdigest()
    with pytest.raises(AppError):
        workbook_copy(content, plan)


def test_fractional_scale_and_source_mismatch_are_not_hidden():
    content, plan = case([15])
    plan["commonScale"] = 0.987
    with pytest.raises(AppError, match="fractional"):
        workbook_copy(content, plan)
    plan["commonScale"] = 1
    plan["sourceSha256"] = "0" * 64
    with pytest.raises(AppError, match="SHA"):
        workbook_copy(content, plan)


def test_unreferenced_worksheet_part_is_not_rewritten():
    from hashlib import sha256

    content, plan = case([15])
    buffer = BytesIO(content)
    with ZipFile(buffer, "a") as archive:
        unused = archive.read("xl/worksheets/sheet1.xml")
        archive.writestr("xl/worksheets/unused.xml", unused)
    content = buffer.getvalue()
    plan["sourceSha256"] = sha256(content).hexdigest()
    output = workbook_copy(content, plan)
    with ZipFile(BytesIO(output)) as archive:
        assert archive.read("xl/worksheets/unused.xml") == unused
