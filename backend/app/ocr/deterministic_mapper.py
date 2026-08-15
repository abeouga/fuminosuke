from __future__ import annotations

import re
import unicodedata
from typing import cast

from app.domain.field_targets import (
    CALVING_ROW_COUNT,
    SALE_CALF_ROW_COUNT,
    custom_field_key,
)
from app.domain.japanese_dates import canonical_japanese_birth_date
from app.domain.numeric_values import normalize_decimal, normalize_integer, normalize_yen
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
from app.domain.sale_notes import canonical_sale_note
from app.domain.sire_names import canonical_sire_candidate
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

DITTO_MARKS = {"=", "==", "〃", "″", "こ"}
CONTEXTUAL_DITTO_MARKS = {".", "..", "・", "・・", "·", "••", "∙", "ヽ"}


def build_mapped_document(request: OcrProviderRequest, batch: TranscriptionBatch) -> OcrDocument:
    requested_ids = [region.region_id for region in request.regions]
    if len(requested_ids) != len(set(requested_ids)):
        raise ValueError("Requested mappingId values must be unique")
    requested_id_set = set(requested_ids)
    returned_ids = [item.mapping_id for item in batch.items]
    if len(returned_ids) != len(set(returned_ids)):
        raise ValueError("OCR response contains duplicate mappingId values")
    unknown_ids = set(returned_ids) - requested_id_set
    document = blank_ocr_document(
        provider="openai-mapped",
        source_regions=[region.source_region_id or region.region_id for region in request.regions],
    )
    document.template_id = request.template_id
    document.template_version = request.template_version
    # Structured OCR can occasionally append a sequential mappingId that was
    # never requested. It has no trusted target path, so ignore it rather than
    # failing an otherwise complete job. Duplicate requested IDs remain a hard
    # error because choosing between conflicting readings would be ambiguous.
    returned = {
        item.mapping_id: item for item in batch.items if item.mapping_id in requested_id_set
    }
    ditto_paths: list[tuple[str, str]] = []
    for region in request.regions:
        if not region.target_path:
            continue
        item = returned.get(region.region_id)
        source_region_id = region.source_region_id or region.region_id
        reading, issue_code, is_ditto = _reading(
            source_region_id,
            region.target_path,
            region.bounding_box,
            item,
            candidates=region.candidates,
            allowed_suffixes=region.allowed_suffixes,
            candidate_type=region.candidate_type,
            transcription_format=region.transcription_format,
        )
        _assign(document, region.target_path, reading)
        if is_ditto:
            ditto_paths.append((region.target_path, source_region_id))
        if item is None:
            document.review_items.append(
                ReviewItem(
                    code="OCR_MAPPING_MISSING",
                    path=region.target_path,
                    message="OCR応答にこの矩形の転記結果がないため確認してください。",
                    severity="warning",
                    source_region_id=source_region_id,
                )
            )
        elif issue_code:
            message = {
                "SIRE_NAME_CANDIDATE_UNRESOLVED": (
                    "種雄牛名を登録済み候補へ確実に照合できないため確認してください。"
                ),
                "SALE_NOTE_SEX_SYMBOL_UNRESOLVED": (
                    "備考の先頭を♂または♀として確定できないため確認してください。"
                ),
                "BIRTH_DATE_ERA_REQUIRED": ("日付の元号(平成または令和)を選択してください。"),
                "BIRTH_DATE_NUMBERS_INVALID": (
                    "日付を元号年・月・日の3つの数字として確認してください。"
                ),
                "MARK_CHOICE_UNRESOLVED": (
                    "選択マークを確定できないため、丸の付いた項目を確認してください。"
                ),
            }.get(issue_code, "読み取り結果を確認してください。")
            document.review_items.append(
                ReviewItem(
                    code=issue_code,
                    path=region.target_path,
                    message=message,
                    severity="warning",
                    source_region_id=source_region_id,
                )
            )
    _resolve_ditto_readings(document, ditto_paths)
    document.processing_metadata.notes = [
        "RAW_TRANSCRIPTION_ONLY",
        "DETERMINISTIC_FIELD_MAPPING",
        "LABEL_INFERENCE_DISABLED",
        "TEMPLATE_INPUT_CELLS_ONLY",
    ]
    if ditto_paths:
        document.processing_metadata.notes.append("DITTO_MARK_RESOLUTION")
    if unknown_ids:
        document.processing_metadata.notes.append("IGNORED_UNKNOWN_MAPPING_IDS")
    return document


