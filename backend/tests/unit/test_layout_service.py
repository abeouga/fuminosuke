import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.layout_analysis.detector import TextRegion
from app.layout_analysis.models import (
    LayoutCell,
    LayoutDefinition,
    LayoutLabelPoolEntry,
    LayoutMarkOption,
    LayoutMarkSet,
    LayoutRecordGroup,
    LayoutRecordSection,
    LayoutRelation,
)
from app.services.label_cell_ocr_service import (
    protected_label_cell_ids,
    recognize_label_cells,
)
from app.services.layout_service import LayoutService


def test_record_section_migrates_the_legacy_single_label_anchor() -> None:
    section = LayoutRecordSection.model_validate(
        {
            "id": "sale",
            "title": "売却子牛一覧",
            "collectionPath": "saleCalves",
            "recordType": "saleCalf",
            "recordLabelCellId": "price_label",
            "parentLabelCellId": None,
            "source": "manual",
        }
    )

    assert section.record_label_cell_ids == ["price_label"]
    assert section.model_dump(mode="json", by_alias=True)["recordLabelCellIds"] == [
        "price_label"
    ]


def test_layout_preserves_a_reusable_label_candidate_pool() -> None:
    layout = LayoutDefinition(
        canonical_width=300,
        canonical_height=200,
        label_pool=[
            LayoutLabelPoolEntry(
                id="sale_price",
                label="価格",
                target_path="customFields.salePrice",
                section="追加項目",
                aliases=["売価", "販売価格", "売価"],
            )
        ],
    )

    dumped = layout.model_dump(mode="json", by_alias=True)
    assert dumped["labelPool"][0] == {
        "id": "sale_price",
        "label": "価格",
        "targetPath": "customFields.salePrice",
        "section": "追加項目",
        "aliases": ["売価", "販売価格"],
        "source": "manual",
    }


def test_verified_record_structure_requires_a_parent_label_link() -> None:
    column_label = LayoutCell(
        id="price_label",
        role="label",
        x=10,
        y=10,
        width=80,
        height=30,
        confidence=1,
        text="価格",
    )
    parent_label = LayoutCell(
        id="sale_parent",
        role="parent_label",
        x=100,
        y=10,
        width=120,
        height=30,
        confidence=1,
        text="売却子牛一覧",
    )
    input_cell = LayoutCell(
        id="price_1",
        role="input",
        x=10,
        y=60,
        width=80,
        height=30,
        confidence=1,
    )
    section = LayoutRecordSection(
        id="sale",
        title="売却子牛一覧",
        collection_path="saleCalves",
        record_type="saleCalf",
        record_label_cell_ids=[column_label.id],
    )
    layout = LayoutDefinition(
        canonical_width=300,
        canonical_height=120,
        cells=[column_label, parent_label, input_cell],
        relations=[
            LayoutRelation(
                id="price_relation",
                input_cell_id=input_cell.id,
                label_cell_ids=[column_label.id],
                axis="vertical",
                order=1,
                target_path="saleCalves.0.priceYen",
            )
        ],
        record_sections=[section],
        record_groups=[
            LayoutRecordGroup(
                id="sale_1",
                section_id=section.id,
                order=1,
                x=input_cell.x,
                y=input_cell.y,
                width=input_cell.width,
                height=input_cell.height,
                input_cell_ids=[input_cell.id],
                record_key_value="1",
            )
        ],
        record_structure_verified=True,
    )

    with pytest.raises(AppError) as caught:
        LayoutService._require_verified_record_structure(layout)

    assert caught.value.code == "RECORD_PARENT_LABEL_REQUIRED"

    linked = layout.model_copy(
        update={
            "record_sections": [
                section.model_copy(update={"parent_label_cell_id": parent_label.id})
            ]
        }
    )
    LayoutService._require_verified_record_structure(linked)

    stale_but_complete = linked.model_copy(
        update={"record_structure_verified": False}
    )
    assert LayoutService._record_structure_is_complete(stale_but_complete) is True
    LayoutService._require_verified_record_structure(stale_but_complete)


