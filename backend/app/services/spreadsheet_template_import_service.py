from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pypdfium2 as pdfium
import yaml
from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.worksheet import Worksheet

from app.core.errors import AppError
from app.db.models import ProcessingJob, TemplateVersion
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode
from app.domain.template_config import build_blank_template_config
from app.domain.template_fields import build_spreadsheet_field_catalog
from app.image_processing.artifacts import ArtifactWriter, encode_png
from app.layout_analysis.models import (
    LayoutCell,
    LayoutCompositeSubfield,
    LayoutDefinition,
    LayoutLabelPoolEntry,
    LayoutMarkOption,
    LayoutMarkSet,
)
from app.layout_analysis.text_placement import spreadsheet_vertical_alignment
from app.repositories.job_repository import JobRepository
from app.repositories.template_repository import TemplateRepository
from app.services.spreadsheet_preview_geometry import LINUX_MODEL as LINUX_PREVIEW_MODEL
from app.services.spreadsheet_preview_geometry import MODEL, project_preview_geometry
from app.services.spreadsheet_source_bounds import (
    MAX_SOURCE_CELLS as DEFAULT_MAX_SOURCE_CELLS,
)
from app.services.spreadsheet_source_bounds import (
    effective_worksheet_bounds,
    empty_tail_is_inspectable,
)
from app.services.workbook_geometry import read_office_dimensions
from app.services.xlsx_checkboxes import preview_bytes
from app.storage.immutable_artifact import publish_config
from app.storage.local_storage import LocalStorageService


