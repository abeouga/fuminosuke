import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from app.layout_analysis.detector import TextRegion
from app.layout_analysis.models import (
    LayoutCell,
    LayoutDefinition,
    LayoutRecordGroup,
    LayoutRecordSection,
    LayoutRelation,
)
from app.services.layout_service import LayoutService


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
