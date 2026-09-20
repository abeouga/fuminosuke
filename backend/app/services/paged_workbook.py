"""Versioned paged output: edit owned cells/merges, not the workbook serializer."""

from __future__ import annotations

import hashlib
import io
import re
from typing import Any, NoReturn
from xml.sax.saxutils import escape
from zipfile import ZipFile

from openpyxl import load_workbook  # type: ignore[import-untyped]
from openpyxl.utils.cell import (  # type: ignore[import-untyped]
    coordinate_to_tuple,
    get_column_letter,
    range_boundaries,
)

from app.domain.print_layout import print_error
from app.services import print_xml
from app.services.preserving_print_workbook import S, _one, _sheet_path, inspect
from app.services.spreadsheet_template_output_service import SpreadsheetTemplateOutputService

ADAPTER = "ooxml-paged-values-v1"


def _invalid(detail: str) -> NoReturn:
    raise print_error(detail, "PAGE_OUTPUT_MAPPING_INVALID", 409)


def _attribute(opening: bytes, name: str, value: str) -> bytes:
    pattern = rb"(?<![\w:.-])" + name.encode() + rb"\s*=\s*(?:\"[^\"]*\"|'[^']*')"
    replacement = name.encode() + b'="' + value.encode() + b'"'
    if re.search(pattern, opening):
        return re.sub(pattern, lambda _: replacement, opening, count=1)
    end = -2 if opening.endswith(b"/>") else -1
    return opening[:end] + b" " + replacement + opening[end:]


def _text(value: str) -> bytes:
    if len(value) > 32767 or any(
        (ord(c) < 32 and c not in "\t\n\r")
        or 0xD800 <= ord(c) <= 0xDFFF
        or ord(c) in {0xFFFE, 0xFFFF}
        for c in value
    ):
        raise print_error(
            "XLSXへ安全に保存できない文字があります。", "PAGE_OUTPUT_TEXT_INVALID", 409
        )
    # ST_Xstring: a literal escape-looking sequence must not become another character.
    value = re.sub(r"_x[0-9a-fA-F]{4}_", lambda m: "_x005F_" + m[0][1:], value)
    return escape(value).replace("\r", "&#13;").encode("utf-8")


def _cell(data: bytes, node: print_xml.Element, value: str | None) -> bytes:
    opening = _attribute(
        data[node.start : node.open_end], "t", "inlineStr" if value is not None else "n"
    )
    prefix = node.prefix
    payload = (
        (
            b"<"
            + prefix
            + b"is><"
            + prefix
            + b't xml:space="preserve">'
            + _text(value)
            + b"</"
            + prefix
            + b"t></"
            + prefix
            + b"is>"
        )
        if value is not None
        else b""
    )
    if opening.endswith(b"/>"):
        return opening[:-2] + b">" + payload + b"</" + node.qname + b">"
    inner = data[node.open_end : node.close_start]
    inner = print_xml.edits(
        inner,
        [
            (child.start - node.open_end, child.end - node.open_end, b"")
            for child in node.children
            if child.name in {S + "v", S + "is"}
        ],
    )
    return opening + payload + inner + data[node.close_start : node.end]


def _new_cell(coordinate: str, value: str | None, prefix: bytes) -> bytes:
    raw = b"<" + prefix + b"c xmlns" + (b":" + prefix[:-1] if prefix else b"")
    # Parse the small standalone cell to share exact value encoding with existing cells.
    raw += (
        b'="http://schemas.openxmlformats.org/spreadsheetml/2006/main" r="'
        + coordinate.encode()
        + b'"/>'
    )
    return _cell(raw, print_xml.parse(raw), value)


