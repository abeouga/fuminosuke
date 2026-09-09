"""Generate synthetic print fixtures and try the actual mandatory sandbox path.

Run from backend: python scripts/check_template_print.py --output <new-directory>
No real template, user workbook, OCR API or qualification record is modified.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Border, Side
from openpyxl.worksheet.pagebreak import Break

from app.core.errors import AppError
from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services.print_layout_source import read_source
from app.services.print_pdf import render_print_pdf
from app.services.print_workbook import workbook_copy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    book = Workbook()
    sheet = book.active
    sheet.title = "Print fixture"
    sheet.print_area = "B2:D33"
    sheet.row_breaks.append(Break(id=9))
    for row in range(2, 34):
        sheet.row_dimensions[row].height = 24
        for col in range(2, 5):
            sheet.cell(row, col, f"R{row}C{col}").border = Border(
                top=Side(style="thin"), bottom=Side(style="thin"),
                left=Side(style="thin"), right=Side(style="thin")
            )
    original = io.BytesIO()
    book.save(original)
    content = original.getvalue()
    source_sha = hashlib.sha256(content).hexdigest()
    (args.output / "source.xlsx").write_bytes(content)
    report = {
        "sourceSha256": source_sha,
        "originalBreaks": [9],
        "cases": [],
        "qualificationChanged": False,
    }
    target = None
    for name, breaks in [
        ("single", []),
        ("two-pages", [16]),
        ("three-pages", [10, 20]),
        ("version-1", [16]),
        ("version-2", [20]),
    ]:
        plan = page_plan(
            read_source(content, sheet.title, {"cells": []}),
            PrintLayoutRequest(base_revision="0" * 64, break_after_rows=breaks),
        )
        output = workbook_copy(content, plan)
        (args.output / f"{name}.xlsx").write_bytes(output)
        (args.output / f"{name}.plan.json").write_text(
            json.dumps(plan, ensure_ascii=False), encoding="utf-8"
        )
        actual = [b.id for b in load_workbook(io.BytesIO(output)).active.row_breaks.brk]
        assert actual == breaks
        report["cases"].append(
            {
                "case": name,
                "ranges": [p["range"] for p in plan["pages"]],
                "manualBreaks": actual,
                "commonScale": plan["commonScale"],
                "xlsxSha256": hashlib.sha256(output).hexdigest(),
            }
        )
        if name == "two-pages":
            target = output, plan
    assert (args.output / "source.xlsx").read_bytes() == content
    assert (args.output / "version-1.xlsx").read_bytes() == (
        args.output / "two-pages.xlsx"
    ).read_bytes()
    try:
        assert target is not None
        pdf = render_print_pdf(*target)
        (args.output / "two-pages.pdf").write_bytes(pdf)
        report["render"] = {
            "status": "GENERATED",
            "case": "two-pages",
            "pageCountAndPaperChecked": True,
            "cellRangeVisualCheck": "NOT_PERFORMED",
        }
    except (AppError, OSError, ValueError, RuntimeError) as exc:
        report["render"] = {
            "status": "FAILED",
            "case": "two-pages",
            "code": exc.code if isinstance(exc, AppError) else type(exc).__name__,
            "policy": "AppContainer required; no fallback",
            "pageCountAndCellRanges": "UNVERIFIED",
        }
    report["sourceUnchanged"] = (
        hashlib.sha256((args.output / "source.xlsx").read_bytes()).hexdigest() == source_sha
    )
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