def test_label_ocr_updates_only_user_classified_label_cells() -> None:
    cells = [
        LayoutCell(
            id="label",
            role="label",
            x=10,
            y=10,
            width=80,
            height=30,
            confidence=1,
            text="旧価格",
        ),
        LayoutCell(
            id="parent",
            role="parent_label",
            x=10,
            y=60,
            width=80,
            height=30,
            confidence=1,
        ),
        LayoutCell(
            id="value",
            role="input",
            x=120,
            y=10,
            width=80,
            height=30,
            confidence=1,
            text="手書き値",
        ),
    ]

    class FakeDetector:
        available = True

        def __init__(self) -> None:
            self.calls: list[tuple[int, ...]] = []

        def detect(self, image_rgb):
            self.calls.append(image_rgb.shape)
            text, confidence = [("価格", 0.96), ("売上", 0.91)][len(self.calls) - 1]
            return [
                TextRegion(((2, 2), (60, 2), (60, 20), (2, 20)), text, confidence),
            ]

    detector = FakeDetector()
    result = recognize_label_cells(
        np.zeros((120, 240, 3), dtype=np.uint8),
        cells,
        text_detector=detector,
    )

    assert detector.calls == [(30, 80, 3), (30, 80, 3)]
    assert result.recognized_count == 2
    assert result.ocr_available is True
    assert result.warnings == []
    assert result.cells[0].text == "価格"
    assert result.cells[0].text_confidence == 0.96
    assert result.cells[1].text == "売上"
    assert result.cells[2].text == "手書き値"


def test_label_ocr_retains_existing_text_when_engine_is_unavailable() -> None:
    cells = [
        LayoutCell(
            id="label",
            role="label",
            x=10,
            y=10,
            width=80,
            height=30,
            confidence=1,
            text="価格",
        )
    ]

    class UnavailableDetector:
        available = False

        def detect(self, _image_rgb):
            raise AssertionError("detect must not be called")

    result = recognize_label_cells(
        np.zeros((60, 120, 3), dtype=np.uint8),
        cells,
        text_detector=UnavailableDetector(),
    )

    assert result.cells[0].text == "価格"
    assert result.ocr_available is False
    assert result.warnings == ["LABEL_OCR_NOT_AVAILABLE_EXISTING_TEXT_RETAINED"]


def test_label_ocr_excludes_confirmed_meaning_and_record_labels() -> None:
    confirmed = LayoutCell(
        id="confirmed_label",
        role="label",
        x=10,
        y=10,
        width=80,
        height=30,
        confidence=1,
        text="確定済み",
    )
    record_label = LayoutCell(
        id="record_label",
        role="label",
        x=10,
        y=50,
        width=80,
        height=30,
        confidence=1,
        text="産歴",
    )
    pending = LayoutCell(
        id="pending_label",
        role="label",
        x=10,
        y=90,
        width=80,
        height=30,
        confidence=1,
    )
    input_cell = LayoutCell(
        id="input",
        role="input",
        x=120,
        y=10,
        width=80,
        height=30,
        confidence=1,
    )
    layout = LayoutDefinition(
        canonical_width=240,
        canonical_height=140,
        cells=[confirmed, record_label, pending, input_cell],
        relations=[
            LayoutRelation(
                id="confirmed_relation",
                input_cell_id="input",
                label_cell_ids=["confirmed_label"],
                axis="horizontal",
                order=1,
                target_path="document.cattleName",
            )
        ],
        record_sections=[
            LayoutRecordSection(
                id="calvings",
                title="繁殖履歴",
                collection_path="calvings",
                record_type="calving",
                record_label_cell_ids=["record_label"],
            )
        ],
    )

    class FakeDetector:
        available = True

        def __init__(self) -> None:
            self.calls = 0

        def detect(self, _image_rgb):
            self.calls += 1
            return [
                TextRegion(((2, 2), (60, 2), (60, 20), (2, 20)), "新規ラベル", 0.95),
            ]

    detector = FakeDetector()
    protected = protected_label_cell_ids(layout)
    result = recognize_label_cells(
        np.zeros((140, 240, 3), dtype=np.uint8),
        layout.cells,
        text_detector=detector,
        excluded_cell_ids=protected,
    )

    assert protected == {"confirmed_label", "record_label"}
    assert detector.calls == 1
    assert result.recognized_count == 1
    assert result.cells[0].text == "確定済み"
    assert result.cells[1].text == "産歴"
    assert result.cells[2].text == "新規ラベル"


