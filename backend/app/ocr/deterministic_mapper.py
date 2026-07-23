from __future__ import annotations

import re

from app.domain.ocr_factory import blank_ocr_document, blank_reading
from app.domain.ocr_models import (
    BoundingBox,
    Calving,
    FieldReading,
    OcrDocument,
    ReadingStatus,
    ReviewItem,
    SaleCalf,
)
from app.ocr.base import OcrProviderRequest
from app.ocr.transcription_models import TranscriptionBatch, TranscriptionItem

DOCUMENT_ATTRIBUTES = {
    "unifiedEarTag": "unified_ear_tag",
    "cattleName": "cattle_name",
    "birthDate": "birth_date",
    "registrationNumber": "registration_number",
    "coatColor": "coat_color",
    "breeder": "breeder",
    "owner": "owner",
}

PEDIGREE_ATTRIBUTES = {
    "father": "father",
    "mother": "mother",
    "paternalGrandfather": "paternal_grandfather",
    "paternalGrandmother": "paternal_grandmother",
    "maternalGrandfather": "maternal_grandfather",
    "maternalGrandmother": "maternal_grandmother",
}

SALE_CALF_ATTRIBUTES = {
    "parity": "parity",
    "ageDays": "age_days",
    "weightKg": "weight_kg",
    "priceYen": "price_yen",
    "sireName": "sire_name",
    "saleDate": "sale_date",
    "notes": "notes",
}

CALVING_ATTRIBUTES = {
    "parity": "parity",
    "calvingDate": "calving_date",
    "sex": "sex",
    "outcome": "outcome",
    "notes": "notes",
}


def build_mapped_document(request: OcrProviderRequest, batch: TranscriptionBatch) -> OcrDocument:
    document = blank_ocr_document(
        provider="openai-mapped",
        source_regions=[region.region_id for region in request.regions],
    )
    document.template_id = request.template_id
    document.template_version = request.template_version
    returned = {item.mapping_id: item for item in batch.items}
    for region in request.regions:
        if not region.target_path:
            continue
        item = returned.get(region.region_id)
        reading = _reading(region.region_id, region.target_path, region.bounding_box, item)
        _assign(document, region.target_path, reading)
        if item is None:
            document.review_items.append(
                ReviewItem(
                    code="OCR_MAPPING_MISSING",
                    path=region.target_path,
                    message="OCR応答にこの矩形の転記結果がないため確認してください。",
                    severity="warning",
                    source_region_id=region.region_id,
                )
            )
    document.processing_metadata.notes = [
        "RAW_TRANSCRIPTION_ONLY",
        "DETERMINISTIC_FIELD_MAPPING",
    ]
    return document


def _reading(
    mapping_id: str,
    target_path: str,
    box: tuple[int, int, int, int] | None,
    item: TranscriptionItem | None,
) -> FieldReading:
    if item is None:
        return _missing_mapped_reading(mapping_id, box)
    raw = item.raw.strip() if item.raw else None
    normalized = _normalize(target_path, raw) if item.status is ReadingStatus.READABLE else None
    bounding_box = BoundingBox(x=box[0], y=box[1], width=box[2], height=box[3]) if box else None
    return FieldReading(
        raw=raw,
        normalized=normalized,
        status=item.status,
        alternatives=item.alternatives,
        review_reason=item.review_reason,
        source_region_id=mapping_id,
        bounding_box=bounding_box,
    )


def _missing_mapped_reading(
    mapping_id: str, box: tuple[int, int, int, int] | None
) -> FieldReading:
    return FieldReading(
        raw=None,
        normalized=None,
        status=ReadingStatus.UNCERTAIN,
        alternatives=[],
        review_reason="MISSING_TRANSCRIPTION",
        source_region_id=mapping_id,
        bounding_box=(
            BoundingBox(x=box[0], y=box[1], width=box[2], height=box[3]) if box else None
        ),
    )


def _normalize(target_path: str, raw: str | None) -> str | None:
    if raw is None:
        return None
    if target_path == "document.unifiedEarTag":
        digits = re.sub(r"\D", "", raw)
        return digits if len(digits) == 10 else None
    if target_path.endswith((".ageDays", ".weightKg", ".priceYen", ".parity")):
        compact = raw.replace(",", "").strip()
        return compact if compact.isdigit() else None
    if target_path.endswith(("Date", ".birthDate")):
        return None
    return raw


def _assign(document: OcrDocument, target_path: str, reading: FieldReading) -> None:
    parts = target_path.split(".")
    if len(parts) == 2 and parts[0] == "document" and parts[1] in DOCUMENT_ATTRIBUTES:
        setattr(document.document, DOCUMENT_ATTRIBUTES[parts[1]], reading)
        return
    if len(parts) == 2 and parts[0] == "pedigree" and parts[1] in PEDIGREE_ATTRIBUTES:
        setattr(document.pedigree, PEDIGREE_ATTRIBUTES[parts[1]], reading)
        return
    if len(parts) != 3 or not parts[1].isdigit():
        raise ValueError(f"Unsupported field mapping target: {target_path}")
    index = int(parts[1])
    if parts[0] == "saleCalves" and parts[2] in SALE_CALF_ATTRIBUTES:
        while len(document.sale_calves) <= index:
            document.sale_calves.append(_blank_sale_calf())
        setattr(document.sale_calves[index], SALE_CALF_ATTRIBUTES[parts[2]], reading)
        return
    if parts[0] == "calvings" and parts[2] in CALVING_ATTRIBUTES:
        while len(document.calvings) <= index:
            document.calvings.append(_blank_calving())
        setattr(document.calvings[index], CALVING_ATTRIBUTES[parts[2]], reading)
        return
    raise ValueError(f"Unsupported field mapping target: {target_path}")


def _blank_sale_calf() -> SaleCalf:
    return SaleCalf(
        parity=blank_reading("unmapped"),
        age_days=blank_reading("unmapped"),
        weight_kg=blank_reading("unmapped"),
        price_yen=blank_reading("unmapped"),
        sire_name=blank_reading("unmapped"),
        sale_date=blank_reading("unmapped"),
        notes=blank_reading("unmapped"),
    )


def _blank_calving() -> Calving:
    return Calving(
        parity=blank_reading("unmapped"),
        calving_date=blank_reading("unmapped"),
        sex=blank_reading("unmapped"),
        outcome=blank_reading("unmapped"),
        notes=blank_reading("unmapped"),
    )
