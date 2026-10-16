"""Render an XLSX input against its registered single-page template."""
from __future__ import annotations

import hashlib
import io

from openpyxl import load_workbook

from app.db.models import TemplateVersion
from app.db.session import Database
from app.image_processing.artifacts import encode_png
from app.services.spreadsheet_template_import_service import SpreadsheetTemplateImportService
from app.services.spreadsheet_template_output_service import SpreadsheetTemplateOutputService
from app.services.xlsx_checkboxes import checkbox_cells, invalid, parts, preview_bytes
from app.storage.local_storage import LocalStorageService


def prepare_xlsx_input(database: Database, storage: LocalStorageService,
                       version_id: str, content: bytes) -> tuple[bytes, dict]:
    package = parts(content)
    if any(n.startswith(("xl/activeX/", "xl/ctrlProps/", "xl/externalLinks/"))
           or n.endswith("vbaProject.bin") for n in package):
        raise invalid("この読込はセル内チェックボックスに対応しています。旧フォーム・ActiveX・外部リンク付きExcelは画像にして読み込んでください。")
    with database.session() as session:
        version = session.get(TemplateVersion, version_id)
        if version is None or version.status != "ACTIVE" or version.mode != "SPREADSHEET_TEMPLATE":
            raise invalid("有効なExcelテンプレートを選択してください。")
        if version.quality_json.get("printLayout") is not None:
            raise invalid("Excelファイルの直接読込は単一ページのテンプレートで使用してください。")
        source = storage.read_bytes(version.quality_json["spreadsheetSourcePath"])
        sheet_name = version.quality_json["sourceSheetName"]
        layout = version.layout_json
        from app.services.template_management_service import TemplateManagementService
        mappings = TemplateManagementService(database, storage).get_mapping_configuration(version_id)[2]
        width, height = version.canonical_width, version.canonical_height
    checks = checkbox_cells(content).get(sheet_name, {})
    incoming = load_workbook(io.BytesIO(content), data_only=False, keep_links=False)
    template = load_workbook(io.BytesIO(preview_bytes(source)), data_only=False, keep_links=False)
    try:
        if sheet_name not in incoming.sheetnames or sheet_name not in template.sheetnames:
            raise invalid("テンプレートと同じシート名のExcelを指定してください。")
        sheet, baseline = incoming[sheet_name], template[sheet_name]
        def geometry(ws):
            return (ws.max_row, ws.max_column, sorted(str(r) for r in ws.merged_cells.ranges),
                    [(i, d.height, d.hidden) for i, d in ws.row_dimensions.items()],
                    [(i, d.width, d.hidden) for i, d in ws.column_dimensions.items()])
        if geometry(sheet) != geometry(baseline):
            raise invalid("Excelの行・列・結合がテンプレートと異なります。元のテンプレートへ記入してください。")
        owners = {}
        for mapping in mappings:
            raw = mapping.model_dump(mode="json", by_alias=True)
            cell = SpreadsheetTemplateOutputService._layout_cell(raw, layout)
            coordinate = SpreadsheetTemplateOutputService._coordinate(cell["id"]) if cell else None
            if not coordinate or coordinate in owners or mapping.composite_subfields:
                raise invalid("Excel入力のセル対応が一意に確定できません。")
            owners[coordinate] = mapping
        for row in sheet:
            for cell in row:
                if cell.data_type == "f":
                    raise invalid("読み取り用Excelは数式を値に変換してから指定してください。")
                if cell.coordinate not in owners and cell.value != baseline[cell.coordinate].value:
                    raise invalid(f"記入欄以外のセル {cell.coordinate} がテンプレートと異なります。")
        for coordinate, mapping in owners.items():
            value = sheet[coordinate].value
            if coordinate in checks:
                if mapping.mark_recognition_mode != "checklist" or len(mapping.mark_options) != 1:
                    raise invalid(f"{coordinate} のセル内チェックボックスには、候補が1つのチェックリスト欄を設定してください。")
                value = "☑" if checks[coordinate] else "☐"
            elif isinstance(value, bool) and mapping.mark_recognition_mode == "checklist":
                raise invalid(f"{coordinate} はチェックボックス書式がありません。TRUE/FALSEだけでは判定しません。")
            baseline[coordinate].value = value
            if isinstance(value, str):
                baseline[coordinate].data_type = "s"
        buffer = io.BytesIO()
        template.save(buffer)
        renderer = SpreadsheetTemplateImportService(database, storage)
        _, image, count = renderer._render_workbook_preview(
            buffer.getvalue(), sheet_name=sheet_name,
            canonical_width=width, canonical_height=height)
        if image is None or count != 1:
            raise invalid("単一ページのExcelを指定してください。")
        return encode_png(image), {"kind": "xlsx-checklist-v1",
                                  "sourceSha256": hashlib.sha256(content).hexdigest(),
                                  "checkboxCellCount": len(checks), "sheetName": sheet_name}
    finally:
        incoming.close()
        template.close()

