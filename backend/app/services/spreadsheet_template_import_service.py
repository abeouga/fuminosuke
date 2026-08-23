from __future__ import annotations

import hashlib
import html
import io
import json
import math
from datetime import UTC, datetime
from typing import Any

import cv2
import numpy as np
import yaml
from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries

from app.core.errors import AppError
from app.db.models import ProcessingJob, TemplateVersion
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode
from app.domain.template_config import build_blank_template_config
from app.domain.template_fields import build_spreadsheet_field_catalog
from app.image_processing.artifacts import ArtifactWriter, encode_png
from app.layout_analysis.models import LayoutCell, LayoutDefinition, LayoutLabelPoolEntry
from app.repositories.job_repository import JobRepository
from app.repositories.template_repository import TemplateRepository
from app.storage.local_storage import LocalStorageService


class SpreadsheetTemplateImportService:
    CANONICAL_PORTRAIT = (1240, 1754)
    MAX_SOURCE_CELLS = 20_000
    MAX_LAYOUT_CELLS = 1_000

    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage
        self._artifacts = ArtifactWriter(database, storage)

    def import_xlsx(
        self,
        *,
        name: str,
        source_name: str,
        content: bytes,
        idempotency_key: str | None,
    ) -> ProcessingJob:
        cleaned_name = name.strip()
        if not cleaned_name or len(cleaned_name) > 200:
            raise AppError(
                status=422,
                code="VALIDATION_FAILED",
                title="Invalid template name",
                detail="テンプレート名は1〜200文字で指定してください。",
            )
        if not source_name.lower().endswith(".xlsx"):
            raise AppError(
                status=422,
                code="SPREADSHEET_FORMAT_UNSUPPORTED",
                title="Spreadsheet format is not supported",
                detail="XLSX形式のファイルを選択してください。",
            )

        request_hash = hashlib.sha256(cleaned_name.encode("utf-8") + b"\0" + content).hexdigest()
        with self._database.session() as session:
            jobs = JobRepository(session)
            if idempotency_key:
                existing = jobs.find_by_idempotency_key(idempotency_key)
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise AppError(
                            status=409,
                            code="IDEMPOTENCY_KEY_REUSED",
                            title="Idempotency key was reused",
                            detail="同じ送信識別子で異なるファイルは取り込めません。",
                        )
                    session.expunge(existing)
                    return existing

        layout, print_cells, sheet_name, image = self._parse_workbook(content)
        png = encode_png(image)
        image_hash = hashlib.sha256(png).hexdigest()
        inferred_labels = list(
            dict.fromkeys(
                cell.text.strip()
                for cell in layout.cells
                if cell.role in {"label", "parent_label"} and cell.text.strip()
            )
        )[:200]
        field_catalog = build_spreadsheet_field_catalog(inferred_labels)
        layout = layout.model_copy(
            update={
                "label_pool": [
                    LayoutLabelPoolEntry.model_validate(item) for item in field_catalog
                ]
            },
            deep=True,
        )
        config_snapshot = {
            "name": cleaned_name,
            "mode": TemplateMode.SPREADSHEET_TEMPLATE.value,
            "fieldCatalog": field_catalog,
            "sourceFileName": source_name,
            "sourceSheetName": sheet_name,
        }

        with self._database.session() as session:
            job = JobRepository(session).create(
                job_type=JobType.PREPARE_TEMPLATE,
                request_hash=request_hash,
                config_snapshot=config_snapshot,
                status=JobStatus.PROCESSING,
                mode=TemplateMode.SPREADSHEET_TEMPLATE,
                idempotency_key=idempotency_key,
                image_sha256=image_hash,
            )
            session.expunge(job)

        input_relative = self._storage.write_bytes(
            f"uploads/{job.id}/spreadsheet-template.png",
            png,
        )
        workbook_relative = self._storage.write_bytes(
            f"uploads/{job.id}/{self._safe_source_name(source_name)}",
            content,
        )
        with self._database.session() as session:
            jobs = JobRepository(session)
            jobs.add_input(
                job_id=job.id,
                input_index=0,
                media_type="image/png",
                format="png",
                width=layout.canonical_width,
                height=layout.canonical_height,
                file_bytes=len(png),
                sha256=image_hash,
                relative_path=input_relative,
                content_bytes=png,
            )

        clean_artifact = self._artifacts.image(
            job_id=job.id,
            attempt_number=1,
            name="cleanTemplate",
            file_name="spreadsheet-template.png",
            image=image,
        )
        self._artifacts.bytes(
            job_id=job.id,
            attempt_number=1,
            name="sourceWorkbook",
            file_name=self._safe_source_name(source_name),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=content,
        )

        quality = {
            "sourceType": "xlsx",
            "sourceFileName": source_name,
            "sourceSheetName": sheet_name,
            "spreadsheetPrintCells": print_cells,
            "spreadsheetSourcePath": workbook_relative,
            "autoClassifiedCellCount": len(layout.cells),
            "layoutFinalized": False,
            "layoutVerified": False,
            "structureReviewRequired": True,
        }
        with self._database.session() as session:
            templates = TemplateRepository(session)
            template = templates.create_template(
                name=cleaned_name,
                document_type="spreadsheet",
            )
            self._storage.write_text(
                f"templates/{template.id}/spreadsheet-import.json",
                json.dumps(config_snapshot, ensure_ascii=False, indent=2),
            )
            version_number = 1
            version_config = build_blank_template_config(
                template_id=template.id,
                template_version=version_number,
                name=cleaned_name,
                canonical_width=layout.canonical_width,
                canonical_height=layout.canonical_height,
            )
            config_relative = self._storage.write_text(
                f"templates/{template.id}/versions/{version_number}/template.yaml",
                yaml.safe_dump(
                    version_config.model_dump(mode="json", by_alias=True),
                    allow_unicode=True,
                    sort_keys=False,
                ),
            )
            version = templates.create_version(
                template_id=template.id,
                mode=TemplateMode.SPREADSHEET_TEMPLATE,
                canonical_width=layout.canonical_width,
                canonical_height=layout.canonical_height,
                source_job_id=job.id,
                clean_template_artifact_id=clean_artifact.id,
                mask_artifact_id=None,
                config_relative_path=config_relative,
                quality=quality,
            )
            if version.version_number != version_number:
                raise RuntimeError("Spreadsheet template version number changed during persistence")
            version.layout_json = layout.model_dump(mode="json", by_alias=True)
            stored_job = session.get(ProcessingJob, job.id)
            if stored_job is None:
                raise RuntimeError("Spreadsheet template job disappeared during import")
            stored_job.template_version_id = version.id
            stored_job.status = JobStatus.REVIEW_REQUIRED.value
            stored_job.progress_stage = "SPREADSHEET_TEMPLATE_READY"
            stored_job.progress_percent = 100
            stored_job.template_snapshot_bytes = png
            stored_job.completed_at = datetime.now(UTC)
            session.commit()
            session.refresh(stored_job)
            JobRepository(session).add_event(
                job_id=stored_job.id,
                level="INFO",
                event_code="SPREADSHEET_TEMPLATE_READY",
                stage="SPREADSHEET_TEMPLATE_READY",
                metadata={
                    "sourceSheetName": sheet_name,
                    "cellCount": len(layout.cells),
                },
            )
            session.expunge(stored_job)
            return stored_job

    @staticmethod
    def printable_html(
        version: TemplateVersion,
        *,
        template_name: str,
        auto_print: bool,
    ) -> str:
        raw_cells = version.quality_json.get("spreadsheetPrintCells")
        if not isinstance(raw_cells, list):
            if not version.source_job_id:
                raise AppError(
                    status=409,
                    code="TEMPLATE_PRINT_SOURCE_NOT_AVAILABLE",
                    title="Template print source is not available",
                    detail="このテンプレートには印刷用の基準画像がありません。",
                )
            image_url = (
                f"/api/v1/jobs/{html.escape(version.source_job_id)}/artifacts/cleanTemplate"
            )
            body = f'<img class="reference-image" src="{image_url}" alt="印刷用テンプレート">'
        else:
            cells: list[str] = []
            width = max(1, version.canonical_width)
            height = max(1, version.canonical_height)
            for raw in raw_cells:
                if not isinstance(raw, dict):
                    continue
                x = max(0, int(raw.get("x", 0)))
                y = max(0, int(raw.get("y", 0)))
                cell_width = max(1, int(raw.get("width", 1)))
                cell_height = max(1, int(raw.get("height", 1)))
                borders = raw.get("borders") if isinstance(raw.get("borders"), dict) else {}
                border_css = ";".join(
                    f"border-{side}:{'0.22mm solid #111' if borders.get(side) else 'none'}"
                    for side in ("top", "right", "bottom", "left")
                )
                align = raw.get("align") if raw.get("align") in {"left", "center", "right"} else "left"
                vertical = raw.get("vertical") if raw.get("vertical") in {"top", "center", "bottom"} else "center"
                justify = {"left": "flex-start", "center": "center", "right": "flex-end"}[align]
                items = {"top": "flex-start", "center": "center", "bottom": "flex-end"}[vertical]
                fill = raw.get("fill") if isinstance(raw.get("fill"), str) else "#ffffff"
                font_size = max(6.0, min(18.0, float(raw.get("fontSize", 10) or 10)))
                font_weight = "700" if raw.get("bold") is True else "400"
                text = html.escape(str(raw.get("text", "")))
                style = (
                    f"left:{x / width * 100:.6f}%;top:{y / height * 100:.6f}%;"
                    f"width:{cell_width / width * 100:.6f}%;height:{cell_height / height * 100:.6f}%;"
                    f"{border_css};background:{fill};justify-content:{justify};align-items:{items};"
                    f"font-size:{font_size:.2f}pt;font-weight:{font_weight};text-align:{align};"
                )
                cells.append(f'<div class="cell" style="{style}"><span>{text}</span></div>')
            body = "".join(cells)

        auto_script = (
            "<script>addEventListener('load',()=>setTimeout(()=>window.print(),150));</script>"
            if auto_print
            else ""
        )
        title = html.escape(template_name)
        return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>{title} - 印刷</title>
