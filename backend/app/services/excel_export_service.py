from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import Any, Literal

from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.cell import Cell  # type: ignore[import-untyped]
from openpyxl.styles import (  # type: ignore[import-untyped]
    Alignment,
    Border,
    Font,
    PatternFill,
    Side,
)
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]

from app.core.errors import AppError

CellKind = Literal["text", "identifier", "integer", "decimal", "currency", "date"]
RowKind = Literal["title", "section", "header", "data", "blank"]

_JAPANESE_DATE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$")
_UNSAFE_FILE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_FORMULA_PREFIXES = ("=", "+", "-", "@")


@dataclass(frozen=True, slots=True)
class ExcelCell:
    value: str | int | float | date | None
    display: str
    kind: CellKind = "text"

    def payload(self) -> dict[str, object | None]:
        value: str | int | float | None = (
            self.value.isoformat() if isinstance(self.value, date) else self.value
        )
        return {"value": value, "display": self.display, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class ExcelRow:
    kind: RowKind
    cells: tuple[ExcelCell, ...] = ()

    def payload(self) -> dict[str, object]:
        return {"kind": self.kind, "cells": [cell.payload() for cell in self.cells]}


@dataclass(frozen=True, slots=True)
class ExcelTable:
    file_name: str
    sheet_name: str
    rows: tuple[ExcelRow, ...]
    max_columns: int

    def payload(self) -> dict[str, object]:
        return {
            "fileName": self.file_name,
            "sheetName": self.sheet_name,
            "maxColumns": self.max_columns,
            "rows": [row.payload() for row in self.rows],
        }


class ExcelExportService:
    """Builds one canonical table used by both clipboard and XLSX exports."""

    def build_table(
        self,
        document: dict[str, Any],
        *,
        config_snapshot: dict[str, Any] | None = None,
        confirmed_at: datetime | None = None,
        document_name: str | None = None,
    ) -> ExcelTable:
        generic_data = self._mapping(document.get("genericData"))
        generic_title = generic_data.get("templateName")
        snapshot = config_snapshot or {}
        template_snapshot = self._mapping(snapshot.get("templateSnapshot"))
        template_config = self._mapping(template_snapshot.get("config"))
        title = document_name or (
            str(generic_title)
            if isinstance(generic_title, str) and generic_title.strip()
            else str(template_config.get("name") or "帳票読み取り結果")
        )
        rows: list[ExcelRow] = [
            ExcelRow("title", (ExcelCell(title, title),)),
            ExcelRow("blank"),
        ]
        mappings = snapshot.get("fieldMappings")
        if not isinstance(mappings, list) or not mappings:
            raise AppError(
                status=409,
                code="XLSX_REVIEW_STRUCTURE_INVALID",
                title="Saved export structure missing",
                detail="受付時の出力構造がありません。",
            )
        used_paths = self._append_review_structure(
            rows, document, snapshot.get("reviewStructure"), mappings
        )
        self._append_unbound_values(rows, document, used_paths, config_snapshot)

        review_items = document.get("reviewItems")
        if isinstance(review_items, list) and review_items:
            rows.extend(
                [
                    ExcelRow("section", (ExcelCell("確認メモ", "確認メモ"),)),
                    ExcelRow(
                        "header",
                        tuple(self._text_cell(value) for value in ("種別", "項目", "内容")),
                    ),
                ]
            )
            for item in review_items:
                if not isinstance(item, dict):
                    continue
                severity = "要確認" if item.get("severity") == "error" else "注意"
                path_value: object = item.get("path")
                message_value: object = item.get("message")
                path = path_value if isinstance(path_value, str) else ""
                message = message_value if isinstance(message_value, str) else ""
                rows.append(
                    ExcelRow(
                        "data",
                        (
                            self._text_cell(severity),
                            self._text_cell(path),
                            self._text_cell(message),
                        ),
                    )
                )
            rows.append(ExcelRow("blank"))

        stamp = (confirmed_at or datetime.now()).strftime("%Y%m%d")
        file_stem = self._safe_file_component(document_name or f"{title}_{stamp}")
        max_columns = max((len(row.cells) for row in rows), default=1)
        return ExcelTable(
            file_name=f"{file_stem}.xlsx",
            sheet_name="読み取り結果",
            rows=tuple(rows),
            max_columns=max_columns,
        )

    def _append_review_structure(
        self,
        rows: list[ExcelRow],
        document: dict[str, Any],
        structure: object,
        mappings: list[Any],
    ) -> set[str]:
        """Project accepted field IDs; labels and array positions never resolve values."""

        def invalid() -> AppError:
            return AppError(
                status=409,
                code="XLSX_REVIEW_STRUCTURE_INVALID",
                title="Saved export structure is invalid",
                detail="受付時の項目と表の対応を確認できません。結果を再取得してください。",
            )

        if not isinstance(structure, dict) or structure.get("schemaVersion") != 1:
            raise invalid()
        accepted: dict[str, str] = {}
        for mapping in mappings:
            if not isinstance(mapping, dict) or mapping.get("enabled") is False:
                continue
            identifier, path = mapping.get("mappingId"), mapping.get("targetPath")
            if (
                not isinstance(identifier, str)
                or not isinstance(path, str)
                or identifier in accepted
            ):
                raise invalid()
            accepted[identifier] = path
        if len(set(accepted.values())) != len(accepted):
            raise invalid()
        fields: dict[str, dict[str, Any]] = {}
        for field in self._records(structure.get("fields")):
            identifier = field.get("id")
            if (
                not isinstance(identifier, str)
                or identifier in fields
                or field.get("mappingId") != identifier
                or accepted.get(identifier) != field.get("targetPath")
            ):
                raise invalid()
            fields[identifier] = field
        if set(fields) != set(accepted):
            raise invalid()
        visited: set[str] = set()

        def value(identifier: object) -> ExcelCell:
            if not isinstance(identifier, str) or identifier not in fields or identifier in visited:
                raise invalid()
            visited.add(identifier)
            reading: object = document
            for part in accepted[identifier].split("."):
                if isinstance(reading, dict):
                    reading = reading.get(part)
                elif isinstance(reading, list) and part.isdigit() and int(part) < len(reading):
                    reading = reading[int(part)]
                else:
                    raise invalid()
            if not isinstance(reading, dict) or not ("raw" in reading or "normalized" in reading):
                raise invalid()
            text = reading.get("normalized")
            if text is None:
                text = reading.get("raw")
            # Generic fields stay literal: no inferred dates, units, or numeric identifiers.
            return ExcelCell(
                text if isinstance(text, str) else None,
                text if isinstance(text, str) else "",
            )

        def append_fields(title: str, identifiers: object) -> None:
            if not isinstance(identifiers, list):
                raise invalid()
            if not identifiers:
                return
            rows.extend(
                [
                    ExcelRow("section", (self._text_cell(title),)),
                    ExcelRow("header", (self._text_cell("項目"), self._text_cell("値"))),
                ]
            )
            for identifier in identifiers:
                cell = value(identifier)
                field = fields[str(identifier)]
                label = str(field.get("label") or accepted[str(identifier)])
                rows.append(ExcelRow("data", (self._text_cell(label), cell)))
            rows.append(ExcelRow("blank"))

        append_fields("基本項目", structure.get("independentFieldIds"))
        for section in self._records(structure.get("parentSections")):
            append_fields(str(section.get("title") or "項目"), section.get("fieldIds"))
        for section in self._records(structure.get("recordSections")):
            columns = self._records(section.get("columns"))
            column_ids = [column.get("id") for column in columns]
            if (
                not columns
                or any(not isinstance(item, str) for item in column_ids)
                or len(set(column_ids)) != len(column_ids)
            ):
                raise invalid()
            rows.extend(
                [
                    ExcelRow("section", (self._text_cell(str(section.get("title") or "明細")),)),
                    ExcelRow(
                        "header",
                        tuple(self._text_cell(str(c.get("label") or "項目")) for c in columns),
                    ),
                ]
            )
            for record in self._records(section.get("rows")):
                entries = self._records(record.get("cells"))
                by_column = {entry.get("columnId"): entry.get("fieldId") for entry in entries}
                if len(by_column) != len(entries) or set(by_column) != set(column_ids):
                    raise invalid()
                rows.append(
                    ExcelRow(
                        "data",
                        tuple(
                            ExcelCell(None, "")
                            if by_column[column_id] is None
                            else value(by_column[column_id])
                            for column_id in column_ids
                        ),
                    )
                )
            rows.append(ExcelRow("blank"))
        if visited != set(fields):
            raise invalid()
        return set(accepted.values())

    def _append_unbound_values(self, rows, document, used_paths, config_snapshot) -> None:
        readings = {
            key: reading
            for key, reading in self._mapping(document.get("fields")).items()
            if f"fields.{key}" not in used_paths
        }
        if readings:
            labels = self._mapping(document.get("fieldLabels"))
            self._append_key_value_section(
                rows, "追加項目", {key: str(labels.get(key) or key) for key in readings}, readings
            )

    def render_xlsx(self, table: ExcelTable) -> bytes:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = table.sheet_name
        sheet.sheet_view.showGridLines = False
        sheet.sheet_view.zoomScale = 90
        sheet.freeze_panes = "A2"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.outlinePr.summaryBelow = True

        title_fill = PatternFill("solid", fgColor="1F4E78")
        section_fill = PatternFill("solid", fgColor="D9EAF7")
        header_fill = PatternFill("solid", fgColor="5B9BD5")
        white_font = Font(name="Yu Gothic UI", color="FFFFFF", bold=True)
        title_font = Font(name="Yu Gothic UI", color="FFFFFF", bold=True, size=16)
        section_font = Font(name="Yu Gothic UI", color="173F5D", bold=True, size=12)
        body_font = Font(name="Yu Gothic UI", color="172431", size=10)
        light_side = Side(style="thin", color="D7E0E7")
        section_side = Side(style="medium", color="8FAFC6")

        for row_index, row in enumerate(table.rows, start=1):
            if row.kind == "blank":
                sheet.row_dimensions[row_index].height = 8
                continue
            if row.kind in {"title", "section"} and table.max_columns > 1:
                sheet.merge_cells(
                    start_row=row_index,
                    start_column=1,
                    end_row=row_index,
                    end_column=table.max_columns,
                )
            for column_index, excel_cell in enumerate(row.cells, start=1):
                cell = sheet.cell(row=row_index, column=column_index)
                self._write_cell(cell, excel_cell)
                cell.font = body_font
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                if row.kind == "header":
                    cell.fill = header_fill
                    cell.font = white_font
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    cell.border = Border(bottom=section_side)
                elif row.kind == "data":
                    cell.border = Border(bottom=light_side)
            if row.kind == "title":
                anchor = sheet.cell(row=row_index, column=1)
                anchor.fill = title_fill
                anchor.font = title_font
                anchor.alignment = Alignment(vertical="center")
                sheet.row_dimensions[row_index].height = 28
            elif row.kind == "section":
                anchor = sheet.cell(row=row_index, column=1)
                anchor.fill = section_fill
                anchor.font = section_font
                anchor.alignment = Alignment(vertical="center")
                anchor.border = Border(bottom=section_side)
                sheet.row_dimensions[row_index].height = 23
            elif row.kind == "header":
                sheet.row_dimensions[row_index].height = 22
            else:
                sheet.row_dimensions[row_index].height = 20

        self._set_column_widths(sheet, table)
        sheet.print_title_rows = "1:1"
        sheet.sheet_properties.pageSetUpPr.autoPageBreaks = False
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def _append_key_value_section(
        self,
        rows: list[ExcelRow],
        title: str,
        labels: dict[str, str],
        readings: dict[str, Any],
    ) -> None:
        rows.append(ExcelRow("section", (self._text_cell(title),)))
        rows.append(ExcelRow("header", (self._text_cell("項目"), self._text_cell("値"))))
        for key, label in labels.items():
            rows.append(
                ExcelRow(
                    "data",
                    (
                        self._text_cell(label),
                        self._reading_cell(readings.get(key), field_key=key),
                    ),
                )
            )
        rows.append(ExcelRow("blank"))

    @staticmethod
    def _mapping(value: object) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _records(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _reading_cell(
        self,
        reading: object,
        *,
        field_key: str,
        collection: str | None = None,
    ) -> ExcelCell:
        text = self._reading_text(reading)
        if not text:
            return ExcelCell(None, "", "text")
        return self._text_cell(text)

    @staticmethod
    def _reading_text(reading: object) -> str:
        if not isinstance(reading, dict):
            return ""
        value = reading.get("normalized") or reading.get("raw")
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _text_cell(value: str) -> ExcelCell:
        return ExcelCell(value, value, "text")

    @staticmethod
    def _write_cell(cell: Cell, excel_cell: ExcelCell) -> None:
        value = excel_cell.value
        if isinstance(value, str):
            cell.value = value
            cell.data_type = "s"
        else:
            cell.value = value
        if excel_cell.kind in {"text", "identifier"}:
            cell.number_format = "@"
        elif excel_cell.kind == "date":
            cell.number_format = 'yyyy"年"m"月"d"日"'
        elif excel_cell.kind == "currency":
            cell.number_format = '#,##0"円"'
        elif excel_cell.kind == "decimal":
            cell.number_format = '#,##0.###"kg"'
        elif excel_cell.kind == "integer":
            cell.number_format = "#,##0"
        if isinstance(value, str) and value.lstrip().startswith(_FORMULA_PREFIXES):
            cell.data_type = "s"

    @staticmethod
    def _set_column_widths(sheet: Any, table: ExcelTable) -> None:
        for column_index in range(1, table.max_columns + 1):
            widest = 8
            for row in table.rows:
                if column_index > len(row.cells):
                    continue
                display = row.cells[column_index - 1].display
                width = sum(
                    2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1
                    for char in display
                )
                widest = max(widest, width + 2)
            sheet.column_dimensions[get_column_letter(column_index)].width = min(widest, 34)

    @staticmethod
    def _safe_file_component(value: str) -> str:
        sanitized = _UNSAFE_FILE_NAME.sub("_", value).strip(" .")
        return (sanitized or "帳票")[:120]
