from types import SimpleNamespace

from app.layout_analysis.models import LayoutDefinition
from app.services.ocr_result_service import OcrResultService
from app.services.review_display_structure import build_review_display_structure


def _cell(
    cell_id: str,
    role: str,
    x: int,
    y: int,
    *,
    text: str = "",
) -> dict[str, object]:
    return {
        "id": cell_id,
        "role": role,
        "x": x,
        "y": y,
        "width": 80,
        "height": 24,
        "confidence": 1,
        "text": text,
        "printedLabelText": text,
    }


def test_review_structure_prioritizes_records_and_keeps_parent_ids_distinct() -> None:
    layout = LayoutDefinition.model_validate(
        {
            "canonicalWidth": 800,
            "canonicalHeight": 1000,
            "cells": [
                _cell("parent_a", "parent_label", 0, 0, text="帰省先"),
                _cell("parent_b", "parent_label", 400, 0, text="緊急連絡先"),
                _cell("column_city", "label", 0, 100, text="市区町村"),
                _cell("column_note", "label", 100, 100, text="備考"),
                _cell("row_1_city", "input", 0, 140),
                _cell("row_1_note", "input", 100, 140),
                _cell("row_2_city", "input", 0, 180),
                _cell("parent_a_city", "input", 0, 40),
                {
                    **_cell("residence_type", "mark", 0, 70),
                    "markSets": [
                        {
                            "id": "choice",
                            "targetPath": "customFields.residence_type",
                            "recognitionMode": "manual",
                            "selectionMode": "single",
                            "options": [
                                {
                                    "id": "city",
                                    "label": "市",
                                    "value": "city",
                                    "x": 0,
                                    "y": 0,
                                    "width": 40,
                                    "height": 24,
                                }
                            ],
                        }
                    ],
                },
                _cell("parent_b_city", "input", 400, 40),
                _cell("standalone", "input", 0, 260),
            ],
            "relations": [
                {
                    "id": "relation_record_city",
                    "inputCellId": "row_1_city",
                    "parentLabelCellId": "parent_a",
                    "axis": "vertical",
                    "order": 1,
                    "targetPath": "customFields.record_city_1",
                },
                {
                    "id": "relation_record_note",
                    "inputCellId": "row_1_note",
                    "axis": "vertical",
                    "order": 1,
                    "targetPath": "customFields.record_note_1",
                },
                {
                    "id": "relation_record_city_2",
                    "inputCellId": "row_2_city",
                    "axis": "vertical",
                    "order": 1,
                    "targetPath": "customFields.record_city_2",
                },
                {
                    "id": "relation_parent_a",
                    "inputCellId": "parent_a_city",
                    "parentLabelCellId": "parent_a",
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "customFields.parent_a_city",
                },
                {
                    "id": "relation_parent_b",
                    "inputCellId": "parent_b_city",
                    "parentLabelCellId": "parent_b",
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "customFields.parent_b_city",
                },
                {
                    "id": "relation_parent_a_duplicate",
                    "inputCellId": "parent_a_city",
                    "parentLabelCellId": "parent_b",
                    "axis": "horizontal",
                    "order": 2,
                    "targetPath": "customFields.parent_a_city",
                },
                {
                    "id": "relation_residence_type",
                    "inputCellId": "residence_type",
                    "parentLabelCellId": "parent_a",
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "customFields.residence_type",
                },
                {
                    "id": "relation_standalone",
                    "inputCellId": "standalone",
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "customFields.standalone",
                },
            ],
            "recordSections": [
                {
                    "id": "record_section",
                    "title": "訪問履歴",
                    "collectionPath": "genericRecords.visits",
                    "recordType": "generic",
                    "recordLabelCellIds": ["column_city", "column_note"],
                }
            ],
            "recordGroups": [
                {
                    "id": "record_row_1",
                    "sectionId": "record_section",
                    "order": 1,
                    "x": 0,
                    "y": 140,
                    "width": 180,
                    "height": 24,
                    "inputCellIds": ["row_1_city", "row_1_note"],
                },
                {
                    "id": "record_row_2",
                    "sectionId": "record_section",
                    "order": 2,
                    "x": 0,
                    "y": 180,
                    "width": 80,
                    "height": 24,
                    "inputCellIds": ["row_2_city"],
                },
            ],
        }
    )
    mappings = [
        {
            "mappingId": f"layout_{cell_id}",
            "targetPath": target_path,
            "label": label,
            "x": x,
            "y": y,
        }
        for cell_id, target_path, label, x, y in [
            ("row_1_city", "customFields.record_city_1", "市区町村", 0, 140),
            ("row_1_note", "customFields.record_note_1", "備考", 100, 140),
            ("row_2_city", "customFields.record_city_2", "市区町村", 0, 180),
            ("parent_a_city", "customFields.parent_a_city", "市区町村", 0, 40),
            ("parent_b_city", "customFields.parent_b_city", "市区町村", 400, 40),
            ("standalone", "customFields.standalone", "自由記入", 0, 260),
        ]
    ]
    mappings.append(
        {
            "mappingId": "layout_residence_type_choice",
            "targetPath": "customFields.residence_type",
            "label": "区分",
            "x": 0,
            "y": 70,
        }
    )

    structure = build_review_display_structure(layout, mappings)

    record = structure["recordSections"][0]  # type: ignore[index]
    assert [column["id"] for column in record["columns"]] == [  # type: ignore[index]
        "column_city",
        "column_note",
    ]
    assert record["rows"][1]["cells"][1]["fieldId"] is None  # type: ignore[index]
    assert [section["id"] for section in structure["parentSections"]] == [  # type: ignore[index]
        "parent_a",
        "parent_b",
    ]
    assert structure["parentSections"][0]["fieldIds"] == [  # type: ignore[index]
        "layout_parent_a_city",
        "layout_residence_type_choice",
    ]
    assert structure["parentSections"][1]["fieldIds"] == [  # type: ignore[index]
        "layout_parent_b_city"
    ]
    assert structure["independentFieldIds"] == ["layout_standalone"]
    grouped_field_ids = [
        field_id
        for section in structure["parentSections"]  # type: ignore[union-attr]
        for field_id in section["fieldIds"]
    ]
    assert len(grouped_field_ids) == len(set(grouped_field_ids))


