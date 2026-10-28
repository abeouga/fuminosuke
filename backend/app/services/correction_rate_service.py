"""How much of what OCR read people corrected before confirming (the correction rate).

Over the confirmed documents, each text field OCR read is compared with its confirmed value:

- ``read``: the fields OCR read with text (the denominator).
- ``corrected``: of those, the ones whose text was changed (a word, a digit) or cleared.
- ``formatOnly``: of those, the ones changed only in how they are written (spacing, character
  width, separators such as 「,」「/」「-」), counted apart from ``corrected``.
- ``missed``: fields OCR left blank that people filled in; not in the denominator.

Choices and checkmarks, numbered rows, values a keyword setting replaced and the fields a
document update carried over from the earlier version (not read this time) are left out.
Only reads; nothing is written.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import ConfirmedResult, OcrResult, ProcessingJob, Template
from app.db.session import Database
from app.domain.enums import JobType
from app.domain.field_targets import field_key
from app.editing.application.column_rewrite import skeleton
from app.services.document_series import SNAPSHOT_KEY

# Months are the months people work in (Japan).
JST = timezone(timedelta(hours=9))
COUNTS = ("read", "corrected", "formatOnly", "missed")


def _value(reading: object) -> str:
    if not isinstance(reading, Mapping):
        return ""
    value = reading.get("normalized") or reading.get("raw") or ""
    return value.strip() if isinstance(value, str) else ""


def measure(
    snapshot: Mapping[str, Any],
    raw_document: Mapping[str, Any],
    confirmed_document: Mapping[str, Any],
) -> dict[str, int]:
    """The counts of one confirmed document."""
    config = (snapshot.get("templateSnapshot") or {}).get("config") or {}
    mappings = {
        raw["mappingId"]: raw
        for raw in config.get("fieldMappings") or []
        if isinstance(raw, dict) and isinstance(raw.get("mappingId"), str)
    }
    series = snapshot.get(SNAPSHOT_KEY)
    carried = series.get("carried") if isinstance(series, dict) else None
    carried_keys = {
        field_key(spec["targetPath"])
        for spec in (carried.values() if isinstance(carried, dict) else [])
        if isinstance(spec, dict) and isinstance(spec.get("targetPath"), str)
    }
    raw_fields = raw_document.get("fields") or {}
    counts = dict.fromkeys(COUNTS, 0)
    for key, reading in (confirmed_document.get("fields") or {}).items():
        before_reading = raw_fields.get(key)
        if key in carried_keys or not isinstance(reading, dict):
            continue
        if not isinstance(before_reading, dict):
            continue
        mapping = mappings.get(reading.get("sourceRegionId"))
        if (
            mapping is None
            or mapping.get("mappingType", "text") != "text"
            or mapping.get("readingMode") == "number"
            or before_reading.get("reviewReason") == "KEYWORD_ABSORBED"
        ):
            continue
        before, after = _value(before_reading), _value(reading)
        if not before:
            counts["missed"] += 1 if after else 0
            continue
        counts["read"] += 1
        if before == after:
            continue
        if after and skeleton(before) == skeleton(after):
            counts["formatOnly"] += 1
        else:
            counts["corrected"] += 1
    return counts


def _row(**labels: Any) -> dict[str, Any]:
    return {**labels, "documents": 0, **dict.fromkeys(COUNTS, 0)}


def _add(row: dict[str, Any], counts: Mapping[str, int]) -> None:
    row["documents"] += 1
    for name in COUNTS:
        row[name] += counts[name]


class CorrectionRateService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def summary(self) -> dict[str, Any]:
        """Totals, and the same counts by template and by month (newest first)."""
        total = _row()
        templates: dict[str, dict[str, Any]] = {}
        months: dict[str, dict[str, Any]] = {}
        with self._database.session() as session:
            names = {row.id: row.name for row in session.scalars(select(Template))}
            rows = session.execute(
                select(ProcessingJob, OcrResult.raw_candidate_json, ConfirmedResult)
                .join(OcrResult, OcrResult.job_id == ProcessingJob.id)
                .join(ConfirmedResult, ConfirmedResult.job_id == ProcessingJob.id)
                .where(ProcessingJob.job_type == JobType.EXTRACT_DOCUMENT.value)
            ).all()
            for job, raw_document, confirmed in rows:
                snapshot = job.config_snapshot_json or {}
                counts = measure(snapshot, raw_document or {}, confirmed.confirmed_json or {})
                template_id = str(snapshot.get("templateId") or "")
                name = (
                    names.get(template_id)
                    or (snapshot.get("templateSnapshot") or {}).get("name")
                    or "名称なし"
                )
                month = _month(confirmed.confirmed_at)
                _add(total, counts)
                template = templates.setdefault(
                    template_id, _row(templateId=template_id, templateName=name)
                )
                _add(template, counts)
                _add(months.setdefault(month, _row(month=month)), counts)
        return {
            "total": total,
            "byTemplate": sorted(templates.values(), key=lambda item: -item["read"]),
            "byMonth": [months[month] for month in sorted(months, reverse=True)],
        }


def _month(value: datetime) -> str:
    moment = value if value.tzinfo else value.replace(tzinfo=UTC)
    return moment.astimezone(JST).strftime("%Y-%m")
