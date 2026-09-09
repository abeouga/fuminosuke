"""Print PDF conversion. AppContainer is mandatory; no uncontained fallback."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

from app.domain.print_layout import PAPER_MM, print_error
from app.services import office_reference_renderer as office
from app.services.renderer_appcontainer import render_environment
from app.services.renderer_process import renderer_mutex, run_contained
from app.services.renderer_temp import render_directory


def validate_pdf(directory: Path, plan: dict[str, Any]) -> bytes:
    metrics = run_contained(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("print_pdf_worker.py")),
            str(directory),
            str(len(plan["pages"])),
        ],
        timeout=30,
        memory_bytes=512 * 1024 * 1024,
        process_limit=2,
        environment=render_environment(directory),
    )
    report = directory / "print-pdf.json"
    data = (
        json.loads(report.read_text(encoding="utf-8"))
        if (report.is_file() and report.stat().st_size <= 1024 * 1024)
        else {}
    )
    if metrics["exitCode"] or data.get("status") != "PASS":
        code = (
            "PAGE_COUNT_MISMATCH" if data.get("code") == "PAGE_COUNT_MISMATCH" else "RENDER_FAILED"
        )
        raise print_error("PDFのページ数・構造を確認できません。", code)
    paper = PAPER_MM[plan["paperSize"]]
    if plan["orientation"] == "landscape":
        paper = paper[::-1]
    sizes = data.get("sizes", [])
    if len(sizes) != len(plan["pages"]) or any(
        len(size) != 2
        or any(
            not math.isclose(a, b * 72 / 25.4, abs_tol=0.1)
            for a, b in zip(size, paper, strict=True)
        )
        for size in sizes
    ):
        raise print_error("PDFの用紙寸法が保存済み設定と一致しません。", "PAGE_GEOMETRY_MISMATCH")
    return (directory / "page.pdf").read_bytes()


def render_print_pdf(workbook: bytes, plan: dict[str, Any]) -> bytes:
    # The exact downloadable workbook, including all manual breaks, is converted.
    if not office._RENDER_LOCK.acquire(blocking=False):
        raise print_error("別のPDF変換を実行中です。", "RENDERER_BUSY", 409)
    try:
        identity = office.renderer_identity()
        with renderer_mutex(), render_directory() as directory:
            (directory / "page.xlsx").write_bytes(workbook)
            office.convert_office_file(directory, identity, sandboxed=True)
            return validate_pdf(directory, plan)
    finally:
        office._RENDER_LOCK.release()
