from io import BytesIO
from types import SimpleNamespace

import cv2
import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.services.spreadsheet_template_import_service import (
    SpreadsheetTemplateImportService,
)
from app.storage.local_storage import LocalStorageService


def test_restores_virtual_xlsx_cells_and_manual_choice_groups(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "面接カード"
    thin = Side(style="thin")
    bordered = Border(left=thin, right=thin, top=thin, bottom=thin)
    for cell_range in ("A1:B2", "C1:D2", "E1:F2", "G1:H2", "I1:J2"):
        sheet.merge_cells(cell_range)
        sheet[cell_range.split(":", 1)[0]].border = bordered
    sheet["A1"] = "帰省先"
    sheet["E1"] = "都 道 府 県"
    sheet["I1"] = "市 区 町 村"
    buffer = BytesIO()
    workbook.save(buffer)

    service = SpreadsheetTemplateImportService(
        SimpleNamespace(),  # type: ignore[arg-type]
        LocalStorageService(tmp_path),
    )
    source = buffer.getvalue()
    layout = service.restore_layout(source)

    assert {cell.id for cell in layout.cells} >= {
        "xlsx_A1",
        "xlsx_C1",
        "xlsx_E1",
        "xlsx_G1",
        "xlsx_I1",
    }
    prefecture = next(cell for cell in layout.cells if cell.id == "xlsx_E1")
    municipality = next(cell for cell in layout.cells if cell.id == "xlsx_I1")
    assert prefecture.role == municipality.role == "mark"
    assert prefecture.mark_sets[0].recognition_mode == "manual"
    assert prefecture.mark_sets[0].selection_mode == "single"
    assert [option.label for option in prefecture.mark_sets[0].options] == [
        "都",
        "道",
        "府",
        "県",
    ]
    assert [option.label for option in municipality.mark_sets[0].options] == [
        "市",
        "区",
        "町",
        "村",
    ]
    assert buffer.getvalue() == source
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_excludes_decorative_narrow_empty_cells_but_keeps_the_printed_grid(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    thin = Side(style="thin")
    bordered = Border(left=thin, right=thin, top=thin, bottom=thin)
    sheet.column_dimensions["A"].width = 15
    sheet.column_dimensions["B"].width = 0.5
    sheet.column_dimensions["C"].width = 0.5
    sheet.column_dimensions["D"].width = 15
    sheet.row_dimensions[1].height = 30
    sheet.row_dimensions[2].height = 30
    for coordinate in ("A1", "B1", "C1", "D1", "B2"):
        sheet[coordinate].border = bordered
    sheet["A1"] = "項目名"
    sheet["B2"] = "頃"
    buffer = BytesIO()
    workbook.save(buffer)

    service = SpreadsheetTemplateImportService(
        SimpleNamespace(),  # type: ignore[arg-type]
        LocalStorageService(tmp_path),
    )
    layout, print_cells, _, _ = service._parse_workbook(buffer.getvalue())
    layout_ids = {cell.id for cell in layout.cells}
    print_ids = {str(cell["id"]) for cell in print_cells}

    assert "xlsx_B1" not in layout_ids
    assert "xlsx_C1" not in layout_ids
    assert "xlsx_D1" in layout_ids
    assert "xlsx_B2" in layout_ids
    assert {"xlsx_B1", "xlsx_C1"} <= print_ids
    assert service._is_decorative_narrow_cell(
        text="",
        bordered=True,
        borders={"top": True, "right": False, "bottom": False, "left": True},
        has_validation=False,
        merged=False,
        width=13,
        height=13,
    )
    assert not service._is_decorative_narrow_cell(
        text="",
        bordered=True,
        borders={"top": True, "right": True, "bottom": True, "left": True},
        has_validation=False,
        merged=False,
        width=13,
        height=13,
    )


def test_classifies_text_with_adjacent_blank_cell_as_label_not_heading(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    thin = Side(style="thin")
    bordered = Border(left=thin, right=thin, top=thin, bottom=thin)
    heading_fill = PatternFill(fill_type="solid", fgColor="D9EAD3")

    sheet.merge_cells("A1:H1")
    sheet["A1"] = "職場訪問参加状況"
    sheet["A1"].border = bordered
    sheet["A1"].font = sheet["A1"].font.copy(bold=True)
    sheet["A1"].alignment = Alignment(horizontal="center")
    sheet["A1"].fill = heading_fill

    sheet.merge_cells("A2:B2")
    sheet["A2"] = "参加時期"
    sheet["A2"].border = bordered
    sheet["A2"].font = sheet["A2"].font.copy(bold=True)
    sheet["A2"].alignment = Alignment(horizontal="center")
    sheet["A2"].fill = heading_fill
    sheet.merge_cells("C2:D2")
    sheet["C2"].border = bordered

    sheet.merge_cells("E2:F2")
    sheet["E2"] = "参加場所"
    sheet["E2"].border = bordered
    sheet.merge_cells("G2:H2")
    sheet["G2"].border = bordered

    sheet.merge_cells("A4:B5")
    sheet["A4"] = "希望順位"
    sheet["A4"].border = bordered
    sheet["A4"].font = sheet["A4"].font.copy(bold=True)
    sheet["A4"].alignment = Alignment(horizontal="center")
    sheet["A4"].fill = heading_fill
    sheet["A6"] = 1
    sheet["A6"].border = bordered
    sheet["A8"] = 2
    sheet["A8"].border = bordered

    buffer = BytesIO()
    workbook.save(buffer)
    service = SpreadsheetTemplateImportService(
        SimpleNamespace(),  # type: ignore[arg-type]
        LocalStorageService(tmp_path),
    )

    layout = service.restore_layout(buffer.getvalue())
    roles = {cell.id: cell.role for cell in layout.cells}

    assert roles["xlsx_A1"] == "parent_label"
    assert roles["xlsx_A2"] == "label"
    assert roles["xlsx_E2"] == "label"
    assert roles["xlsx_C2"] == "input"
    assert roles["xlsx_G2"] == "input"
    assert roles["xlsx_A4"] == "label"
    assert roles["xlsx_A6"] == "input"
    assert roles["xlsx_A8"] == "input"
    assert not ({"record_key_cell", "column_key_cell"} & set(roles.values()))


def test_aligns_xlsx_cells_to_rendered_table_bounds(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    thin = Side(style="thin")
    bordered = Border(left=thin, right=thin, top=thin, bottom=thin)
    for column in range(1, 7):
        sheet.column_dimensions[get_column_letter(column)].width = 15
    for row in range(1, 5):
        sheet.row_dimensions[row].height = 60
        for column in range(1, 7):
            sheet.cell(row=row, column=column).border = bordered
    sheet["A1"] = "項目名"
    buffer = BytesIO()
    workbook.save(buffer)

    service = SpreadsheetTemplateImportService(
        SimpleNamespace(),  # type: ignore[arg-type]
        LocalStorageService(tmp_path),
    )
    layout, print_cells, _, _ = service._parse_workbook(buffer.getvalue())
    source_bounds = service._printed_border_bounds(print_cells)
    assert source_bounds is not None
    source_left, source_top, source_right, source_bottom = source_bounds
    rendered_bounds = (
        source_left + 9,
        source_top - 7,
        source_right + 18,
        source_bottom - 28,
    )
    preview = np.full(
        (layout.canonical_height, layout.canonical_width, 3),
        255,
        dtype=np.uint8,
    )
    cv2.rectangle(
        preview,
        rendered_bounds[:2],
        rendered_bounds[2:],
        (0, 0, 0),
        1,
    )

    aligned, aligned_print_cells = service._align_layout_to_preview(
        layout,
        print_cells,
        preview,
    )

    first = next(cell for cell in aligned.cells if cell.id == "xlsx_A1")
    last = next(cell for cell in aligned.cells if cell.id == "xlsx_F4")
    assert (first.x, first.y) == rendered_bounds[:2]
    assert last.x + last.width == rendered_bounds[2]
    assert last.y + last.height == rendered_bounds[3]
    assert service._printed_border_bounds(aligned_print_cells) == rendered_bounds
    assert aligned.warnings[-1].startswith("LibreOffice")


def test_keeps_estimated_coordinates_when_rendered_bounds_are_not_detectable(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "項目名"
    sheet["A1"].border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(),
        bottom=Side(),
    )
    buffer = BytesIO()
    workbook.save(buffer)
    service = SpreadsheetTemplateImportService(
        SimpleNamespace(),  # type: ignore[arg-type]
        LocalStorageService(tmp_path),
    )
    layout, print_cells, _, _ = service._parse_workbook(buffer.getvalue())
    blank_preview = np.full(
        (layout.canonical_height, layout.canonical_width, 3),
        255,
        dtype=np.uint8,
    )

    aligned, aligned_print_cells = service._align_layout_to_preview(
        layout,
        print_cells,
        blank_preview,
    )

    assert aligned == layout
    assert aligned_print_cells == print_cells
