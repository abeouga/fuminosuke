"""Bounded, fixed-file PDF validation. No PDF parser runs in the backend."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    if len(sys.argv) != 3 or not sys.argv[2].isdigit() or not 1 <= int(sys.argv[2]) <= 50:
        return 2
    directory = Path(sys.argv[1]).resolve(strict=True)
    source = directory / "page.pdf"
    report = directory / "print-pdf.json"
    if (
        report.exists()
        or not source.is_file()
        or not 0 < source.stat().st_size <= 64 * 1024 * 1024
        or source.is_symlink()
        or directory.is_symlink()
        or getattr(source.stat(), "st_file_attributes", 0) & 0x400
        or getattr(directory.stat(), "st_file_attributes", 0) & 0x400
    ):
        return 2
    from pypdf import PdfReader

    result: dict[str, Any]
    try:
        pdf = PdfReader(source, strict=True)
        if pdf.is_encrypted or len(pdf.pages) != int(sys.argv[2]):
            result = {"status": "FAIL", "code": "PAGE_COUNT_MISMATCH"}
        else:
            result = {
                "status": "PASS",
                "pageCount": len(pdf.pages),
                "sizes": [[float(p.mediabox.width), float(p.mediabox.height)] for p in pdf.pages],
            }
    except Exception:
        result = {"status": "FAIL", "code": "RENDER_FAILED"}
    report.write_text(json.dumps(result), encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