def test_calving_mark_cell_becomes_two_choice_mappings() -> None:
    label_cell = LayoutCell(
        id="calving_label",
        role="label",
        x=10,
        y=60,
        width=25,
        height=30,
        confidence=1,
        text="分娩状況",
    )
    mark_cell = LayoutCell(
        id="calving_marks",
        role="mark",
        x=40,
        y=60,
        width=180,
        height=30,
        confidence=1,
        mark_sets=[
            LayoutMarkSet(
                id="outcome",
                target_path="calvings.0.outcome",
                options=[
                    LayoutMarkOption(
                        id="twin",
                        label="双",
                        value="twin",
                        x=0,
                        y=0,
                        width=30,
                        height=30,
                    ),
                    LayoutMarkOption(
                        id="early",
                        label="早",
                        value="premature",
                        x=30,
                        y=0,
                        width=30,
                        height=30,
                    ),
                ],
            ),
            LayoutMarkSet(
                id="sex",
                target_path="calvings.0.sex",
                options=[
                    LayoutMarkOption(
                        id="male",
                        label="♂",
                        value="male",
                        x=120,
                        y=0,
                        width=30,
                        height=30,
                    ),
                    LayoutMarkOption(
                        id="female",
                        label="♀",
                        value="female",
                        x=150,
                        y=0,
                        width=30,
                        height=30,
                    ),
                ],
            ),
        ],
    )
    layout = LayoutDefinition(
        canonical_width=300,
        canonical_height=140,
        cells=[label_cell, mark_cell],
        relations=[
            LayoutRelation(
                id="calving_mark_label",
                input_cell_id=mark_cell.id,
                label_cell_ids=[label_cell.id],
                axis="horizontal",
                order=1,
                target_path="calvings.0.outcome",
            )
        ],
        record_sections=[
            LayoutRecordSection(
                id="calvings",
                title="繁殖履歴",
                collection_path="calvings",
                record_type="calving",
                source="manual",
            )
        ],
        record_groups=[
            LayoutRecordGroup(
                id="calving_1",
                section_id="calvings",
                order=1,
                x=mark_cell.x,
                y=mark_cell.y,
                width=mark_cell.width,
                height=mark_cell.height,
                input_cell_ids=[mark_cell.id],
                record_key_field="parity",
                record_key_value="1",
                source="manual",
            )
        ],
        record_structure_verified=True,
    )

    mappings = LayoutService._as_field_mappings(layout)

    assert [mapping.target_path for mapping in mappings] == [
        "calvings.0.outcome",
        "calvings.0.sex",
    ]
    assert all(mapping.mapping_type == "mark" for mapping in mappings)
    assert all(mapping.label == "分娩状況" for mapping in mappings)
    assert [option.label for option in mappings[0].mark_options] == ["双", "早"]
    assert [option.label for option in mappings[1].mark_options] == ["♂", "♀"]


