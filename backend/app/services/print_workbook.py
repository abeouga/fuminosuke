"""Print-only copies of saved vertical page ranges; never re-plan OCR geometry."""

from __future__ import annotations

import io
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from openpyxl.utils.cell import range_boundaries  # type: ignore[import-untyped]

from app.domain.print_layout import MAX_PAGES, PrintLayoutRequest, print_error
from app.services.office_reference_renderer import S, inspect_package, page_copy
from app.services.print_layout_source import read_source


def workbook_copy(content: bytes, plan: dict[str, Any]) -> bytes:
    """Reuse the preserving Office adapter, then add the saved manual row breaks."""
    try:
        inspect_package(content)
        pages = plan["pages"]
        if (
            plan["schemaVersion"] != 1
            or plan["targetPagesWide"] != 1
            or not 1 <= len(pages) <= MAX_PAGES
        ):
            raise ValueError("Unsupported page structure")
        # Validate settings without calculating new page ranges or transforms.
        PrintLayoutRequest.model_validate(
            {
                "baseRevision": "0" * 64,
                "paperSize": plan["paperSize"],
                "orientation": plan["orientation"],
                "margins": plan["margins"],
                "horizontalCentering": plan["horizontalCentering"],
                "verticalCentering": plan["verticalCentering"],
                "breakAfterRows": [p["endRow"] for p in pages[:-1]],
            }
        )
        source = read_source(content, plan["sheetName"], {"cells": []})
        if source["unsupported"]:
            raise print_error(" / ".join(source["unsupported"]), "PRINT_LAYOUT_UNSUPPORTED")
        next_row = source["startRow"]
        for number, page in enumerate(pages, 1):
            bounds = (page["startCol"], page["startRow"], page["endCol"], page["endRow"])
            if (
                page["page"] != number
                or page["sheetName"] != plan["sheetName"]
                or page["startRow"] != next_row
                or page["endRow"] < page["startRow"]
                or page["startCol"] != source["startCol"]
                or page["endCol"] != source["endCol"]
                or range_boundaries(page["range"]) != bounds
                or (number < len(pages) and page["endRow"] not in source["validBreakRows"])
            ):
                raise ValueError("Invalid saved page range")
            next_row = page["endRow"] + 1
        if next_row != source["endRow"] + 1:
            raise ValueError("Incomplete saved page ranges")
        whole = {
            **pages[0],
            "range": pages[0]["range"].split(":")[0] + ":" + pages[-1]["range"].split(":")[-1],
        }
        copied = page_copy(content, plan, whole)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise print_error("保存済みの印刷ページ設定が不正です。", "PRINT_LAYOUT_INVALID") from exc

    output = io.BytesIO()
    with ZipFile(io.BytesIO(copied)) as archive, ZipFile(output, "w") as result:
        # Change only the worksheet referenced by the verified workbook, even
        # when a package contains an unused worksheet part.
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        selected = book.find(f"{S}sheets/{S}sheet")
        assert selected is not None  # verified by page_copy
        relation_id = selected.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target", "") for r in relations if r.get("Id") == relation_id)
        sheet_path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        for entry in archive.infolist():
            data = archive.read(entry)
            if entry.filename == sheet_path:
                count = len(pages) - 1
                breaks = (
                    (
                        f'<rowBreaks count="{count}" manualBreakCount="{count}">'
                        + "".join(
                            f'<brk id="{p["endRow"]}" min="0" max="16383" man="1"/>'
                            for p in pages[:-1]
                        )
                        + "</rowBreaks>"
                    )
                    if count
                    else ""
                )
                data = data.replace(b"</worksheet>", breaks.encode() + b"</worksheet>")
            result.writestr(entry, data)
    return output.getvalue()