def test_review_structure_groups_independent_values_by_label_cell_identity() -> None:
    layout = LayoutDefinition.model_validate(
        {
            "canonicalWidth": 800,
            "canonicalHeight": 1000,
            "cells": [
                _cell("furigana_label", "label", 0, 0, text="フリガナ"),
                _cell("other_furigana_label", "label", 0, 80, text="フリガナ"),
                _cell("furigana_1", "input", 100, 0),
                _cell("furigana_2", "input", 200, 0),
                _cell("other_furigana", "input", 100, 80),
                _cell("unlabelled", "input", 100, 160),
            ],
            "relations": [
                {
                    "id": "relation_furigana_1",
                    "inputCellId": "furigana_1",
                    "labelCellIds": ["furigana_label"],
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "customFields.furigana_1",
                },
                {
                    "id": "relation_furigana_2",
                    "inputCellId": "furigana_2",
                    "labelCellIds": ["furigana_label"],
                    "axis": "horizontal",
                    "order": 2,
                    "targetPath": "customFields.furigana_2",
                },
                {
                    "id": "relation_other_furigana",
                    "inputCellId": "other_furigana",
                    "labelCellIds": ["other_furigana_label"],
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "customFields.other_furigana",
                },
                {
                    "id": "relation_unlabelled",
                    "inputCellId": "unlabelled",
                    "labelCellIds": [],
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "customFields.unlabelled",
                },
            ],
            "recordSections": [],
            "recordGroups": [],
        }
    )
    mappings = [
        {
            "mappingId": f"layout_{cell_id}",
            "targetPath": target_path,
            "label": label,
        }
        for cell_id, target_path, label in [
            ("furigana_1", "customFields.furigana_1", "フリガナ"),
            ("furigana_2", "customFields.furigana_2", "フリガナ"),
            ("other_furigana", "customFields.other_furigana", "フリガナ"),
            ("unlabelled", "customFields.unlabelled", "フリガナ"),
        ]
    ]

    fields = build_review_display_structure(layout, mappings)["fields"]
    group_ids = {field["id"]: field["groupId"] for field in fields}  # type: ignore[index]

    assert group_ids["layout_furigana_1"] == group_ids["layout_furigana_2"]
    assert group_ids["layout_other_furigana"] != group_ids["layout_furigana_1"]
    assert group_ids["layout_unlabelled"] == "field:layout_unlabelled"


