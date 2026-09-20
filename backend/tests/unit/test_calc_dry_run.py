from copy import deepcopy

import pytest

from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services import wsl_workbook_geometry
from scripts.dry_run_print_preparation import expected_calc_plan, field_identity
from tests.unit.test_calc_workbook_geometry import source_and_evidence


def test_prediction_keeps_input_and_all_non_geometry_field_metadata():
    source, evidence = source_and_evidence()
    evidence["model"] = wsl_workbook_geometry.MODEL
    old_source = wsl_workbook_geometry.apply_dimensions(source, evidence)
    request = PrintLayoutRequest(base_revision="0" * 64, break_after_rows=[2])
    saved = page_plan(old_source, request)
    initial = {"saved": saved, "source": old_source, "revision": "0" * 64}
    original = deepcopy(initial)
    candidate = expected_calc_plan(initial)
    assert initial == original
    assert candidate["geometryModelVersion"] == "wsl-calc-print-grid-v1"
    assert [p["range"] for p in candidate["pages"]] == [p["range"] for p in saved["pages"]]
    assert field_identity(candidate["pages"]) == field_identity(saved["pages"])
    candidate["pages"][0]["fields"][0]["sourceParts"] = [{"cellId": "unexpected"}]
    assert field_identity(candidate["pages"]) != field_identity(saved["pages"])


@pytest.mark.parametrize("key", ["cellId", "sourceRange", "bindings", "startRow", "extraMetadata"])
def test_identity_comparison_does_not_hide_non_geometry_changes(key):
    before = [{"fields": [{"cellId": "a", "sourceRange": "A1:A1", "bindings": [],
                           "startRow": 1, "rectangle": {"x": 1}, "x": 1}]}]
    after = deepcopy(before)
    after[0]["fields"][0]["rectangle"]["x"] = 2
    after[0]["fields"][0]["x"] = 2
    assert field_identity(before) == field_identity(after)
    after[0]["fields"][0][key] = "changed"
    assert field_identity(before) != field_identity(after)


@pytest.mark.parametrize("model", ["fixed-7px-plus-5-v1", "wsl-calc-print-grid-v1", "unknown"])
def test_prediction_never_relabels_unmeasured_or_already_migrated_sources(model):
    with pytest.raises(ValueError, match="existing measured WSL draft"):
        expected_calc_plan({"saved": {"geometryModelVersion": model}})
