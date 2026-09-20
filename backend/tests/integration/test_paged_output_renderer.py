"""Actual Office interpretation of the versioned writer; no production gate changes."""

import hashlib
from xml.etree import ElementTree as ET

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side

from app.services.paged_workbook import render_workbook
from app.services.renderer_wsl import import_wsl_geometry, render_wsl_reference
from tests.unit.test_paged_workbook import binding, saved, workbook_bytes


def test_real_isolated_office_preserves_literal_output_values(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "記入済み帳票"
    sheet.column_dimensions["A"].width = 22
    sheet.column_dimensions["B"].width = 48
    values = ["00123", "=SUM(A1:A2)", "_x0041_ _x005F_", "一行目\n二行目", "任意 & <文字>"]
    for number in range(1, len(values) + 1):
        sheet.cell(number, 1, f"検証項目 {number}")
        sheet.cell(number, 2, "未入力")
        sheet.row_dimensions[number].height = 44
        for cell in sheet[number]:
            cell.font = Font(name="Yu Gothic", size=12)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            side = Side(style="thin", color="000000")
            cell.border = Border(left=side, right=side, top=side, bottom=side)
    sheet.print_area = "A1:B5"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    source = workbook_bytes(book)
    source_sha = hashlib.sha256(source).hexdigest()
    bindings = [binding(f"B{number}") for number in range(1, len(values) + 1)]
    output = render_workbook(
        source,
        saved(source, bindings, sheet=sheet.title),
        [{"binding": b, "value": v} for b, v in zip(bindings, values, strict=True)],
    )
    (tmp_path / "output.xlsx").write_bytes(output)
    fods, imported = import_wsl_geometry(output, font_source="windows")
    ns = {
        "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    }
    table = ET.fromstring(fods).find(".//table:table", ns)
    actual = []
    for row in table.findall("table:table-row", ns)[:5]:
        cell = row.findall("table:table-cell", ns)[1]
        assert "{" + ns["table"] + "}formula" not in cell.attrib
        actual.append("\n".join("".join(p.itertext()) for p in cell.findall("text:p", ns)))
    assert actual == values
    assert imported["networkDenied"] is True
    assert imported["sourceSha256"] == hashlib.sha256(output).hexdigest()
    pdf, images, report = render_wsl_reference(output)
    assert report["pageCount"] == 1 and len(images) == 1
    assert report["networkDenied"] is True and report["referenceFontMountReadOnly"] is True
    assert report["sourceSha256"] == hashlib.sha256(output).hexdigest()
    assert hashlib.sha256(source).hexdigest() == source_sha
    (tmp_path / "output.pdf").write_bytes(pdf)
    (tmp_path / "output.png").write_bytes(images[0])
