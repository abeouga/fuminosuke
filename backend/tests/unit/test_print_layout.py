from __future__ import annotations

import copy
import hashlib
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from openpyxl import Workbook
from openpyxl.styles import Border, Side
from openpyxl.worksheet.pagebreak import Break
from PIL import Image

from app.core.errors import AppError
from app.domain.print_layout import (
    PrintLayoutRequest,
    page_plan,
    require_legacy_ocr,
    transform_rect,
)
from app.services.print_layout_renderer import render_page
from app.services.print_layout_source import NS, read_source


def workbook_bytes(rows=63, merge=None, manual=None):
    book = Workbook()
    sheet = book.active
    sheet.title = "帳票"
    sheet.print_area = f"B2:D{rows}"
    for r in range(2, rows + 1):
        sheet.row_dimensions[r].height = 24
        for c in range(2, 5):
            cell = sheet.cell(r, c)
            cell.border = Border(
                left=Side(style="thin"),
                top=Side(style="thin"),
                right=Side(style="thin"),
                bottom=Side(style="thin"),
            )
    sheet["B2"] = "氏名"
    if merge:
        sheet.merge_cells(merge)
    if manual:
        sheet.row_breaks.append(Break(id=manual))
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def source_from(content):
    return read_source(
        content,
        "帳票",
        {
            "cells": [
                {"id": "xlsx_C2", "role": "input"},
                {"id": "xlsx_C32", "role": "input"},
            ]
        },
    )


def request(**values):
    return PrintLayoutRequest(base_revision="a" * 64, **values)


def test_two_pages_share_uniform_scale_and_exact_field_ownership():
    content = workbook_bytes()
    sha = hashlib.sha256(content).hexdigest()
    source = source_from(content)
    result = page_plan(source, request(break_after_rows=[31], target_pages_tall=2))
    assert [p["range"] for p in result["pages"]] == ["B2:D31", "B32:D63"]
    assert [p["fields"][0]["cellId"] for p in result["pages"]] == ["xlsx_C2", "xlsx_C32"]
    assert {p["transform"]["scale"] for p in result["pages"]} == {result["commonScale"] * 150 / 72}
    assert 0 < result["commonScale"] <= 1
    for page in result["pages"]:
        for field in page["fields"]:
            assert field["templatePage"] == page["page"]
            assert field["rectangle"] == transform_rect(field, page["transform"])
            assert field["rectangle"]["y"] >= page["transform"]["offsetY"] - 1
    assert hashlib.sha256(content).hexdigest() == sha


def test_saved_three_pages_are_not_replaced_by_two_page_target():
    source = source_from(workbook_bytes())
    saved = page_plan(source, request(break_after_rows=[20, 40]))
    before = copy.deepcopy(saved)
    next_plan = page_plan(source, request(target_pages_tall=2), saved)
    assert len(next_plan["pages"]) == 3
    assert next_plan["warnings"]
    assert saved == before
    auto = page_plan(source, request(target_pages_tall=2, auto_arrange=True), saved)
    assert len(auto["pages"]) == 2


def test_manual_break_priority_and_no_break_inside_merge():
    source = source_from(workbook_bytes(merge="B30:B32", manual=40))
    assert 30 not in source["validBreakRows"] and 31 not in source["validBreakRows"]
    assert page_plan(source, request(target_pages_tall=3))["pages"][0]["endRow"] == 40
    with pytest.raises(AppError):
        page_plan(source, request(break_after_rows=[31]))
    assert page_plan(source, request(break_after_rows=[29]))["pages"][1]["startRow"] == 30


@pytest.mark.parametrize("breaks", [[31, 31], [40, 20], [1], [63], [999]])
def test_invalid_ranges_rejected(breaks):
    with pytest.raises(AppError):
        page_plan(source_from(workbook_bytes()), request(break_after_rows=breaks))


def test_extreme_shrink_warns_and_never_enlarges():
    source = source_from(workbook_bytes())
    plan = page_plan(source, request(break_after_rows=[]))
    assert plan["commonScale"] < 0.70
    assert any("安全下限" in w for w in plan["warnings"])
    small = read_source(workbook_bytes(rows=3), "帳票", {"cells": []})
    assert page_plan(small, request(break_after_rows=[]))["commonScale"] == 1


def test_missing_ooxml_is_null_not_openpyxl_defaults():
    content = workbook_bytes()
    out = BytesIO()
    with ZipFile(BytesIO(content)) as original, ZipFile(out, "w") as edited:
        for entry in original.infolist():
            data = original.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(data)
                for tag in ("pageSetup", "pageMargins", "printOptions"):
                    node = root.find(f"s:{tag}", NS)
                    if node is not None:
                        root.remove(node)
                data = ET.tostring(root)
            edited.writestr(entry, data)
    imported = source_from(out.getvalue())["importedSettings"]
    assert imported["pageSetup"] is None
    assert imported["margins"] is None
    assert imported["printOptions"] is None


@pytest.mark.parametrize("feature", ["hidden", "titles", "areas", "header", "wide", "formula"])
def test_advanced_features_are_detected(feature):
    from openpyxl import load_workbook

    book = load_workbook(BytesIO(workbook_bytes()))
    sheet = book.active
    if feature == "hidden":
        sheet.row_dimensions[5].hidden = True
    elif feature == "titles":
        sheet.print_title_rows = "2:3"
    elif feature == "areas":
        sheet.print_area = ["B2:D20", "B30:D63"]
    elif feature == "header":
        sheet.oddHeader.center.text = "Page &P"
    elif feature == "wide":
        sheet.page_setup.fitToWidth = 2
    else:
        sheet["B2"] = "=1+1"
    out = BytesIO()
    book.save(out)
    source = source_from(out.getvalue())
    assert source["unsupported"]
    with pytest.raises(AppError):
        page_plan(source, request())


def test_reference_image_and_rectangle_have_identical_grid_edges():
    source = source_from(workbook_bytes())
    plan = page_plan(source, request(break_after_rows=[31]))
    page = plan["pages"][1]
    first, second = render_page(page), render_page(page)
    assert first == second
    with Image.open(BytesIO(first)) as image:
        assert image.size == (page["width"], page["height"])
        rect = page["fields"][0]["rectangle"]
        assert image.getpixel((rect["x"], rect["y"])) == (34, 34, 34)
        assert image.getpixel(
            (rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2)
        ) == (255, 255, 255)


def test_legacy_guard_does_not_change_old_quality():
    old = {"layoutVerified": True}
    require_legacy_ocr(old)
    assert old == {"layoutVerified": True}
    with pytest.raises(AppError) as exc:
        require_legacy_ocr({"printLayout": {"pages": [{}, {}]}})
    assert exc.value.code == "PRINT_LAYOUT_OCR_NOT_AVAILABLE"
