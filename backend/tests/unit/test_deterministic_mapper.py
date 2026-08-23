import pytest

from app.domain.ocr_models import ReadingStatus
from app.ocr.base import OcrProviderRequest, OcrRegionInput
from app.ocr.deterministic_mapper import build_mapped_document
from app.ocr.transcription_models import TranscriptionBatch, TranscriptionItem


def test_transcriptions_are_assigned_only_to_gui_selected_paths() -> None:
    request = OcrProviderRequest(
        template_id="gui-template",
        template_version="3",
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"png",
                target_path="document.cattleName",
                source_region_id="field_name",
                bounding_box=(100, 200, 300, 80),
            ),
            OcrRegionInput(
                region_id="cell_0002",
                image_png=b"png",
                target_path="saleCalves.0.priceYen",
                source_region_id="field_price",
                bounding_box=(700, 350, 160, 60),
            ),
            OcrRegionInput(
                region_id="cell_0003",
                image_png=b"png",
                target_path="calvings.1.notes",
                source_region_id="field_note",
                bounding_box=(850, 1200, 220, 70),
            ),
        ],
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(mapping_id="cell_0002", raw="540,000", status=ReadingStatus.READABLE),
            TranscriptionItem(mapping_id="cell_0001", raw="こよみ", status=ReadingStatus.READABLE),
        ]
    )

    result = build_mapped_document(request, batch)

    assert result.template_id == "gui-template"
    assert result.document.cattle_name.raw == "こよみ"
    assert result.document.registration_number.raw is None
    assert result.sale_calves[0].price_yen.normalized == "540000"
    assert result.sale_calves[0].sire_name.raw is None
    assert result.calvings[1].notes.status is ReadingStatus.UNCERTAIN
    assert result.calvings[1].notes.review_reason == "MISSING_TRANSCRIPTION"
    assert result.calvings[1].notes.bounding_box is not None
    assert result.calvings[1].notes.bounding_box.x == 850
    assert result.document.cattle_name.source_region_id == "field_name"
    assert result.review_items[0].path == "calvings.1.notes"
    assert result.processing_metadata.notes == [
        "RAW_TRANSCRIPTION_ONLY",
        "DETERMINISTIC_FIELD_MAPPING",
        "LABEL_INFERENCE_DISABLED",
        "TEMPLATE_INPUT_CELLS_ONLY",
    ]


def test_japanese_dot_grouped_price_is_normalized_as_yen_not_a_decimal() -> None:
    request = OcrProviderRequest(
        template_id="gui-template",
        template_version="1",
        regions=[
            OcrRegionInput(
                region_id="price",
                image_png=b"png",
                target_path="saleCalves.0.priceYen",
            )
        ],
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="price",
                raw="762.300",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    assert result.sale_calves[0].price_yen.raw == "762.300"
    assert result.sale_calves[0].price_yen.normalized == "762300"


def test_large_mapped_template_and_custom_json_fields_are_supported() -> None:
    regions = [
        OcrRegionInput(
            region_id=f"field_{index}",
            image_png=b"png",
            target_path=f"customFields.extra{index}",
        )
        for index in range(53)
    ]
    request = OcrProviderRequest(
        template_id="large-gui-template",
        template_version="1",
        regions=regions,
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id=region.region_id,
                raw=f"値{index}",
                status=ReadingStatus.READABLE,
            )
            for index, region in enumerate(regions)
        ]
    )

    result = build_mapped_document(request, batch)

    assert len(result.processing_metadata.source_regions) == 53
    assert result.custom_fields["extra0"].raw == "値0"
    assert result.custom_fields["extra52"].raw == "値52"


def test_duplicate_mapping_id_in_response_is_rejected() -> None:
    request = OcrProviderRequest(
        template_id="gui-template",
        template_version="1",
        regions=[
            OcrRegionInput(
                region_id="field_name",
                image_png=b"cell",
                target_path="document.cattleName",
            )
        ],
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="field_name",
                raw="こよみ",
                status=ReadingStatus.READABLE,
            ),
            TranscriptionItem(
                mapping_id="field_name",
                raw="こよみ",
                status=ReadingStatus.READABLE,
            ),
        ]
    )

    with pytest.raises(ValueError, match="duplicate mappingId"):
        build_mapped_document(request, batch)