def _reading(
    mapping_id: str,
    target_path: str,
    box: tuple[int, int, int, int] | None,
    item: TranscriptionItem | None,
    *,
    candidates: tuple[str, ...] = (),
    allowed_suffixes: tuple[str, ...] = (),
    candidate_type: str | None = None,
    transcription_format: str | None = None,
) -> tuple[FieldReading, str | None, bool]:
    if item is None:
        return _missing_mapped_reading(mapping_id, box), None, False
    raw = item.raw.strip() if item.raw else None
    is_ditto = item.is_ditto or _looks_like_ditto(raw, target_path)
    bounding_box = BoundingBox(x=box[0], y=box[1], width=box[2], height=box[3]) if box else None
    if is_ditto:
        return (
            FieldReading(
                raw=raw or "〃",
                normalized=None,
                status=ReadingStatus.UNCERTAIN,
                alternatives=[],
                review_reason="DITTO_PENDING",
                source_region_id=mapping_id,
                bounding_box=bounding_box,
            ),
            None,
            True,
        )
    status = item.status
    review_reason = item.review_reason
    alternatives = item.alternatives
    issue_code: str | None = None
    normalized = _normalize(target_path, raw) if status is ReadingStatus.READABLE else None
    if transcription_format == "japanese_era_date_numbers" and status is not ReadingStatus.BLANK:
        numeric_raw, canonical_normalized, reason = canonical_japanese_birth_date(raw, None)
        raw = numeric_raw
        normalized = canonical_normalized
        if reason:
            status = ReadingStatus.UNCERTAIN
            review_reason = reason
            issue_code = reason
        elif status is ReadingStatus.READABLE:
            review_reason = None
    elif candidate_type == "sale_calf_note_prefix" and status is not ReadingStatus.BLANK:
        matched = canonical_sale_note(raw, item.matched_candidate, candidates)
        alternatives = [
            candidate
            for value in item.alternatives
            if (
                candidate := canonical_sale_note(
                    value,
                    item.matched_candidate,
                    candidates,
                )
            )
            is not None
        ]
        if status is ReadingStatus.READABLE and matched is not None:
            normalized = matched
            review_reason = None
        else:
            status = ReadingStatus.UNCERTAIN
            normalized = None
            review_reason = "SALE_NOTE_SEX_SYMBOL_UNRESOLVED"
            issue_code = review_reason
    elif candidate_type == "mark_choice" and status is not ReadingStatus.BLANK:
        selected = [candidate for candidate in candidates if raw and candidate in raw]
        canonical = "・".join(selected) if selected else None
        if status is ReadingStatus.READABLE and canonical is not None:
            raw = canonical
            normalized = canonical
            alternatives = []
            review_reason = None
        else:
            status = ReadingStatus.UNCERTAIN
            normalized = None
            review_reason = "MARK_CHOICE_UNRESOLVED"
            issue_code = review_reason
    elif candidates and status is not ReadingStatus.BLANK:
        matched = canonical_sire_candidate(
            item.matched_candidate,
            candidates,
            allowed_suffixes,
        )
        if matched is None:
            matched = canonical_sire_candidate(raw, candidates, allowed_suffixes)
        alternatives = [
            candidate
            for value in item.alternatives
            if (
                candidate := canonical_sire_candidate(
                    value,
                    candidates,
                    allowed_suffixes,
                )
            )
            is not None
        ]
        if status is ReadingStatus.READABLE and matched is not None:
            normalized = matched
        else:
            status = ReadingStatus.UNCERTAIN
            normalized = None
            review_reason = review_reason or "SIRE_NAME_CANDIDATE_UNRESOLVED"
            issue_code = review_reason
    return (
        FieldReading(
            raw=raw,
            normalized=normalized,
            status=status,
            alternatives=alternatives,
            review_reason=review_reason,
            source_region_id=mapping_id,
            bounding_box=bounding_box,
        ),
        issue_code,
        False,
    )


def _looks_like_ditto(raw: str | None, target_path: str) -> bool:
    if raw is None:
        return False
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw))
    if compact in DITTO_MARKS:
        return True
    supports_contextual_ditto = (
        target_path.startswith("saleCalves.") and target_path.endswith(".sireName")
    ) or (target_path.startswith("calvings.") and target_path.endswith(".notes"))
    if supports_contextual_ditto and compact in CONTEXTUAL_DITTO_MARKS:
        return True
    # On the ledger's short double-stroke ditto mark, vision OCR can return the
    # visually similar katakana ミ. Restrict this alias to calving notes so a
    # legitimate character in another field is never copied accidentally.
    return (
        target_path.startswith("calvings.") and target_path.endswith(".notes") and compact == "ミ"
    )