def test_old_job_without_review_snapshot_falls_back_to_independent_fields() -> None:
    service = OcrResultService.__new__(OcrResultService)
    job = SimpleNamespace(
        template_version_id="version-that-may-have-changed",
        config_snapshot_json={
            "fieldMappings": [
                {
                    "mappingId": "legacy_mapping",
                    "targetPath": "customFields.legacy_value",
                    "label": "旧項目",
                }
            ]
        },
    )

    structure = service._review_structure(job)

    assert structure["recordSections"] == []
    assert structure["parentSections"] == []
    assert structure["independentFieldIds"] == ["legacy_mapping"]


def test_multiple_mark_fields_in_one_record_cell_keep_record_priority() -> None:
    layout = LayoutDefinition.model_validate(
        {
            "canonicalWidth": 320,
            "canonicalHeight": 100,
            "cells": [
                _cell("status_label", "label", 10, 10, text="分娩状況"),
                {
                    **_cell("status_mark", "mark", 100, 10),
                    "width": 180,
                    "markSets": [
                        {
                            "id": "outcome",
                            "targetPath": "calvings.0.outcome",
                            "options": [
                                {
                                    "id": "twin",
                                    "label": "双",
                                    "value": "twin",
                                    "x": 0,
                                    "y": 0,
                                    "width": 40,
                                    "height": 24,
                                }
                            ],
                        },
                        {
                            "id": "sex",
                            "targetPath": "calvings.0.sex",
                            "options": [
                                {
                                    "id": "male",
                                    "label": "♂",
                                    "value": "male",
                                    "x": 100,
                                    "y": 0,
                                    "width": 40,
                                    "height": 24,
                                }
                            ],
                        },
                    ],
                },
            ],
            "relations": [
                {
                    "id": "status_relation",
                    "inputCellId": "status_mark",
                    "labelCellIds": ["status_label"],
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "calvings.0.outcome",
                }
            ],
            "recordSections": [
                {
                    "id": "calvings",
                    "title": "繁殖履歴",
                    "displayName": "出産歴",
                    "collectionPath": "calvings",
                    "recordType": "calving",
                    "recordLabelCellIds": ["status_label"],
                }
            ],
            "recordGroups": [
                {
                    "id": "calving_1",
                    "sectionId": "calvings",
                    "order": 1,
                    "x": 100,
                    "y": 10,
                    "width": 180,
                    "height": 24,
                    "inputCellIds": ["status_mark"],
                }
            ],
        }
    )
    mappings = [
        {
            "mappingId": "layout_status_mark_outcome",
            "targetPath": "calvings.0.outcome",
            "label": "分娩状況",
        },
        {
            "mappingId": "layout_status_mark_sex",
            "targetPath": "calvings.0.sex",
            "label": "性別",
        },
    ]

    structure = build_review_display_structure(layout, mappings)

    record = structure["recordSections"][0]  # type: ignore[index]
    assert record["title"] == "出産歴"  # type: ignore[index]
    assert [column["label"] for column in record["columns"]] == [  # type: ignore[index]
        "分娩状況",
        "性別",
    ]
    assert [cell["fieldId"] for cell in record["rows"][0]["cells"]] == [  # type: ignore[index]
        "layout_status_mark_outcome",
        "layout_status_mark_sex",
    ]
    assert structure["independentFieldIds"] == []
