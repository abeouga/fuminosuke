"""Run from backend with the repository virtualenv. No OCR or external API calls.

Writes only the explicitly requested output directory. Real workbook cell values
are never included in the audit. Original files are read and hashed again at end.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.errors import AppError
from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services.office_reference_renderer import inspect_package, render_office, sha
from app.services.print_layout_source import read_source
from app.services.reference_geometry import measure_page


def fixture(rows: int, explicit: bool) -> bytes:
    openpyxl = importlib.import_module("openpyxl")
    styles = importlib.import_module("openpyxl.styles")
    breaks = importlib.import_module("openpyxl.worksheet.pagebreak")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Fixture"
    sheet.print_area = f"B2:D{rows}"
    if explicit:
        for column in "BCD":
            sheet.column_dimensions[column].width = 8.43
    for row in range(2, rows + 1):
        sheet.row_dimensions[row].height = 24
        for column in range(2, 5):
            sheet.cell(row, column).border = styles.Border(
                **{k: styles.Side(style="thin") for k in ("left", "right", "top", "bottom")})
    # Original manual break layout has three pages. Saved ranges override it.
    sheet.row_breaks.append(breaks.Break(id=10))
    sheet.row_breaks.append(breaks.Break(id=20))
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xlsx-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report: dict = {"schemaVersion": 1, "qualified": False, "fixtures": [], "workbooks": []}
    for label, rows, explicit, boundaries in [
        ("default-width", 12, False, []), ("explicit-width", 12, True, []),
        ("saved-two-pages", 30, True, [15]),
    ]:
        content = fixture(rows, explicit)
        source = read_source(content, "Fixture", {"cells": [
            {"id": f"xlsx_C{row}", "role": "input"} for row in [2, *[b + 1 for b in boundaries]]
        ]})
        plan = page_plan(source, PrintLayoutRequest(base_revision="0" * 64,
                                                   break_after_rows=boundaries))
        renderer, images = render_office(content, plan)
        repeat_renderer, repeat_images = render_office(content, plan)
        metrics = [measure_page(p, image) for p, image in zip(plan["pages"], images, strict=True)]
        for number, image in enumerate(images, 1):
            (args.output / f"{label}-{number}.png").write_bytes(image)
        (args.output / f"{label}.xlsx").write_bytes(content)
        report["fixtures"].append({"name": label, "sourceSha256": sha(content),
            "renderer": renderer, "pages": metrics, "ranges": [p["range"] for p in plan["pages"]],
            "originalManualBreaks": [10, 20], "referenceSha256": [sha(i) for i in images],
            "deterministic": renderer == repeat_renderer and images == repeat_images})
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    for path in sorted(args.xlsx_directory.glob("*.xlsx")):
        content = path.read_bytes()
        record: dict = {"filename": path.name, "sourceSha256": sha(content), "sheets": []}
        try:
            inspect_package(content)
            record["officePreflight"] = "PASS"
        except AppError as exc:
            record["officePreflight"] = exc.code
            record["officeReason"] = exc.detail
        # Metadata only. Real files are never opened by Office after failed preflight.
        with ZipFile(BytesIO(content)) as archive:
            book = ET.fromstring(archive.read("xl/workbook.xml"))
            for node in book.findall(f"{namespace}sheets/{namespace}sheet"):
                name = node.get("name", "")
                item: dict = {"name": name}
                try:
                    source = read_source(content, name, {"cells": []})
                    item.update(startRow=source["startRow"], endRow=source["endRow"],
                                startCol=source["startCol"], endCol=source["endCol"],
                                unsupported=source["unsupported"],
                                importedSettings=source["importedSettings"])
                except AppError as exc:
                    item.update(code=exc.code, reason=exc.detail)
                record["sheets"].append(item)
        record["classification"] = ("needs manual page setup" if any(
            "20,000" in s.get("reason", "") for s in record["sheets"]) else "unsupported")
        record["qualified"] = False
        record["sourceUnchanged"] = sha(path.read_bytes()) == sha(content)
        report["workbooks"].append(record)
    (args.output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    print(json.dumps({"fixtures": [{"name": f["name"], "deterministic": f["deterministic"],
                                   "pages": f["pages"]} for f in report["fixtures"]],
                      "workbooks": [{k: v for k, v in w.items() if k != "sheets"}
                                    for w in report["workbooks"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
