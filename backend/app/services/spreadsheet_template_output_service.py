from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook  # type: ignore[import-untyped]
from openpyxl.worksheet.worksheet import Worksheet  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.domain.enums import TemplateMode
from app.storage.local_storage import LocalStorageService


_SOURCE_CELL_ID = re.compile(r"^xlsx_([A-Z]{1,3}[1-9]\d*)(?:$|_)")
_UNSAFE_FILE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_HYPHENS = "‐‑‒–—―ー－-"
_MISSING = object()


@dataclass(frozen=True, slots=True)
class FilledSpreadsheetTemplate:
    content: bytes
    file_name: str


class SpreadsheetTemplateOutputService:
    """Writes confirmed OCR values into a copy of the source XLSX workbook."""

    def __init__(self, storage: LocalStorageService) -> None:
        self._storage = storage

    def render(
        self,
        session: Session,
        *,
        job: ProcessingJob,
        document: dict[str, Any],
        confirmed_at: datetime,
    ) -> FilledSpreadsheetTemplate | None:
        if not job.template_version_id:
            return None
        version = session.get(TemplateVersion, job.template_version_id)
        if version is None or version.mode != TemplateMode.SPREADSHEET_TEMPLATE.value:
            return None
        if not version.source_job_id:
            raise self._source_error("元のXLSXに接続されたテンプレート作成履歴がありません。")

        source_artifact = session.scalar(
            select(JobArtifact)
            .where(
                JobArtifact.job_id == version.source_job_id,
                JobArtifact.artifact_name == "sourceWorkbook",
            )
            .order_by(JobArtifact.attempt_number.desc())
            .limit(1)
        )
        if source_artifact is None or not self._storage.exists(source_artifact.relative_path):
            raise self._source_error("テンプレート作成時の元XLSXを取得できません。")

        try:
            workbook = load_workbook(
                io.BytesIO(self._storage.read_bytes(source_artifact.relative_path)),
                data_only=False,
                read_only=False,
                keep_links=True,
            )
        except Exception as exc:
            raise self._source_error("テンプレート作成時の元XLSXを開けません。") from exc

        source_sheet_name = version.quality_json.get("sourceSheetName")
        if isinstance(source_sheet_name, str) and source_sheet_name in workbook.sheetnames:
            worksheet = workbook[source_sheet_name]
        else:
            worksheet = workbook.active

        mappings = job.config_snapshot_json.get("fieldMappings")
        if not isinstance(mappings, list):
            raise AppError(
                status=409,
                code="SPREADSHEET_OUTPUT_MAPPING_MISSING",
                title="Spreadsheet output mapping is missing",
                detail="XLSXのセルと読み取り項目の対応を取得できません。",
            )

        written_cells = 0
        for raw_mapping in mappings:
            if not isinstance(raw_mapping, dict) or raw_mapping.get("enabled") is False:
                continue
            value = self._mapping_value(document, raw_mapping)
            if value is _MISSING:
                continue
            destinations = self._destinations(raw_mapping, version.layout_json, value)
            for coordinate, destination_value in destinations:
                anchor = self._merged_anchor(worksheet, coordinate)
                cell = worksheet[anchor]
                cell.value = destination_value
                if isinstance(destination_value, str):
                    # OCR text must remain text even when it begins with "=".
                    cell.data_type = "s"
                written_cells += 1

        if written_cells == 0:
            raise AppError(
                status=409,
                code="SPREADSHEET_OUTPUT_MAPPING_MISSING",
                title="Spreadsheet output mapping is missing",
                detail="読み取り項目に対応する元XLSXのセルが見つかりません。",
            )

        output = io.BytesIO()
        try:
            workbook.save(output)
        except Exception as exc:
            raise AppError(
                status=409,
                code="SPREADSHEET_OUTPUT_FAILED",
                title="Spreadsheet output failed",
                detail="入力済みXLSXを生成できませんでした。",
            ) from exc

        raw_source_name = version.quality_json.get("sourceFileName")
        source_name = (
            raw_source_name
            if isinstance(raw_source_name, str) and raw_source_name.strip()
            else Path(source_artifact.relative_path).name
        )
        stem = _UNSAFE_FILE_NAME.sub("_", Path(source_name).stem).strip(" .") or "帳票"
        stamp = confirmed_at.strftime("%Y%m%d_%H%M%S")
        return FilledSpreadsheetTemplate(
            content=output.getvalue(),
            file_name=f"{stem}_入力済み_{stamp}.xlsx",
        )

    @classmethod
    def _mapping_value(cls, document: dict[str, Any], mapping: dict[str, Any]) -> object:
        target_path = mapping.get("targetPath")
        value = cls._value_at_path(document, target_path) if isinstance(target_path, str) else _MISSING
        if value not in {_MISSING, None, ""}:
            return value

        composite_values: list[str] = []
        raw_subfields = mapping.get("compositeSubfields")
        if isinstance(raw_subfields, list):
            for raw_subfield in raw_subfields:
                if not isinstance(raw_subfield, dict):
                    continue
                subfield_path = raw_subfield.get("targetPath")
                subfield_value = (
                    cls._value_at_path(document, subfield_path)
                    if isinstance(subfield_path, str)
                    else _MISSING
                )
                if subfield_value not in {_MISSING, None, ""}:
                    composite_values.append(str(subfield_value))
        if composite_values:
            return "".join(composite_values)
        return value

    @staticmethod
    def _value_at_path(document: dict[str, Any], path: str) -> object:
        current: object = document
        for part in path.split("."):
            if isinstance(current, dict):
                if part not in current:
                    return _MISSING
                current = current[part]
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                if index >= len(current):
                    return _MISSING
                current = current[index]
            else:
                return _MISSING

        if isinstance(current, dict) and ("normalized" in current or "raw" in current):
            if current.get("status") == "blank":
                return None
            normalized = current.get("normalized")
            raw = current.get("raw")
            return normalized if normalized is not None else raw
        if isinstance(current, list):
            return "、".join(str(item) for item in current)
        if isinstance(current, (str, int, float, bool)) or current is None:
            return current
        return _MISSING

    @classmethod
    def _destinations(
        cls,
        mapping: dict[str, Any],
        layout: dict[str, Any],
        value: object,
    ) -> list[tuple[str, str | int | float | bool | None]]:
        cell = cls._layout_cell(mapping, layout)
        if cell is None:
            return []
        cell_id = cell.get("id")
        if not isinstance(cell_id, str):
            return []

        merge = next(
            (
                item
                for item in layout.get("cellMerges", [])
                if isinstance(item, dict) and item.get("parentCellId") == cell_id
            ),
            None,
        )
        if not isinstance(merge, dict):
            coordinate = cls._coordinate(cell_id)
            return [(coordinate, cls._excel_value(value))] if coordinate else []

        parts = merge.get("parts")
        if not isinstance(parts, list):
            return []
        input_parts = [
            part
            for part in parts
            if isinstance(part, dict) and part.get("partType") == "input"
        ]
        coordinates = [
            coordinate
            for part in input_parts
            if isinstance(part.get("cell"), dict)
            and isinstance(part["cell"].get("id"), str)
            and (coordinate := cls._coordinate(part["cell"]["id"])) is not None
        ]
        if not coordinates:
            return []
        excel_value = cls._excel_value(value)
        if len(coordinates) == 1 or excel_value is None:
            return [(coordinate, excel_value) for coordinate in coordinates]

        split_values = cls._split_merged_value(str(excel_value), parts, len(coordinates))
        return list(zip(coordinates, split_values, strict=True))

    @staticmethod
    def _layout_cell(mapping: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any] | None:
        cells = [item for item in layout.get("cells", []) if isinstance(item, dict)]
        mapping_id = mapping.get("mappingId")
        if isinstance(mapping_id, str) and mapping_id.startswith("layout_"):
            raw_cell_id = mapping_id.removeprefix("layout_")
            exact = next((item for item in cells if item.get("id") == raw_cell_id), None)
            if exact is not None:
                return exact
            prefixed = sorted(
                (
                    item
                    for item in cells
                    if isinstance(item.get("id"), str)
                    and mapping_id.startswith(f"layout_{item['id']}_")
                ),
                key=lambda item: len(str(item["id"])),
                reverse=True,
            )
            if prefixed:
                return prefixed[0]

        geometry_keys = ("x", "y", "width", "height")
        return next(
            (
                item
                for item in cells
                if all(item.get(key) == mapping.get(key) for key in geometry_keys)
            ),
            None,
        )

    @staticmethod
    def _coordinate(cell_id: str) -> str | None:
        match = _SOURCE_CELL_ID.match(cell_id)
        return match.group(1) if match else None

    @staticmethod
    def _excel_value(value: object) -> str | int | float | bool | None:
        if value is _MISSING or value is None or value == "":
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @classmethod
    def _split_merged_value(
        cls,
        value: str,
        parts: list[object],
        input_count: int,
    ) -> list[str | None]:
        literals: list[str] = []
        for part in parts:
            if not isinstance(part, dict) or part.get("partType") != "literal":
                continue
            cell = part.get("cell")
            literal = part.get("literal") or (cell.get("text") if isinstance(cell, dict) else "")
            if isinstance(literal, str) and literal.strip():
                compact = literal.strip()
                literals.append(
                    f"[{re.escape(_HYPHENS)}]+"
                    if all(character in _HYPHENS for character in compact)
                    else re.escape(compact)
                )
        if literals:
            segments = [item.strip() for item in re.split("|".join(literals), value)]
            while len(segments) > input_count and segments and not segments[-1]:
                segments.pop()
            while len(segments) > input_count and segments and not segments[0]:
                segments.pop(0)
            if len(segments) == input_count:
                return [item or None for item in segments]
        return [value, *([None] * (input_count - 1))]

    @staticmethod
    def _merged_anchor(worksheet: Worksheet, coordinate: str) -> str:
        for merged_range in worksheet.merged_cells.ranges:
            if coordinate in merged_range:
                return worksheet.cell(merged_range.min_row, merged_range.min_col).coordinate
        return coordinate

    @staticmethod
    def _source_error(detail: str) -> AppError:
        return AppError(
            status=409,
            code="SPREADSHEET_TEMPLATE_SOURCE_MISSING",
            title="Spreadsheet template source is missing",
            detail=detail,
        )
