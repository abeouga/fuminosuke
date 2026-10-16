from app.domain.ocr_models import FieldReading, OcrDocument, ProcessingMetadata, ReadingStatus

def blank_reading(region_id: str) -> FieldReading:
    return FieldReading(status=ReadingStatus.BLANK, source_region_id=region_id)

def blank_ocr_document(*, provider: str, source_regions: list[str]) -> OcrDocument:
    return OcrDocument(schema_version="2.0", template_id="generic-document-v1",
        template_version="1", fields={}, processing_metadata=ProcessingMetadata(
            source_regions=source_regions, notes=[f"OCR_PROVIDER_{provider.upper()}"]))
