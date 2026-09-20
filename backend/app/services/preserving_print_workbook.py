"""Explicit v1 print adapter. Preserve cells, styles, hidden sheets and passive metadata.

Not a geometry qualification. Historical plans continue through their original adapter.
Only the selected worksheet's print nodes and its Print_Area name are changed.
"""

from __future__ import annotations

import hashlib
import io
import math
import posixpath
import re
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import BadZipFile, ZipFile

from openpyxl.utils.cell import range_boundaries  # type: ignore[import-untyped]

from app.domain.print_layout import MAX_PAGES, PrintLayoutRequest, print_error
from app.services import print_xml
from app.services.print_layout_source import read_source

ADAPTER = "ooxml-print-preserving-v1"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
S = MAIN + "}"
_EXTENSIONS = {
    "http://schemas.openxmlformats.org/markup-compatibility/2006": {
        "AlternateContent",
        "Choice",
        "Fallback",
    },
    "http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac": {"absPath"},
    "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main": {"slicerStyles"},
    "http://schemas.microsoft.com/office/spreadsheetml/2010/11/main": {
        "workbookPr",
        "timelineStyles",
    },
    "http://schemas.microsoft.com/office/spreadsheetml/2014/revision": {"revisionPtr"},
    "http://schemas.microsoft.com/office/spreadsheetml/2024/workbookCompatibilityVersion": {
        "version"
    },
    "http://schemas.microsoft.com/office/thememl/2012/main": {"themeFamily"},
}
_NAMESPACES = {
    MAIN,
    "http://schemas.openxmlformats.org/drawingml/2006/main",
    "http://schemas.openxmlformats.org/package/2006/relationships",
    "http://schemas.openxmlformats.org/package/2006/content-types",
    "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "http://purl.org/dc/elements/1.1/",
    "http://purl.org/dc/terms/",
}
_RELATIONS = {
    REL + "/" + name
    for name in (
        "officeDocument",
        "worksheet",
        "styles",
        "theme",
        "sharedStrings",
        "extended-properties",
        "printerSettings",
    )
} | {"http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"}
_DENIED = {
    "f",
    "formula",
    "externalReferences",
    "connections",
    "oleObjects",
    "controls",
    "drawing",
    "legacyDrawing",
    "legacyDrawingHF",
    "picture",
    "hyperlinks",
    "webPublishItems",
    "pivotTable",
    "queryTable",
    "tableParts",
}


def _reject(detail: str) -> None:
    raise print_error(detail, "UNSUPPORTED_XLSX_FEATURE")


