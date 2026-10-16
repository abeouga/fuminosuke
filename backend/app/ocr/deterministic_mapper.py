from __future__ import annotations

import re
import unicodedata

from app.domain.field_targets import field_key
from app.domain.ocr_factory import blank_ocr_document
from app.domain.ocr_models import BoundingBox, FieldReading, OcrDocument, ReadingStatus, ReviewItem
from app.domain.template_candidates import canonical_managed_candidate
from app.ocr.base import OcrProviderRequest
from app.ocr.transcription_models import TranscriptionBatch, TranscriptionItem


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
            identity_value_type=region.identity_value_type,
            identity_exact_length=region.identity_exact_length,
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
                "MANAGED_CANDIDATE_UNRESOLVED": (
                    "登録済みの読み取り候補と一致しないため確認してください。"
                ),
                "MARK_CHOICE_UNRESOLVED": (
                    "選択マークを確定できないため、印の付いた項目を確認してください。"
                ),
                "DOCUMENT_IDENTITY_FORMAT_INVALID": (
                    "書類を見分ける番号の形式が設定と一致しないため確認してください。"
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
    identity_value_type: str | None = None,
    identity_exact_length: int | None = None,
) -> tuple[FieldReading, str | None, bool]:
    if item is None:
        return _missing_mapped_reading(mapping_id, box), None, False
    raw = item.raw.strip() if item.raw else None
    is_ditto = item.is_ditto
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
    normalized = raw if status is ReadingStatus.READABLE else None
    if identity_value_type == "digits" and status is not ReadingStatus.BLANK:
        compact = re.sub(
            r"[\s\-\u2010\u2011\u2012\u2013\u2014\u2015\u30fc\uff0d]",
            "",
            unicodedata.normalize("NFKC", raw or ""),
        )
        valid_length = identity_exact_length is None or len(compact) == identity_exact_length
        if compact.isdigit() and valid_length:
            normalized = compact
            if status is ReadingStatus.READABLE:
                review_reason = None
        else:
            status = ReadingStatus.UNCERTAIN
            normalized = None
            review_reason = "DOCUMENT_IDENTITY_FORMAT_INVALID"
            issue_code = review_reason
    elif candidate_type in {"mark_choice", "symbol_choice", "checklist"} and status is not ReadingStatus.BLANK:
        selected = (
            ([raw] if raw in candidates else [])
            if candidate_type == "symbol_choice"
            else _selected_mark_labels(raw, candidates)
        )
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
        candidate_matcher = canonical_managed_candidate
        matched = candidate_matcher(
            item.matched_candidate,
            candidates,
            allowed_suffixes,
        )
        if matched is None:
            matched = candidate_matcher(raw, candidates, allowed_suffixes)
        alternatives = [
            candidate
            for value in item.alternatives
            if (
                candidate := candidate_matcher(
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
            review_reason = review_reason or "MANAGED_CANDIDATE_UNRESOLVED"
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


def _selected_mark_labels(raw: str | None, candidates: tuple[str, ...]) -> list[str]:
    if raw is None:
        return []
    # A label may contain another label (or a list separator). Match it whole
    # before parsing a multi-selection; never accept only part of an unknown label.
    if raw in candidates:
        return [raw]
    labels = {label.strip() for label in re.split(r"[・、,;\n]+", raw)}
    if not labels or not labels.issubset(candidates):
        return []
    return [candidate for candidate in candidates if candidate in labels]


def _resolve_ditto_readings(document: OcrDocument, paths: list[tuple[str, str]]) -> None:
    for path, region in paths:
        document.review_items.append(
            ReviewItem(
                code="DITTO_UNRESOLVED",
                path=path,
                message="同上の値を確認して入力してください。",
                severity="warning",
                source_region_id=region,
            )
        )


def _missing_mapped_reading(mapping_id: str, box: tuple[int, int, int, int] | None) -> FieldReading:
    return FieldReading(
        status=ReadingStatus.UNCERTAIN,
        source_region_id=mapping_id,
        review_reason="OCR_MAPPING_MISSING",
        bounding_box=BoundingBox(x=box[0], y=box[1], width=box[2], height=box[3]) if box else None,
    )


def _assign(document: OcrDocument, target_path: str, reading: FieldReading) -> None:
    key = field_key(target_path)
    if key is None:
        raise ValueError(f"Unsupported generic field path: {target_path}")
    document.fields[key] = reading


def assign_mapped_reading(document: OcrDocument, target_path: str, reading: FieldReading) -> None:
    _assign(document, target_path, reading)


def get_mapped_reading(document: OcrDocument, target_path: str) -> FieldReading | None:
    key = field_key(target_path)
    return document.fields.get(key) if key is not None else None
