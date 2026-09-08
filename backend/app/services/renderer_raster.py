"""Backend broker for the bounded PDFium worker. No native PDF parsing here."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from app.domain.print_layout import print_error
from app.services.renderer_appcontainer import render_environment
from app.services.renderer_dependencies import LoadedImages
from app.services.renderer_process import run_contained

RASTER_POLICY = "pdfium-helper-job-512m-2proc-20cpu-30s-v1"


def rasterize(directory: Path, dpi: int) -> tuple[bytes, dict[str, Any]]:
    if dpi not in {96, 150, 300}:
        raise print_error("Invalid raster DPI.", "RENDER_FAILED")
    modules = LoadedImages()
    result = run_contained(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("renderer_raster_worker.py")),
            str(directory),
            str(dpi),
        ],
        timeout=30,
        memory_bytes=512 * 1024 * 1024,
        process_limit=2,
        loaded_images=modules,
        environment=render_environment(directory),
    )
    # Windows venv launcher may create its base interpreter; cap 2 includes it.
    report = directory / "raster.json"
    data: dict[str, Any] = {}
    if report.is_file() and report.stat().st_size <= 1024 * 1024:
        data = json.loads(report.read_text(encoding="utf-8"))
    if result["exitCode"] or data.get("status") != "PASS":
        code = (
            "PAGE_COUNT_MISMATCH" if data.get("code") == "PAGE_COUNT_MISMATCH" else "RENDER_FAILED"
        )
        raise print_error("PDF raster helper failed.", code)
    image = directory / "page.png"
    if not image.is_file() or image.stat().st_size > 64 * 1024 * 1024:
        raise print_error("Raster output exceeds limit.", "RENDER_FAILED")
    return image.read_bytes(), {
        **result,
        **data,
        "policy": RASTER_POLICY,
        "loadedDependencies": modules.report(),
        "networkIsolated": False,
    }