<style>
@page{{size:A4;margin:0}}*{{box-sizing:border-box}}html,body{{margin:0;background:#dfe3e6;color:#17252c;font-family:'Yu Gothic','Meiryo',sans-serif}}
.toolbar{{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;padding:12px 20px;background:#1c2a31;color:#fff}}
.toolbar strong{{font-size:14px}}.toolbar button{{border:1px solid #fff;background:#fff;color:#173a37;padding:9px 22px;font-weight:700;cursor:pointer}}
.sheet{{position:relative;width:210mm;height:297mm;margin:18px auto;background:#fff;overflow:hidden;box-shadow:0 12px 35px #1c2a3133}}
.cell{{position:absolute;display:flex;overflow:hidden;padding:0.8mm;line-height:1.15;white-space:pre-wrap;overflow-wrap:anywhere}}
.reference-image{{display:block;width:100%;height:100%;object-fit:fill}}
@media print{{html,body{{background:#fff}}.toolbar{{display:none}}.sheet{{margin:0;box-shadow:none;page-break-after:avoid}}}}
</style></head><body><div class="toolbar"><strong>{title}</strong><button type="button" onclick="window.print()">印刷する</button></div><main class="sheet">{body}</main>{auto_script}</body></html>"""

    def _parse_workbook(
        self,
        content: bytes,
    ) -> tuple[LayoutDefinition, list[dict[str, Any]], str, np.ndarray[Any, Any]]:
        try:
            workbook = load_workbook(io.BytesIO(content), data_only=False, read_only=False)
        except Exception as exc:
            raise AppError(
                status=422,
                code="SPREADSHEET_PARSE_FAILED",
                title="Spreadsheet could not be parsed",
                detail="XLSXファイルを解析できませんでした。破損または保護されていないか確認してください。",
            ) from exc
        worksheets = [sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"]
        if not worksheets:
            raise AppError(
                status=422,
                code="SPREADSHEET_HAS_NO_VISIBLE_SHEET",
                title="Spreadsheet has no visible sheet",
                detail="表示状態のシートがありません。",
            )
        worksheet = workbook.active if workbook.active in worksheets else worksheets[0]
        min_col, min_row, max_col, max_row = range_boundaries(worksheet.calculate_dimension())
        source_cell_count = (max_col - min_col + 1) * (max_row - min_row + 1)
        if source_cell_count > self.MAX_SOURCE_CELLS:
            raise AppError(
                status=422,
                code="SPREADSHEET_RANGE_TOO_LARGE",
                title="Spreadsheet range is too large",
                detail="使用範囲が大きすぎます。対象シートの不要な行・列を削除してください。",
            )

        column_widths: dict[int, float] = {}
        for column in range(min_col, max_col + 1):
            dimension = worksheet.column_dimensions[get_column_letter(column)]
            width = dimension.width or worksheet.sheet_format.defaultColWidth or 8.43
            column_widths[column] = max(12.0, float(width) * 7.0 + 5.0)
        row_heights: dict[int, float] = {}
        for row in range(min_row, max_row + 1):
            dimension = worksheet.row_dimensions[row]
            height = dimension.height or worksheet.sheet_format.defaultRowHeight or 15.0
            row_heights[row] = max(14.0, float(height) * 96.0 / 72.0)

        portrait = worksheet.page_setup.orientation != "landscape"
        canonical_width, canonical_height = self.CANONICAL_PORTRAIT
        if not portrait:
            canonical_width, canonical_height = canonical_height, canonical_width
        margin = 44
        raw_width = sum(column_widths.values())
        raw_height = sum(row_heights.values())
        scale = min(
            (canonical_width - margin * 2) / max(raw_width, 1),
            (canonical_height - margin * 2) / max(raw_height, 1),
        )
        x_positions: dict[int, int] = {}
        y_positions: dict[int, int] = {}
        cursor = margin
        for column in range(min_col, max_col + 1):
            x_positions[column] = cursor
            cursor += max(1, int(round(column_widths[column] * scale)))
        cursor = margin
        for row in range(min_row, max_row + 1):
            y_positions[row] = cursor
            cursor += max(1, int(round(row_heights[row] * scale)))

        merged_top_left: dict[tuple[int, int], tuple[int, int]] = {}
        merged_covered: set[tuple[int, int]] = set()
        for merged in worksheet.merged_cells.ranges:
            top_left = (merged.min_row, merged.min_col)
            merged_top_left[top_left] = (merged.max_row, merged.max_col)
            for row in range(merged.min_row, merged.max_row + 1):
                for column in range(merged.min_col, merged.max_col + 1):
                    if (row, column) != top_left:
                        merged_covered.add((row, column))

        record_keys = self._sequential_key_cells(worksheet, min_row, max_row, min_col, max_col)
        layout_cells: list[LayoutCell] = []
        print_cells: list[dict[str, Any]] = []
        canvas = np.full((canonical_height, canonical_width, 3), 255, dtype=np.uint8)
        for row in range(min_row, max_row + 1):
            if worksheet.row_dimensions[row].hidden:
                continue
            for column in range(min_col, max_col + 1):
                if worksheet.column_dimensions[get_column_letter(column)].hidden:
                    continue
                if (row, column) in merged_covered:
                    continue
                cell = worksheet.cell(row=row, column=column)
                merged_end = merged_top_left.get((row, column), (row, column))
                end_row, end_col = merged_end
                text = self._display_text(cell)
                bordered = self._has_border(cell)
                has_validation = self._has_data_validation(worksheet, cell.coordinate)
                if not text and not bordered and not has_validation:
                    continue
                x = x_positions[column]
                y = y_positions[row]
                width = sum(
                    max(1, int(round(column_widths[item] * scale)))
                    for item in range(column, end_col + 1)
                )
                height = sum(
                    max(1, int(round(row_heights[item] * scale)))
                    for item in range(row, end_row + 1)
                )
                role = self._infer_role(
                    cell,
                    text=text,
                    merged=(end_row, end_col) != (row, column),
                    has_validation=has_validation,
                    record_key=(row, column) in record_keys,
                )
                cell_id = f"xlsx_{get_column_letter(column)}{row}"
                layout_cells.append(
                    LayoutCell(
                        id=cell_id,
                        role=role,
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                        confidence=0.9 if role == "record_key_cell" else 0.72,
                        text=text[:300],
                        printed_label_text=text[:300] if role in {"label", "parent_label"} else "",
                        text_confidence=1.0 if text else None,
                        source="detected",
                    )
                )
                borders = self._border_map(cell)
                fill = self._fill_color(cell)
                print_cells.append(
                    {
                        "id": cell_id,
                        "x": x,
                        "y": y,
                        "width": width,
                        "height": height,
                        "text": text,
                        "borders": borders,
                        "fill": fill,
                        "align": cell.alignment.horizontal or "left",
                        "vertical": cell.alignment.vertical or "center",
                        "fontSize": cell.font.sz or 10,
                        "bold": bool(cell.font.bold),
                    }
                )
                color = self._hex_to_bgr(fill)
                cv2.rectangle(canvas, (x, y), (x + width, y + height), color, thickness=-1)
                for side, enabled in borders.items():
                    if not enabled:
                        continue
                    start, end = {
                        "top": ((x, y), (x + width, y)),
                        "right": ((x + width, y), (x + width, y + height)),
                        "bottom": ((x, y + height), (x + width, y + height)),
                        "left": ((x, y), (x, y + height)),
                    }[side]
                    cv2.line(canvas, start, end, (30, 30, 30), 1)
                if text.isascii() and text:
                    cv2.putText(
                        canvas,
                        text[:40],
                        (x + 4, min(y + height - 4, y + 16)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.38,
                        (20, 20, 20),
                        1,
                        cv2.LINE_AA,
                    )

        if not layout_cells:
            raise AppError(
                status=422,
                code="SPREADSHEET_HAS_NO_TEMPLATE_CELLS",
                title="Spreadsheet has no template cells",
                detail="固定文字、罫線または入力規則を持つセルが見つかりません。",
            )
        if len(layout_cells) > self.MAX_LAYOUT_CELLS:
            raise AppError(
                status=422,
                code="SPREADSHEET_HAS_TOO_MANY_TEMPLATE_CELLS",
                title="Spreadsheet has too many template cells",
                detail="テンプレート対象セルが1,000件を超えています。シートまたは印刷範囲を分割してください。",
            )
        layout = LayoutDefinition(
            canonical_width=canonical_width,
            canonical_height=canonical_height,
            detector="xlsx-structure",
            ocr_available=False,
            cells=layout_cells,
            relations=[],
            label_pool=[],
            record_sections=[],
            record_groups=[],
            record_structure_verified=False,
            allow_cross_axis_labels=True,
            warnings=[
                "XLSXのセル構造から自動作成した案です。区分、意味、繰り返し構造を確認してください。"
            ],
        )
        return layout, print_cells, worksheet.title, canvas

    @staticmethod
    def _display_text(cell: Cell) -> str:
        value = cell.value
        if value is None:
            return ""
        if isinstance(value, str) and value.startswith("="):
            return ""
        return str(value).strip()

    @staticmethod
    def _has_border(cell: Cell) -> bool:
        return any(
            side.style is not None
            for side in (cell.border.top, cell.border.right, cell.border.bottom, cell.border.left)
        )

    @staticmethod
    def _border_map(cell: Cell) -> dict[str, bool]:
        return {
            "top": cell.border.top.style is not None,
            "right": cell.border.right.style is not None,
            "bottom": cell.border.bottom.style is not None,
            "left": cell.border.left.style is not None,
        }

    @staticmethod
    def _has_data_validation(worksheet: Any, coordinate: str) -> bool:
        validations = worksheet.data_validations
        if validations is None:
            return False
        return any(coordinate in validation.sqref for validation in validations.dataValidation)

    @staticmethod
    def _infer_role(
        cell: Cell,
        *,
        text: str,
        merged: bool,
        has_validation: bool,
        record_key: bool,
    ) -> str:
        if record_key:
            return "record_key_cell"
        if has_validation and not text:
            return "mark"
        if text:
            return "parent_label" if merged or bool(cell.font.bold) else "label"
        return "input"

    @staticmethod
    def _sequential_key_cells(
        worksheet: Any,
        min_row: int,
        max_row: int,
        min_col: int,
        max_col: int,
    ) -> set[tuple[int, int]]:
        result: set[tuple[int, int]] = set()
        for column in range(min_col, max_col + 1):
            coordinates: list[tuple[int, int]] = []
            values: list[int] = []
            for row in range(min_row, max_row + 1):
                value = worksheet.cell(row=row, column=column).value
                if isinstance(value, int) and not isinstance(value, bool):
                    coordinates.append((row, column))
                    values.append(value)
            if len(values) >= 2 and values == list(range(1, len(values) + 1)):
                result.update(coordinates)
        for row in range(min_row, max_row + 1):
            coordinates = []
            values = []
            for column in range(min_col, max_col + 1):
                value = worksheet.cell(row=row, column=column).value
                if isinstance(value, int) and not isinstance(value, bool):
                    coordinates.append((row, column))
                    values.append(value)
            if len(values) >= 2 and values == list(range(1, len(values) + 1)):
                result.update(coordinates)
        return result

    @staticmethod
    def _fill_color(cell: Cell) -> str:
        color = cell.fill.fgColor
        if cell.fill.fill_type and color.type == "rgb" and isinstance(color.rgb, str):
            rgb = color.rgb[-6:]
            if len(rgb) == 6 and all(character in "0123456789abcdefABCDEF" for character in rgb):
                return f"#{rgb}"
        return "#ffffff"

    @staticmethod
    def _hex_to_bgr(value: str) -> tuple[int, int, int]:
        normalized = value.lstrip("#")
        if len(normalized) != 6:
            return (255, 255, 255)
        red, green, blue = (int(normalized[index : index + 2], 16) for index in (0, 2, 4))
        return blue, green, red

    @staticmethod
    def _safe_source_name(value: str) -> str:
        name = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        safe = "".join(character for character in name if character.isalnum() or character in "._-")
        return (safe or "source.xlsx")[:180]
