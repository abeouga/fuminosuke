"""Bounded local resource/network evidence. No external endpoint or user workbook."""
from __future__ import annotations

import ctypes
import importlib
import json
import socket
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.office_reference_renderer import render_office, sha
from app.services.renderer_process import run_contained
from app.services.renderer_temp import private_root


def main():
    output = Path(__file__).resolve().parents[2] / "docs/reference-renderer-evidence/environment"
    output.mkdir(exist_ok=True)
    xl = importlib.import_module("openpyxl")
    styles = importlib.import_module("openpyxl.styles")
    records = []
    for name, rows, cols, ranges, scale in [
        ("three-pages", 60, 3, ["A1:C20", "A21:C40", "A41:C60"], 1),
        ("20000-cells", 200, 100, ["A1:CV200"], .1),
    ]:
        book = xl.Workbook()
        sheet = book.active
        sheet.title = "Resource"
        sheet.print_area = f"A1:{xl.utils.get_column_letter(cols)}{rows}"
        for col in range(1, cols + 1):
            sheet.column_dimensions[xl.utils.get_column_letter(col)].width = 5
        border = styles.Border(**{k: styles.Side(style="thin")
                                  for k in ["left", "right", "top", "bottom"]})
        for row in range(1, rows + 1):
            sheet.row_dimensions[row].height = 10
            for col in range(1, cols + 1):
                sheet.cell(row, col).border = border
        buffer = BytesIO()
        book.save(buffer)
        content = buffer.getvalue()
        plan = {"sourceSha256": sha(content), "commonScale": scale, "paperSize": "A4",
                "orientation": "portrait", "margins": dict(left=10, right=10, top=10, bottom=10),
                "horizontalCentering": False, "verticalCentering": False,
                "pages": [{"sheetName": "Resource", "range": r} for r in ranges]}
        metrics = []
        identity, images = render_office(content, plan, execution_metrics=metrics)
        (output / f"{name}.xlsx").write_bytes(content)
        records.append({"fixture": name, "cells": rows * cols, "sourceSha256": sha(content),
                        "renderer": identity, "execution": metrics, "pageCount": len(images),
                        "imageHashes": [sha(i) for i in images]})
        print(name, metrics, flush=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        result = run_contained([sys._base_executable, "-c", "import socket;"
                                f"socket.create_connection({listener.getsockname()!r},2).close()"])
    data = {"environmentQualified": False, "administrator": bool(
        ctypes.windll.shell32.IsUserAnAdmin()), "resources": records,
        "network": {"endpoint": "loopback-only", "connected": result["exitCode"] == 0,
                    "isolationProven": False, "internetAttempts": 0},
        "tempRoot": str(private_root()),
        "limitsQualified": False}
    (output / "resource-probes.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