def inspect(content: bytes) -> dict[str, bytes]:
    """Bound the entire package and deny active content, including unused parts."""
    if not content or len(content) > 64 * 1024 * 1024:
        _reject("Invalid XLSX source size.")
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 5000
                or len({e.filename for e in entries}) != len(entries)
                or sum(e.file_size for e in entries) > 128 * 1024 * 1024
                or any(e.file_size > 16 * 1024 * 1024 or e.flag_bits & 1 for e in entries)
            ):
                _reject("Invalid or excessive ZIP resources.")
            parts = {e.filename: archive.read(e) for e in entries}
    except (BadZipFile, RuntimeError) as exc:
        raise print_error("Invalid XLSX package.", "UNSUPPORTED_XLSX_FEATURE") from exc
    binary = set()
    declared_printers = set()
    for name, data in parts.items():
        if (
            name.startswith("/")
            or ".." in name.split("/")
            or "\\" in name
            or posixpath.normpath(name) != name
        ):
            _reject("Invalid package path.")
        if not name.endswith((".xml", ".rels")):
            if not re.fullmatch(r"xl/printerSettings/printerSettings[1-9]\d*\.bin", name):
                _reject("Unsupported binary package part.")
            binary.add(name)
            continue
        try:
            print_xml.parse(data)
            root = ET.fromstring(data)
        except (ValueError, ET.ParseError) as exc:
            raise print_error("Invalid package XML.", "UNSUPPORTED_XLSX_FEATURE") from exc
        parents = {child: parent for parent in root.iter() for child in parent}
        for node in root.iter():
            namespace, _, tag = node.tag[1:].rpartition("}")
            if namespace not in _NAMESPACES and tag not in _EXTENSIONS.get(namespace, set()):
                _reject("Unsupported XML extension element.")
            if tag in _DENIED:
                _reject(f"Unsupported active XML element: {tag}.")
            if tag == "Relationship" and (
                node.get("TargetMode", "").lower() == "external"
                or node.get("Type") not in _RELATIONS
                or re.match(r"(?:[a-zA-Z][\w+.-]*:|//)", node.get("Target", ""))
                or "\\" in node.get("Target", "")
            ):
                _reject("Unsupported or external package relationship.")
            if tag in {"formula1", "formula2"}:
                parent = parents.get(node)
                value = (node.text or "").strip()
                if (
                    parent is None
                    or parent.tag != "{" + MAIN + "}dataValidation"
                    or not re.fullmatch(r'(?:-?\d+(?:\.\d+)?|"(?:[^\"]|\"\")*")', value)
                ):
                    _reject("Only literal data-validation limits and lists are supported.")
            if tag in {"Override", "Default"}:
                kind = node.get("ContentType", "").lower()
                if any(token in kind for token in ("macro", "vba", "activex", "ole")):
                    _reject("Executable package content is not allowed.")
                if (
                    kind
                    == "application/vnd.openxmlformats-officedocument.spreadsheetml.printersettings"
                ):
                    if tag == "Override":
                        declared_printers.add(node.get("PartName", "").lstrip("/"))
                    elif node.get("Extension") == "bin":
                        declared_printers.update(
                            p
                            for p in parts
                            if re.fullmatch(r"xl/printerSettings/printerSettings[1-9]\d*\.bin", p)
                        )
            if tag == "definedName":
                value = (node.text or "").strip()
                # Inert names are preserved, never evaluated. DDE, functions,
                # named formulas and external workbook references remain denied.
                cell_range = r"\$?[A-Z]{1,3}\$?[1-9]\d*(?::\$?[A-Z]{1,3}\$?[1-9]\d*)?"
                sheet = r"(?:'(?:[^'\[\]]|'')+'|[^'!\[\]\s]+)!"
                if not (
                    value == "#REF!"
                    or re.fullmatch(r"-?\d+(?:\.\d+)?", value)
                    or re.fullmatch(sheet + cell_range, value)
                ):
                    _reject("Unsupported defined-name expression.")
                if node.get("name") == "_xlnm.Print_Titles":
                    _reject("Repeating print titles are not supported.")
            if tag == "headerFooter" and any((c.text or "").strip() for c in node):
                _reject("Headers and footers are unsupported.")
    if binary != declared_printers:
        _reject("Undeclared or missing printer settings part.")
    return parts


def _one(
    parent: print_xml.Element, name: str, *, required: bool = True
) -> print_xml.Element | None:
    nodes = parent.named(S + name)
    if len(nodes) > 1 or (required and not nodes):
        raise ValueError("Invalid duplicate or missing XML element")
    return nodes[0] if nodes else None


def _sheet_path(parts: dict[str, bytes], book: print_xml.Element, name: str) -> tuple[str, int]:
    sheets_node = _one(book, "sheets")
    assert sheets_node is not None
    sheets = sheets_node.named(S + "sheet")
    matching = [(i, s) for i, s in enumerate(sheets) if s.attrs.get("name") == name]
    if len(matching) != 1:
        raise ValueError("Missing or ambiguous selected sheet")
    index, selected = matching[0]
    # Existing hidden management sheets are preserved. Do not silently hide a
    # visible user's sheet or let Office print a second sheet into this document.
    if selected.attrs.get("state", "visible") != "visible" or any(
        s.attrs.get("state", "visible") not in {"hidden", "veryHidden"}
        for i, s in enumerate(sheets)
        if i != index
    ):
        _reject("Exactly one visible selected sheet is required for this print document.")
    rel_id = selected.attrs.get(REL + "}id")
    rels = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
    targets = [
        r.get("Target", "")
        for r in rels
        if r.get("Id") == rel_id and r.get("Type") == REL + "/worksheet"
    ]
    if len(targets) != 1:
        raise ValueError("Invalid worksheet relationship")
    target = targets[0]
    path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
    if not path.startswith("xl/worksheets/") or path not in parts:
        raise ValueError("Missing worksheet part")
    return path, index


