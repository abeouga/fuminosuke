"""Checklist confirmation rules shared by image and paged reviews."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.core.errors import AppError
from app.domain.ocr_models import OcrDocument, ReadingStatus, ReviewItem


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


def flag_symbol_write_ins(document: OcrDocument, mappings: Iterable[Mapping[str, Any]]) -> bool:
    """A symbol box (○ etc.) left blank beside its filled write-in 「その他(　)」 needs a person.

    Which symbol was meant cannot be known, so nothing is filled in. Applied once after OCR
    only: a person's later correction to blank is kept as it is.
    """
    changed = False
    for mapping in mappings:
        if mapping.get("markRecognitionMode") != "symbol" or mapping.get("enabled") is False:
            continue
        key = str(mapping["targetPath"]).removeprefix("fields.")
        reading = document.fields.get(key)
        if reading is None or reading.status != ReadingStatus.BLANK:
            continue
        for option in mapping.get("markOptions", []):
            path = option.get("writeInTargetPath")
            note = document.fields.get(str(path).removeprefix("fields.")) if path else None
            text = (note.normalized or note.raw) if note is not None else None
            if note is None or note.status == ReadingStatus.BLANK or not (text or "").strip():
                continue
            document.fields[key] = reading.model_copy(
                update={"status": ReadingStatus.UNCERTAIN, "review_reason": "WRITE_IN_WITHOUT_MARK"}
            )
            document.review_items.append(
                ReviewItem(
                    code="WRITE_IN_WITHOUT_MARK",
                    path=str(mapping["targetPath"]),
                    message="記入欄に記入がありますが、記号が読み取れません。記号を確認してください。",
                    severity="warning",
                    source_region_id=reading.source_region_id,
                )
            )
            changed = True
            break
    return changed


def apply_write_ins(document: OcrDocument, mappings: Iterable[Mapping[str, Any]]) -> bool:
    """Text written in an option's write-in field (「その他(　)」) selects that option.

    Applied after OCR and on every saved correction, so review, confirmation, output and
    questions read one state. An undecided checklist stays undecided for a person.
    """
    changed = False
    for mapping in mappings:
        if mapping.get("markRecognitionMode") != "checklist" or mapping.get("enabled") is False:
            continue
        options = mapping.get("markOptions", [])
        key = str(mapping["targetPath"]).removeprefix("fields.")
        for option in options:
            path = option.get("writeInTargetPath")
            reading = document.fields.get(key)
            if not path or reading is None or reading.status == ReadingStatus.UNCERTAIN:
                continue
            note = document.fields.get(str(path).removeprefix("fields."))
            text = (note.normalized or note.raw) if note is not None else None
            if note is None or note.status == ReadingStatus.BLANK or not (text or "").strip():
                continue
            value = (
                (reading.normalized or reading.raw)
                if reading.status == ReadingStatus.READABLE
                else None
            )
            selected = set(value.split("・")) if value else set()
            if option["label"] in selected:
                continue
            selected.add(option["label"])
            joined = "・".join(o["label"] for o in options if o["label"] in selected)
            document.fields[key] = reading.model_copy(
                update={
                    "raw": joined,
                    "normalized": joined,
                    "status": ReadingStatus.READABLE,
                    "review_reason": None,
                    "alternatives": [],
                }
            )
            changed = True
    return changed
