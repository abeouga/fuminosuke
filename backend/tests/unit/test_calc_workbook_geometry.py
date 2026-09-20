from copy import deepcopy

import pytest

from app.core.errors import AppError
from app.domain.calc_print_grid import MODEL
from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services import calc_workbook_geometry as calc
from app.services import wsl_workbook_geometry as historical
from tests.unit.test_wsl_workbook_geometry import fods


def source_and_evidence():
    fields = [{"cellId": "source-" + str(row), "sourceRange": f"A{row}:B{row}",
               "startRow": row, "endRow": row, "startCol": 1, "endCol": 2,
               "x": 0, "y": row - 1, "width": 2, "height": 1,
               "bindings": [{"mappingId": f"meaning-{row}", "targetPath": f"custom.{row}",
                             "sourceRegionId": f"meaning-{row}"}]}
              for row in (1, 3)]
    source = {"sha256": "f" * 64, "sheetName": "Sample", "startRow": 1, "endRow": 4,
              "startCol": 1, "endCol": 3, "startColumn": "A", "endColumn": "C",
              "hiddenRows": [], "hiddenColumns": [], "cells": deepcopy(fields),
              "fields": fields, "validBreakRows": [1, 2, 3], "manualBreakRows": [],
              "unsupported": [], "warnings": [], "importedSettings": {}}
    evidence = calc.dimensions(fods(), "Sample", 4, 3)
    evidence.update(sourceSha256=source["sha256"],
                    importReport={"fodsSha256": evidence["fodsSha256"]})
    return source, evidence


def observation_for(source, evidence):
    from app.domain.calc_print_grid import dimension_grid

    result = {"policy": "wsl-uno-dimension-v1",
              "request": {"sheetName": source["sheetName"], "rows": 4, "columns": 3},
              "sourceSha256": source["sha256"], "fodsSha256": evidence["fodsSha256"]}
    for axis in ("columns", "rows"):
        cumulative, values = 0, []
        for twips in dimension_grid(evidence[axis], evidence["hidden" + axis.title()])["twips"]:
            first = (cumulative * 127 + 36) // 72
            cumulative += twips
            values.append([(twips * 127 + 36) // 72,
                           (cumulative * 127 + 36) // 72 - first + 1, True])
        result[axis] = values
    return {"runtimeImage": {"sha256": "a" * 64}, "sourceSha256": source["sha256"],
            "fodsSha256": evidence["fodsSha256"], "dimensionObservation": result}


@pytest.mark.parametrize("change", [
    None, "missing", "source", "sheet", "hash", "runtime", "count", "scalar",
    "wide", "wrapped", "visibility", "bool", "request_bool",
])
def test_same_import_observation_verifies_identity_and_wide_dimension(change):
    source, evidence = source_and_evidence()
    report = observation_for(source, evidence)
    obs = report["dimensionObservation"]
    if change == "missing":
        report.pop("dimensionObservation")
    elif change == "source":
        report["sourceSha256"] = "a" * 64
    elif change == "sheet":
        obs["request"]["sheetName"] = "another"
    elif change == "hash":
        obs["fodsSha256"] = "a" * 64
    elif change == "runtime":
        report["runtimeImage"]["sha256"] = None
    elif change == "count":
        obs["rows"].pop()
    elif change == "scalar":
        obs["rows"][0][0] += 1
    elif change == "wide":
        obs["rows"][0][1] += 1
    elif change == "wrapped":
        # Same plausible small scalar/FODS value, but the real wide size is large.
        obs["rows"][0][1] += 65536
    elif change == "visibility":
        obs["rows"][0][2] = False
    elif change == "bool":
        obs["rows"][0][1] = True
    elif change == "request_bool":
        obs["request"]["rows"] = True
    before = deepcopy((source, evidence, report))
    if change is None:
        calc.verify_dimension_observation(evidence, report, source["sheetName"], source["sha256"])
    else:
        with pytest.raises(AppError) as error:
            calc.verify_dimension_observation(
                evidence, report, source["sheetName"], source["sha256"])
        assert error.value.code == "PRINT_GEOMETRY_CHANGED"
    assert (source, evidence, report) == before


def test_new_source_separates_nominal_fit_from_grid_coordinates_and_preserves_identity():
    source, evidence = source_and_evidence()
    original = deepcopy((source, evidence))
    projected = calc.apply_dimensions(source, evidence)
    assert (source, evidence) == original
    assert projected["geometryModelVersion"] == MODEL
    assert projected["nominalWidth"] == 84
    assert projected["width"] == pytest.approx(3 * 987 * 72 / 2540)
    assert projected["nominalHeight"] == 72
    assert projected["height"] == pytest.approx(4 * 635 * 72 / 2540)
    for actual, old in zip(projected["fields"], source["fields"], strict=True):
        for key in ("cellId", "sourceRange", "bindings", "startRow", "endRow"):
            assert actual[key] == old[key]
    plan = page_plan(projected, PrintLayoutRequest(
        base_revision="0" * 64, break_after_rows=[2], horizontal_centering=True))
    assert [p["range"] for p in plan["pages"]] == ["A1:C2", "A3:C4"]
    assert plan["ocrReady"] is False
    assert [p["fields"][0]["bindings"] for p in plan["pages"]] == [
        f["bindings"] for f in source["fields"]]
    assert plan["pages"][0]["fields"][0]["rectangle"] == (
        plan["pages"][1]["fields"][0]["rectangle"])
    assert plan["pages"][1]["transform"]["originY"] == projected["rowBoundaries"][2]["y"]


@pytest.mark.parametrize("unit", ["2.54cm", "25.4mm", "72pt", "0.38900in"])
def test_new_model_requires_the_fixed_export_without_changing_historical_parser(unit):
    assert historical.dimensions(fods(width=unit), "Sample", 4, 3)["model"] == historical.MODEL
    with pytest.raises(AppError):
        calc.dimensions(fods(width=unit), "Sample", 4, 3)


def test_integer_origin_rejection_is_a_domain_error_and_keeps_source(monkeypatch):
    from app.domain import print_layout

    source, evidence = source_and_evidence()
    projected = calc.apply_dimensions(source, evidence)
    original = deepcopy(projected)

    def reject(*args, **kwargs):
        raise ValueError("Printed content exceeds the nominal page extent")

    monkeypatch.setattr(print_layout, "frame_origin", reject)
    with pytest.raises(AppError) as error:
        page_plan(projected, PrintLayoutRequest(base_revision="0" * 64, break_after_rows=[]))
    assert error.value.code == "PAGE_GEOMETRY_MISMATCH"
    assert error.value.status == 422
    assert projected == original


@pytest.mark.parametrize("change", ["model", "exportPolicy", "fodsSha256", "report",
                                    "columns", "source", "visibility"])
def test_new_evidence_change_is_rejected_without_mutating_source(change):
    source, evidence = source_and_evidence()
    original = deepcopy(source)
    if change in {"model", "exportPolicy", "fodsSha256"}:
        evidence[change] = "changed"
    elif change == "report":
        evidence["importReport"]["fodsSha256"] = "a" * 64
    elif change == "columns":
        evidence["columns"][0] = 28.01
    elif change == "source":
        evidence["sourceSha256"] = "e" * 64
    else:
        evidence["hiddenColumns"] = [1]
    with pytest.raises(AppError) as error:
        calc.apply_dimensions(source, evidence)
    assert error.value.code == "PRINT_GEOMETRY_CHANGED"
    assert source == original