def _write_cells(data: bytes, values: dict[str, str | None]) -> bytes:
    root = print_xml.parse(data)
    table = _one(root, "sheetData")
    assert table is not None
    changes = []
    row_nodes: dict[int, print_xml.Element] = {}
    cell_nodes: dict[str, print_xml.Element] = {}
    previous_row = 0
    for row in table.named(S + "row"):
        number = int(row.attrs["r"])
        if not previous_row < number <= 1048576:
            raise ValueError("Invalid worksheet row order")
        previous_row = number
        row_nodes[number] = row
        previous_column = 0
        for cell in row.named(S + "c"):
            coordinate = cell.attrs["r"]
            actual_row, column = coordinate_to_tuple(coordinate)
            if actual_row != number or not previous_column < column <= 16384:
                raise ValueError("Invalid worksheet cell order")
            previous_column = column
            cell_nodes[coordinate] = cell
    additions: dict[int, list[tuple[int, str, str | None]]] = {}
    for coordinate, value in values.items():
        node = cell_nodes.get(coordinate)
        if node is not None:
            changes.append((node.start, node.end, _cell(data, node, value)))
        elif value is not None:
            row, column = coordinate_to_tuple(coordinate)
            additions.setdefault(row, []).append((column, coordinate, value))
    # Group equal insertion points to keep new cells and rows in numerical order.
    inserts: dict[int, bytes] = {}
    table_tail = b""
    for number, cells in sorted(additions.items()):
        row_node = row_nodes.get(number)
        if row_node is None:
            prefix = table.prefix
            raw = b"<" + prefix + b'row r="' + str(number).encode() + b'">'
            raw += b"".join(_new_cell(c, v, prefix) for _, c, v in sorted(cells))
            raw += b"</" + prefix + b"row>"
            following = next((r for n, r in row_nodes.items() if n > number), None)
            if following is None:
                table_tail += raw
            else:
                inserts[following.start] = inserts.get(following.start, b"") + raw
            continue
        tail = b""
        for column, coordinate, value in sorted(cells):
            following_cell = next(
                (
                    c
                    for c in row_node.named(S + "c")
                    if coordinate_to_tuple(c.attrs["r"])[1] > column
                ),
                None,
            )
            raw = _new_cell(coordinate, value, row_node.prefix)
            if following_cell is None:
                tail += raw
            else:
                inserts[following_cell.start] = inserts.get(following_cell.start, b"") + raw
        if tail:
            changes.append(print_xml.insert_children(data, row_node, tail))
    if table_tail:
        changes.append(print_xml.insert_children(data, table, table_tail))
    changes.extend((at, at, raw) for at, raw in inserts.items())
    dimension = _one(root, "dimension", required=False)
    if dimension is not None and values:
        left, top, right, bottom = range_boundaries(dimension.attrs["ref"])
        positions = [coordinate_to_tuple(c) for c in values]
        bounds = (
            min(left, *(c for _, c in positions)),
            min(top, *(r for r, _ in positions)),
            max(right, *(c for _, c in positions)),
            max(bottom, *(r for r, _ in positions)),
        )
        if bounds != (left, top, right, bottom):
            left, top, right, bottom = bounds
            reference = f"{get_column_letter(left)}{top}:{get_column_letter(right)}{bottom}"
            changes.append(
                (
                    dimension.start,
                    dimension.open_end,
                    _attribute(data[dimension.start : dimension.open_end], "ref", reference),
                )
            )
    return print_xml.edits(data, changes)


def _write_merges(data: bytes, ranges: set[str]) -> bytes:
    root = print_xml.parse(data)
    old = _one(root, "mergeCells", required=False)
    prefix = old.prefix if old is not None else root.prefix
    raw = b"<" + prefix + b'mergeCells count="' + str(len(ranges)).encode() + b'">'
    raw += b"".join(
        b"<" + prefix + b'mergeCell ref="' + r.encode() + b'"/>'
        for r in sorted(ranges, key=lambda r: coordinate_to_tuple(r.split(":")[0]))
    )
    raw += b"</" + prefix + b"mergeCells>"
    if old is not None:
        # Retain namespace declarations/other attributes on this existing container.
        opening = _attribute(data[old.start : old.open_end], "count", str(len(ranges)))
        children = raw[raw.index(b">") + 1 : raw.rindex(b"</")]
        raw = opening[:-2] + b">" if opening.endswith(b"/>") else opening
        raw += children + b"</" + old.qname + b">"
        return print_xml.edits(data, [(old.start, old.end, raw)])
    preceding = {
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
    }
    following = next((n for n in root.children if n.name.rsplit("}", 1)[-1] not in preceding), None)
    change = (
        (following.start, following.start, raw)
        if following
        else print_xml.insert_children(data, root, raw)
    )
    return print_xml.edits(data, [change])