def test_generic_manual_choice_mapping_keeps_mode_and_selection_count() -> None:
    layout = LayoutDefinition(
        canonical_width=300,
        canonical_height=140,
        cells=[
            LayoutCell(
                id="attendance",
                role="mark",
                x=40,
                y=60,
                width=120,
                height=30,
                confidence=1,
                text="有 無",
                mark_sets=[
                    LayoutMarkSet(
                        id="choice",
                        target_path="customFields.attendance",
                        recognition_mode="manual",
                        selection_mode="single",
                        options=[
                            LayoutMarkOption(
                                id="yes",
                                label="有",
                                value="yes",
                                x=0,
                                y=0,
                                width=60,
                                height=30,
                            ),
                            LayoutMarkOption(
                                id="no",
                                label="無",
                                value="no",
                                x=60,
                                y=0,
                                width=60,
                                height=30,
                            ),
                        ],
                    )
                ],
            )
        ],
    )

    mappings = LayoutService._as_field_mappings(layout)

    assert len(mappings) == 1
    assert mappings[0].target_path == "customFields.attendance"
    assert mappings[0].mark_recognition_mode == "manual"
    assert mappings[0].mark_selection_mode == "single"
    assert [option.value for option in mappings[0].mark_options] == ["yes", "no"]


def test_choice_options_reject_duplicate_labels() -> None:
    shared = {
        "label": "有",
        "x": 0,
        "y": 0,
        "width": 30,
        "height": 30,
    }

    with pytest.raises(ValidationError, match="duplicate labels"):
        LayoutMarkSet(
            id="choice",
            target_path="customFields.attendance",
            recognition_mode="manual",
            selection_mode="single",
            options=[
                LayoutMarkOption(id="yes", value="yes", **shared),
                LayoutMarkOption(id="no", value="no", **shared),
            ],
        )


def test_repeated_table_label_creates_multiple_mappings_and_keeps_secondary_labels() -> None:
    cells = [
        LayoutCell(
            id="label_price",
            role="label",
            x=10,
            y=10,
            width=80,
            height=30,
            confidence=1,
            text="価格",
        ),
        LayoutCell(
            id="label_row",
            role="label",
            x=10,
            y=60,
            width=80,
            height=30,
            confidence=1,
            text="売却子牛",
        ),
        LayoutCell(
            id="input_1",
            role="input",
            x=110,
            y=10,
            width=100,
            height=30,
            confidence=1,
        ),
        LayoutCell(
            id="input_2",
            role="input",
            x=220,
            y=10,
            width=100,
            height=30,
            confidence=1,
        ),
    ]
    relations = [
        LayoutRelation(
            id="price_1",
            input_cell_id="input_1",
            label_cell_ids=["label_price"],
            axis="horizontal",
            order=1,
            target_path="saleCalves.0.priceYen",
        ),
        LayoutRelation(
            id="price_2",
            input_cell_id="input_2",
            label_cell_ids=["label_price"],
            axis="horizontal",
            order=2,
            target_path="saleCalves.1.priceYen",
        ),
        LayoutRelation(
            id="row_1",
            input_cell_id="input_1",
            label_cell_ids=["label_row"],
            axis="vertical",
            order=1,
            target_path="saleCalves.0.priceYen",
        ),
        LayoutRelation(
            id="row_2",
            input_cell_id="input_2",
            label_cell_ids=["label_row"],
            axis="vertical",
            order=2,
            target_path="saleCalves.1.priceYen",
        ),
    ]
    layout = LayoutDefinition(
        canonical_width=400,
        canonical_height=120,
        cells=cells,
        relations=relations,
        allow_cross_axis_labels=True,
    )

    mappings = LayoutService._as_field_mappings(layout)

    assert [mapping.target_path for mapping in mappings] == [
        "saleCalves.0.priceYen",
        "saleCalves.1.priceYen",
    ]
    assert all(mapping.label == "価格 / 売却子牛" for mapping in mappings)


