"""Project confirmed readings onto the accepted review structure.

Same ownership checks as the XLSX export (mappingId/targetPath agreement, every accepted
field used exactly once), but mappingId, targetPath, logical IDs and reading status are kept.
Labels and array positions never resolve values; no value is normalized or inferred here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.errors import AppError
from app.domain.field_targets import field_key
from app.editing.errors import reject

BASIC_SECTION = "基本項目"
_STATUSES = {"readable", "uncertain", "blank"}


def _invalid() -> AppError:
    return reject("STRUCTURED_SNAPSHOT_INVALID", "受付時の項目と表の対応を確認できません。", 409)


def _records(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def project_values(
    document: Mapping[str, Any],
    *,
    mappings: object,
    structure: object,
    logical_ids: object = None,
    identity_fields: object = None,
    page_by_mapping: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return identity/fields/tables/extraFields for one validated OcrDocument payload."""
    if not isinstance(structure, dict) or structure.get("schemaVersion") != 1:
        raise _invalid()
    if not isinstance(mappings, list) or not mappings:
        raise _invalid()
    accepted: dict[str, str] = {}
    for mapping in mappings:
        if not isinstance(mapping, dict) or mapping.get("enabled") is False:
            continue
        identifier, path = mapping.get("mappingId"), mapping.get("targetPath")
        if (
            not isinstance(identifier, str)
            or not isinstance(path, str)
            or identifier in accepted
            or field_key(path) is None
        ):
            raise _invalid()
        accepted[identifier] = path
    if len(set(accepted.values())) != len(accepted):
        raise _invalid()
    fields: dict[str, dict[str, Any]] = {}
    for field in _records(structure.get("fields")):
        identifier = field.get("id")
        if (
            not isinstance(identifier, str)
            or identifier in fields
            or field.get("mappingId") != identifier
            or accepted.get(identifier) != field.get("targetPath")
        ):
            raise _invalid()
        fields[identifier] = field
    if set(fields) != set(accepted):
        raise _invalid()
    readings = document.get("fields")
    if not isinstance(readings, dict):
        raise _invalid()
    logical = logical_ids if isinstance(logical_ids, dict) else {}
    pages = page_by_mapping or {}

    visited: set[str] = set()
    projected: list[dict[str, Any]] = []

    def reading_of(key: str) -> dict[str, Any]:
        reading = readings.get(key)
        if not isinstance(reading, dict) or reading.get("status") not in _STATUSES:
            raise _invalid()
        return reading

    def emit(
        identifier: object,
        *,
        section: str | None,
        table_id: str | None = None,
        row_number: int | None = None,
        column_id: str | None = None,
    ) -> None:
        if not isinstance(identifier, str) or identifier not in fields or identifier in visited:
            raise _invalid()
        visited.add(identifier)
        path = accepted[identifier]
        key = field_key(path)
        assert key is not None
        reading = reading_of(key)
        raw = _text(reading.get("raw"))
        normalized = _text(reading.get("normalized"))
        logical_id = logical.get(identifier)
        projected.append(
            {
                "fieldId": identifier,
                "logicalFieldId": logical_id if isinstance(logical_id, str) else None,
                "key": key,
                "targetPath": path,
                "label": str(fields[identifier].get("label") or path),
                "section": section,
                "pageId": pages.get(identifier),
                "tableId": table_id,
                "rowNumber": row_number,
                "columnId": column_id,
                "raw": raw,
                "normalized": normalized,
                "value": normalized if normalized is not None else raw,
                "status": reading["status"],
                "reviewReason": _text(reading.get("reviewReason")),
            }
        )

    def emit_all(section: str, identifiers: object) -> None:
        if not isinstance(identifiers, list):
            raise _invalid()
        for identifier in identifiers:
            emit(identifier, section=section)

    emit_all(BASIC_SECTION, structure.get("independentFieldIds"))
    for parent in _records(structure.get("parentSections")):
        emit_all(str(parent.get("title") or "項目"), parent.get("fieldIds"))

    tables: list[dict[str, Any]] = []
    for section in _records(structure.get("recordSections")):
        table_id = section.get("id")
        columns = _records(section.get("columns"))
        column_ids = [column.get("id") for column in columns]
        if (
            not isinstance(table_id, str)
            or not columns
            or any(not isinstance(item, str) for item in column_ids)
            or len(set(column_ids)) != len(column_ids)
        ):
            raise _invalid()
        title = str(section.get("title") or "明細")
        rows: list[dict[str, Any]] = []
        for row_number, record in enumerate(_records(section.get("rows")), start=1):
            row_id = record.get("id")
            entries = _records(record.get("cells"))
            by_column = {entry.get("columnId"): entry.get("fieldId") for entry in entries}
            if (
                not isinstance(row_id, str)
                or len(by_column) != len(entries)
                or set(by_column) != set(column_ids)
            ):
                raise _invalid()
            cells: list[dict[str, Any]] = []
            for column_id in column_ids:
                assert isinstance(column_id, str)
                field_id = by_column[column_id]
                if field_id is not None:
                    emit(
                        field_id,
                        section=title,
                        table_id=table_id,
                        row_number=row_number,
                        column_id=column_id,
                    )
                cells.append({"columnId": column_id, "fieldId": field_id})
            rows.append({"id": row_id, "rowNumber": row_number, "cells": cells})
        collection = section.get("collectionPath")
        tables.append(
            {
                "id": table_id,
                "title": title,
                "collectionPath": collection if isinstance(collection, str) else None,
                "columns": [
                    {"id": column["id"], "label": str(column.get("label") or "項目")}
                    for column in columns
                ],
                "rows": rows,
            }
        )
    if visited != set(fields):
        raise _invalid()

    identity: list[dict[str, Any]] = []
    for hint in _records(identity_fields):
        path = hint.get("targetPath")
        key = field_key(path) if isinstance(path, str) else None
        if key is None:
            raise _invalid()
        reading = readings.get(key)
        status = reading.get("status") if isinstance(reading, dict) else None
        raw = _text(reading.get("raw")) if isinstance(reading, dict) else None
        normalized = _text(reading.get("normalized")) if isinstance(reading, dict) else None
        mapping_id = hint.get("mappingId")
        identity.append(
            {
                "label": str(hint.get("label") or path),
                "fieldId": mapping_id if isinstance(mapping_id, str) else None,
                "targetPath": path,
                "value": normalized if normalized is not None else raw,
                "status": status if status in _STATUSES else None,
                "groupLabel": _text(hint.get("matchGroupLabel")),
            }
        )

    used_paths = set(accepted.values())
    labels = document.get("fieldLabels")
    labels = labels if isinstance(labels, dict) else {}
    extra: list[dict[str, Any]] = []
    for key, reading in readings.items():
        if f"fields.{key}" in used_paths:
            continue
        reading = reading_of(key)
        raw = _text(reading.get("raw"))
        normalized = _text(reading.get("normalized"))
        extra.append(
            {
                "key": key,
                "label": str(labels.get(key) or key),
                "raw": raw,
                "normalized": normalized,
                "value": normalized if normalized is not None else raw,
                "status": reading["status"],
            }
        )
    return {"identity": identity, "fields": projected, "tables": tables, "extraFields": extra}