def _resolve_ditto_readings(
    document: OcrDocument,
    ditto_paths: list[tuple[str, str]],
) -> None:
    ordered = sorted(
        ditto_paths,
        key=lambda item: _record_path_sort_key(item[0]),
    )
    for target_path, source_region_id in ordered:
        parts = target_path.split(".")
        if (
            len(parts) != 3
            or parts[0] not in {"saleCalves", "calvings"}
            or not parts[1].isdigit()
            or int(parts[1]) == 0
        ):
            _mark_ditto_unresolved(document, target_path, source_region_id)
            continue
        previous_path = f"{parts[0]}.{int(parts[1]) - 1}.{parts[2]}"
        current = _get_record_reading(document, target_path)
        previous = _get_record_reading(document, previous_path)
        if (
            current is None
            or previous is None
            or previous.status is not ReadingStatus.READABLE
            or previous.raw is None
        ):
            _mark_ditto_unresolved(document, target_path, source_region_id)
            continue
        _assign(
            document,
            target_path,
            current.model_copy(
                update={
                    "normalized": previous.normalized or previous.raw,
                    "status": ReadingStatus.READABLE,
                    "alternatives": [],
                    "review_reason": None,
                }
            ),
        )


def _record_path_sort_key(target_path: str) -> tuple[str, str, int]:
    parts = target_path.split(".")
    if len(parts) == 3 and parts[1].isdigit():
        return parts[0], parts[2], int(parts[1])
    return target_path, "", -1


def _mark_ditto_unresolved(
    document: OcrDocument,
    target_path: str,
    source_region_id: str,
) -> None:
    current = _get_record_reading(document, target_path)
    if current is not None:
        _assign(
            document,
            target_path,
            current.model_copy(
                update={
                    "normalized": None,
                    "status": ReadingStatus.UNCERTAIN,
                    "review_reason": "DITTO_SOURCE_UNAVAILABLE",
                }
            ),
        )
    document.review_items.append(
        ReviewItem(
            code="DITTO_SOURCE_UNAVAILABLE",
            path=target_path,
            message="同上マークの直上セルを確定できないため確認してください。",
            severity="warning",
            source_region_id=source_region_id,
        )
    )


def _get_record_reading(
    document: OcrDocument,
    target_path: str,
) -> FieldReading | None:
    parts = target_path.split(".")
    if len(parts) != 3 or not parts[1].isdigit():
        return None
    index = int(parts[1])
    if (
        parts[0] == "saleCalves"
        and parts[2] in SALE_CALF_ATTRIBUTES
        and index < len(document.sale_calves)
    ):
        return cast(
            FieldReading,
            getattr(document.sale_calves[index], SALE_CALF_ATTRIBUTES[parts[2]]),
        )
    if parts[0] == "calvings" and parts[2] in CALVING_ATTRIBUTES and index < len(document.calvings):
        return cast(
            FieldReading,
            getattr(document.calvings[index], CALVING_ATTRIBUTES[parts[2]]),
        )
    return None


def _missing_mapped_reading(mapping_id: str, box: tuple[int, int, int, int] | None) -> FieldReading:
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
    if target_path.endswith(".priceYen"):
        return normalize_yen(raw)
    if target_path.endswith(".weightKg"):
        return normalize_decimal(raw)
    if target_path.endswith((".ageDays", ".parity")):
        return normalize_integer(raw)
    if target_path.endswith(("Date", ".birthDate")):
        return None
    return raw


def _assign(document: OcrDocument, target_path: str, reading: FieldReading) -> None:
    parts = target_path.split(".")
    custom_key = custom_field_key(target_path)
    if custom_key is not None:
        document.custom_fields[custom_key] = reading
        return
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
        if index >= SALE_CALF_ROW_COUNT:
            raise ValueError(
                f"Sale calf field mapping row must be 1-{SALE_CALF_ROW_COUNT}: {target_path}"
            )
        while len(document.sale_calves) <= index:
            document.sale_calves.append(_blank_sale_calf())
        setattr(document.sale_calves[index], SALE_CALF_ATTRIBUTES[parts[2]], reading)
        return
    if parts[0] == "calvings" and parts[2] in CALVING_ATTRIBUTES:
        if index >= CALVING_ROW_COUNT:
            raise ValueError(
                f"Calving field mapping row must be 1-{CALVING_ROW_COUNT}: {target_path}"
            )
        while len(document.calvings) <= index:
            document.calvings.append(_blank_calving())
        setattr(document.calvings[index], CALVING_ATTRIBUTES[parts[2]], reading)
        return
    raise ValueError(f"Unsupported field mapping target: {target_path}")


def assign_mapped_reading(
    document: OcrDocument,
    target_path: str,
    reading: FieldReading,
) -> None:
    """Assign a local detector result through the normal field-mapping rules."""
    _assign(document, target_path, reading)


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
