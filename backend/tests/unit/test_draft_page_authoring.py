from __future__ import annotations

import copy
import hashlib
from io import BytesIO

import pytest
from openpyxl import load_workbook
from openpyxl.styles import Alignment
from PIL import Image

from app.core.errors import AppError
from app.domain.print_layout import page_plan
from app.services.draft_page_geometry import draft_geometry
from app.services.print_layout_renderer import render_page
from app.services.print_layout_source import DRAFT_GEOMETRY_VERSION, read_source
from app.services.print_workbook import workbook_copy
from tests.unit.test_print_layout import request, workbook_bytes


def draft_workbook():
    book = load_workbook(BytesIO(workbook_bytes(manual=31)))
    sheet = book.active
    sheet.print_area = "A1:D63"
    sheet.column_dimensions["A"].hidden = True
    sheet["A2"] = "hidden-management-marker"
    sheet["B2"].alignment = Alignment(shrinkToFit=True)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def test_hidden_column_and_shrink_are_draft_only_with_original_breaks():
    content = draft_workbook()
    original_sha = hashlib.sha256(content).hexdigest()
    legacy = read_source(content, "帳票", {"cells": []})
    assert len(legacy["unsupported"]) == 2
    source = read_source(content, "帳票", {"cells": []}, draft_support=True)
    assert source["unsupported"] == []
    assert source["draftFeatures"] == ["hidden-dimensions", "shrink-to-fit"]
    assert source["manualBreakRows"] == [31]
    assert not any(c["text"] == "hidden-management-marker" for c in source["cells"])
    assert next(c for c in source["cells"] if c["startCol"] == 2)["x"] == 0
    plan = page_plan(draft_geometry(source, None), request())
    assert [p["range"] for p in plan["pages"]] == ["A1:D31", "A32:D63"]
    assert plan["geometryModelVersion"] == DRAFT_GEOMETRY_VERSION
    assert plan["ocrReady"] is False
    assert hashlib.sha256(content).hexdigest() == original_sha
    # Draft display support must not open the strict Office/export adapter.
    with pytest.raises(AppError) as refused:
        workbook_copy(content, plan)
    assert refused.value.code == "UNSUPPORTED_XLSX_FEATURE"


def test_office_snapshot_dimensions_exclude_hidden_columns_without_mutating_evidence():
    content = draft_workbook()
    source = read_source(content, "帳票", {"cells": [{"id": "xlsx_C32", "role": "input"}]},
                         draft_support=True)
    evidence = {"columns": [20, 30, 40, 50], "rows": [25] * 63,
                "sourceSha256": source["sha256"], "sheetName": "帳票"}
    before = copy.deepcopy(evidence)
    actual = draft_geometry(source, evidence)
    assert actual["width"] == 120
    assert actual["fields"][0]["x"] == 30
    assert actual["fields"][0]["y"] == 31 * 25
    assert evidence == before
    evidence["sourceSha256"] = "0" * 64
    with pytest.raises(AppError, match="下書き"):
        draft_geometry(source, evidence)


@pytest.mark.parametrize("feature", ["rotation", "hidden-field", "merge-break"])
def test_unsupported_geometry_remains_rejected(feature):
    content = draft_workbook()
    book = load_workbook(BytesIO(content))
    fields = []
    if feature == "rotation":
        book.active["B2"].alignment = Alignment(textRotation=90)
    elif feature == "hidden-field":
        fields = [{"id": "xlsx_A2", "role": "input"}]
    else:
        book.active.merge_cells("B30:B32")
    data = BytesIO()
    book.save(data)
    source = read_source(data.getvalue(), "帳票", {"cells": fields}, draft_support=True)
    with pytest.raises(AppError):
        page_plan(draft_geometry(source, None), request(break_after_rows=[31]))


def test_shrink_reference_keeps_all_text_inside_the_cell():
    cell = {"rectangle": {"x": 0, "y": 0, "width": 180, "height": 35},
            "fontSize": 35, "bold": False, "wrap": False, "align": "left",
            "fill": "#ffffff", "borders": {}, "text": "ABCDE FGHIJ KLMNO", "shrinkToFit": True}
    page = {"width": 200, "height": 50, "transform": {"scale": 1}, "cells": [cell]}
    original = copy.deepcopy(page)
    with Image.open(BytesIO(render_page(page))) as result:
        import PIL.ImageChops
        ink = PIL.ImageChops.difference(result, Image.new("RGB", result.size, "white"))
        bounds = ink.getbbox()
        assert bounds and bounds[2] < 180 and bounds[3] < 35
    assert page == original
