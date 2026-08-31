from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime

from openpyxl import load_workbook  # type: ignore[import-untyped]

from app.template_packages.export_models import TemplatePackageExportError

_FORBIDDEN_ENTRY_PREFIXES = (
    "xl/externalLinks/",
    "xl/embeddings/",
    "xl/oleObjects/",
    "customXml/",
)
_FORBIDDEN_ENTRY_NAMES = {
    "xl/vbaProject.bin",
    "xl/vbaData.xml",
}
_EXTERNAL_FORMULA = re.compile(r"(?:\[[^\]]+\]|(?:https?|file)://)", re.IGNORECASE)


@dataclass(frozen=True)
class SanitizedSpreadsheet:
    content: bytes
    removed_comments: int
    removed_external_hyperlinks: int


class SpreadsheetSanitizer:
    """Sanitizes a trusted local XLSX source before it enters an export package."""

    def sanitize(self, content: bytes, *, source_sheet_name: str) -> SanitizedSpreadsheet:
        self._inspect_container(content)
        try:
            workbook = load_workbook(io.BytesIO(content), keep_links=False, data_only=False)
        except Exception as exc:
            raise TemplatePackageExportError(
                "SPREADSHEET_SOURCE_INVALID",
                "元XLSXを安全に読み込めません。",
            ) from exc
        if source_sheet_name not in workbook.sheetnames:
            raise TemplatePackageExportError(
                "SPREADSHEET_SOURCE_SHEET_MISSING",
                "元XLSXに対象sheetが存在しません。",
            )
        hidden_sheets = [
            sheet.title for sheet in workbook.worksheets if sheet.sheet_state != "visible"
        ]
        if hidden_sheets:
            raise TemplatePackageExportError(
                "SPREADSHEET_SOURCE_HIDDEN_SHEET",
                "非表示sheetを含むXLSXは書き出せません。元ファイルで確認・削除してください。",
            )
        hidden_dimensions = [
            f"{sheet.title}!row:{index}"
            for sheet in workbook.worksheets
            for index, dimension in sheet.row_dimensions.items()
            if dimension.hidden
        ] + [
            f"{sheet.title}!column:{index}"
            for sheet in workbook.worksheets
            for index, dimension in sheet.column_dimensions.items()
            if dimension.hidden
        ]
        hidden_names = [
            item.name
            for item in workbook.defined_names.values()
            if getattr(item, "hidden", False)
        ]
        if hidden_dimensions or hidden_names:
            raise TemplatePackageExportError(
                "SPREADSHEET_SOURCE_HIDDEN_CONTENT",
                "非表示の行・列・定義名を含むXLSXは書き出せません。",
            )

        removed_comments = 0
        removed_links = 0
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.comment is not None:
                        cell.comment = None
                        removed_comments += 1
                    if (
                        isinstance(cell.value, str)
                        and cell.value.startswith("=")
                        and _EXTERNAL_FORMULA.search(cell.value)
                    ):
                        raise TemplatePackageExportError(
                            "SPREADSHEET_SOURCE_EXTERNAL_FORMULA",
                            "外部参照式を含むXLSXは書き出せません。",
                        )
                    hyperlink = cell.hyperlink
                    if hyperlink is not None:
                        target = str(hyperlink.target or "")
                        if target and not target.startswith("#"):
                            cell.hyperlink = None
                            removed_links += 1

        properties = workbook.properties
        properties.creator = ""
        properties.lastModifiedBy = ""
        properties.title = None
        properties.subject = None
        properties.description = None
        properties.keywords = None
        properties.category = None
        properties.contentStatus = None
        properties.identifier = None
        properties.language = None
        properties.version = None
        properties.lastPrinted = None
        properties.created = datetime(2000, 1, 1, tzinfo=UTC)
        properties.modified = datetime(2000, 1, 1, tzinfo=UTC)
        if hasattr(workbook, "custom_doc_props"):
            workbook.custom_doc_props.props.clear()

        output = io.BytesIO()
        workbook.save(output)
        sanitized = self._deterministic_zip(output.getvalue())
        self._inspect_container(sanitized)
        return SanitizedSpreadsheet(
            content=sanitized,
            removed_comments=removed_comments,
            removed_external_hyperlinks=removed_links,
        )

    @staticmethod
    def _deterministic_zip(content: bytes) -> bytes:
        source = io.BytesIO(content)
        target = io.BytesIO()
        with zipfile.ZipFile(source) as input_archive, zipfile.ZipFile(
            target,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as output_archive:
            for name in sorted(input_archive.namelist()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                payload = input_archive.read(name)
                if name == "docProps/core.xml":
                    payload = re.sub(
                        rb"(<dcterms:(?:created|modified)\b[^>]*>)[^<]*(</dcterms:(?:created|modified)>)",
                        rb"\g<1>2000-01-01T00:00:00Z\g<2>",
                        payload,
                    )
                output_archive.writestr(info, payload)
        return target.getvalue()

    @staticmethod
    def _inspect_container(content: bytes) -> None:
        if not content.startswith(b"PK\x03\x04"):
            raise TemplatePackageExportError(
                "SPREADSHEET_SOURCE_INVALID",
                "元ファイルは有効な.xlsxではありません。",
            )
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = archive.namelist()
                if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                    raise TemplatePackageExportError(
                        "SPREADSHEET_SOURCE_INVALID",
                        "元ファイルは有効な.xlsxではありません。",
                    )
                forbidden = [
                    name
                    for name in names
                    if name in _FORBIDDEN_ENTRY_NAMES
                    or any(name.startswith(prefix) for prefix in _FORBIDDEN_ENTRY_PREFIXES)
                ]
                if forbidden:
                    raise TemplatePackageExportError(
                        "SPREADSHEET_SOURCE_UNSAFE_CONTENT",
                        "マクロ、外部リンク、埋め込みオブジェクト等を含むXLSXは書き出せません。",
                    )
        except zipfile.BadZipFile as exc:
            raise TemplatePackageExportError(
                "SPREADSHEET_SOURCE_INVALID",
                "元XLSXのZIP構造が壊れています。",
            ) from exc