def workbook_copy(content: bytes, plan: dict[str, Any]) -> bytes:
    """Generate only an explicitly versioned, integer-scale print copy."""
    parts = inspect(content)
    if hashlib.sha256(content).hexdigest() != plan.get("sourceSha256"):
        raise print_error("Source SHA changed.", "SOURCE_CHANGED", 409)
    try:
        if plan.get("printAdapter") != ADAPTER:
            raise ValueError("Print adapter must be explicitly selected")
        pages = plan["pages"]
        if (
            plan["schemaVersion"] != 1
            or plan["targetPagesWide"] != 1
            or not 1 <= len(pages) <= MAX_PAGES
        ):
            raise ValueError("Invalid saved page structure")
        settings = PrintLayoutRequest.model_validate(
            {
                "baseRevision": "0" * 64,
                "paperSize": plan["paperSize"],
                "orientation": plan["orientation"],
                "margins": plan["margins"],
                "horizontalCentering": plan["horizontalCentering"],
                "verticalCentering": plan["verticalCentering"],
            }
        )
        scale = float(plan["commonScale"]) * 100
        if not math.isfinite(scale) or not math.isclose(scale, round(scale), abs_tol=1e-7):
            _reject("Office print scale is integral percent; saved common scale is fractional.")
        if not 10 <= round(scale) <= 100:
            _reject("Saved print scale is outside the supported range.")
        book = print_xml.parse(parts["xl/workbook.xml"])
        if book.name != S + "workbook":
            raise ValueError("Invalid workbook root")
        path, sheet_index = _sheet_path(parts, book, plan["sheetName"])
        source = read_source(content, plan["sheetName"], {"cells": []}, draft_support=True)
        if source["unsupported"]:
            _reject(" / ".join(source["unsupported"]))
        next_row = source["startRow"]
        for number, page in enumerate(pages, 1):
            bounds = (page["startCol"], page["startRow"], page["endCol"], page["endRow"])
            if (
                page["page"] != number
                or page["sheetName"] != plan["sheetName"]
                or page["startRow"] != next_row
                or page["endRow"] < next_row
                or page["startCol"] != source["startCol"]
                or page["endCol"] != source["endCol"]
                or range_boundaries(page["range"]) != bounds
                or (number < len(pages) and page["endRow"] not in source["validBreakRows"])
            ):
                raise ValueError("Invalid saved page boundary")
            next_row = page["endRow"] + 1
        if next_row != source["endRow"] + 1:
            raise ValueError("Incomplete saved pages")
        sheet_bytes = _print_nodes(parts[path], settings, round(scale), pages)
        whole_range = pages[0]["range"].split(":")[0] + ":" + pages[-1]["range"].split(":")[-1]
        book_bytes = _print_area(
            parts["xl/workbook.xml"], book, sheet_index, plan["sheetName"], whole_range
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise print_error("保存済みの印刷ページ設定が不正です。", "PRINT_LAYOUT_INVALID") from exc
    output = io.BytesIO()
    with ZipFile(io.BytesIO(content)) as archive, ZipFile(output, "w") as result:
        for entry in archive.infolist():
            result.writestr(
                entry,
                sheet_bytes
                if entry.filename == path
                else book_bytes
                if entry.filename == "xl/workbook.xml"
                else parts[entry.filename],
            )
    return output.getvalue()


def _print_nodes(
    data: bytes, settings: PrintLayoutRequest, percent: int, pages: list[dict[str, Any]]
) -> bytes:
    root = print_xml.parse(data)
    if root.name != S + "worksheet":
        raise ValueError("Invalid worksheet root")
    prefix = root.prefix.decode()
    changes = []
    print_tags = {
        "printOptions",
        "pageMargins",
        "pageSetup",
        "headerFooter",
        "rowBreaks",
        "colBreaks",
    }
    for tag in print_tags:
        node = _one(root, tag, required=False)
        if node:
            changes.append((node.start, node.end, b""))
    properties = _one(root, "sheetPr", required=False)
    if properties:
        setup = _one(properties, "pageSetUpPr", required=False)
        if setup:
            changes.append((setup.start, setup.end, b""))
    # OOXML CT_Worksheet order: print nodes follow hyperlinks, and precede all
    # break/drawing/extension tail nodes. Existing earlier nodes are not moved.
    before = {
        "sheetPr",
        "dimension",
        "sheetViews",
        "sheetFormatPr",
        "cols",
        "sheetData",
        "sheetCalcPr",
        "sheetProtection",
        "protectedRanges",
        "scenarios",
        "autoFilter",
        "sortState",
        "dataConsolidate",
        "customSheetViews",
        "mergeCells",
        "phoneticPr",
        "conditionalFormatting",
        "dataValidations",
        "hyperlinks",
    }
    anchor = next(
        (c.start for c in root.children if c.name not in {S + t for t in before}), root.close_start
    )
    options = (
        f'<{prefix}printOptions horizontalCentered="{int(settings.horizontal_centering)}" '
        f'verticalCentered="{int(settings.vertical_centering)}"/>'
    )
    margins = settings.margins.model_dump()
    margin_xml = (
        f"<{prefix}pageMargins "
        + " ".join(
            f'{key}="{margins[key] / 25.4:.12f}"' for key in ("left", "right", "top", "bottom")
        )
        + ' header="0" footer="0"/>'
    )
    paper = {"A4": 9, "A3": 8, "Letter": 1}[settings.paper_size]
    setup_xml = (
        f'<{prefix}pageSetup paperSize="{paper}" orientation="{settings.orientation}" '
        f'scale="{percent}" fitToWidth="0" fitToHeight="0"/>'
    )
    count = len(pages) - 1
    breaks = (
        (
            f'<{prefix}rowBreaks count="{count}" manualBreakCount="{count}">'
            + "".join(
                f'<{prefix}brk id="{p["endRow"]}" min="0" max="16383" man="1"/>' for p in pages[:-1]
            )
            + f"</{prefix}rowBreaks>"
        )
        if count
        else ""
    )
    changes.append((anchor, anchor, (options + margin_xml + setup_xml + breaks).encode()))
    result = print_xml.edits(data, changes)
    print_xml.parse(result)
    return result


def _print_area(data: bytes, book: print_xml.Element, index: int, sheet: str, area: str) -> bytes:
    names = _one(book, "definedNames", required=False)
    prefix = (names or book).prefix.decode()
    absolute = re.sub(r"([A-Z]+)([0-9]+)", r"$\1$\2", area)
    reference = "'" + sheet.replace("'", "''") + "'!" + absolute
    definition = (
        f'<{prefix}definedName name="_xlnm.Print_Area" localSheetId="{index}">'
        + escape(reference)
        + f"</{prefix}definedName>"
    ).encode()
    changes = []
    if names:
        old = [
            n
            for n in names.named(S + "definedName")
            if n.attrs.get("name") == "_xlnm.Print_Area"
            and n.attrs.get("localSheetId") == str(index)
        ]
        if len(old) > 1:
            raise ValueError("Duplicate print area")
        if old:
            changes.append((old[0].start, old[0].end, definition))
        else:
            changes.append(print_xml.insert_children(data, names, definition))
    else:
        before = {
            "fileVersion",
            "fileSharing",
            "workbookPr",
            "workbookProtection",
            "bookViews",
            "sheets",
            "functionGroups",
            "externalReferences",
        }
        # Skip passive compatibility wrappers before the sheets as well.
        sheets = _one(book, "sheets")
        assert sheets is not None
        anchor = next(
            (
                n.start
                for n in book.children
                if n.start >= sheets.end and n.name not in {S + t for t in before}
            ),
            book.close_start,
        )
        wrapped = (
            f"<{prefix}definedNames>".encode() + definition + f"</{prefix}definedNames>".encode()
        )
        changes.append((anchor, anchor, wrapped))
    result = print_xml.edits(data, changes)
    print_xml.parse(result)
    return result
