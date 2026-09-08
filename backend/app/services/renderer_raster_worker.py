"""Fixed PDF -> one page PNG protocol. Executed only as an isolated child."""

from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path

MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 20_000_000


def main() -> int:
    # No code, module name, output path or arbitrary options in the protocol.
    if len(sys.argv) != 3 or sys.argv[2] not in {"96", "150", "300"}:
        return 2
    directory = Path(sys.argv[1]).resolve(strict=True)
    if directory.stat().st_file_attributes & 0x400:
        return 2
    source = directory / "page.pdf"
    if (
        not source.is_file()
        or source.stat().st_file_attributes & 0x400
        or not 0 < source.stat().st_size <= MAX_BYTES
    ):
        return 2
    for name in ["page.png", "raster.json"]:
        if (directory / name).exists():
            return 2
    # Import native PDF parser after the parent has assigned this child to its job.
    pdfium = importlib.import_module("pypdfium2")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    evidence = importlib.import_module("renderer_font_evidence")
    dpi = int(sys.argv[2])
    try:
        with pdfium.PdfDocument(str(source)) as pdf:
            if len(pdf) != 1:
                raise ValueError("PAGE_COUNT_MISMATCH")
            page = pdf[0]
            try:
                width, height = page.get_size()
                pixels = math.ceil(width * dpi / 72) * math.ceil(height * dpi / 72)
                if min(width, height) <= 0 or pixels > MAX_PIXELS:
                    raise ValueError("RASTER_LIMIT_EXCEEDED")
                fonts = evidence.pdf_fonts(source)
                bitmap = page.render(scale=dpi / 72)
                try:
                    image = bitmap.to_pil()
                    image.save(directory / "page.png", format="PNG")
                finally:
                    bitmap.close()
            finally:
                page.close()
        report = {"status": "PASS", "dpi": dpi, "pageCount": 1, "fonts": fonts}
    except Exception as exc:
        code = (
            str(exc)
            if isinstance(exc, ValueError)
            and str(exc) in {"PAGE_COUNT_MISMATCH", "RASTER_LIMIT_EXCEEDED"}
            else "RASTER_FAILED"
        )
        report = {"status": "FAIL", "code": code, "errorType": type(exc).__name__}
    (directory / "raster.json").write_text(json.dumps(report), encoding="utf-8")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
