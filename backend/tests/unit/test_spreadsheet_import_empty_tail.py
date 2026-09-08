from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Border, Font, PatternFill, Side

from app.core.errors import AppError
from app.services.spreadsheet_template_import_service import SpreadsheetTemplateImportService
from app.storage.local_storage import LocalStorageService


def workbook_bytes(workbook):
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def form():
    workbook = Workbook()
    sheet = workbook.active
    sheet["B2"] = "Label"
    sheet.merge_cells("B3:D4")
    sheet["B3"].border = Border(bottom=Side(style="thin"))
    return workbook


def test_empty_font_tail_preserves_original_cell_geometry(tmp_path):
    workbook = form()
    service = SpreadsheetTemplateImportService(SimpleNamespace(), LocalStorageService(tmp_path))
    expected = service.restore_layout(workbook_bytes(workbook))
    workbook.active["AA1000"].font = Font(name="Arial")
    content = workbook_bytes(workbook)
    sheet = load_workbook(BytesIO(content)).active
    assert sheet.calculate_dimension() == "B2:AA1000"
    assert service._worksheet_bounds(sheet, content=content) == (2, 2, 4, 4)
    restored = service.restore_layout(content)
    assert restored.model_dump() == expected.model_dump()
    assert sheet.calculate_dimension() == "B2:AA1000"
    assert not list(tmp_path.rglob("*.xlsx"))


@pytest.mark.parametrize("kind", ["value", "formula", "border", "fill", "merge", "print_area"])
def test_real_extent_is_never_silently_clipped(tmp_path, kind):
    workbook = form()
    sheet = workbook.active
    cell = sheet["AA1000"]
    if kind == "value":
        cell.value = "Tail"
    elif kind == "formula":
        cell.value = "=1+1"
    elif kind == "border":
        cell.border = Border(bottom=Side(style="thin"))
    elif kind == "fill":
        cell.fill = PatternFill("solid", fgColor="FFFF00")
    elif kind == "merge":
        sheet.merge_cells("Z999:AA1000")
    else:
        sheet.print_area = "B2:AA1000"
    service = SpreadsheetTemplateImportService(SimpleNamespace(), LocalStorageService(tmp_path))
    with pytest.raises(AppError) as caught:
        service.restore_layout(workbook_bytes(workbook))
    assert caught.value.code == "SPREADSHEET_RANGE_TOO_LARGE"


def test_previously_accepted_dimensions_and_explicit_area_stay_unchanged():
    workbook = form()
    sheet = workbook.active
    sheet["Z100"].font = Font(name="Arial")
    content = workbook_bytes(workbook)
    bounds = SpreadsheetTemplateImportService._worksheet_bounds(sheet, content=content)
    assert bounds == (2, 2, 26, 100)
    sheet.print_area = "A1:Z1000"
    bounds = SpreadsheetTemplateImportService._worksheet_bounds(sheet, content=content)
    assert bounds == (1, 1, 26, 1000)


@pytest.mark.parametrize("drawing,inspectable", [(b"", True), (b"<twoCellAnchor/>", False)])
def test_original_drawing_is_checked_even_if_library_discards_it(drawing, inspectable):
    content = workbook_bytes(form())
    output = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(output, "w") as result:
        for entry in source.infolist():
            result.writestr(entry, source.read(entry.filename))
        result.writestr("xl/drawings/drawing1.xml", b"<wsDr>" + drawing + b"</wsDr>")
    actual = SpreadsheetTemplateImportService._empty_tail_is_inspectable(output.getvalue())
    assert actual is inspectable


def test_omitted_border_sides_are_not_visible():
    cell = Workbook().active["A1"]
    cell.border = Border(left=None, right=None, top=None, bottom=Side(style="thin"))
    assert SpreadsheetTemplateImportService._has_border(cell)
    assert SpreadsheetTemplateImportService._border_map(cell) == {
        "left": False, "right": False, "top": False, "bottom": True,
    }
