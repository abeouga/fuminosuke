"""Checklist confirmation rules shared by image and paged reviews."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.core.errors import AppError
from app.domain.ocr_models import OcrDocument, ReadingStatus


def require_resolved_checklists(document: OcrDocument, mappings: Iterable[Mapping[str, Any]]) -> None:
    for mapping in mappings:
        if mapping.get("markRecognitionMode") != "checklist" or mapping.get("enabled") is False:
            continue
        reading = document.fields.get(str(mapping["targetPath"]).removeprefix("fields."))
        allowed = {option["label"] for option in mapping.get("markOptions", [])}
        value = (reading.normalized or reading.raw) if reading is not None else None
        selected = value.split("・") if value else []
        valid = reading is not None and (
            reading.status == ReadingStatus.BLANK
            or (reading.status == ReadingStatus.READABLE and bool(selected)
                and len(selected) == len(set(selected)) and set(selected) <= allowed)
        )
        if not valid:
            raise AppError(
                status=409, code="CHECKLIST_REVIEW_REQUIRED",
                title="Checklist review is required",
                detail=f"{mapping.get('label', 'チェックリスト')}のチェック状態を確認してください。判別不能のまま確定できません。",
            )

