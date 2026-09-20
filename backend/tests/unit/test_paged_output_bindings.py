import copy

import pytest

from app.core.errors import AppError
from app.services.paged_document_snapshot import output_bindings


def pages():
    return [
        {
            "pageId": f"physical-page-{index}",
            "pageRange": area,
            "fieldMappings": [
                {
                    "mappingId": f"arbitrary-{index}",
                    "targetPath": f"customFields.value_{index}",
                    "label": "同じ表示名",
                    "x": 10,
                    "y": 10,
                }
            ],
            "fields": [
                {
                    "cellId": f"physical-cell-{index}",
                    "sourceRange": source,
                    "sourceParts": [{"cellId": f"part-{index}", "sourceRange": source}],
                    "bindings": [
                        {
                            "mappingId": f"arbitrary-{index}",
                            "targetPath": f"customFields.value_{index}",
                        }
                    ],
                }
            ],
        }
        for index, (area, source) in enumerate([("A1:D8", "C2:D2"), ("A9:D16", "C10:D10")])
    ]


def test_output_ownership_uses_explicit_page_and_source_range_not_label_or_geometry():
    original = pages()
    frozen = copy.deepcopy(original)
    result = output_bindings(list(reversed(original)))
    assert [(b["pageId"], b["mappingId"], b["anchorCell"]) for b in result] == [
        ("physical-page-1", "arbitrary-1", "C10"),
        ("physical-page-0", "arbitrary-0", "C2"),
    ]
    assert result[0]["sourceParts"] == frozen[1]["fields"][0]["sourceParts"]
    result[0]["sourceParts"][0]["cellId"] = "mutated return value"
    assert original == frozen


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "wrong_page",
        "range",
        "target",
        "duplicate",
        "duplicate_mapping",
        "duplicate_target",
    ],
)
def test_invalid_output_ownership_is_not_guessed(kind):
    value = pages()
    if kind == "missing":
        value[1]["fields"][0]["bindings"] = []
    elif kind == "wrong_page":
        value[1]["fields"][0]["bindings"] = copy.deepcopy(value[0]["fields"][0]["bindings"])
    elif kind == "range":
        value[1]["fields"][0]["sourceRange"] = "C2:D2"
    elif kind == "target":
        value[1]["fields"][0]["bindings"][0]["targetPath"] = "customFields.value_0"
    elif kind == "duplicate":
        value[1]["fields"].append(copy.deepcopy(value[1]["fields"][0]))
    elif kind == "duplicate_mapping":
        value[1]["fieldMappings"].append(copy.deepcopy(value[0]["fieldMappings"][0]))
    else:
        value[1]["fieldMappings"][0]["targetPath"] = "customFields.value_0"
    with pytest.raises(AppError) as error:
        output_bindings(value)
    assert error.value.code == "PAGE_OUTPUT_MAPPING_INVALID"
