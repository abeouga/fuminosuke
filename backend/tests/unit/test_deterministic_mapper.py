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
                region_id="field_name",
                image_png=b"png",
                target_path="document.cattleName",
                label="名号",
                bounding_box=(100, 200, 300, 80),
            ),
            OcrRegionInput(
                region_id="field_price",
                image_png=b"png",
                target_path="saleCalves.0.priceYen",
                label="売却子牛1 価格",
                bounding_box=(700, 350, 160, 60),
            ),
            OcrRegionInput(
                region_id="field_note",
                image_png=b"png",
                target_path="calvings.1.notes",
                label="産歴2 備考",
                bounding_box=(850, 1200, 220, 70),
            ),
        ],
    )
    batch = TranscriptionBatch(
        items=[
            TranscriptionItem(mapping_id="field_name", raw="こよみ", status=ReadingStatus.READABLE),
            TranscriptionItem(
                mapping_id="field_price", raw="540,000", status=ReadingStatus.READABLE
            ),
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
    ]
