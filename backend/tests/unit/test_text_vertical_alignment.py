from contextlib import nullcontext
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from openpyxl import Workbook
from openpyxl.styles import Alignment

from app.layout_analysis.detector import LayoutDetector, TextRegion, _Rect
from app.layout_analysis.models import LayoutCell, LayoutDefinition
from app.layout_analysis.text_placement import infer_vertical_alignment
from app.services.label_cell_ocr_service import _apply_regions
from app.services.layout_service import LayoutService
from app.services.spreadsheet_template_import_service import SpreadsheetTemplateImportService
from app.storage.local_storage import LocalStorageService


@pytest.mark.parametrize(("top", "expected"), [(5, "top"), (40, "middle"), (75, "bottom")])
@pytest.mark.parametrize("origin", [0, 350])
def test_infers_vertical_position_in_local_and_document_coordinates(top, expected, origin):
    assert infer_vertical_alignment(
        cell_height=100, origin_y=origin,
        bounds=[(10, origin + top, 70, origin + top + 20)],
    ) == expected


def test_unknown_and_multiple_line_bounds():
    assert infer_vertical_alignment(cell_height=100, bounds=[]) is None
    assert infer_vertical_alignment(
        cell_height=100, bounds=[(5, 30, 80, 45), (5, 55, 80, 70)],
    ) == "middle"


@pytest.mark.parametrize(("top", "expected"), [(5, "top"), (40, "middle"), (75, "bottom")])
def test_detected_rectangles_use_document_y_origin(top, expected):
    image = np.full((300, 300), 255, dtype=np.uint8)
    rect = _Rect(x=100, y=150, width=100, height=100, confidence=1)
    region = TextRegion(((110, 150 + top), (170, 150 + top),
                         (170, 170 + top), (110, 170 + top)), "見出し", 0.9)
    cell = LayoutDetector._cell(1, rect, image, np.zeros_like(image), [region])
    assert cell.text_vertical_alignment == expected
    assert (cell.x, cell.y, cell.width, cell.height) == (100, 150, 100, 100)
    assert cell.text == "見出し"


def test_label_ocr_adds_position_without_changing_identity_or_geometry():
    cell = LayoutCell(id="label", role="label", x=350, y=420,
                      width=100, height=100, confidence=1, text="項目名")
    region = TextRegion(((10, 75), (70, 75), (70, 95), (10, 95)), "項目名", 0.9)
    updated = _apply_regions(cell, [region])
    assert updated.text_vertical_alignment == "bottom"
    for field in ("id", "role", "x", "y", "width", "height", "text"):
        assert getattr(updated, field) == getattr(cell, field)
    assert cell.text_vertical_alignment is None
    restored = LayoutCell.model_validate_json(updated.model_dump_json(by_alias=True))
    assert restored == updated


def test_imports_mixed_xlsx_positions_without_modifying_source(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    for index, vertical in enumerate(["top", "center", "bottom", None], start=1):
        sheet.cell(index, 1, "項目名")
        sheet.cell(index, 1).alignment = Alignment(horizontal="right", vertical=vertical)
    stream = BytesIO()
    workbook.save(stream)
    source = stream.getvalue()
    service = SpreadsheetTemplateImportService(SimpleNamespace(), LocalStorageService(tmp_path))
    layout = service.restore_layout(source)
    by_id = {cell.id: cell for cell in layout.cells}
    for index, expected in enumerate(["top", "middle", "bottom", "bottom"], start=1):
        assert by_id[f"xlsx_A{index}"].text_vertical_alignment == expected
        assert by_id[f"xlsx_A{index}"].text_alignment == "right"
    assert stream.getvalue() == source


def test_legacy_evidence_matches_ids_and_geometry_and_never_overwrites_saved_position():
    cells = [LayoutCell(id=f"cell{i}", role="label", x=i * 100, y=10,
                        width=90, height=80, confidence=1) for i in range(3)]
    cells[2].text_vertical_alignment = "top"
    layout = LayoutDefinition(canonical_width=400, canonical_height=200, cells=cells)
    original = layout.model_dump()
    evidence = [dict(id=cell.id, x=cell.x, y=cell.y, width=cell.width,
                     height=cell.height, vertical="bottom") for cell in reversed(cells)]
    evidence[1]["y"] = 999
    updated = LayoutService._with_saved_text_placement(layout, evidence)
    assert [cell.text_vertical_alignment for cell in updated.cells] == ["bottom", None, "top"]
    assert layout.model_dump() == original
    duplicated = LayoutService._with_saved_text_placement(layout, [*evidence, evidence[-1]])
    assert duplicated.cells[0].text_vertical_alignment is None


def test_get_layout_exposes_legacy_position_without_writing_version(monkeypatch, tmp_path):
    cell = LayoutCell(id="xlsx_A1", role="label", x=10, y=20,
                      width=100, height=80, confidence=1)
    layout = LayoutDefinition(canonical_width=300, canonical_height=300, cells=[cell])
    legacy = layout.model_dump(mode="json", by_alias=True)
    legacy["cells"][0].pop("textVerticalAlignment")
    version = SimpleNamespace(layout_json=legacy, mode="SPREADSHEET_TEMPLATE",
                              config_relative_path=None, quality_json={
        "spreadsheetPrintCells": [dict(id="xlsx_A1", x=10, y=20, width=100,
                                      height=80, vertical="center")],
    })
    monkeypatch.setattr("app.services.layout_service.TemplateRepository.get_version",
                        lambda self, version_id: version)
    database = SimpleNamespace(session=lambda: nullcontext(None))
    service = LayoutService(database, LocalStorageService(tmp_path))
    result = service.get("version")
    assert result.cells[0].text_vertical_alignment == "middle"
    assert "textVerticalAlignment" not in version.layout_json["cells"][0]
