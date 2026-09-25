from __future__ import annotations

import copy
import hashlib
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.styles import Border, Side

from app.core.errors import AppError
from app.services.spreadsheet_preview_geometry import MODEL, project_preview_geometry
from app.services.spreadsheet_template_import_service import SpreadsheetTemplateImportService
from app.storage.local_storage import LocalStorageService


def fixture(tmp_path, *, merged=False, count=6, centered=False, print_area=True):
    workbook = Workbook()
    sheet = workbook.active
    if print_area:
        sheet.print_area = f"A1:F{count}"
    for row in sheet.iter_rows(min_row=1, max_row=count, max_col=6):
        for cell in row:
            cell.value = cell.coordinate
            cell.border = Border(**{k: Side(style="thin") for k in
                                    ("top", "bottom", "left", "right")})
    sheet.column_dimensions["A"].width = .875
    sheet.row_dimensions[2].height = 2.25
    if centered:
        sheet.print_options.horizontalCentered = True
    if merged:
        sheet.merge_cells("B2:D3")
    output = BytesIO()
    workbook.save(output)
    content = output.getvalue()
    service = SpreadsheetTemplateImportService(None, LocalStorageService(tmp_path))
    layout, printed, _, _ = service._parse_workbook(content)
    snapshot = dict(model=MODEL, sourceSha256=hashlib.sha256(content).hexdigest(),
                    sheetName=sheet.title, bounds=[1, 1, 6, count],
                    columns=[4.8, 8.95, 8.95, 8.95, 8.95, 8.95],
                    rows=[15, 2.25] + [15] * (count - 2))
    return service, content, layout, printed, snapshot


def test_narrow_centered_sheet_keeps_its_printed_size(tmp_path):
    _, content, layout, printed, snapshot = fixture(
        tmp_path, centered=True, print_area=False,
    )
    _, cells = project_preview_geometry(content, layout, printed, snapshot)
    by_id = {cell["id"]: cell for cell in cells}

    # Office keeps this small sheet at its physical point size and centers it.
    # The canonical preview is approximately 150 pixels/inch.
    total_width = sum(snapshot["columns"])
    expected_width = total_width * layout.canonical_width / (8.27 * 72)
    assert abs(by_id["xlsx_A1"]["x"] - (layout.canonical_width - expected_width) / 2) <= 2
    assert abs(by_id["xlsx_F6"]["x"] + by_id["xlsx_F6"]["width"]
               - (layout.canonical_width + expected_width) / 2) <= 2
    assert abs(by_id["xlsx_A1"]["height"] - 15 * layout.canonical_height / (11.69 * 72)) <= 2


def test_saved_v1_geometry_keeps_its_original_coordinates(tmp_path):
    _, content, layout, printed, snapshot = fixture(tmp_path)
    snapshot["model"] = "spreadsheet-preview-office-v1"
    _, cells = project_preview_geometry(content, layout, printed, snapshot)
    by_id = {cell["id"]: cell for cell in cells}

    def rectangle(cell_id):
        return tuple(by_id[cell_id][key] for key in ("x", "y", "width", "height"))
    assert rectangle("xlsx_A1") == (112, 150, 90, 282)
    assert rectangle("xlsx_B2") == (202, 432, 169, 43)


def test_projected_dimensions_preserve_small_rows_and_column_ratios(tmp_path):
    _, content, layout, printed, snapshot = fixture(tmp_path)
    updated, cells = project_preview_geometry(content, layout, printed, snapshot)
    by_id = {c["id"]: c for c in cells}
    assert abs(by_id["xlsx_A1"]["width"] / by_id["xlsx_B1"]["width"] - 4.8 / 8.95) < .02
    assert abs(by_id["xlsx_A2"]["height"] / by_id["xlsx_A1"]["height"] - 2.25 / 15) < .02
    assert [c.id for c in updated.cells] == [c.id for c in layout.cells]
    assert [c.role for c in updated.cells] == [c.role for c in layout.cells]


def test_only_source_merges_determine_projected_range(tmp_path):
    _, content, layout, printed, snapshot = fixture(tmp_path, merged=True)
    _, cells = project_preview_geometry(content, layout, printed, snapshot)
    by_id = {c["id"]: c for c in cells}
    merged, neighbor = by_id["xlsx_B2"], by_id["xlsx_E2"]
    assert merged["x"] + merged["width"] == neighbor["x"]
    assert merged["y"] + merged["height"] == by_id["xlsx_B4"]["y"]
    assert "xlsx_C2" not in by_id and "xlsx_B3" not in by_id


def test_many_small_unmerged_cells_remain_independent(tmp_path):
    _, content, layout, printed, snapshot = fixture(tmp_path, count=90)
    before = copy.deepcopy(printed)
    updated, cells = project_preview_geometry(content, layout, printed, snapshot)
    assert len(updated.cells) == len(layout.cells) == 540
    assert len(cells) == len(printed) == 540
    assert printed == before
    assert hashlib.sha256(content).hexdigest() == snapshot["sourceSha256"]


def test_restore_uses_saved_dimensions_only_for_versions_that_have_them(tmp_path):
    service, content, _, _, snapshot = fixture(tmp_path)
    legacy = service.restore_layout(content)
    modern = service.restore_layout(content, preview_geometry=snapshot)
    assert legacy != modern
    assert service.restore_layout(content) == legacy
    assert service.restore_layout(content, preview_geometry=copy.deepcopy(snapshot)) == modern


def test_failed_office_dimension_export_does_not_create_guessed_snapshot(tmp_path, monkeypatch):
    service, content, layout, _, _ = fixture(tmp_path)
    service._storage.initialize()
    monkeypatch.setattr(service, "_find_soffice", lambda: Path("soffice.exe"))

    def convert(command, **kwargs):
        if command[command.index("--convert-to") + 1] == "fods":
            return subprocess.CompletedProcess(command, 1)
        (Path(kwargs["cwd"]) / "source.pdf").write_bytes(b"unused PDF")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", convert)
    snapshot = {}
    with pytest.raises(AppError) as error:
        service._render_workbook_preview(
            content, sheet_name="Sheet", canonical_width=layout.canonical_width,
            canonical_height=layout.canonical_height, geometry=snapshot,
        )
    assert error.value.code == "SPREADSHEET_PREVIEW_GEOMETRY_FAILED"
    assert snapshot == {}


@pytest.mark.parametrize("mutation", ["hash", "length", "nan", "model"])
def test_invalid_snapshot_is_rejected_without_fallback(tmp_path, mutation):
    _, content, layout, printed, snapshot = fixture(tmp_path)
    if mutation == "hash":
        snapshot["sourceSha256"] = "0" * 64
    elif mutation == "length":
        snapshot["rows"].pop()
    elif mutation == "nan":
        snapshot["columns"][0] = float("nan")
    else:
        snapshot["model"] = "unknown"
    with pytest.raises(AppError) as error:
        project_preview_geometry(content, layout, printed, snapshot)
    assert error.value.code == "PRINT_GEOMETRY_CHANGED"
