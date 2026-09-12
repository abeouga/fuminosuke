from __future__ import annotations

import copy
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook  # type: ignore[import-untyped]
from openpyxl.utils.cell import (  # type: ignore[import-untyped]
    column_index_from_string,
    get_column_letter,
)
from openpyxl.workbook.workbook import Workbook  # type: ignore[import-untyped]
from openpyxl.worksheet.worksheet import Worksheet  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.domain.enums import TemplateMode
from app.domain.spreadsheet_output_name import normalize_spreadsheet_output_name
from app.storage.local_storage import LocalStorageService

_SOURCE_CELL_ID = re.compile(r"^xlsx_([A-Z]{1,3}[1-9]\d*)(?:$|_)")
_CELL_COORDINATE = re.compile(r"^([A-Z]{1,3})([1-9]\d*)$")
_MISSING = object()


@dataclass(frozen=True, slots=True)
class FilledSpreadsheetTemplate:
    content: bytes
    file_name: str


@dataclass(frozen=True, slots=True)
class _MergePlan:
    merge_id: str
    parent_cell_id: str
    excel_range: str
    anchor_cell: str
    parts_by_coordinate: dict[str, dict[str, Any]]
    source_ranges: tuple[str, ...] = ()


class SpreadsheetTemplateOutputService:
    """Writes confirmed OCR values into a copy of the source XLSX workbook."""

    def __init__(self, storage: LocalStorageService) -> None:
        self._storage = storage

    def snapshot(
        self,
        session: Session,
        *,
        version: TemplateVersion,
        layout: dict[str, Any] | None = None,
    ) -> dict[str, object] | None:
        """Capture the XLSX output structure used by a newly accepted OCR job."""
        if version.mode != TemplateMode.SPREADSHEET_TEMPLATE.value:
            return None
        if not version.source_job_id:
            raise self._source_error("元のXLSXに接続されたテンプレート作成履歴がありません。")

        source_artifact = self._latest_source_artifact(session, version.source_job_id)
        if source_artifact is None or not self._storage.exists(source_artifact.relative_path):
            raise self._source_error("テンプレート作成時の元XLSXを取得できません。")
        source_bytes = self._storage.read_bytes(source_artifact.relative_path)
        if hashlib.sha256(source_bytes).hexdigest() != source_artifact.sha256:
            raise self._source_error("テンプレート作成時の元XLSXの整合性を確認できません。")
        try:
            workbook = load_workbook(
                io.BytesIO(source_bytes),
                data_only=False,
                read_only=False,
                keep_links=True,
            )
        except Exception as exc:
            raise self._source_error("テンプレート作成時の元XLSXを開けません。") from exc

        raw_sheet_name = version.quality_json.get("sourceSheetName")
        source_sheet_name = (
            raw_sheet_name
            if isinstance(raw_sheet_name, str) and raw_sheet_name in workbook.sheetnames
            else workbook.active.title
        )
        raw_source_name = version.quality_json.get("sourceFileName")
        source_file_name = (
            raw_source_name
            if isinstance(raw_source_name, str) and raw_source_name.strip()
            else Path(source_artifact.relative_path).name
        )
        layout = version.layout_json if layout is None else layout
        raw_cells = layout.get("cells") if isinstance(layout, dict) else None
        raw_merges = layout.get("cellMerges") if isinstance(layout, dict) else None
        if not isinstance(raw_cells, list) or not isinstance(raw_merges, list):
            raise self._snapshot_error("XLSX出力用のlayout情報を固定できません。")

        cell_merges: list[dict[str, Any]] = []
        for raw_merge in raw_merges:
            if not isinstance(raw_merge, dict):
                raise self._merge_invalid("cellMergesに不正な要素があります。")
            source_ranges = self._source_ranges(workbook[source_sheet_name], raw_merge)
            plan = self._merge_plan(
                raw_merge, require_snapshot_metadata=False, source_ranges=source_ranges
            )
            merge = copy.deepcopy(raw_merge)
            merge.pop("sourceRanges", None)
            if source_ranges:
                merge["sourceRanges"] = source_ranges
            merge["excelRange"] = plan.excel_range
            merge["anchorCell"] = plan.anchor_cell
            cell_merges.append(merge)

        snapshot = {
            "schemaVersion": 2 if any("sourceRanges" in m for m in cell_merges) else 1,
            "templateVersionId": version.id,
            "sourceWorkbook": {
                "artifactId": source_artifact.id,
                "sourceJobId": source_artifact.job_id,
                "sha256": source_artifact.sha256,
            },
            "sourceSheetName": source_sheet_name,
            "sourceFileName": source_file_name,
            "cells": copy.deepcopy(raw_cells),
            "cellMerges": cell_merges,
        }
        # New source-backed plans must also be safe to materialize, before OCR starts.
        plans = self._merge_plans(snapshot, workbook[source_sheet_name])
        for plan in plans:
            if plan.source_ranges:
                self._ensure_merge_contents_safe(workbook[source_sheet_name], plan)
        return snapshot

    @classmethod
    def page_merge_sources(
        cls, worksheet: Worksheet, merge: dict[str, Any],
    ) -> list[dict[str, str]]:
        """Source identity for page authoring, sharing the output range checks."""
        source_ranges = cls._source_ranges(worksheet, merge)
        plan = cls._merge_plan(merge, require_snapshot_metadata=False, source_ranges=source_ranges)
        cls._ensure_merge_contents_safe(worksheet, plan)
        result = []
        for part in merge["parts"]:
            coordinate = cls._coordinate(part["cell"]["id"])
            if coordinate is None:
                raise cls._merge_invalid("merge partの元セル座標を確定できません。")
            result.append({"cellId": part["cell"]["id"],
                           "sourceRange": (source_ranges or {}).get(
                               coordinate, f"{coordinate}:{coordinate}")})
        return result

    @staticmethod
    def is_final_output(content: bytes, document_name: str) -> bool:
        try:
            workbook = load_workbook(
                io.BytesIO(content),
                data_only=False,
                read_only=True,
                keep_links=True,
            )
        except Exception:
            return False
        try:
            return bool(workbook.sheetnames == [document_name])
        finally:
            workbook.close()

    def render(
        self,
        session: Session,
        *,
        job: ProcessingJob,
        document: dict[str, Any],
        document_name: str,
        confirmed_at: datetime,
    ) -> FilledSpreadsheetTemplate | None:
        if not job.template_version_id:
            return None
        version = session.get(TemplateVersion, job.template_version_id)
        if version is None or version.mode != TemplateMode.SPREADSHEET_TEMPLATE.value:
            return None
        snapshot = self._output_snapshot(job)
        source = snapshot["sourceWorkbook"]
        assert isinstance(source, dict)
        artifact_id = source["artifactId"]
        source_job_id = source["sourceJobId"]
        expected_sha256 = source["sha256"]
        assert isinstance(artifact_id, str)
        assert isinstance(source_job_id, str)
        assert isinstance(expected_sha256, str)
        source_artifact = session.get(JobArtifact, artifact_id)
        if source_artifact is None or not self._storage.exists(source_artifact.relative_path):
            raise self._source_error("テンプレート作成時の元XLSXを取得できません。")
        if (
            source_artifact.job_id != source_job_id
            or source_artifact.artifact_name != "sourceWorkbook"
            or source_artifact.sha256 != expected_sha256
        ):
            raise self._snapshot_error("固定された元XLSXのidentityが一致しません。")

        source_bytes = self._storage.read_bytes(source_artifact.relative_path)
        if hashlib.sha256(source_bytes).hexdigest() != expected_sha256:
            raise self._source_error("テンプレート作成時の元XLSXの整合性を確認できません。")
        try:
            workbook = load_workbook(
                io.BytesIO(source_bytes),
                data_only=False,
                read_only=False,
                keep_links=True,
            )
        except Exception as exc:
            raise self._source_error("テンプレート作成時の元XLSXを開けません。") from exc

        source_sheet_name = snapshot["sourceSheetName"]
        assert isinstance(source_sheet_name, str)
        if source_sheet_name not in workbook.sheetnames:
            raise self._snapshot_error("固定された出力先sheetが元XLSXに存在しません。")
        worksheet = workbook[source_sheet_name]

        mappings = job.config_snapshot_json.get("fieldMappings")
        if not isinstance(mappings, list):
            raise AppError(
                status=409,
                code="SPREADSHEET_OUTPUT_MAPPING_MISSING",
                title="Spreadsheet output mapping is missing",
                detail="XLSXのセルと読み取り項目の対応を取得できません。",
            )

        merge_plans = self._merge_plans(snapshot, worksheet)
        self._apply_merges(worksheet, merge_plans)

        written_cells = 0
        for raw_mapping in mappings:
            if not isinstance(raw_mapping, dict) or raw_mapping.get("enabled") is False:
                continue
            value = self._mapping_value(document, raw_mapping)
            if value is _MISSING or value is None or value == "":
                continue
            destinations = self._destinations(raw_mapping, snapshot, value)
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

        output_name = normalize_spreadsheet_output_name(document_name)
        self._retain_and_rename_result_sheet(
            workbook,
            worksheet,
            output_name=output_name,
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

        del confirmed_at
        return FilledSpreadsheetTemplate(
            content=output.getvalue(),
            file_name=f"{output_name}.xlsx",
        )

    @classmethod
    def _mapping_value(cls, document: dict[str, Any], mapping: dict[str, Any]) -> object:
        target_path = mapping.get("targetPath")
        value = (
            cls._value_at_path(document, target_path)
            if isinstance(target_path, str)
            else _MISSING
        )
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
        if isinstance(current, str | int | float | bool) or current is None:
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
        anchor = merge.get("anchorCell")
        return (
            [(anchor, cls._excel_value(value))]
            if isinstance(anchor, str) and cls._coordinate(f"xlsx_{anchor}") is not None
            else []
        )

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
        if isinstance(value, str | int | float | bool):
            return value
        return str(value)

    @classmethod
    def _merge_plans(
        cls, snapshot: dict[str, Any], worksheet: Worksheet | None = None
    ) -> list[_MergePlan]:
        raw_merges = snapshot.get("cellMerges")
        if not isinstance(raw_merges, list):
            raise cls._snapshot_error("固定されたcellMergesを取得できません。")
        plans: list[_MergePlan] = []
        parent_ids: set[str] = set()
        excel_ranges: set[str] = set()
        for raw_merge in raw_merges:
            if not isinstance(raw_merge, dict):
                raise cls._merge_invalid("cellMergesに不正な要素があります。")
            source_ranges = None
            if snapshot.get("schemaVersion") == 2 and "sourceRanges" in raw_merge:
                if worksheet is None:
                    raise cls._snapshot_error("元XLSXの結合範囲を検証できません。")
                source_ranges = cls._source_ranges(worksheet, raw_merge)
                if not source_ranges or source_ranges != raw_merge["sourceRanges"]:
                    raise cls._snapshot_error("固定された元セル範囲が元XLSXと一致しません。")
            plan = cls._merge_plan(
                raw_merge, require_snapshot_metadata=True, source_ranges=source_ranges
            )
            if plan.parent_cell_id in parent_ids or plan.excel_range in excel_ranges:
                raise cls._merge_invalid("cellMergesに重複するrangeまたはparentがあります。")
            if any(
                cls._ranges_overlap(
                    cls._range_bounds(plan.excel_range), cls._range_bounds(p.excel_range)
                )
                for p in plans
            ):
                raise cls._merge_invalid("別の結合範囲と重複しています。")
            parent_ids.add(plan.parent_cell_id)
            excel_ranges.add(plan.excel_range)
            plans.append(plan)
        return plans

    @classmethod
    def _merge_plan(
        cls,
        merge: dict[str, Any],
        *,
        require_snapshot_metadata: bool,
        source_ranges: dict[str, str] | None = None,
    ) -> _MergePlan:
        merge_id = merge.get("id")
        parent_cell_id = merge.get("parentCellId")
        raw_parts = merge.get("parts")
        if (
            not isinstance(merge_id, str)
            or not isinstance(parent_cell_id, str)
            or not isinstance(raw_parts, list)
            or len(raw_parts) < 2
        ):
            raise cls._merge_invalid("cellMergesのidentityまたはpartsが不正です。")

        parts_by_coordinate: dict[str, dict[str, Any]] = {}
        rows: list[int] = []
        columns: list[int] = []
        for raw_part in raw_parts:
            cell = raw_part.get("cell") if isinstance(raw_part, dict) else None
            cell_id = cell.get("id") if isinstance(cell, dict) else None
            coordinate = cls._coordinate(cell_id) if isinstance(cell_id, str) else None
            match = _CELL_COORDINATE.fullmatch(coordinate or "")
            if not isinstance(raw_part, dict) or match is None or coordinate is None:
                raise cls._merge_invalid("merge partの元セル座標を確定できません。")
            if coordinate in parts_by_coordinate:
                raise cls._merge_invalid("merge partに同じ元セル座標が重複しています。")
            parts_by_coordinate[coordinate] = raw_part
            columns.append(column_index_from_string(match.group(1)))
            rows.append(int(match.group(2)))

        if source_ranges:
            expanded: dict[str, dict[str, Any]] = {}
            for coordinate, part in parts_by_coordinate.items():
                min_col, first_row, max_col, last_row = cls._range_bounds(
                    source_ranges[coordinate]
                )
                for row in range(first_row, last_row + 1):
                    for column in range(min_col, max_col + 1):
                        covered = f"{get_column_letter(column)}{row}"
                        if covered in expanded:
                            raise cls._merge_invalid("merge partの元Excel範囲が重複しています。")
                        expanded[covered] = part
                        rows.append(row)
                        columns.append(column)
            parts_by_coordinate = expanded

        min_column, max_column = min(columns), max(columns)
        min_row, max_row = min(rows), max(rows)
        expected_coordinates = {
            f"{get_column_letter(column)}{row}"
            for row in range(min_row, max_row + 1)
            for column in range(min_column, max_column + 1)
        }
        if set(parts_by_coordinate) != expected_coordinates:
            missing = ", ".join(sorted(expected_coordinates - set(parts_by_coordinate))[:12])
            raise cls._merge_invalid(
                "merge partsが欠落セルのない連続したExcel矩形rangeを構成していません。"
                f" 結合 {merge_id} の未選択セル: {missing}。テンプレートで結合を見直してください。"
            )

        anchor_cell = f"{get_column_letter(min_column)}{min_row}"
        end_cell = f"{get_column_letter(max_column)}{max_row}"
        excel_range = f"{anchor_cell}:{end_cell}"
        if require_snapshot_metadata and (
            merge.get("excelRange") != excel_range or merge.get("anchorCell") != anchor_cell
        ):
            raise cls._snapshot_error("固定されたmerge rangeまたはanchorがpartsと一致しません。")
        return _MergePlan(
            merge_id=merge_id,
            parent_cell_id=parent_cell_id,
            excel_range=excel_range,
            anchor_cell=anchor_cell,
            parts_by_coordinate=parts_by_coordinate,
            source_ranges=tuple(source_ranges.values()) if source_ranges else (),
        )

    @classmethod
    def _source_ranges(cls, worksheet: Worksheet, merge: dict[str, Any]) -> dict[str, str]:
        """Resolve imported anchors only; never infer coverage from image geometry.

        Fully enumerated legacy merges keep their existing validation/output policy.
        """
        ranges: dict[str, str] = {}
        has_merged_source = False
        parts = merge.get("parts")
        if not isinstance(parts, list):
            return {}
        for part in parts:
            cell = part.get("cell") if isinstance(part, dict) else None
            cell_id = cell.get("id") if isinstance(cell, dict) else None
            coordinate = cls._coordinate(cell_id) if isinstance(cell_id, str) else None
            if coordinate is None or not isinstance(cell_id, str):
                return {}
            if cell_id != f"xlsx_{coordinate}" and not re.fullmatch(
                rf"xlsx_{coordinate}_[0-9a-f]{{20}}", cell_id,
            ):
                # Virtual subcells do not own the entire source Excel cell/range.
                # TemplateVersionService's UUID suffix changes identity, not coverage.
                return {}
            source = next((r for r in worksheet.merged_cells.ranges if coordinate in r), None)
            if source is not None:
                if coordinate != f"{get_column_letter(source.min_col)}{source.min_row}":
                    return {}
                has_merged_source = True
            ranges[coordinate] = str(source) if source else f"{coordinate}:{coordinate}"
        return ranges if has_merged_source else {}

    @classmethod
    def _apply_merges(cls, worksheet: Worksheet, plans: list[_MergePlan]) -> None:
        for plan in plans:
            exact_match = False
            replace_ranges: list[str] = []
            for existing in worksheet.merged_cells.ranges:
                existing_range = str(existing)
                if existing_range == plan.excel_range:
                    exact_match = True
                    break
                if cls._ranges_overlap(existing.bounds, cls._range_bounds(plan.excel_range)):
                    if existing_range in plan.source_ranges:
                        replace_ranges.append(existing_range)
                        continue
                    raise cls._merge_conflict(
                        f"既存merge {existing_range} と要求merge "
                        f"{plan.excel_range} が重複しています。"
                    )
            if exact_match:
                continue
            cls._ensure_merge_contents_safe(worksheet, plan)
            perimeter: dict[str, dict[str, Any]] = {}
            if replace_ranges:
                left, top, right, bottom = cls._range_bounds(plan.excel_range)
                for coordinate in plan.parts_by_coordinate:
                    cell = worksheet[coordinate]
                    edges = {}
                    for side, applies in (
                        ("left", cell.column == left), ("right", cell.column == right),
                        ("top", cell.row == top), ("bottom", cell.row == bottom),
                    ):
                        if applies:
                            edges[side] = copy.copy(getattr(cell.border, side))
                    if edges:
                        perimeter[coordinate] = edges
                anchor_border = copy.copy(worksheet[plan.anchor_cell].border)
                anchor_border.right = copy.copy(worksheet.cell(top, right).border.right)
                anchor_border.bottom = copy.copy(worksheet.cell(bottom, left).border.bottom)
            for existing_range in replace_ranges:
                worksheet.unmerge_cells(existing_range)
            if replace_ranges:
                worksheet[plan.anchor_cell].border = anchor_border
            worksheet.merge_cells(plan.excel_range)
            for coordinate, edges in perimeter.items():
                border = copy.copy(worksheet[coordinate].border)
                for side, value in edges.items():
                    setattr(border, side, value)
                worksheet[coordinate].border = border

    @staticmethod
    def _range_bounds(excel_range: str) -> tuple[int, int, int, int]:
        start, end = excel_range.split(":", 1)
        start_match = _CELL_COORDINATE.fullmatch(start)
        end_match = _CELL_COORDINATE.fullmatch(end)
        assert start_match is not None
        assert end_match is not None
        return (
            column_index_from_string(start_match.group(1)),
            int(start_match.group(2)),
            column_index_from_string(end_match.group(1)),
            int(end_match.group(2)),
        )

    @staticmethod
    def _ranges_overlap(
        left: tuple[int, int, int, int],
        right: tuple[int, int, int, int],
    ) -> bool:
        left_min_col, left_min_row, left_max_col, left_max_row = left
        right_min_col, right_min_row, right_max_col, right_max_row = right
        return not (
            left_max_col < right_min_col
            or right_max_col < left_min_col
            or left_max_row < right_min_row
            or right_max_row < left_min_row
        )

    @classmethod
    def _ensure_merge_contents_safe(cls, worksheet: Worksheet, plan: _MergePlan) -> None:
        for coordinate, part in plan.parts_by_coordinate.items():
            if coordinate == plan.anchor_cell:
                continue
            cell = worksheet[coordinate]
            if cell.value is None:
                continue
            if cell.data_type == "f":
                raise cls._merge_conflict(
                    f"要求merge {plan.excel_range} の内部に数式 {coordinate} があります。"
                )
            part_type = part.get("partType")
            if part_type == "ignored":
                continue
            if part_type == "literal":
                part_cell = part.get("cell")
                expected = part.get("literal") or (
                    part_cell.get("text") if isinstance(part_cell, dict) else None
                )
                if expected is not None and str(cell.value) == str(expected):
                    continue
                # Import/editor literals omit boundary whitespace. Compare only;
                # do not rewrite source values or normalize meaningful inner text.
                if (
                    isinstance(cell.value, str)
                    and isinstance(expected, str)
                    and expected.strip()
                    and cell.value.strip() == expected.strip()
                ):
                    continue
            raise cls._merge_conflict(
                f"要求merge {plan.excel_range} の内部値 {coordinate} を安全に削除できません。"
            )

    @classmethod
    def _retain_and_rename_result_sheet(
        cls,
        workbook: Workbook,
        worksheet: Worksheet,
        *,
        output_name: str,
    ) -> None:
        source_sheet_name = worksheet.title
        removed_worksheets = [item for item in workbook.worksheets if item is not worksheet]
        removed_sheet_names = {item.title for item in removed_worksheets}
        formula_texts = list(cls._worksheet_formula_texts(worksheet))

        for formula in formula_texts:
            references = cls._referenced_sheet_names(formula, workbook.sheetnames)
            unsafe = references & removed_sheet_names
            if unsafe:
                raise cls._sheet_reference_conflict(
                    "結果sheetから削除対象sheetへのformula参照があります: "
                    + ", ".join(sorted(unsafe))
                )

        cls._prepare_defined_names_for_sheet_removal(
            workbook,
            worksheet,
            removed_worksheets=removed_worksheets,
            result_formula_texts=formula_texts,
        )
        for removed in removed_worksheets:
            workbook.remove(removed)

        cls._rewrite_worksheet_sheet_references(
            worksheet,
            old_name=source_sheet_name,
            new_name=output_name,
        )
        cls._rewrite_defined_name_sheet_references(
            workbook,
            worksheet,
            old_name=source_sheet_name,
            new_name=output_name,
        )
        worksheet.title = output_name

    @classmethod
    def _prepare_defined_names_for_sheet_removal(
        cls,
        workbook: Workbook,
        worksheet: Worksheet,
        *,
        removed_worksheets: list[Worksheet],
        result_formula_texts: list[str],
    ) -> None:
        removed_names = {item.title for item in removed_worksheets}
        all_sheet_names = set(workbook.sheetnames)
        removed_indexes = {
            workbook.index(item)
            for item in removed_worksheets
        }
        for name, defined_name in list(workbook.defined_names.items()):
            local_sheet_id = defined_name.localSheetId
            if isinstance(local_sheet_id, int) and local_sheet_id in removed_indexes:
                del workbook.defined_names[name]
                continue
            text_value = defined_name.attr_text
            if not isinstance(text_value, str):
                continue
            references = cls._referenced_sheet_names(text_value, all_sheet_names)
            removed_references = references & removed_names
            if not removed_references:
                continue
            if references <= removed_names:
                if any(
                    cls._formula_uses_defined_name(formula, name)
                    for formula in result_formula_texts
                ):
                    raise cls._sheet_reference_conflict(
                        f"結果sheetが削除対象sheet専用のdefined name {name} を使用しています。"
                    )
                del workbook.defined_names[name]
                continue
            raise cls._sheet_reference_conflict(
                f"defined name {name} が結果sheetと削除対象sheetを同時に参照しています。"
            )

        # Sheet-local names owned by removed worksheets disappear with those sheets.
        # A result-sheet local name must not depend on a sheet that will be removed.
        for name, defined_name in worksheet.defined_names.items():
            text_value = defined_name.attr_text
            if not isinstance(text_value, str):
                continue
            references = cls._referenced_sheet_names(text_value, all_sheet_names)
            unsafe = references & removed_names
            if unsafe:
                raise cls._sheet_reference_conflict(
                    f"結果sheetのlocal defined name {name} が削除対象sheetを参照しています。"
                )

    @classmethod
    def _worksheet_formula_texts(cls, worksheet: Worksheet) -> list[str]:
        formulas: list[str] = []
        for row in worksheet.iter_rows():
            for cell in row:
                if cell.data_type == "f" and isinstance(cell.value, str):
                    formulas.append(cell.value)
        for validation in worksheet.data_validations.dataValidation:
            for value in (validation.formula1, validation.formula2):
                if isinstance(value, str) and value:
                    formulas.append(value)
        for conditional_range in worksheet.conditional_formatting:
            for rule in worksheet.conditional_formatting[conditional_range]:
                raw_formulas = getattr(rule, "formula", None)
                if isinstance(raw_formulas, list):
                    formulas.extend(item for item in raw_formulas if isinstance(item, str))
        for chart in worksheet._charts:
            formulas.extend(value for _, value in cls._serialisable_formula_fields(chart))
        return formulas

    @classmethod
    def _rewrite_worksheet_sheet_references(
        cls,
        worksheet: Worksheet,
        *,
        old_name: str,
        new_name: str,
    ) -> None:
        if old_name == new_name:
            return
        for row in worksheet.iter_rows():
            for cell in row:
                if cell.data_type == "f" and isinstance(cell.value, str):
                    cell.value = cls._replace_sheet_reference(cell.value, old_name, new_name)
        for validation in worksheet.data_validations.dataValidation:
            for attribute in ("formula1", "formula2"):
                value = getattr(validation, attribute)
                if isinstance(value, str) and value:
                    setattr(
                        validation,
                        attribute,
                        cls._replace_sheet_reference(value, old_name, new_name),
                    )
        for conditional_range in worksheet.conditional_formatting:
            for rule in worksheet.conditional_formatting[conditional_range]:
                raw_formulas = getattr(rule, "formula", None)
                if isinstance(raw_formulas, list):
                    rule.formula = [
                        cls._replace_sheet_reference(item, old_name, new_name)
                        if isinstance(item, str)
                        else item
                        for item in raw_formulas
                    ]
        for chart in worksheet._charts:
            for owner, value in cls._serialisable_formula_fields(chart):
                owner.f = cls._replace_sheet_reference(value, old_name, new_name)

    @classmethod
    def _rewrite_defined_name_sheet_references(
        cls,
        workbook: Workbook,
        worksheet: Worksheet,
        *,
        old_name: str,
        new_name: str,
    ) -> None:
        if old_name == new_name:
            return
        for defined_name in workbook.defined_names.values():
            if isinstance(defined_name.attr_text, str):
                defined_name.attr_text = cls._replace_sheet_reference(
                    defined_name.attr_text,
                    old_name,
                    new_name,
                )
        for defined_name in worksheet.defined_names.values():
            if isinstance(defined_name.attr_text, str):
                defined_name.attr_text = cls._replace_sheet_reference(
                    defined_name.attr_text,
                    old_name,
                    new_name,
                )

    @classmethod
    def _serialisable_formula_fields(
        cls,
        root: object,
        seen: set[int] | None = None,
    ) -> list[tuple[Any, str]]:
        if seen is None:
            seen = set()
        if root is None or isinstance(root, str | bytes | int | float | bool):
            return []
        root_id = id(root)
        if root_id in seen:
            return []
        seen.add(root_id)
        results: list[tuple[Any, str]] = []
        if isinstance(root, list | tuple):
            for item in root:
                results.extend(cls._serialisable_formula_fields(item, seen))
            return results
        for attribute in getattr(root, "__elements__", ()):  # openpyxl serialisable tree
            value = getattr(root, attribute, None)
            if attribute == "f" and isinstance(value, str) and value:
                results.append((root, value))
            else:
                results.extend(cls._serialisable_formula_fields(value, seen))
        return results

    @classmethod
    def _referenced_sheet_names(
        cls,
        formula: str,
        sheet_names: list[str] | set[str],
    ) -> set[str]:
        return {
            name
            for name in sheet_names
            if cls._formula_references_sheet(formula, name)
        }

    @staticmethod
    def _formula_references_sheet(formula: str, sheet_name: str) -> bool:
        escaped_quoted = re.escape("'" + sheet_name.replace("'", "''") + "'")
        if re.search(rf"{escaped_quoted}!", formula, flags=re.IGNORECASE):
            return True
        escaped = re.escape(sheet_name)
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9_.']){escaped}(?=[:!])|(?<=:){escaped}(?=!)",
                formula,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _replace_sheet_reference(formula: str, old_name: str, new_name: str) -> str:
        quoted_old = "'" + old_name.replace("'", "''") + "'"
        quoted_new = "'" + new_name.replace("'", "''") + "'"
        replaced = re.sub(
            re.escape(quoted_old) + r"!",
            lambda _: f"{quoted_new}!",
            formula,
            flags=re.IGNORECASE,
        )
        return re.sub(
            rf"(?<![A-Za-z0-9_.']){re.escape(old_name)}!",
            lambda _: f"{quoted_new}!",
            replaced,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _formula_uses_defined_name(formula: str, name: str) -> bool:
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9_.]){re.escape(name)}(?![A-Za-z0-9_.])",
                formula,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _latest_source_artifact(session: Session, source_job_id: str) -> JobArtifact | None:
        return session.scalar(
            select(JobArtifact)
            .where(
                JobArtifact.job_id == source_job_id,
                JobArtifact.artifact_name == "sourceWorkbook",
            )
            .order_by(JobArtifact.attempt_number.desc())
            .limit(1)
        )

    @classmethod
    def _output_snapshot(cls, job: ProcessingJob) -> dict[str, Any]:
        raw_snapshot = job.config_snapshot_json.get("spreadsheetOutput")
        if not isinstance(raw_snapshot, dict):
            raise AppError(
                status=409,
                code="SPREADSHEET_OUTPUT_SNAPSHOT_MISSING",
                title="Spreadsheet output snapshot is missing",
                detail=(
                    "OCR受付時のXLSX出力構造が保存されていないため、"
                    "現在のtemplateから推測して出力することはできません。"
                ),
            )
        source = raw_snapshot.get("sourceWorkbook")
        if (
            raw_snapshot.get("schemaVersion") not in (1, 2)
            or raw_snapshot.get("templateVersionId") != job.template_version_id
            or not isinstance(raw_snapshot.get("sourceSheetName"), str)
            or not isinstance(raw_snapshot.get("sourceFileName"), str)
            or not isinstance(raw_snapshot.get("cells"), list)
            or not isinstance(raw_snapshot.get("cellMerges"), list)
            or not isinstance(source, dict)
            or not isinstance(source.get("artifactId"), str)
            or not isinstance(source.get("sourceJobId"), str)
            or not isinstance(source.get("sha256"), str)
        ):
            raise cls._snapshot_error("固定されたXLSX出力構造が不正です。")
        return raw_snapshot

    @staticmethod
    def _merged_anchor(worksheet: Worksheet, coordinate: str) -> str:
        for merged_range in worksheet.merged_cells.ranges:
            if coordinate in merged_range:
                return str(
                    worksheet.cell(
                        merged_range.min_row,
                        merged_range.min_col,
                    ).coordinate
                )
        return coordinate

    @staticmethod
    def _source_error(detail: str) -> AppError:
        return AppError(
            status=409,
            code="SPREADSHEET_TEMPLATE_SOURCE_MISSING",
            title="Spreadsheet template source is missing",
            detail=detail,
        )

    @staticmethod
    def _snapshot_error(detail: str) -> AppError:
        return AppError(
            status=409,
            code="SPREADSHEET_OUTPUT_SNAPSHOT_INVALID",
            title="Spreadsheet output snapshot is invalid",
            detail=detail,
        )

    @staticmethod
    def _merge_invalid(detail: str) -> AppError:
        return AppError(
            status=409,
            code="SPREADSHEET_OUTPUT_MERGE_INVALID",
            title="Spreadsheet output merge is invalid",
            detail=detail,
        )

    @staticmethod
    def _merge_conflict(detail: str) -> AppError:
        return AppError(
            status=409,
            code="SPREADSHEET_OUTPUT_MERGE_CONFLICT",
            title="Spreadsheet output merge conflicts with source workbook",
            detail=detail,
        )

    @staticmethod
    def _sheet_reference_conflict(detail: str) -> AppError:
        return AppError(
            status=409,
            code="SPREADSHEET_OUTPUT_SHEET_REFERENCE_CONFLICT",
            title="Spreadsheet output sheet reference conflicts",
            detail=detail,
        )
