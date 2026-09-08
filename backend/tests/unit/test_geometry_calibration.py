from __future__ import annotations

import copy
import json
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services.geometry_calibration import compare_boundaries, grid_boundaries
from app.services.print_layout_source import read_source
from app.services.reference_geometry import measure_page
from app.services.workbook_geometry import (
    GEOMETRY_MODEL_VERSION,
    apply_geometry,
    normal_font,
    read_office_dimensions,
)
from tests.unit.test_print_layout import workbook_bytes


@pytest.mark.parametrize("thickness", [1, 2, 3])
@pytest.mark.parametrize("position", [100.0, 100.25, 100.5])
@pytest.mark.parametrize("kernel", [1, 9, 15])
@pytest.mark.parametrize("axis", ["x", "y"])
def test_detector_ground_truth_without_prediction_window(thickness, position, kernel, axis):
    centers = np.arange(240, dtype=float)
    coverage = np.maximum(0, np.minimum(centers + .5, position + thickness / 2)
                          - np.maximum(centers - .5, position - thickness / 2))
    ink = np.zeros((240, 240), dtype=np.uint8)
    ink[20:220, :] = np.rint(coverage * 255).astype(np.uint8)
    if axis == "y":
        ink = ink.T
    image = Image.fromarray(255 - ink)
    output = BytesIO()
    image.save(output, format="PNG")
    found = grid_boundaries(output.getvalue(), axis, kernel)
    assert len(found) == 1
    assert abs(found[0] - position) <= .002


def test_topology_mismatch_cannot_be_reported_as_zero_error():
    assert compare_boundaries([10, 20, 30], [10, 20])["status"] == "UNMEASURABLE"
    values = compare_boundaries([10, 20, 30], [11, 23, 35])
    assert values["offsetError"] == 1
    assert values["endCumulativeDrift"] == 4
    assert [i["deltaError"] for i in values["intervals"]] == [2, 2]


def test_geometry_only_changes_source_positions_not_identity_or_original():
    source = read_source(workbook_bytes(rows=12), "帳票", {"cells": [
        {"id": "xlsx_C2", "role": "input"}]})
    before = copy.deepcopy(source)
    dims = dict(model=GEOMETRY_MODEL_VERSION, columns=[48.15] * 4, rows=[15] + [24] * 11)
    updated = apply_geometry(source, dims)
    assert source == before
    old = source["fields"][0]
    new = updated["fields"][0]
    assert new["x"] != old["x"]
    for key in ("cellId", "sourceRange", "startRow", "endRow", "startCol", "endCol"):
        assert new[key] == old[key]
    assert updated["geometryModelVersion"] == GEOMETRY_MODEL_VERSION


def test_normal_font_uses_style_xf_and_available_file():
    content = (Path(__file__).parents[3] / "docs/reference-renderer-evidence/calibration/"
               "font-Meiryo/source.xlsx").read_bytes()
    font = normal_font(content)
    assert font["family"] == "Meiryo"
    assert font["fontId"] != 0
    assert font["sizePt"] == 11
    assert font["fallbackVerified"] is False


def test_fods_repeated_dimensions_are_bounded_and_recover_twips():
    content = b'''<office:document
      xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
      xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
      xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
      <style:style style:name="c"><style:table-column-properties style:column-width="1.699cm"/>
      </style:style><style:style style:name="r">
      <style:table-row-properties style:row-height="0.847cm"/></style:style>
      <table:table table:name="fixture"><table:table-column table:style-name="c"
      table:number-columns-repeated="16384"/><table:table-row table:style-name="r"
      table:number-rows-repeated="1048576"/></table:table></office:document>'''
    dims = read_office_dimensions(content, "fixture", 3, 2)
    assert dims["columns"] == [48.15, 48.15]
    assert dims["rows"] == [24, 24, 24]


def test_saved_office_raster_reproduces_old_drift_and_new_field_geometry():
    folder = (Path(__file__).parents[3] / "docs/reference-renderer-evidence/"
              "calibration/explicit843")
    content = (folder / "source.xlsx").read_bytes()
    evidence = json.loads((folder / "measurement.json").read_text())
    source = read_source(content, "Calibration", {"cells": [
        {"id": "xlsx_C2", "role": "input"}]})
    request = PrintLayoutRequest(base_revision="0" * 64, break_after_rows=[])
    old = page_plan(source, request)
    new = page_plan(apply_geometry(source, evidence["geometry"]), request)
    image = (folder / "office.png").read_bytes()
    old_result = measure_page(old["pages"][0], image)
    new_result = measure_page(new["pages"][0], image)
    assert old_result["maxErrorPx"] >= 7
    assert old_result["status"] == "FAIL"
    assert new_result["status"] == "PASS"
    assert new_result["maxErrorPx"] <= 2
    assert new_result["thresholdQualified"] is False
