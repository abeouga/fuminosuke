"""Reproducible local closure evidence. Never calls OCR or Google providers."""

from __future__ import annotations

import argparse
import ctypes
import importlib
import io
import json
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.errors import AppError
from app.services import office_reference_renderer as office
from app.services.reference_crop_safety import clearance
from app.services.renderer_appcontainer import container
from app.services.renderer_dependencies import LoadedImages
from app.services.renderer_environment import (
    environment_identity,
    environment_report,
    font_identity,
)
from app.services.renderer_process import run_contained
from app.services.renderer_temp import render_directory

ROOT = Path(__file__).resolve().parents[2] / "docs/reference-renderer-evidence"
OUT = ROOT / "closure"
LO_PYTHON = "C:/Program Files/LibreOffice/program/python.exe"


def originals_dir(argv: list[str] | None = None) -> Path:
    """Folder of the original workbooks whose hashes the evidence records.

    --originals-dir, or the XLSX_ORIGINALS_DIR environment variable.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--originals-dir", type=Path,
                        default=os.environ.get("XLSX_ORIGINALS_DIR") or None,
                        help="folder of the original .xlsx files (default: $XLSX_ORIGINALS_DIR)")
    folder = parser.parse_args(argv).originals_dir
    if folder is None:
        parser.error("set --originals-dir or XLSX_ORIGINALS_DIR")
    return Path(folder)


def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2), encoding="utf-8")


def fixture(rows, cols, ranges, scale=1, font="Calibri", text=False):
    xl = importlib.import_module("openpyxl")
    styles = importlib.import_module("openpyxl.styles")
    book = xl.Workbook()
    sheet = book.active
    sheet.title = "Closure"
    sheet.print_area = f"A1:{xl.utils.get_column_letter(cols)}{rows}"
    book._named_styles[0].font = styles.Font(name=font, sz=11)
    border = styles.Border(
        **{key: styles.Side(style="thin") for key in ["left", "right", "top", "bottom"]}
    )
    for column in range(1, cols + 1):
        sheet.column_dimensions[xl.utils.get_column_letter(column)].width = 5
    for row in range(1, rows + 1):
        sheet.row_dimensions[row].height = 10
        for column in range(1, cols + 1):
            sheet.cell(row, column).border = border
    if text:
        sheet["A1"] = "0123456789 ABCDEFG xyz"
        sheet["A1"].font = styles.Font(name=font, sz=11)
        sheet.column_dimensions["A"].width = 45
        sheet.row_dimensions[1].height = 25
    stream = io.BytesIO()
    book.save(stream)
    content = stream.getvalue()
    return content, {
        "sourceSha256": office.sha(content),
        "commonScale": scale,
        "paperSize": "A4",
        "orientation": "portrait",
        "margins": dict(left=10, right=10, top=10, bottom=10),
        "horizontalCentering": False,
        "verticalCentering": False,
        "pages": [{"sheetName": "Closure", "range": r} for r in ranges],
    }


def network():
    with socket.socket() as listener, render_directory() as directory:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        probe = (
            "import socket,json,pathlib\nr={}\ntry:\n"
            f" socket.create_connection({listener.getsockname()!r},1).close();r['connected']=True\n"
            "except OSError as e:r.update(connected=False,errorType=type(e).__name__,"
            "errno=e.errno,winerror=getattr(e,'winerror',None))\n"
            f"pathlib.Path({str(directory / 'probe.json')!r}).write_text(json.dumps(r))"
        )
        command = [LO_PYTHON, "-I", "-c", probe]
        ordinary = run_contained(command)
        ordinary["probe"] = json.loads((directory / "probe.json").read_text())
        listener.accept()[0].close()
        with container(directory) as ac:
            restricted = run_contained(command, appcontainer=ac)
            restricted["probe"] = json.loads((directory / "probe.json").read_text())
            restricted["capabilityCount"] = ac["capabilityCount"]
        assert ordinary["probe"]["connected"] and not restricted["probe"]["connected"]
        listener.settimeout(0.2)
        try:
            listener.accept()[0].close()
            raise AssertionError("Sandbox connected")
        except TimeoutError:
            pass
    return {
        "unsandboxed": ordinary,
        "sandboxed": restricted,
        "internetAttempts": 0,
        "endpoint": "loopback-only",
        "administrator": bool(ctypes.windll.shell32.IsUserAnAdmin()),
    }


def sandbox_office(content, plan):
    # A bounded diagnostic attempt; no unrestricted render fallback in this call.
    with render_directory() as directory, container(directory) as ac:
        source = directory / "page.xlsx"
        source.write_bytes(office.page_copy(content, plan, plan["pages"][0]))
        modules = LoadedImages()
        command = [
            "C:/Program Files/LibreOffice/program/soffice.com",
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "-env:UserInstallation=" + (directory / "profile").as_uri(),
            "--convert-to",
            "pdf:calc_pdf_Export",
            "--outdir",
            str(directory),
            str(source),
        ]
        try:
            result = run_contained(command, appcontainer=ac, timeout=15, loaded_images=modules)
        except AppError as exc:
            result = {"code": exc.code}
        result.update(
            pdfProduced=(directory / "page.pdf").is_file(),
            loadedDependencies=modules.report(),
            sourceSha256=office.sha(content),
            timeoutSeconds=15,
            networkCapabilities=[],
        )
    result["tempRemoved"] = not directory.exists()
    return result


def main():
    originals = originals_dir()
    OUT.mkdir(exist_ok=True)
    net = network()
    content, plan = fixture(10, 3, ["A1:C10"])
    net["office"] = sandbox_office(content, plan)
    save("network.json", net)
    print("network", net["sandboxed"]["probe"], net["office"].get("code"), flush=True)
    fonts = []
    for family in ["Calibri", "Arial", "Meiryo", "Yu Gothic", "Aptos"]:
        content, plan = fixture(2, 1, ["A1:A2"], font=family, text=True)
        metrics = []
        _, images = office.render_office(content, plan, execution_metrics=metrics)
        observed = metrics[0]["rasterizer"]["fonts"]
        matches = [
            c
            for font in observed
            for c in font["hostCandidates"]
            if c["family"] == family
            and c["allSimpleGlyphsMatch"]
            and font["simpleGlyphCount"] >= 10
        ]
        fonts.append(
            {
                "requested": family,
                "sourceSha256": office.sha(content),
                "normalResolution": font_identity(content),
                "pdfObserved": observed,
                "observedSimpleGlyphSelectionVerified": len(matches) == 1,
                "fullTemplateFontQualified": False,
                "execution": metrics,
            }
        )
        name = family.replace(" ", "")
        (OUT / (name + ".xlsx")).write_bytes(content)
        (OUT / (name + ".png")).write_bytes(images[0])
        print("font", family, [f["baseFont"] for f in observed], flush=True)
    save("fonts.json", fonts)
    resources = []
    for name, rows, cols, ranges, scale in [
        ("simple", 10, 3, ["A1:C10"], 1),
        ("two-pages", 40, 3, ["A1:C20", "A21:C40"], 1),
        ("three-pages", 60, 3, ["A1:C20", "A21:C40", "A41:C60"], 1),
        ("many-columns", 10, 100, ["A1:CV10"], 0.1),
        ("many-rows", 200, 3, ["A1:C200"], 0.1),
        ("20000-cells", 200, 100, ["A1:CV200"], 0.1),
    ]:
        content, plan = fixture(rows, cols, ranges, scale)
        metrics = []
        _, images = office.render_office(content, plan, execution_metrics=metrics)
        resources.append(
            {
                "fixture": name,
                "cells": rows * cols,
                "pageCount": len(images),
                "sourceSha256": office.sha(content),
                "execution": metrics,
                "officeNetworkIsolated": False,
                "rasterizerInBackend": False,
                "imageHashes": [office.sha(image) for image in images],
            }
        )
        print("resource", name, len(images), flush=True)
    save("resources.json", resources)
    wide = dict(
        widthMm=20,
        heightMm=10,
        contentInsetMm=2,
        neighborGapMm=2,
        borderStrokeMm=0.1,
        inkStrokeMm=0.3,
        paddingMm=0.5,
    )
    crop = [
        {
            "dpi": dpi,
            "wide": clearance(wide, 0.05 * dpi / 25.4, dpi),
            "narrow": clearance({**wide, "contentInsetMm": 0.25}, 0.05 * dpi / 25.4, dpi),
            "displaced20px": clearance(wide, 20, dpi),
        }
        for dpi in [96, 150, 300]
    ]
    save("crop-clearance.json", crop)
    original = json.loads((ROOT / "calibration/analysis.json").read_text())["originalHashes"]
    for record in original:
        path = originals / record["filename"]
        record["unchanged"] = office.sha(path.read_bytes()) == record["sha256"]
        try:
            office.inspect_package(path.read_bytes())
            record["rendererPreflight"] = "accepted"
        except AppError as exc:
            record["rendererPreflight"] = exc.code
    assert all(item["unchanged"] for item in original)
    save("originals.json", original)
    content = (ROOT / "calibration/explicit843/source.xlsx").read_bytes()
    report = environment_report(environment_identity(content))
    report["closureEvidence"] = {
        name: {"path": name, "sha256": office.sha((OUT / name).read_bytes())}
        for name in [
            "network.json",
            "fonts.json",
            "resources.json",
            "crop-clearance.json",
            "originals.json",
        ]
    }
    report["productionQualified"] = False
    save("environment.json", report)
    print("environment", report["fingerprint"], flush=True)


if __name__ == "__main__":
    main()