def test_unknown_mapping_id_in_response_is_ignored_without_assigning_it() -> None:
    request = OcrProviderRequest(
        template_id="gui-template",
        template_version="1",
        regions=[
            OcrRegionInput(
                region_id="field_name",
                image_png=b"cell",
                target_path="document.cattleName",
            )
        ],
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="label_cell",
                raw="名号",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    assert result.document.cattle_name.raw is None
    assert result.document.cattle_name.status is ReadingStatus.UNCERTAIN
    assert result.review_items[0].code == "OCR_MAPPING_MISSING"
    assert "IGNORED_UNKNOWN_MAPPING_IDS" in result.processing_metadata.notes


def test_sale_calf_mapping_beyond_ten_rows_is_rejected() -> None:
    request = OcrProviderRequest(
        template_id="gui-template",
        template_version="1",
        regions=[
            OcrRegionInput(
                region_id="cell_0011",
                image_png=b"cell",
                target_path="saleCalves.10.priceYen",
            )
        ],
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="cell_0011",
                raw="500000",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    with pytest.raises(ValueError, match="row must be 1-10"):
        build_mapped_document(request, batch)


def test_sire_name_candidate_and_et_suffix_become_the_normalized_value() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"sire",
                target_path="saleCalves.0.sireName",
                candidates=("勝忠平", "光晴重"),
                allowed_suffixes=("ET",),
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="cell_0001",
                raw="勝忠平 ET",
                matched_candidate="勝忠平ET",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    assert result.sale_calves[0].sire_name.raw == "勝忠平 ET"
    assert result.sale_calves[0].sire_name.normalized == "勝忠平ET"
    assert result.sale_calves[0].sire_name.status is ReadingStatus.READABLE
    assert result.review_items == []


def test_birth_date_is_numeric_only_and_infers_heisei() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="birth",
                image_png=b"birth",
                target_path="document.birthDate",
                transcription_format="japanese_era_date_numbers",
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="birth",
                raw="26 9 27 生",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    assert result.document.birth_date.raw == "26 9 27"
    assert result.document.birth_date.normalized == "2014年9月27日"
    assert result.document.birth_date.status is ReadingStatus.READABLE
    assert result.document.birth_date.review_reason is None
    assert result.review_items == []


def test_calving_date_is_numeric_only_and_infers_heisei() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="calving-date",
                image_png=b"calving-date",
                target_path="calvings.0.calvingDate",
                transcription_format="japanese_era_date_numbers",
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="calving-date",
                raw="28 7 25",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    reading = result.calvings[0].calving_date
    assert reading.raw == "28 7 25"
    assert reading.normalized == "2016年7月25日"
    assert reading.status is ReadingStatus.READABLE
    assert reading.review_reason is None
    assert result.review_items == []


def test_sale_date_is_numeric_only_and_infers_reiwa() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="sale-date",
                image_png=b"sale-date",
                target_path="saleCalves.0.saleDate",
                transcription_format="japanese_era_date_numbers",
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="sale-date",
                raw="6 11 7",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    reading = result.sale_calves[0].sale_date
    assert reading.raw == "6 11 7"
    assert reading.normalized == "2024年11月7日"
    assert reading.status is ReadingStatus.READABLE
    assert reading.review_reason is None
    assert result.review_items == []


def test_sale_note_uses_matched_sex_symbol_and_preserves_suffix() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="note",
                image_png=b"note",
                target_path="saleCalves.0.notes",
                candidates=("♂", "♀"),
                candidate_type="sale_calf_note_prefix",
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="note",
                raw="る双子",
                matched_candidate="♂",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    assert result.sale_calves[0].notes.raw == "る双子"
    assert result.sale_calves[0].notes.normalized == "♂双子"
    assert result.sale_calves[0].notes.status is ReadingStatus.READABLE
    assert result.review_items == []


def test_sale_note_without_symbol_match_requires_review() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="note",
                image_png=b"note",
                target_path="saleCalves.0.notes",
                candidates=("♂", "♀"),
                candidate_type="sale_calf_note_prefix",
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="note",
                raw="チ",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    assert result.sale_calves[0].notes.normalized is None
    assert result.sale_calves[0].notes.status is ReadingStatus.UNCERTAIN
    assert result.review_items[0].code == "SALE_NOTE_SEX_SYMBOL_UNRESOLVED"


def test_sire_name_outside_the_pool_requires_review() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"sire",
                target_path="saleCalves.0.sireName",
                candidates=("勝忠平", "光晴重"),
                allowed_suffixes=("ET",),
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="cell_0001",
                raw="未知号",
                matched_candidate="候補外",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    reading = result.sale_calves[0].sire_name
    assert reading.raw == "未知号"
    assert reading.normalized is None
    assert reading.status is ReadingStatus.UNCERTAIN
    assert reading.review_reason == "SIRE_NAME_CANDIDATE_UNRESOLVED"
    assert result.review_items[0].code == "SIRE_NAME_CANDIDATE_UNRESOLVED"


def test_template_managed_candidate_can_restrict_any_text_field() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cattle_name",
                image_png=b"name",
                target_path="document.cattleName",
                candidates=("こよみ", "さくら"),
                candidate_type="managed_text",
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="cattle_name",
                raw="候補外の名前",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    assert result.document.cattle_name.status is ReadingStatus.UNCERTAIN
    assert result.document.cattle_name.normalized is None
    assert result.document.cattle_name.review_reason == "MANAGED_CANDIDATE_UNRESOLVED"
    assert result.review_items[0].code == "MANAGED_CANDIDATE_UNRESOLVED"


def test_ditto_marks_copy_the_value_from_the_cell_directly_above() -> None:
    candidates = ("勝忠平", "光晴重")
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"sire-1",
                target_path="saleCalves.0.sireName",
                candidates=candidates,
                allowed_suffixes=("ET",),
            ),
            OcrRegionInput(
                region_id="cell_0002",
                image_png=b"sire-2",
                target_path="saleCalves.1.sireName",
                candidates=candidates,
                allowed_suffixes=("ET",),
            ),
            OcrRegionInput(
                region_id="cell_0003",
                image_png=b"sire-3",
                target_path="saleCalves.2.sireName",
                candidates=candidates,
                allowed_suffixes=("ET",),
            ),
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="cell_0001",
                raw="勝忠平ET",
                matched_candidate="勝忠平ET",
                status=ReadingStatus.READABLE,
            ),
            TranscriptionItem(
                mapping_id="cell_0002",
                raw="=",
                is_ditto=True,
                status=ReadingStatus.READABLE,
            ),
            TranscriptionItem(
                mapping_id="cell_0003",
                raw="こ",
                status=ReadingStatus.READABLE,
            ),
        ]
    )

    result = build_mapped_document(request, batch)

    first, second, third = [row.sire_name for row in result.sale_calves[:3]]
    assert first.normalized == "勝忠平ET"
    assert second.raw == "="
    assert second.normalized == "勝忠平ET"
    assert second.status is ReadingStatus.READABLE
    assert third.raw == "こ"
    assert third.normalized == "勝忠平ET"
    assert third.status is ReadingStatus.READABLE
    assert result.review_items == []
    assert "DITTO_MARK_RESOLUTION" in result.processing_metadata.notes


