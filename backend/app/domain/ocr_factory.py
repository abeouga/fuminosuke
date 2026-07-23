from app.domain.ocr_models import (
    DocumentIdentity,
    FieldReading,
    OcrDocument,
    Pedigree,
    ProcessingMetadata,
    ReadingStatus,
)


def blank_reading(region_id: str) -> FieldReading:
    return FieldReading(
        raw=None,
        normalized=None,
        status=ReadingStatus.BLANK,
        alternatives=[],
        review_reason=None,
        source_region_id=region_id,
        bounding_box=None,
    )


def blank_ocr_document(*, provider: str, source_regions: list[str]) -> OcrDocument:
    return OcrDocument(
        schema_version="1.0",
        template_id="cow-breeding-ledger-v1",
        template_version="1",
        document=DocumentIdentity(
            unified_ear_tag=blank_reading("ear_tag"),
            cattle_name=blank_reading("header_identity"),
            birth_date=blank_reading("header_identity"),
            registration_number=blank_reading("header_identity"),
            coat_color=blank_reading("header_identity"),
            breeder=blank_reading("header_identity"),
            owner=blank_reading("header_identity"),
        ),
        sale_calves=[],
        pedigree=Pedigree(
            father=blank_reading("pedigree"),
            mother=blank_reading("pedigree"),
            paternal_grandfather=blank_reading("pedigree"),
            paternal_grandmother=blank_reading("pedigree"),
            maternal_grandfather=blank_reading("pedigree"),
            maternal_grandmother=blank_reading("pedigree"),
        ),
        calvings=[],
        review_items=[],
        processing_metadata=ProcessingMetadata(
            model_id=None,
            prompt_version=None,
            source_regions=source_regions,
            notes=[f"OCR_PROVIDER_{provider.upper()}"],
        ),
    )
