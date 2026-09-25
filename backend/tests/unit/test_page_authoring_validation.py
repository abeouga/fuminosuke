from __future__ import annotations

import copy

import pytest

from app.core.errors import AppError
from app.services.page_authoring_validation import validate_page_edit


def layout_with_defaults():
    return {"cells": [{"id": "a", "x": 1, "y": 2, "width": 3, "height": 4,
                       "role": "input", "shapeType": "rectangle", "vertices": [],
                       "virtual": False}], "cellMerges": []}


@pytest.mark.parametrize("key", ["shapeType", "vertices", "virtual", "all"])
def test_missing_defaults_are_equivalent_without_mutating_either_layout(key):
    current = layout_with_defaults()
    legacy = copy.deepcopy(current)
    for name in ([key] if key != "all" else ["shapeType", "vertices", "virtual"]):
        legacy["cells"][0].pop(name)
    before = copy.deepcopy((legacy, current))
    validate_page_edit(legacy, current)
    validate_page_edit(current, legacy)
    assert (legacy, current) == before


@pytest.mark.parametrize("key,value", [
    ("shapeType", None), ("vertices", None), ("virtual", None),
    ("shapeType", "polygon"), ("vertices", [{"x": 1, "y": 2}]), ("virtual", True),
])
def test_explicit_nondefault_shape_values_are_not_treated_as_omitted(key, value):
    previous = layout_with_defaults()
    proposed = copy.deepcopy(previous)
    proposed["cells"][0][key] = value
    with pytest.raises(AppError) as error:
        validate_page_edit(previous, proposed)
    assert error.value.code == "PRINT_AUTHORING_GEOMETRY_CHANGED"


def merged_layout():
    part = layout_with_defaults()["cells"][0]
    return {"cells": [{**part, "id": "parent"}], "cellMerges": [
        {"id": "merge", "parentCellId": "parent", "direction": "horizontal",
         "parts": [{"cell": part, "partType": "input", "literal": ""}]}]}


def test_legacy_merge_comparison_does_not_mutate_parts_or_metadata():
    current = merged_layout()
    legacy = copy.deepcopy(current)
    for key in ("shapeType", "vertices", "virtual"):
        legacy["cellMerges"][0]["parts"][0]["cell"].pop(key)
    before = copy.deepcopy((legacy, current))
    validate_page_edit(legacy, current)
    assert (legacy, current) == before


@pytest.mark.parametrize("mutation", ["literal", "type", "role", "text", "direction", "id"])
def test_existing_merge_changes_other_than_missing_shape_defaults_are_rejected(mutation):
    previous = merged_layout()
    proposed = copy.deepcopy(previous)
    part = proposed["cellMerges"][0]["parts"][0]
    if mutation == "literal":
        part["literal"] = "changed"
    elif mutation == "type":
        part["partType"] = "literal"
    elif mutation in {"role", "text"}:
        part["cell"][mutation] = "changed"
    else:
        proposed["cellMerges"][0][mutation] = "changed"
    with pytest.raises(AppError) as error:
        validate_page_edit(previous, proposed)
    assert error.value.code == "PRINT_AUTHORING_GEOMETRY_CHANGED"