def test_ditto_without_a_readable_cell_above_requires_review() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"ditto",
                target_path="saleCalves.0.sireName",
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="cell_0001",
                raw="〃",
                is_ditto=True,
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    reading = result.sale_calves[0].sire_name
    assert reading.raw == "〃"
    assert reading.normalized is None
    assert reading.status is ReadingStatus.UNCERTAIN
    assert reading.review_reason == "DITTO_SOURCE_UNAVAILABLE"
    assert result.review_items[0].code == "DITTO_SOURCE_UNAVAILABLE"


def test_a_normal_word_containing_ko_is_not_treated_as_ditto() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"name",
                target_path="document.cattleName",
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="cell_0001",
                raw="こよみ",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    assert result.document.cattle_name.raw == "こよみ"
    assert result.document.cattle_name.normalized == "こよみ"
    assert "DITTO_MARK_RESOLUTION" not in result.processing_metadata.notes


@pytest.mark.parametrize("raw_mark", ["・", ".", "・・", "..", "ヽ"])
def test_dot_like_marks_in_sire_cells_copy_the_value_above(raw_mark: str) -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"sire-1",
                target_path="saleCalves.0.sireName",
                candidates=("光晴重",),
                allowed_suffixes=("ET",),
            ),
            OcrRegionInput(
                region_id="cell_0002",
                image_png=b"sire-2",
                target_path="saleCalves.1.sireName",
                candidates=("光晴重",),
                allowed_suffixes=("ET",),
            ),
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="cell_0001",
                raw="光晴重",
                matched_candidate="光晴重",
                status=ReadingStatus.READABLE,
            ),
            TranscriptionItem(
                mapping_id="cell_0002",
                raw=raw_mark,
                status=ReadingStatus.UNCERTAIN,
            ),
        ]
    )

    result = build_mapped_document(request, batch)

    reading = result.sale_calves[1].sire_name
    assert reading.raw == raw_mark
    assert reading.normalized == "光晴重"
    assert reading.status is ReadingStatus.READABLE
    assert result.review_items == []