def test_custom_json_target_is_accepted_from_layout_editor() -> None:
    layout = LayoutDefinition(
        canonical_width=400,
        canonical_height=120,
        cells=[
            LayoutCell(
                id="custom_label",
                role="label",
                x=10,
                y=10,
                width=80,
                height=30,
                confidence=1,
                text="治療歴",
            ),
            LayoutCell(
                id="custom_input",
                role="input",
                x=110,
                y=10,
                width=200,
                height=30,
                confidence=1,
            ),
        ],
        relations=[
            LayoutRelation(
                id="custom_relation",
                input_cell_id="custom_input",
                label_cell_ids=["custom_label"],
                axis="horizontal",
                order=1,
                target_path="customFields.treatmentHistory",
            )
        ],
    )

    mappings = LayoutService._as_field_mappings(layout)

    assert mappings[0].target_path == "customFields.treatmentHistory"
    assert mappings[0].label == "治療歴"


def test_legacy_array_paths_are_migrated_to_explicit_record_groups() -> None:
    layout = LayoutDefinition(
        canonical_width=400,
        canonical_height=160,
        cells=[
            LayoutCell(
                id="price",
                role="input",
                x=100,
                y=60,
                width=80,
                height=40,
                confidence=1,
            ),
            LayoutCell(
                id="sire",
                role="input",
                x=200,
                y=60,
                width=100,
                height=40,
                confidence=1,
            ),
            LayoutCell(
                id="price_label",
                role="label",
                x=100,
                y=10,
                width=80,
                height=30,
                confidence=1,
                text="価格",
            ),
            LayoutCell(
                id="sire_label",
                role="label",
                x=200,
                y=10,
                width=100,
                height=30,
                confidence=1,
                text="種雄牛名",
            ),
        ],
        relations=[
            LayoutRelation(
                id="price_relation",
                input_cell_id="price",
                label_cell_ids=["price_label"],
                axis="vertical",
                order=1,
                target_path="saleCalves.0.priceYen",
            ),
            LayoutRelation(
                id="sire_relation",
                input_cell_id="sire",
                label_cell_ids=["sire_label"],
                axis="vertical",
                order=1,
                target_path="saleCalves.0.sireName",
            ),
        ],
    )

    assert len(layout.record_sections) == 1
    assert layout.record_sections[0].title == "売却子牛一覧"
    assert len(layout.record_groups) == 1
    assert set(layout.record_groups[0].input_cell_ids) == {"price", "sire"}
    assert layout.record_groups[0].record_key_value == "1"
    assert layout.record_structure_verified is False


def test_record_group_rejects_fields_from_a_different_array_row() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        LayoutDefinition(
            canonical_width=400,
            canonical_height=160,
            cells=[
                LayoutCell(
                    id="price",
                    role="input",
                    x=100,
                    y=60,
                    width=80,
                    height=40,
                    confidence=1,
                ),
                LayoutCell(
                    id="price_label",
                    role="label",
                    x=100,
                    y=10,
                    width=80,
                    height=30,
                    confidence=1,
                    text="価格",
                ),
            ],
            relations=[
                LayoutRelation(
                    id="price_relation",
                    input_cell_id="price",
                    label_cell_ids=["price_label"],
                    axis="vertical",
                    order=2,
                    target_path="saleCalves.1.priceYen",
                )
            ],
            record_sections=[
                LayoutRecordSection(
                    id="sale",
                    title="売却子牛一覧",
                    collection_path="saleCalves",
                    record_type="saleCalf",
                )
            ],
            record_groups=[
                LayoutRecordGroup(
                    id="row_1",
                    section_id="sale",
                    order=1,
                    x=90,
                    y=50,
                    width=100,
                    height=60,
                    input_cell_ids=["price"],
                    record_key_value="1",
                )
            ],
            record_structure_verified=True,
        )


