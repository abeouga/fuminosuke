"""Diagnostic projection does not update any source cell or saved plan."""
from copy import deepcopy

from scripts.verify_calc_grid_holdout import project


def test_candidate_projection_retains_ranges_and_does_not_mutate_its_input():
    plan = {
        "paperSize": "A4", "orientation": "portrait", "commonScale": .69, "dpi": 150,
        "margins": {"left": 18, "right": 22, "top": 19, "bottom": 13},
        "horizontalCentering": True, "verticalCentering": False,
        "geometryEvidence": {"columns": [11.592, 20.1528],
                             "rows": [11.592, 20.1528, 11.592, 20.1528],
                             "hiddenColumns": [], "hiddenRows": []},
        "pages": [{"page": p, "startRow": r, "endRow": r + 1,
                   "startCol": 1, "endCol": 2, "fields": [],
                   "cells": [{"sourceRange": f"A{r}:B{r + 1}",
                              "startRow": r, "endRow": r + 1, "startCol": 1, "endCol": 2,
                              "rectangle": {"x": 0, "y": 0, "width": 2, "height": 2}}]}
                  for p, r in ((1, 1), (2, 3))],
    }
    before = deepcopy(plan)
    result = project(plan)
    assert plan == before
    assert [p["cells"] for p in result["pages"]] == [p["cells"] for p in before["pages"]]
    fields = [p["fields"][0] for p in result["pages"]]
    assert [f["sourceRange"] for f in fields] == ["A1:B2", "A3:B4"]
    assert [f["cellId"] for f in fields] == ["holdout:A1:B2", "holdout:A3:B4"]
    assert fields[0]["rectangle"] == fields[1]["rectangle"]
    assert fields[0]["bindings"] != fields[1]["bindings"]
    for field in fields:
        assert field["rectangle"]["width"] > 1
        assert field["rectangle"]["height"] > 1
        assert field["bindings"][0]["mappingId"] == field["cellId"]
        assert field["bindings"][0]["sourceRegionId"] == field["cellId"]