def test_a_period_in_a_non_sire_cell_is_not_treated_as_ditto() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"price",
                target_path="saleCalves.1.priceYen",
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="cell_0001",
                raw=".",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    assert result.sale_calves[1].price_yen.raw == "."
    assert result.sale_calves[1].price_yen.review_reason is None
    assert "DITTO_MARK_RESOLUTION" not in result.processing_metadata.notes


@pytest.mark.parametrize("raw_mark", [".", "ミ", "ヽ"])
def test_a_period_in_calving_notes_copies_the_value_above(raw_mark: str) -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"calving-notes-1",
                target_path="calvings.0.notes",
            ),
            OcrRegionInput(
                region_id="cell_0002",
                image_png=b"calving-notes-2",
                target_path="calvings.1.notes",
            ),
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="cell_0001",
                raw="自然分娩",
                status=ReadingStatus.READABLE,
            ),
            TranscriptionItem(
                mapping_id="cell_0002",
                raw=raw_mark,
                status=ReadingStatus.READABLE,
            ),
        ]
    )

    result = build_mapped_document(request, batch)

    reading = result.calvings[1].notes
    assert reading.raw == raw_mark
    assert reading.normalized == "自然分娩"
    assert reading.status is ReadingStatus.READABLE
    assert reading.review_reason is None
    assert result.review_items == []
    assert "DITTO_MARK_RESOLUTION" in result.processing_metadata.notes


def test_invalid_document_identity_text_is_never_accepted_as_readable() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="identity_cell",
                image_png=b"identity",
                target_path="document.unifiedEarTag",
                is_document_identity=True,
                identity_value_type="digits",
                identity_exact_length=10,
            )
        ]
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(
                mapping_id="identity_cell",
                raw="第2光晴",
                status=ReadingStatus.READABLE,
            )
        ]
    )

    result = build_mapped_document(request, batch)

    reading = result.document.unified_ear_tag
    assert reading.raw == "第2光晴"
    assert reading.normalized is None
    assert reading.status is ReadingStatus.UNCERTAIN
    assert reading.review_reason == "DOCUMENT_IDENTITY_FORMAT_INVALID"
    assert result.review_items[0].code == "DOCUMENT_IDENTITY_FORMAT_INVALID"