def test_input_can_be_mapped_without_a_separate_label_cell() -> None:
    layout = LayoutDefinition(
        canonical_width=400,
        canonical_height=120,
        cells=[
            LayoutCell(
                id="mixed_name",
                role="input",
                x=10,
                y=10,
                width=300,
                height=40,
                confidence=1,
                printed_label_text="名号",
            )
        ],
        relations=[
            LayoutRelation(
                id="direct_name",
                input_cell_id="mixed_name",
                label_cell_ids=[],
                axis="horizontal",
                order=1,
                target_path="document.cattleName",
            )
        ],
    )

    mappings = LayoutService._as_field_mappings(layout)

    assert len(mappings) == 1
    assert mappings[0].target_path == "document.cattleName"
    assert mappings[0].label == "名号"
    assert mappings[0].printed_label_text == "名号"


def test_unlabelled_birth_date_input_can_be_given_meaning_directly() -> None:
    layout = LayoutDefinition(
        canonical_width=400,
        canonical_height=120,
        cells=[
            LayoutCell(
                id="birth_date",
                role="input",
                x=10,
                y=60,
                width=300,
                height=40,
                confidence=1,
            )
        ],
        relations=[
            LayoutRelation(
                id="direct_birth_date",
                input_cell_id="birth_date",
                axis="horizontal",
                order=1,
                target_path="document.birthDate",
            )
        ],
    )

    mappings = LayoutService._as_field_mappings(layout)

    assert len(mappings) == 1
    assert mappings[0].target_path == "document.birthDate"
    assert mappings[0].printed_label_text == ""


def test_direct_text_fallback_recovers_handwriting_but_skips_exact_printed_label() -> None:
    gray = np.full((100, 320), 255, dtype=np.uint8)
    cv2.rectangle(gray, (20, 30), (65, 50), 0, 2)
    cv2.rectangle(gray, (100, 30), (175, 50), 0, 2)
    layout = LayoutDefinition(
        canonical_width=320,
        canonical_height=100,
        cells=[
            LayoutCell(
                id="mixed_name",
                role="input",
                x=10,
                y=10,
                width=200,
                height=70,
                confidence=1,
                printed_label_text="名号",
            )
        ],
        relations=[
            LayoutRelation(
                id="direct_name",
                input_cell_id="mixed_name",
                axis="horizontal",
                order=1,
                target_path="pedigree.mother",
            )
        ],
    )
    regions = [
        TextRegion(
            points=((15, 20), (70, 20), (70, 60), (15, 60)),
            text="名号",
            confidence=0.99,
        ),
        TextRegion(
            points=((90, 20), (185, 20), (185, 60), (90, 60)),
            text="こよみ",
            confidence=0.9,
        ),
    ]

    fallback = LayoutService._direct_text_fallback_mask(
        gray=gray,
        protected_lines=np.zeros_like(gray),
        scope=np.full_like(gray, 255),
        layout=layout,
        text_regions=regions,
    )

    assert not np.any(fallback[20:61, 15:71])
    assert np.any(fallback[20:61, 90:186])


def test_direct_text_fallback_recovers_an_unlabelled_birth_date() -> None:
    gray = np.full((100, 320), 255, dtype=np.uint8)
    cv2.rectangle(gray, (30, 30), (180, 50), 0, 2)
    layout = LayoutDefinition(
        canonical_width=320,
        canonical_height=100,
        cells=[
            LayoutCell(
                id="birth_date",
                role="input",
                x=10,
                y=10,
                width=220,
                height=70,
                confidence=1,
            )
        ],
        relations=[
            LayoutRelation(
                id="direct_birth",
                input_cell_id="birth_date",
                axis="horizontal",
                order=1,
                target_path="document.birthDate",
            )
        ],
    )

    fallback = LayoutService._direct_text_fallback_mask(
        gray=gray,
        protected_lines=np.zeros_like(gray),
        scope=np.full_like(gray, 255),
        layout=layout,
        text_regions=[
            TextRegion(
                points=((20, 20), (190, 20), (190, 60), (20, 60)),
                text="26年9月27日生",
                confidence=0.95,
            )
        ],
    )

    assert np.any(fallback[20:61, 20:191])