class SpreadsheetTemplateImportService:
    CANONICAL_PORTRAIT = (1240, 1754)
    MAX_SOURCE_CELLS = DEFAULT_MAX_SOURCE_CELLS
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
                            detail="取り込みが重なりました。画面を再読み込みしてから、もう一度ファイルを選んでください。",
                        )
                    session.expunge(existing)
                    return existing

        layout, print_cells, sheet_name, fallback_image = self._parse_workbook(content)
        preview_geometry: dict[str, Any] = {}
        pdf, image, pdf_page_count = self._render_workbook_preview(
            content,
            sheet_name=sheet_name,
            canonical_width=layout.canonical_width,
            canonical_height=layout.canonical_height,
            geometry=preview_geometry,
        )
        if image is None:
            image = fallback_image
        if preview_geometry:
            layout, print_cells = project_preview_geometry(
                content, layout, print_cells, preview_geometry,
            )
        layout, print_cells = self._align_layout_to_preview(layout, print_cells, image)
        png = encode_png(image)
        image_hash = hashlib.sha256(png).hexdigest()
        layout, field_catalog = self._with_field_catalog(layout)
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
        pdf_relative = self._storage.write_bytes(
            f"uploads/{job.id}/spreadsheet-template.pdf",
            pdf,
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
        self._artifacts.bytes(
            job_id=job.id,
            attempt_number=1,
            name="printablePdf",
            file_name="spreadsheet-template.pdf",
            media_type="application/pdf",
            content=pdf,
        )

        quality = {
            "sourceType": "xlsx",
            "sourceFileName": source_name,
            "sourceSheetName": sheet_name,
            "spreadsheetPrintCells": print_cells,
            "spreadsheetSourcePath": workbook_relative,
            "spreadsheetPrintablePdfPath": pdf_relative,
            "spreadsheetPrintablePageCount": pdf_page_count,
            "spreadsheetPreviewEngine": "libreoffice-pdf",
            "autoClassifiedCellCount": len(layout.cells),
            "layoutFinalized": False,
            "layoutVerified": False,
            "structureReviewRequired": True,
        }
        if preview_geometry:
            quality["spreadsheetPreviewGeometry"] = preview_geometry
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
            config_relative = publish_config(
                self._storage,
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
                layout=layout.model_dump(mode="json", by_alias=True),
            )
            if version.version_number != version_number:
                raise RuntimeError("Spreadsheet template version number changed during persistence")
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
                    "pdfPageCount": pdf_page_count,
                },
            )
            session.expunge(stored_job)
            return stored_job

    def restore_layout(
        self,
        content: bytes,
        *,
        preview_image: np.ndarray[Any, Any] | None = None,
        preview_geometry: dict[str, Any] | None = None,
    ) -> LayoutDefinition:
        """Rebuild virtual XLSX cells without writing to the source workbook."""

        layout, print_cells, _, _ = self._parse_workbook(content)
        if preview_geometry is not None:
            layout, print_cells = project_preview_geometry(
                content, layout, print_cells, preview_geometry,
            )
        if preview_image is not None:
            layout, _ = self._align_layout_to_preview(layout, print_cells, preview_image)
        restored, _ = self._with_field_catalog(layout)
        return restored

    @classmethod
    def _align_layout_to_preview(
        cls,
        layout: LayoutDefinition,
        print_cells: list[dict[str, Any]],
        preview_image: np.ndarray[Any, Any],
    ) -> tuple[LayoutDefinition, list[dict[str, Any]]]:
        """Map estimated XLSX coordinates onto the actual rendered table bounds."""

        rendered_bounds = cls._rendered_table_bounds(preview_image)
        source_bounds = cls._printed_border_bounds(print_cells)
        if rendered_bounds is None or source_bounds is None:
            return layout, print_cells

        source_left, source_top, source_right, source_bottom = source_bounds
        rendered_left, rendered_top, rendered_right, rendered_bottom = rendered_bounds
        source_width = source_right - source_left
        source_height = source_bottom - source_top
        if source_width <= 0 or source_height <= 0:
            return layout, print_cells

        scale_x = (rendered_right - rendered_left) / source_width
        scale_y = (rendered_bottom - rendered_top) / source_height
        if not (0.75 <= scale_x <= 1.25 and 0.75 <= scale_y <= 1.25):
            return layout, print_cells

        def transform_rect(
            x: int,
            y: int,
            width: int,
            height: int,
        ) -> tuple[int, int, int, int]:
            left = round(rendered_left + (x - source_left) * scale_x)
            top = round(rendered_top + (y - source_top) * scale_y)
            right = round(rendered_left + (x + width - source_left) * scale_x)
            bottom = round(rendered_top + (y + height - source_top) * scale_y)
            left = max(0, min(layout.canonical_width - 1, left))
            top = max(0, min(layout.canonical_height - 1, top))
            right = max(left + 1, min(layout.canonical_width, right))
            bottom = max(top + 1, min(layout.canonical_height, bottom))
            return left, top, right - left, bottom - top

        aligned_cells = []
        for cell in layout.cells:
            x, y, width, height = transform_rect(cell.x, cell.y, cell.width, cell.height)
            aligned_cells.append(
                cell.model_copy(
                    update={"x": x, "y": y, "width": width, "height": height},
                    deep=True,
                )
            )

        aligned_print_cells: list[dict[str, Any]] = []
        for cell in print_cells:
            aligned = dict(cell)
            x, y, width, height = transform_rect(
                int(cell.get("x", 0)),
                int(cell.get("y", 0)),
                max(1, int(cell.get("width", 1))),
                max(1, int(cell.get("height", 1))),
            )
            aligned.update({"x": x, "y": y, "width": width, "height": height})
            aligned_print_cells.append(aligned)

        warning = "LibreOfficeの印刷プレビュー上の罫線へXLSXセル座標を補正しました。"
        return (
            layout.model_copy(
                update={
                    "cells": aligned_cells,
                    "warnings": [*layout.warnings, warning],
                },
                deep=True,
            ),
            aligned_print_cells,
        )

    @staticmethod
    def _printed_border_bounds(
        print_cells: list[dict[str, Any]],
    ) -> tuple[int, int, int, int] | None:
        vertical_positions: list[int] = []
        horizontal_positions: list[int] = []
        for cell in print_cells:
            borders = cell.get("borders")
            if not isinstance(borders, dict):
                continue
            x = int(cell.get("x", 0))
            y = int(cell.get("y", 0))
            width = max(1, int(cell.get("width", 1)))
            height = max(1, int(cell.get("height", 1)))
            if borders.get("left"):
                vertical_positions.append(x)
            if borders.get("right"):
                vertical_positions.append(x + width)
            if borders.get("top"):
                horizontal_positions.append(y)
            if borders.get("bottom"):
                horizontal_positions.append(y + height)
        if len(vertical_positions) < 2 or len(horizontal_positions) < 2:
            return None
        return (
            min(vertical_positions),
            min(horizontal_positions),
            max(vertical_positions),
            max(horizontal_positions),
        )

    @staticmethod
    def _rendered_table_bounds(
        preview_image: np.ndarray[Any, Any],
    ) -> tuple[int, int, int, int] | None:
        if preview_image.ndim == 3:
            gray = cv2.cvtColor(preview_image, cv2.COLOR_BGR2GRAY)
        elif preview_image.ndim == 2:
            gray = preview_image
        else:
            return None
        height, width = gray.shape[:2]
        if width < 40 or height < 40:
            return None

        dark = np.asarray(gray < 180, dtype=np.uint8) * 255
        horizontal = cv2.morphologyEx(
            dark,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(24, width // 30), 1)),
        )
        vertical = cv2.morphologyEx(
            dark,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(24, height // 45))),
        )
        horizontal_scores = np.count_nonzero(horizontal, axis=1)
        vertical_scores = np.count_nonzero(vertical, axis=0)
        y_positions = np.flatnonzero(horizontal_scores >= max(20, round(width * 0.12)))
        x_positions = np.flatnonzero(vertical_scores >= max(20, round(height * 0.06)))
        if y_positions.size < 2 or x_positions.size < 2:
            return None
        return (
            int(x_positions[0]),
            int(y_positions[0]),
            int(x_positions[-1]),
            int(y_positions[-1]),
        )

    @staticmethod
    def _with_field_catalog(
        layout: LayoutDefinition,
    ) -> tuple[LayoutDefinition, list[dict[str, object]]]:
        inferred_labels = list(
            dict.fromkeys(
                cell.text.strip()
                for cell in layout.cells
                if cell.role in {"label", "parent_label"} and cell.text.strip()
            )
        )[:200]
        field_catalog = build_spreadsheet_field_catalog(inferred_labels)
        target_by_label = {
            re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(item["label"]))): str(
                item["targetPath"]
            )
            for item in field_catalog
        }
        choice_cells: list[LayoutCell] = []
        for cell in layout.cells:
            labels = SpreadsheetTemplateImportService._printed_choice_labels(cell.text)
            compact_label = re.sub(
                r"\s+", "", unicodedata.normalize("NFKC", cell.text)
            )
            target_path = target_by_label.get(compact_label)
            if not labels or target_path is None:
                choice_cells.append(cell)
                continue
            options: list[LayoutMarkOption] = []
            for index, label in enumerate(labels):
                left = round(cell.width * index / len(labels))
                right = round(cell.width * (index + 1) / len(labels))
                options.append(
                    LayoutMarkOption(
                        id=f"choice_{index + 1}",
                        label=label,
                        value=label,
                        x=left,
                        y=0,
                        width=max(1, right - left),
                        height=cell.height,
                    )
                )
            choice_cells.append(
                cell.model_copy(
                    update={
                        "role": "mark",
                        "mark_sets": [
                            LayoutMarkSet(
                                id="choice",
                                target_path=target_path,
                                recognition_mode="manual",
                                selection_mode="single",
                                options=options,
                            )
                        ],
                    },
                    deep=True,
                )
            )
        return (
            layout.model_copy(
                update={
                    "cells": choice_cells,
                    "label_pool": [
                        LayoutLabelPoolEntry.model_validate(item) for item in field_catalog
                    ]
                },
                deep=True,
            ),
            field_catalog,
        )

    @staticmethod
    def _printed_choice_labels(text: str) -> list[str]:
        compact = re.sub(r"[\s　・,、/]+", "", unicodedata.normalize("NFKC", text))
        for labels in (("都", "道", "府", "県"), ("市", "区", "町", "村")):
            if all(label in compact for label in labels) and len(compact) <= len(labels) + 4:
                return list(labels)
        return []

    @staticmethod
    def printable_html(
        version: TemplateVersion,
        *,
        template_name: str,
        auto_print: bool,
    ) -> str:
        paged = version.quality_json.get("printLayout")
        if isinstance(paged, dict):
            from app.services.print_layout_service import digest

            if paged.get("layoutSha256") != digest(version.layout_json):
                raise AppError(status=409, code="PRINT_LAYOUT_STALE",
                               title="Print layout is stale",
                               detail="枠を変更したため、印刷ページの設定を保存し直してください。")
            pages = "".join(
                f'<section><p>Page {p["page"]}: {html.escape(p["range"])}</p>'
                f'<img src="/api/v1/template-versions/{html.escape(version.id, quote=True)}'
                f'/print-layout/pages/{p["page"]}/image" alt="Page {p["page"]}"></section>'
                for p in paged["pages"]
            )
            paper = html.escape(paged["paperSize"])
            orientation = html.escape(paged["orientation"])
            return (
                '<!doctype html><html lang="ja"><meta charset="utf-8">'
                f'<title>{html.escape(template_name)} - ページ配置の参考描画</title>'
                '<style>body{font-family:sans-serif;background:#eee}section{max-width:800px;'
                'margin:20px auto}img{width:100%;display:block}header{padding:16px}'
                f'@page{{size:{paper} {orientation};margin:0}}'
                '@media print{header,section p{display:none}body,section{margin:0;'
                'background:white}section{break-after:page;max-width:none}'
                'section:last-child{break-after:auto}}</style>'
                '<header>セル構造からの参考描画です。Excel組版の完全再現・実OCRには未対応です。'
                '<button onclick="window.print()">参考ページを印刷</button></header>'
                f'{pages}</html>'
            )
        raw_cells = version.quality_json.get("spreadsheetPrintCells")
        has_pdf = (
            version.quality_json.get("spreadsheetPreviewEngine") == "libreoffice-pdf"
            and isinstance(version.quality_json.get("spreadsheetPrintablePdfPath"), str)
        )
        print_action = "window.print()"
        if has_pdf:
            pdf_url = f"/api/v1/template-versions/{html.escape(version.id, quote=True)}/print-pdf"
            body = (
                f'<iframe id="reference-pdf" class="reference-pdf" src="{pdf_url}" '
                'title="Excel印刷プレビュー"></iframe>'
            )
            print_action = (
                "const frame=document.getElementById('reference-pdf');"
                "if(frame&&frame.contentWindow){frame.contentWindow.focus();"
                "frame.contentWindow.print();}"
            )
        elif not isinstance(raw_cells, list):
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

        if auto_print and has_pdf:
            auto_script = (
                "<script>const frame=document.getElementById('reference-pdf');"
                "frame.addEventListener('load',()=>setTimeout(()=>{"
                "frame.contentWindow.focus();frame.contentWindow.print();},500));</script>"
            )
        elif auto_print:
            auto_script = "<script>addEventListener('load',()=>setTimeout(()=>window.print(),150));</script>"
        else:
            auto_script = ""
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
.reference-pdf{{display:block;width:100%;height:100%;border:0;background:#fff}}
@media print{{html,body{{background:#fff}}.toolbar{{display:none}}.sheet{{margin:0;box-shadow:none;page-break-after:avoid}}}}
</style></head><body><div class="toolbar"><strong>{title}</strong><button type="button" onclick="{print_action}">印刷する</button></div><main class="sheet">{body}</main>{auto_script}</body></html>"""

    def _parse_workbook(
        self,
        content: bytes,
    ) -> tuple[LayoutDefinition, list[dict[str, Any]], str, np.ndarray[Any, Any]]:
        try:
            workbook = load_workbook(io.BytesIO(preview_bytes(content)), data_only=False, read_only=False)
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
        min_col, min_row, max_col, max_row = self._worksheet_bounds(worksheet, content=content)
        source_cell_count = (max_col - min_col + 1) * (max_row - min_row + 1)
        if source_cell_count > self.MAX_SOURCE_CELLS:
            raise AppError(
                status=422,
                code="SPREADSHEET_RANGE_TOO_LARGE",
                title="Spreadsheet range is too large",
                detail="使用範囲が大きすぎます。対象シートの不要な行・列を削除してください。",
            )

        default_column_width = float(
            worksheet.sheet_format.defaultColWidth
            or worksheet.sheet_format.baseColWidth
            or 8.43
        )
        grouped_column_widths: dict[int, float] = {}
        grouped_column_hidden: dict[int, bool] = {}
        for dimension in worksheet.column_dimensions.values():
            start = max(min_col, int(dimension.min or min_col))
            end = min(max_col, int(dimension.max or start))
            resolved_width = float(dimension.width or default_column_width)
            for column in range(start, end + 1):
                grouped_column_widths[column] = resolved_width
                grouped_column_hidden[column] = bool(dimension.hidden)

        column_widths: dict[int, float] = {}
        for column in range(min_col, max_col + 1):
            if grouped_column_hidden.get(column, False):
                column_widths[column] = 0.0
                continue
            width = grouped_column_widths.get(column, default_column_width)
            column_widths[column] = max(12.0, float(width) * 7.0 + 5.0)
        row_heights: dict[int, float] = {}
        for row in range(min_row, max_row + 1):
            dimension = worksheet.row_dimensions[row]
            if dimension.hidden:
                row_heights[row] = 0.0
                continue
            height = dimension.height or worksheet.sheet_format.defaultRowHeight or 15.0
            row_heights[row] = max(14.0, float(height) * 96.0 / 72.0)

        portrait = worksheet.page_setup.orientation != "landscape"
        canonical_width, canonical_height = self.CANONICAL_PORTRAIT
        if not portrait:
            canonical_width, canonical_height = canonical_height, canonical_width
        page_width_inches = 8.27 if portrait else 11.69
        page_height_inches = 11.69 if portrait else 8.27
        dpi_x = canonical_width / page_width_inches
        dpi_y = canonical_height / page_height_inches
        left_margin = max(0, int(round(float(worksheet.page_margins.left or 0) * dpi_x)))
        right_margin = max(0, int(round(float(worksheet.page_margins.right or 0) * dpi_x)))
        top_margin = max(0, int(round(float(worksheet.page_margins.top or 0) * dpi_y)))
        bottom_margin = max(0, int(round(float(worksheet.page_margins.bottom or 0) * dpi_y)))
        raw_width = sum(column_widths.values())
        raw_height = sum(row_heights.values())
        available_width = max(1, canonical_width - left_margin - right_margin)
        available_height = max(1, canonical_height - top_margin - bottom_margin)
        scale = min(
            available_width / max(raw_width, 1),
            available_height / max(raw_height, 1),
        )
        rendered_width = raw_width * scale
        rendered_height = raw_height * scale
        x_origin = left_margin
        y_origin = top_margin
        if worksheet.print_options.horizontalCentered:
            x_origin += max(0, int(round((available_width - rendered_width) / 2)))
        if worksheet.print_options.verticalCentered:
            y_origin += max(0, int(round((available_height - rendered_height) / 2)))
        x_positions: dict[int, int] = {}
        y_positions: dict[int, int] = {}
        cursor = x_origin
        for column in range(min_col, max_col + 1):
            x_positions[column] = cursor
            cursor += max(0, int(round(column_widths[column] * scale)))
        cursor = y_origin
        for row in range(min_row, max_row + 1):
            y_positions[row] = cursor
            cursor += max(0, int(round(row_heights[row] * scale)))

        merged_top_left: dict[tuple[int, int], tuple[int, int]] = {}
        merged_covered: set[tuple[int, int]] = set()
        for merged in worksheet.merged_cells.ranges:
            if not (
                min_row <= merged.min_row <= max_row
                and min_col <= merged.min_col <= max_col
            ):
                continue
            top_left = (merged.min_row, merged.min_col)
            merged_end = (min(merged.max_row, max_row), min(merged.max_col, max_col))
            merged_top_left[top_left] = merged_end
            for row in range(merged.min_row, merged_end[0] + 1):
                for column in range(merged.min_col, merged_end[1] + 1):
                    if (row, column) != top_left:
                        merged_covered.add((row, column))

        sequential_numbers = self._sequential_number_cells(
            worksheet,
            min_row,
            max_row,
            min_col,
            max_col,
        )
        layout_cells: list[LayoutCell] = []
        print_cells: list[dict[str, Any]] = []
        canvas = np.full((canonical_height, canonical_width, 3), 255, dtype=np.uint8)
        for row in range(min_row, max_row + 1):
            if worksheet.row_dimensions[row].hidden:
                continue
            for column in range(min_col, max_col + 1):
                if grouped_column_hidden.get(column, False):
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
                    max(0, int(round(column_widths[item] * scale)))
                    for item in range(column, end_col + 1)
                )
                height = sum(
                    max(0, int(round(row_heights[item] * scale)))
                    for item in range(row, end_row + 1)
                )
                width = max(1, width)
                height = max(1, height)
                has_adjacent_input = self._has_adjacent_input_cell(
                    worksheet,
                    row=row,
                    column=column,
                    end_row=end_row,
                    end_column=end_col,
                    max_row=max_row,
                    max_column=max_col,
                )
                has_sequential_number_below = end_row < max_row and any(
                    (end_row + 1, candidate_column) in sequential_numbers
                    for candidate_column in range(column, end_col + 1)
                )
                role = self._infer_role(
                    cell,
                    text=text,
                    merged=(end_row, end_col) != (row, column),
                    has_validation=has_validation,
                    sequential_number=(row, column) in sequential_numbers,
                    has_adjacent_input=has_adjacent_input,
                    has_sequential_number_below=has_sequential_number_below,
                    width=width,
                    height=height,
                )
                text_direction = self._spreadsheet_text_direction(
                    cell,
                    text=text,
                    width=width,
                    height=height,
                )
                text_alignment = self._spreadsheet_text_alignment(
                    cell,
                    text=text,
                    text_direction=text_direction,
                )
                cell_id = f"xlsx_{get_column_letter(column)}{row}"
                borders = self._border_map(cell)
                if not self._is_decorative_narrow_cell(
                    text=text,
                    bordered=bordered,
                    borders=borders,
                    has_validation=has_validation,
                    merged=(end_row, end_col) != (row, column),
                    width=width,
                    height=height,
                ):
                    layout_cells.append(
                        LayoutCell(
                            id=cell_id,
                            role=role,
                            x=x,
                            y=y,
                            width=width,
                            height=height,
                            confidence=0.72,
                            text=text[:300],
                            printed_label_text=text[:300] if role in {"label", "parent_label"} else "",
                            text_confidence=1.0 if text else None,
                            text_direction=text_direction,
                            text_alignment=text_alignment,
                            text_vertical_alignment=spreadsheet_vertical_alignment(
                                cell.alignment.vertical,
                            ),
                            source="detected",
                            composite_subfields=self._composite_address_subfields(
                                worksheet,
                                row=row,
                                column=column,
                                text=text,
                            ),
                        )
                    )
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
                        "align": text_alignment,
                        "vertical": cell.alignment.vertical or "center",
                        "textDirection": text_direction,
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

    @classmethod
    def _worksheet_bounds(
        cls, worksheet: Any, *, content: bytes | None = None,
    ) -> tuple[int, int, int, int]:
        return effective_worksheet_bounds(
            worksheet,
            content=content,
            max_source_cells=cls.MAX_SOURCE_CELLS,
        )

    @staticmethod
    def _empty_tail_is_inspectable(content: bytes) -> bool:
        return empty_tail_is_inspectable(content)

    def _render_workbook_preview(
        self,
        content: bytes,
        *,
        sheet_name: str,
        canonical_width: int,
        canonical_height: int,
        geometry: dict[str, Any] | None = None,
    ) -> tuple[bytes, np.ndarray[Any, Any] | None, int]:
        linux = os.environ.get("PRINT_PDF_RENDERER") == "linux"
        soffice = None if linux else self._find_soffice()
        if soffice is None and not linux:
            raise AppError(
                status=503,
                code="SPREADSHEET_RENDERER_NOT_AVAILABLE",
                title="Spreadsheet renderer is not available",
                detail="Excelプレビューに必要なLibreOfficeがインストールされていません。",
            )

        temporary_root = self._storage.resolve("tmp")
        with tempfile.TemporaryDirectory(prefix="spreadsheet-preview-", dir=temporary_root) as name:
            directory = Path(name)
            source_path = directory / "source.xlsx"
            profile_path = directory / "libreoffice-profile"
            try:
                workbook = load_workbook(io.BytesIO(preview_bytes(content)), data_only=False, read_only=False)
                selected_index = next(
                    index
                    for index, sheet in enumerate(workbook.worksheets)
                    if sheet.title == sheet_name
                )
                for index, sheet in enumerate(workbook.worksheets):
                    sheet.sheet_state = "visible" if index == selected_index else "hidden"
                workbook.active = selected_index
                worksheet = workbook.worksheets[selected_index]
                if not worksheet.print_area:
                    min_col, min_row, max_col, max_row = self._worksheet_bounds(
                        worksheet, content=content,
                    )
                    worksheet.print_area = (
                        f"${get_column_letter(min_col)}${min_row}:"
                        f"${get_column_letter(max_col)}${max_row}"
                    )
                if worksheet.sheet_properties.pageSetUpPr is None:
                    worksheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
                else:
                    worksheet.sheet_properties.pageSetUpPr.fitToPage = True
                worksheet.page_setup.fitToWidth = 1
                worksheet.page_setup.fitToHeight = 1
                worksheet.page_setup.scale = None
                workbook.save(source_path)
            except Exception as exc:
                raise AppError(
                    status=422,
                    code="SPREADSHEET_PREVIEW_PREPARATION_FAILED",
                    title="Spreadsheet preview could not be prepared",
                    detail="XLSXの印刷プレビューを準備できませんでした。",
                ) from exc

            if linux:
                from app.rendering.client import render
                from app.rendering.geometry import dimensions as linux_dimensions

                files, report = render(source_path.read_bytes(), "preview")
                dimensions = linux_dimensions(
                    files["page.fods"], sheet_name,
                    self._worksheet_bounds(worksheet, content=content)[3],
                    self._worksheet_bounds(worksheet, content=content)[2],
                )
                if geometry is not None:
                    geometry.update(
                        model=LINUX_PREVIEW_MODEL, sourceSha256=hashlib.sha256(content).hexdigest(),
                        sheetName=sheet_name,
                        bounds=list(self._worksheet_bounds(worksheet, content=content)),
                        rows=dimensions["rows"], columns=dimensions["columns"],
                    )
                # Native PDF parsing and rasterization happened in the isolated worker.
                image = cv2.imdecode(
                    np.frombuffer(files["page-1.png"], dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError("Isolated preview is missing")
                image = cv2.resize(image, (canonical_width, canonical_height),
                                   interpolation=cv2.INTER_AREA)
                return files["page.pdf"], image, report["pageCount"]

            command = [
                str(soffice),
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile_path.as_uri()}",
                "--convert-to",
                'pdf:calc_pdf_Export:{"PageRange":{"type":"string","value":"1"}}',
                "--outdir",
                str(directory),
                str(source_path),
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=directory,
                    capture_output=True,
                    check=False,
                    timeout=90,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise AppError(
                    status=504,
                    code="SPREADSHEET_RENDER_TIMEOUT",
                    title="Spreadsheet rendering timed out",
                    detail="Excelプレビューの生成が90秒以内に完了しませんでした。",
                ) from exc

            pdf_path = directory / "source.pdf"
            if completed.returncode != 0 or not pdf_path.is_file():
                raise AppError(
                    status=422,
                    code="SPREADSHEET_RENDER_FAILED",
                    title="Spreadsheet could not be rendered",
                    detail="LibreOfficeでXLSXをPDFへ変換できませんでした。",
                )
            pdf = pdf_path.read_bytes()
            if geometry is not None:
                # The same prepared copy and Office profile supply the dimensions
                # used by this PDF. Never route new reference rendering through
                # this legacy renderer or infer a qualification from this snapshot.
                bounds = self._worksheet_bounds(worksheet, content=content)
                command[command.index("--convert-to") + 1] = "fods"
                try:
                    converted = subprocess.run(
                        command, cwd=directory, capture_output=True, check=False, timeout=90,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    fods_path = directory / "source.fods"
                    if converted.returncode != 0 or not fods_path.is_file():
                        raise ValueError("Office dimensions were not exported")
                    dimensions = read_office_dimensions(
                        fods_path.read_bytes(), sheet_name, bounds[3], bounds[2],
                    )
                except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
                    raise AppError(
                        status=422, code="SPREADSHEET_PREVIEW_GEOMETRY_FAILED",
                        title="Spreadsheet preview geometry could not be read",
                        detail="プレビューと同じOffice読込寸法を取得できませんでした。",
                    ) from exc
                geometry.update(
                    model=MODEL, sourceSha256=hashlib.sha256(content).hexdigest(),
                    sheetName=sheet_name, bounds=list(bounds),
                    rows=dimensions["rows"], columns=dimensions["columns"],
                )

        try:
            document = pdfium.PdfDocument(pdf)
            page_count = len(document)
            if page_count == 0:
                raise ValueError("The rendered PDF has no pages")
            page = document[0]
            page_width, _ = page.get_size()
            bitmap = page.render(scale=canonical_width / max(page_width, 1.0))
            rgb = np.asarray(bitmap.to_pil().convert("RGB"))
            image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            if image.shape[1] != canonical_width or image.shape[0] != canonical_height:
                image = cv2.resize(
                    image,
                    (canonical_width, canonical_height),
                    interpolation=cv2.INTER_AREA,
                )
            bitmap.close()
            page.close()
            document.close()
        except Exception as exc:
            raise AppError(
                status=422,
                code="SPREADSHEET_PREVIEW_IMAGE_FAILED",
                title="Spreadsheet preview image could not be created",
                detail="生成したPDFからプレビュー画像を作成できませんでした。",
            ) from exc
        return pdf, image, page_count

    @staticmethod
    def _find_soffice() -> Path | None:
        configured = os.getenv("LIBREOFFICE_PATH")
        candidates = [
            configured,
            shutil.which("soffice"),
            shutil.which("soffice.exe"),
            str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LibreOffice/program/soffice.exe"),
            str(Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "LibreOffice/program/soffice.exe"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return Path(candidate)
        return None

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
            side is not None and side.style is not None
            for side in (cell.border.top, cell.border.right, cell.border.bottom, cell.border.left)
        )

    @staticmethod
    def _border_map(cell: Cell) -> dict[str, bool]:
        return {
            name: side is not None and side.style is not None
            for name, side in (
                ("top", cell.border.top), ("right", cell.border.right),
                ("bottom", cell.border.bottom), ("left", cell.border.left),
            )
        }

    @staticmethod
    def _has_data_validation(worksheet: Any, coordinate: str) -> bool:
        validations = worksheet.data_validations
        if validations is None:
            return False
        return any(coordinate in validation.sqref for validation in validations.dataValidation)

    @staticmethod
    def _composite_address_subfields(
        worksheet: Worksheet,
        *,
        row: int,
        column: int,
        text: str,
    ) -> list[LayoutCompositeSubfield]:
        if text:
            return []
        nearby_labels: list[str] = []
        for offset in range(1, 4):
            if column - offset >= 1:
                nearby_labels.append(
                    SpreadsheetTemplateImportService._display_text(
                        worksheet.cell(row=row, column=column - offset)
                    )
                )
        for offset in range(1, 3):
            if row - offset >= 1:
                nearby_labels.append(
                    SpreadsheetTemplateImportService._display_text(
                        worksheet.cell(row=row - offset, column=column)
                    )
                )
        context = "".join(nearby_labels).replace(" ", "").replace("　", "")
        if not any(
            keyword in context
            for keyword in ("帰省先", "現住所", "住所", "所在地")
        ):
            return []
        return [
            LayoutCompositeSubfield(
                id="address_prefecture",
                semantic_type="address_prefecture",
                label="都道府県",
                x_ratio=0,
                y_ratio=0,
                width_ratio=0.35,
                height_ratio=1,
            ),
            LayoutCompositeSubfield(
                id="address_municipality",
                semantic_type="address_municipality",
                label="市区町村",
                x_ratio=0.35,
                y_ratio=0,
                width_ratio=0.65,
                height_ratio=1,
            ),
        ]

    @staticmethod
    def _infer_role(
        cell: Cell,
        *,
        text: str,
        merged: bool,
        has_validation: bool,
        sequential_number: bool,
        width: int,
        height: int,
        has_adjacent_input: bool = False,
        has_sequential_number_below: bool = False,
    ) -> str:
        if sequential_number:
            return "input"

        scores = {
            "label": 0,
            "parent_label": 0,
            "input": 0,
            "mark": 0,
        }
        alignment = cell.alignment.horizontal
        has_fill = cell.fill.fill_type not in {None, "none"}
        is_unlocked = cell.protection.locked is False
        aspect_ratio = width / max(height, 1)
        is_compact_square = (
            0.75 <= aspect_ratio <= 1.25 and max(width, height) <= 64
        )

        if text:
            scores["label"] += 6
            if has_adjacent_input:
                scores["label"] += 8
            if has_sequential_number_below:
                scores["label"] += 8
            if cell.data_type == "f":
                scores["label"] += 2
            if merged:
                scores["parent_label"] += 6
            if bool(cell.font.bold):
                scores["parent_label"] += 2
            if alignment in {
                "center",
                "centerContinuous",
                "distributed",
                "justify",
            }:
                scores["parent_label"] += 1
            if has_fill:
                scores["parent_label"] += 1
            if has_validation:
                scores["input"] += 8
            if is_unlocked:
                scores["input"] += 8
        else:
            scores["input"] += 6
            if has_validation:
                scores["input"] += 2
                scores["mark"] += 6
            if is_unlocked:
                scores["input"] += 3
            if is_compact_square:
                scores["mark"] += 3

        priority = {
            "input": 1,
            "label": 2,
            "mark": 3,
            "parent_label": 4,
        }
        return max(scores, key=lambda role: (scores[role], priority[role]))

    @classmethod
    def _has_adjacent_input_cell(
        cls,
        worksheet: Worksheet,
        *,
        row: int,
        column: int,
        end_row: int,
        end_column: int,
        max_row: int,
        max_column: int,
    ) -> bool:
        """Detect a blank XLSX field immediately to the right or below a label."""

        candidates = (
            (row, end_column + 1),
            (end_row + 1, column),
        )
        for candidate_row, candidate_column in candidates:
            if candidate_row > max_row or candidate_column > max_column:
                continue
            if worksheet.row_dimensions[candidate_row].hidden:
                continue
            column_dimension = worksheet.column_dimensions[
                get_column_letter(candidate_column)
            ]
            if column_dimension.hidden:
                continue
            candidate = worksheet.cell(row=candidate_row, column=candidate_column)
            if cls._display_text(candidate):
                continue
            if cls._has_border(candidate) or cls._has_data_validation(
                worksheet,
                candidate.coordinate,
            ):
                return True
        return False

    @staticmethod
    def _is_decorative_narrow_cell(
        *,
        text: str,
        bordered: bool,
        borders: dict[str, bool],
        has_validation: bool,
        merged: bool,
        width: int,
        height: int,
    ) -> bool:
        """Keep the printed grid while excluding empty layout-only spacer cells."""
        if text or not bordered or has_validation or merged:
            return False
        if width <= 0 or height <= 0:
            return True
        too_narrow = width < max(8, round(height * 0.38))
        too_short = height < max(6, min(14, round(width * 0.06)))
        incomplete_tiny_box = width <= 16 and height <= 16 and not all(borders.values())
        return too_narrow or too_short or incomplete_tiny_box

    @staticmethod
    def _spreadsheet_text_direction(
        cell: Cell,
        *,
        text: str,
        width: int,
        height: int,
    ) -> str:
        rotation = int(cell.alignment.text_rotation or 0)
        compact_text = "".join(text.split())
        if rotation in {90, 255}:
            return "vertical"
        if len(compact_text) >= 2 and height >= width * 1.4:
            return "vertical"
        return "horizontal"

    @staticmethod
    def _spreadsheet_text_alignment(
        cell: Cell,
        *,
        text: str,
        text_direction: str,
    ) -> str:
        alignment = cell.alignment.horizontal
        if alignment == "right":
            return "right"
        if alignment == "left":
            return "left"
        if alignment in {"center", "centerContinuous", "distributed", "justify"}:
            return "center"
        if text_direction == "vertical":
            return "center"
        if isinstance(cell.value, int | float) and not isinstance(cell.value, bool):
            return "right"
        return "left" if text else "center"

    @staticmethod
    def _sequential_number_cells(
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