def render_workbook(
    content: bytes, snapshot: dict[str, Any], readings: list[dict[str, Any]]
) -> bytes:
    """Values carry explicit accepted bindings. Do not infer a destination from geometry."""
    if snapshot.get("outputAdapter") != ADAPTER:
        raise print_error(
            "この受付には対応するXLSX出力形式がありません。", "PAGE_OUTPUT_ADAPTER_REQUIRED", 409
        )
    if hashlib.sha256(content).hexdigest() != snapshot["sourceWorkbook"]["sha256"]:
        raise print_error("受付原本のSHAが一致しません。", "PAGE_OUTPUT_SOURCE_CHANGED", 409)
    parts = inspect(content)
    output = snapshot["spreadsheetOutput"]
    try:
        workbook = load_workbook(io.BytesIO(content), data_only=False, keep_links=True)
    except Exception as exc:
        raise print_error(
            "受付原本のXLSXを開けません。", "PAGE_OUTPUT_SOURCE_INVALID", 409
        ) from exc
    try:
        book_node = print_xml.parse(parts["xl/workbook.xml"])
        path, _ = _sheet_path(parts, book_node, output["sourceSheetName"])
        worksheet = workbook[output["sourceSheetName"]]
        original_ranges = {str(r) for r in worksheet.merged_cells.ranges}
        helper = SpreadsheetTemplateOutputService
        plans = helper._merge_plans(output, worksheet)
        plan_by_parent = {p.parent_cell_id: p for p in plans}
        merges = {m["parentCellId"]: m for m in output["cellMerges"]}
        cells = {c["id"] for c in output["cells"]}
        values: dict[str, str | None] = {}
        for reading in readings:
            binding, value = reading["binding"], reading["value"]
            if value is not None and not isinstance(value, str):
                _invalid("出力値が文字列ではありません。")
            cell_id = binding["cellId"]
            if cell_id not in cells:
                _invalid("出力先セルのidentityがありません。")
            plan = plan_by_parent.get(cell_id)
            if plan is not None:
                source_range = plan.excel_range
                source_parts = helper.page_merge_sources(worksheet, merges[cell_id])
            else:
                coordinate = helper._coordinate(cell_id)
                if coordinate is None or not re.fullmatch(
                    r"xlsx_[A-Z]{1,3}[1-9]\d*(?:_[0-9a-f]{20})?", cell_id
                ):
                    _invalid("元XLSXセルのidentityを確認できません。")
                source_range = next(
                    (str(r) for r in worksheet.merged_cells.ranges if coordinate in r),
                    f"{coordinate}:{coordinate}",
                )
                source_parts = []
                if source_range.split(":")[0] != coordinate:
                    _invalid("結合内部のセルを出力先にできません。")
            if binding["sourceRange"] != source_range or binding["sourceParts"] != source_parts:
                _invalid("出力元のセル範囲と保存済みbindingが一致しません。")
            anchor = source_range.split(":")[0]
            if binding["anchorCell"] != anchor:
                _invalid("出力anchorが一致しません。")
            if anchor in values and values[anchor] != value:
                raise print_error(
                    "同じ元セルに異なる確定値があります。", "PAGE_OUTPUT_CELL_CONFLICT", 409
                )
            values[anchor] = value
        if not readings:
            _invalid("出力する項目がありません。")
        clear = {
            c
            for p in plans
            if p.excel_range not in original_ranges
            for c in p.parts_by_coordinate
            if c != p.anchor_cell
        }
        source_table = _one(print_xml.parse(parts[path]), "sheetData")
        assert source_table is not None
        for row in source_table.named(S + "row"):
            for cell in row.named(S + "c"):
                coordinate = cell.attrs["r"]
                if (
                    coordinate in clear
                    and worksheet[coordinate].value is None
                    and (cell.named(S + "v") or cell.named(S + "is"))
                ):
                    _invalid("元結合内部の隠れた値を削除できません。")
        # Reuse the established overlap, selected-source, formula and literal safeguards.
        helper._apply_merges(worksheet, plans)
        final_ranges = {str(r) for r in worksheet.merged_cells.ranges}
        if clear & values.keys():
            _invalid("出力項目が別の結合内部へ重複しています。")
        for coordinate in clear:
            values[coordinate] = None
        data = _write_cells(parts[path], values)
        if final_ranges != original_ranges:
            data = _write_merges(data, final_ranges)
        result = io.BytesIO()
        with ZipFile(io.BytesIO(content)) as original, ZipFile(result, "w") as archive:
            archive.comment = original.comment
            for entry in original.infolist():
                archive.writestr(entry, data if entry.filename == path else parts[entry.filename])
        return result.getvalue()
    except (KeyError, ValueError, TypeError) as exc:
        raise print_error(
            "保存されたXLSX出力構造を検証できません。", "PAGE_OUTPUT_MAPPING_INVALID", 409
        ) from exc
    finally:
        workbook.close()
