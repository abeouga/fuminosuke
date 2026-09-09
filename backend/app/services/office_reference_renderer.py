"""Restricted, diagnostic Office rendering. Never changes a source workbook.

This is not a security sandbox: production qualification stays disabled until
network/process resource isolation is independently established on the host.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import re
import shutil
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import BadZipFile, ZipFile

from app.core.errors import AppError
from app.domain.print_layout import print_error
from app.services.renderer_appcontainer import container, render_environment
from app.services.renderer_dependencies import LoadedImages
from app.services.renderer_process import capture_contained, renderer_mutex, run_contained
from app.services.renderer_raster import rasterize
from app.services.renderer_temp import render_directory

RENDERER_SCHEMA = "libreoffice-contained-diagnostic-v2"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_PART_BYTES = 16 * 1024 * 1024
MAX_RENDER_PAGES = 10
MAX_RASTER_PIXELS = 20_000_000
RENDER_TIMEOUT_SECONDS = 45
_RENDER_LOCK = threading.Lock()
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _unsupported(message: str) -> None:
    raise print_error(message, "UNSUPPORTED_XLSX_FEATURE")


def inspect_package(content: bytes) -> dict[str, bytes]:
    """Deny active content before Office sees anything, including unused sheets."""
    if len(content) > MAX_SOURCE_BYTES:
        _unsupported("XLSX exceeds the compressed size limit.")
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if (len(entries) > 5000 or len({e.filename for e in entries}) != len(entries)
                    or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES
                    or any(e.file_size > MAX_PART_BYTES or e.flag_bits & 1 for e in entries)):
                _unsupported("Invalid or excessive ZIP resources.")
            parts = {e.filename: archive.read(e) for e in entries}
    except (BadZipFile, RuntimeError) as exc:
        raise print_error("Invalid XLSX package.", "UNSUPPORTED_XLSX_FEATURE") from exc
    for name, data in parts.items():
        if (name.startswith("/") or ".." in name.split("/") or "\\" in name
                or not name.endswith((".xml", ".rels"))):
            _unsupported("Non-XML parts, including images and embedded objects, are unsupported.")
        if b"\x00" in data:
            _unsupported("Only UTF-8 XML is supported by the preserving adapter.")
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise print_error("Only UTF-8 XML is supported.", "UNSUPPORTED_XLSX_FEATURE") from exc
        if any(token in data.upper() for token in (b"<!DOCTYPE", b"<!ENTITY")):
            _unsupported("XML entities are unsupported.")
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise print_error("Malformed XML.", "UNSUPPORTED_XLSX_FEATURE") from exc
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag in {"f", "externalReferences", "connections", "oleObjects", "controls",
                       "drawing", "legacyDrawing", "picture", "AlternateContent", "extLst"}:
                _unsupported(f"Unsupported active or advanced XML element: {tag}.")
            if tag == "Relationship" and (
                node.get("TargetMode", "").lower() == "external"
                or re.match(r"(?:[a-zA-Z][\w+.-]*:|//)", node.get("Target", ""))
            ):
                _unsupported("External relationships are not allowed.")
            if tag == "Override" and any(word in node.get("ContentType", "").lower()
                                         for word in ("macro", "vba", "activex", "ole")):
                _unsupported("Executable package content is not allowed.")
            if tag in {"row", "col"} and node.get("hidden") in {"1", "true"}:
                _unsupported("Hidden dimensions need a separately verified logical geometry model.")
            if tag == "definedName" and node.get("name") != "_xlnm.Print_Area":
                _unsupported("Defined names other than print areas are not yet supported.")
            if tag == "headerFooter" and any((c.text or "").strip() for c in node):
                _unsupported("Headers and footers are unsupported.")
    return parts


def page_copy(content: bytes, plan: dict[str, Any], page: dict[str, Any]) -> bytes:
    parts = inspect_package(content)
    if sha(content) != plan["sourceSha256"]:
        raise print_error("Source SHA changed.", "SOURCE_CHANGED", 409)
    workbook = ET.fromstring(parts["xl/workbook.xml"])
    sheets = workbook.findall(f"{S}sheets/{S}sheet")
    # Avoid changing visibility, print selection or active tabs in other sheets.
    if len(sheets) != 1 or sheets[0].get("name") != page["sheetName"]:
        _unsupported("Diagnostic renderer currently requires exactly one sheet.")
    rel_id = sheets[0].get(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    relations = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
    targets = [r.get("Target", "") for r in relations if r.get("Id") == rel_id]
    if len(targets) != 1:
        _unsupported("Invalid worksheet relationship.")
    target = targets[0]
    path = target.lstrip("/") if target.startswith("/") else "xl/" + target
    if path not in parts:
        _unsupported("Worksheet part missing.")
    # Only the known unprefixed serialization is accepted for byte surgery.
    # Unknown namespace serialization is rejected, not normalized with openpyxl.
    sheet = parts[path]
    if (not re.search(rb'<worksheet\s[^>]*xmlns="' + S[1:-1].encode() + rb'"', sheet)
            or re.search(rb"</?[A-Za-z_][\w.-]*:", sheet)):
        _unsupported("Prefixed worksheet XML is not supported by the preserving adapter.")
    percent = plan["commonScale"] * 100
    if not math.isclose(percent, round(percent), abs_tol=1e-7):
        _unsupported("Office print scale is integral percent; saved common scale is fractional.")
    if not 10 <= round(percent) <= 100:
        _unsupported("Saved scale is outside the qualified diagnostic range.")
    # Strip only root print nodes and sheetPr/pageSetUpPr. Cell/style/formula
    # bytes and every other ZIP entry remain byte-for-byte identical.
    for tag in (b"printOptions", b"pageMargins", b"pageSetup", b"rowBreaks", b"colBreaks",
                b"headerFooter", b"pageSetUpPr"):
        sheet = re.sub(rb"<" + tag + rb"\b[^>]*(?:/>|>.*?</" + tag + rb">)", b"", sheet,
                       flags=re.DOTALL)
    margins = plan["margins"]
    options = (f'<printOptions horizontalCentered="{int(plan["horizontalCentering"])}" '
               f'verticalCentered="{int(plan["verticalCentering"])}"/>')
    margin_xml = '<pageMargins ' + ' '.join(
        f'{key}="{margins[key] / 25.4:.12f}"' for key in ("left", "right", "top", "bottom")
    ) + ' header="0" footer="0"/>'
    paper = {"A4": 9, "A3": 8, "Letter": 1}[plan["paperSize"]]
    setup = (f'<pageSetup paperSize="{paper}" orientation="{plan["orientation"]}" '
             f'scale="{round(percent)}" fitToWidth="0" fitToHeight="0"/>')
    # Accepted packages have no later drawing/extension elements; print nodes
    # precede optional ignored tail nodes by inserting after sheetData/merges.
    later = re.search(rb"<(?:customSheetViews|phoneticPr|conditionalFormatting|dataValidations|"
                      rb"hyperlinks|rowBreaks|colBreaks|customProperties|cellWatches|"
                      rb"ignoredErrors|smartTags|webPublishItems)\b", sheet)
    if later:
        _unsupported("Advanced worksheet elements require an ordering-aware adapter.")
    sheet = sheet.replace(b"</worksheet>", (options + margin_xml + setup).encode()
                          + b"</worksheet>")
    old_book = parts["xl/workbook.xml"]
    if b"<workbook " not in old_book:
        _unsupported("Prefixed workbook XML is unsupported.")
    book = re.sub(rb"<definedNames\b[^>]*(?:/>|>.*?</definedNames>)", b"", old_book,
                  flags=re.DOTALL)
    absolute_range = re.sub(r"([A-Z]+)([0-9]+)", r"$\1$\2", page["range"])
    ref = "'" + page["sheetName"].replace("'", "''") + "'!" + absolute_range
    names = ('<definedNames><definedName name="_xlnm.Print_Area" localSheetId="0">'
             + escape(ref) + '</definedName></definedNames>').encode()
    book = book.replace(b"</sheets>", b"</sheets>" + names)
    output = io.BytesIO()
    with ZipFile(io.BytesIO(content)) as original, ZipFile(output, "w") as result:
        for entry in original.infolist():
            result.writestr(entry, sheet if entry.filename == path else
                            book if entry.filename == "xl/workbook.xml" else parts[entry.filename])
    return output.getvalue()


def renderer_identity() -> dict[str, Any]:
    found = shutil.which("soffice.com") or shutil.which("soffice")
    if not found:
        candidate = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        candidate = candidate / "LibreOffice/program/soffice.com"
        found = str(candidate) if candidate.is_file() else None
    if not found:
        raise print_error("LibreOffice is not installed.", "RENDERER_UNAVAILABLE", 409)
    try:
        with renderer_mutex():
            version = capture_contained([found, "--version"])
    except OSError as exc:
        raise print_error("LibreOffice version check failed.", "RENDERER_UNAVAILABLE", 409) from exc
    return {"type": "LibreOffice", "version": version.decode(errors="replace").strip(),
            "adapter": RENDERER_SCHEMA, "executableSha256": sha(Path(found).read_bytes()),
            "executable": found, "dpi": 150, "pdfRasterizer": "pypdfium2",
            "productionIsolationVerified": False}


def render_office(
    content: bytes, plan: dict[str, Any], *, geometry_only: bool = False, dpi: int = 150,
    execution_metrics: list[dict[str, Any]] | None = None,
    sandboxed: bool = False,
) -> tuple[dict[str, Any], list[bytes]]:
    if dpi not in {96, 150, 300}:
        raise print_error("Unsupported diagnostic DPI.", "PAGE_GEOMETRY_MISMATCH")
    if not 1 <= len(plan["pages"]) <= MAX_RENDER_PAGES:
        raise print_error("Diagnostic page limit exceeded.", "PAGE_COUNT_MISMATCH")
    if not _RENDER_LOCK.acquire(blocking=False):
        raise print_error("Another Office render is running.", "RENDERER_BUSY", 409)
    try:
        identity = renderer_identity()
        identity["dpi"] = dpi
        images = []
        # Short, ASCII, unique filenames avoid source-name and Windows long-path issues.
        with renderer_mutex(), render_directory() as root:
            for index, saved_page in enumerate(plan["pages"]):
                data = page_copy(content, plan, saved_page)
                directory = root / str(index)
                directory.mkdir()
                source = directory / "page.xlsx"
                source.write_bytes(data)
                pdf_path, metrics = convert_office_file(
                    directory, identity, geometry_only=geometry_only, sandboxed=sandboxed,
                    execution_metrics=execution_metrics)
                if geometry_only:
                    images.append(pdf_path.read_bytes())
                    continue
                image, raster_metrics = rasterize(directory, dpi)
                metrics["rasterizer"] = raster_metrics
                images.append(image)
        return identity, images
    finally:
        _RENDER_LOCK.release()


def convert_office_file(
    directory: Path, identity: dict[str, Any], *, geometry_only: bool = False,
    sandboxed: bool = False,
    execution_metrics: list[dict[str, Any]] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Convert fixed page.xlsx using the existing profile and process containment."""
    source = directory / "page.xlsx"
    profile = directory / "profile"
    (profile / "user").mkdir(parents=True)
    (profile / "user/registrymodifications.xcu").write_text(
        '<?xml version="1.0"?><oor:items '
        'xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="DisableMacrosExecution" oor:op="fuse">'
        '<value>true</value></prop>'
        '<prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop>'
        '</item></oor:items>', encoding="utf-8")
    command = [identity["executable"], "--headless", "--nologo", "--nodefault",
               "--norestore", "--nofirststartwizard",
               f"-env:UserInstallation={profile.as_uri()}", "--convert-to",
               "fods" if geometry_only else "pdf:calc_pdf_Export",
               "--outdir", str(directory), str(source)]
    try:
        modules = LoadedImages()
        with container(directory) if sandboxed else nullcontext(None) as ac:
            metrics = run_contained(command, timeout=RENDER_TIMEOUT_SECONDS,
                                    loaded_images=modules, appcontainer=ac,
                                    environment=render_environment(directory))
        metrics["networkPolicy"] = (
            "appcontainer-zero-capabilities-v1" if sandboxed else "uncontained-v1")
        metrics["loadedDependencies"] = modules.report()
    except AppError as exc:
        if exc.code == "RENDER_TIMEOUT":
            raise print_error("Office render timed out.", "RENDER_FAILED") from exc
        raise
    if execution_metrics is not None:
        execution_metrics.append(metrics)
    code = metrics["exitCode"]
    pdf_path = directory / ("page.fods" if geometry_only else "page.pdf")
    if code or not pdf_path.is_file() or pdf_path.stat().st_size > MAX_SOURCE_BYTES:
        raise print_error("Office PDF conversion failed.", "RENDER_FAILED")
    return pdf_path, metrics
