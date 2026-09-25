"""Conservative source bounds shared by XLSX import and print-layout reads."""

from __future__ import annotations

import io
import re
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from openpyxl.utils.cell import range_boundaries

MAX_SOURCE_CELLS = 20_000


def rectangular_cell_count(bounds: tuple[int, int, int, int]) -> int:
    min_col, min_row, max_col, max_row = bounds
    return (max_col - min_col + 1) * (max_row - min_row + 1)


def effective_worksheet_bounds(
    worksheet: Any,
    *,
    content: bytes | None = None,
    max_source_cells: int = MAX_SOURCE_CELLS,
) -> tuple[int, int, int, int]:
    """Return explicit print bounds or a conservatively trimmed used range.

    Explicit print areas are never shortened. An oversized calculated range is
    shortened only when the original OOXML is inspectable and every trailing
    cell is free of printed content. This prevents formatting residue from
    consuming the source-cell budget without hiding meaningful workbook data.
    """

    raw_print_area = worksheet.print_area
    if raw_print_area:
        matches = re.findall(
            r"\$?([A-Z]{1,3})\$?(\d+):\$?([A-Z]{1,3})\$?(\d+)",
            str(raw_print_area).upper(),
        )
        if matches:
            boundaries = [
                range_boundaries(f"{start_column}{start_row}:{end_column}{end_row}")
                for start_column, start_row, end_column, end_row in matches
            ]
            return (
                min(item[0] for item in boundaries),
                min(item[1] for item in boundaries),
                max(item[2] for item in boundaries),
                max(item[3] for item in boundaries),
            )

    bounds: tuple[int, int, int, int] = range_boundaries(worksheet.calculate_dimension())
    min_col, min_row, max_col, max_row = bounds
    if rectangular_cell_count(bounds) <= max_source_cells:
        return bounds
    if (
        content is None
        or worksheet.print_titles
        or worksheet.print_options.gridLines
        or worksheet.print_options.headings
        or worksheet.conditional_formatting
        or any(d.has_style for d in worksheet.row_dimensions.values())
        or any(d.has_style for d in worksheet.column_dimensions.values())
        or not empty_tail_is_inspectable(content)
    ):
        return bounds

    last_col, last_row = min_col, min_row
    # Sparse cells avoid expanding an oversized rectangle just to inspect it.
    for cell in worksheet._cells.values():
        border = cell.border
        visible_border = any(
            getattr(border, side, None) is not None
            and getattr(border, side).style is not None
            for side in (
                "left",
                "right",
                "top",
                "bottom",
                "diagonal",
                "start",
                "end",
                "vertical",
                "horizontal",
            )
        )
        if (
            cell.value is not None
            or visible_border
            or cell.fill.fill_type is not None
            or cell.comment
            or cell.hyperlink
        ):
            last_col = max(last_col, cell.column)
            last_row = max(last_row, cell.row)
    for merged in worksheet.merged_cells.ranges:
        last_col = max(last_col, merged.max_col)
        last_row = max(last_row, merged.max_row)
    return min_col, min_row, last_col, last_row


def empty_tail_is_inspectable(content: bytes) -> bool:
    """Return whether blank style-only tail cells can be safely ignored."""

    # openpyxl can discard unsupported drawings. Inspect original OOXML before
    # treating blank cells as an empty tail; an empty drawing part is harmless.
    sheet_tags = {
        "sheetPr",
        "dimension",
        "sheetViews",
        "sheetFormatPr",
        "cols",
        "sheetData",
        "mergeCells",
        "printOptions",
        "pageMargins",
        "pageSetup",
        "headerFooter",
        "rowBreaks",
        "colBreaks",
        "drawing",
    }
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        with ZipFile(io.BytesIO(content)) as package:
            for name in package.namelist():
                if name.endswith(".vml") or name.startswith(
                    ("xl/ctrlProps/", "xl/activeX/", "xl/embeddings/")
                ):
                    return False
                if (
                    name.startswith("xl/drawings/")
                    and name.endswith(".xml")
                    and len(ElementTree.fromstring(package.read(name)))
                ):
                    return False
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    root = ElementTree.fromstring(package.read(name))
                    if any(
                        node.tag not in {namespace + tag for tag in sheet_tags}
                        for node in root
                    ):
                        return False
    except (BadZipFile, ElementTree.ParseError, KeyError):
        return False
    return True
