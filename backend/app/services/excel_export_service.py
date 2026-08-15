from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
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

from app.domain.field_targets import (
    CALVING_FIELDS,
    DOCUMENT_FIELDS,
    PEDIGREE_FIELDS,
    SALE_CALF_FIELDS,
)
from app.domain.text_encoding import repair_utf8_mojibake

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
        individual_id: str | None = None,
        confirmed_at: datetime | None = None,
    ) -> ExcelTable:
        rows: list[ExcelRow] = [
            ExcelRow("title", (ExcelCell("帳票読み取り結果", "帳票読み取り結果"),)),
            ExcelRow("blank"),
        ]
        identity = self._mapping(document.get("document"))
        pedigree = self._mapping(document.get("pedigree"))
        sale_calves = self._records(document.get("saleCalves"))
        calvings = self._records(document.get("calvings"))
        custom_fields = self._mapping(document.get("customFields"))

        self._append_key_value_section(rows, "基本情報", DOCUMENT_FIELDS, identity)
        self._append_key_value_section(rows, "血統", PEDIGREE_FIELDS, pedigree)
        self._append_record_section(
            rows,
            self._section_title(config_snapshot, "saleCalves", "売却子牛一覧"),
            SALE_CALF_FIELDS,
            sale_calves,
            collection="saleCalves",
        )
        self._append_record_section(
            rows,
            self._section_title(config_snapshot, "calvings", "繁殖履歴"),
            CALVING_FIELDS,
            calvings,
            collection="calvings",
        )
        if custom_fields:
            custom_labels = self._custom_labels(config_snapshot)
            labels = {
                key: custom_labels.get(key) or self._humanize_key(key) for key in custom_fields
            }
            self._append_key_value_section(rows, "追加項目", labels, custom_fields)

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

        resolved_id = individual_id or self._reading_text(identity.get("unifiedEarTag"))
        cattle_name = self._reading_text(identity.get("cattleName")) or "牛名未入力"
        stamp = (confirmed_at or datetime.now()).strftime("%Y%m%d")
        file_stem = self._safe_file_component(
            f"繁殖台帳_{resolved_id or '個体番号未入力'}_{cattle_name}_{stamp}"
        )
        max_columns = max((len(row.cells) for row in rows), default=1)
        return ExcelTable(
            file_name=f"{file_stem}.xlsx",
            sheet_name="読み取り結果",
            rows=tuple(rows),
            max_columns=max_columns,
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

    def _append_record_section(
        self,
        rows: list[ExcelRow],
        title: str,
        labels: dict[str, str],
        records: list[dict[str, Any]],
        *,
        collection: str,
    ) -> None:
        rows.append(ExcelRow("section", (self._text_cell(title),)))
        rows.append(ExcelRow("header", tuple(self._text_cell(label) for label in labels.values())))
        for record in records:
            if not self._record_has_meaningful_value(record):
                continue
            rows.append(
                ExcelRow(
                    "data",
                    tuple(
                        self._reading_cell(
                            record.get(key),
                            field_key=key,
                            collection=collection,
                        )
                        for key in labels
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

    def _record_has_meaningful_value(self, record: dict[str, Any]) -> bool:
        return any(
            key != "parity" and bool(self._reading_text(reading)) for key, reading in record.items()
        )

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
        if field_key == "unifiedEarTag":
            return ExcelCell(text, text, "identifier")
        date_value = self._date_value(text)
        if field_key in {"birthDate", "saleDate", "calvingDate"} and date_value:
            return ExcelCell(date_value, text, "date")
        if field_key in {"parity", "ageDays", "priceYen"}:
            integer_value = self._integer_value(text)
            if integer_value is not None:
                kind: CellKind = "currency" if field_key == "priceYen" else "integer"
                return ExcelCell(integer_value, str(integer_value), kind)
        if field_key == "weightKg":
            decimal_value = self._decimal_value(text)
            if decimal_value is not None:
                return ExcelCell(decimal_value, str(decimal_value), "decimal")
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
    def _integer_value(value: str) -> int | None:
        compact = unicodedata.normalize("NFKC", value).replace(",", "").strip()
        if not compact.isdigit():
            return None
        return int(compact)

    @staticmethod
    def _decimal_value(value: str) -> float | int | None:
        compact = unicodedata.normalize("NFKC", value).replace(",", "").strip()
        try:
            number = Decimal(compact)
        except InvalidOperation:
            return None
        return int(number) if number == number.to_integral_value() else float(number)

    @staticmethod
    def _date_value(value: str) -> date | None:
        match = _JAPANESE_DATE.fullmatch(unicodedata.normalize("NFKC", value).strip())
        if match is None:
            return None
        try:
            return date(*(int(part) for part in match.groups()))
        except ValueError:
            return None

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
    def _section_title(
        config_snapshot: dict[str, Any] | None,
        collection: str,
        fallback: str,
    ) -> str:
        if not isinstance(config_snapshot, dict):
            return fallback
        structure = config_snapshot.get("recordStructure")
        if not isinstance(structure, dict):
            return fallback
        sections = structure.get("sections")
        if not isinstance(sections, list):
            return fallback
        for section in sections:
            if not isinstance(section, dict) or section.get("collectionPath") != collection:
                continue
            title = section.get("title")
            if isinstance(title, str) and title.strip():
                return repair_utf8_mojibake(title.strip())
        return fallback

    @staticmethod
    def _custom_labels(config_snapshot: dict[str, Any] | None) -> dict[str, str]:
        if not isinstance(config_snapshot, dict):
            return {}
        mappings = config_snapshot.get("fieldMappings")
        if not isinstance(mappings, list):
            return {}
        result: dict[str, str] = {}
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            path = mapping.get("targetPath")
            label = mapping.get("label")
            if (
                isinstance(path, str)
                and path.startswith("customFields.")
                and isinstance(label, str)
                and label.strip()
            ):
                result.setdefault(
                    path.removeprefix("customFields."),
                    repair_utf8_mojibake(label.strip()),
                )
        return result

    @staticmethod
    def _humanize_key(value: str) -> str:
        spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ")
        return spaced.strip() or value

    @staticmethod
    def _safe_file_component(value: str) -> str:
        sanitized = _UNSAFE_FILE_NAME.sub("_", value).strip(" .")
        return (sanitized or "繁殖台帳")[:120]
