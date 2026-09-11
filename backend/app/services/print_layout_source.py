"""Read source OOXML without saving it. Library defaults are not imported settings."""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter, range_boundaries
from openpyxl.worksheet.print_settings import PrintArea

from app.domain.print_layout import print_error
from app.services.spreadsheet_template_output_service import SpreadsheetTemplateOutputService

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DRAFT_GEOMETRY_VERSION = "draft-visible-cells-v1"


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    data = archive.read(name)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise print_error("外部定義を含むXMLは扱えません。")
    return ET.fromstring(data)


def read_source(
    content: bytes, sheet_name: str, layout: dict[str, Any], *, draft_support: bool = False,
) -> dict[str, Any]:
    try:
        return _read_source(content, sheet_name, layout, draft_support=draft_support)
    except (ValueError, KeyError, StopIteration, zipfile.BadZipFile, ET.ParseError) as exc:
        raise print_error("元XLSXのセル・印刷構造を安全に取得できません。") from exc


def _read_source(
    content: bytes, sheet_name: str, layout: dict[str, Any], *, draft_support: bool,
) -> dict[str, Any]:
    if len(content) > 64 * 1024 * 1024:
        raise print_error("元XLSXが64MiBを超えています。")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if (
            len(entries) > 5000
            or sum(e.file_size for e in entries) > 128 * 1024 * 1024
            or len({e.filename for e in entries}) != len(entries)
            or any(e.file_size > 16 * 1024 * 1024 or e.flag_bits & 1 for e in entries)
        ):
            raise print_error("XLSX内部のサイズ・構造が許容範囲を超えています。")
        workbook_xml = _xml(archive, "xl/workbook.xml")
        sheets = workbook_xml.findall("s:sheets/s:sheet", NS)
        index = next(i for i, s in enumerate(sheets) if s.get("name") == sheet_name)
        relation_id = sheets[index].get(f"{{{REL}}}id")
        relations = _xml(archive, "xl/_rels/workbook.xml.rels")
        relation = next(r for r in relations if r.get("Id") == relation_id)
        if relation.get("TargetMode") == "External":
            raise print_error("外部シート参照は扱えません。")
        target = relation.get("Target", "")
        sheet_path = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
        if not sheet_path.startswith("xl/"):
            raise print_error("シート参照が不正です。")
        sheet_xml = _xml(archive, sheet_path)

        def attributes(path: str) -> dict[str, str] | None:
            node = sheet_xml.find(path, NS)
            return dict(node.attrib) if node is not None else None

        defined = {
            n.get("name"): n.text
            for n in workbook_xml.findall("s:definedNames/s:definedName", NS)
            if n.get("localSheetId") == str(index)
        }
        imported: dict[str, Any] = {
            "printArea": defined.get("_xlnm.Print_Area"),
            "printTitles": defined.get("_xlnm.Print_Titles"),
            "pageSetup": attributes("s:pageSetup"),
            "margins": attributes("s:pageMargins"),
            "pageSetupProperties": attributes("s:sheetPr/s:pageSetUpPr"),
            "printOptions": attributes("s:printOptions"),
            "horizontalBreaks": [
                dict(n.attrib) for n in sheet_xml.findall("s:rowBreaks/s:brk", NS)
            ],
            "verticalBreaks": [dict(n.attrib) for n in sheet_xml.findall("s:colBreaks/s:brk", NS)],
        }
        unsupported: list[str] = []
        if imported["printTitles"]:
            unsupported.append("繰り返し印刷行・列は未対応です。")
        if any((n.text or "").strip() for n in sheet_xml.findall("s:headerFooter/*", NS)):
            unsupported.append("ヘッダー・フッターは未対応です。")
        if imported["verticalBreaks"]:
            unsupported.append("横方向の複数ページは未対応です。")
        setup = imported["pageSetup"] or {}
        if int(setup.get("fitToWidth", "1")) > 1:
            unsupported.append("横方向の複数ページ指定は未対応です。")
        for tag in (
            "drawing",
            "legacyDrawing",
            "legacyDrawingHF",
            "controls",
            "oleObjects",
            "picture",
        ):
            if sheet_xml.find(f"s:{tag}", NS) is not None:
                unsupported.append("図形・画像・フォーム部品を含むページ描画は未対応です。")
                break

    workbook = load_workbook(io.BytesIO(content), data_only=False, read_only=False)
    try:
        sheet = workbook[sheet_name]
        areas = list(PrintArea.from_string(sheet.print_area).ranges) if sheet.print_area else []
        if len(areas) > 1:
            unsupported.append("離れた複数の印刷範囲は未対応です。")
        bounds = range_boundaries(str(areas[0]) if len(areas) == 1 else sheet.calculate_dimension())
        c1, r1, c2, r2 = bounds
        if (r2 - r1 + 1) * (c2 - c1 + 1) > 20_000:
            raise print_error("印刷対象は20,000セル以内で指定してください。")
        columns: dict[int, float] = {}
        default = sheet.sheet_format.defaultColWidth or sheet.sheet_format.baseColWidth or 8.43
        hidden_cols: set[int] = set()
        for col in range(c1, c2 + 1):
            columns[col] = (float(default) * 7 + 5) * 72 / 96
        for dim in sheet.column_dimensions.values():
            for col in range(max(c1, dim.min or c1), min(c2, dim.max or dim.min or c1) + 1):
                columns[col] = (float(dim.width or default) * 7 + 5) * 72 / 96
                if dim.hidden:
                    hidden_cols.add(col)
        rows = {
            r: float(sheet.row_dimensions[r].height or sheet.sheet_format.defaultRowHeight or 15)
            for r in range(r1, r2 + 1)
        }
        hidden_rows = {r for r in rows if sheet.row_dimensions[r].hidden}
        draft_features = []
        if hidden_cols or hidden_rows:
            if draft_support:
                draft_features.append("hidden-dimensions")
                columns.update({c: 0.0 for c in hidden_cols})
                rows.update({r: 0.0 for r in hidden_rows})
            else:
                unsupported.append("非表示行・列を含む印刷範囲は未対応です。")
        xs, ys = {c1: 0.0}, {r1: 0.0}
        for col, w in columns.items():
            xs[col + 1] = xs[col] + w
        for row, h in rows.items():
            ys[row + 1] = ys[row] + h
        merges = list(sheet.merged_cells.ranges)
        spans: dict[tuple[int, int], tuple[int, int]] = {}
        covered: set[tuple[int, int]] = set()
        invalid: set[int] = set()
        for merged in merges:
            if (
                merged.max_row < r1
                or merged.min_row > r2
                or merged.max_col < c1
                or merged.min_col > c2
            ):
                continue
            if not (
                r1 <= merged.min_row <= merged.max_row <= r2
                and c1 <= merged.min_col <= merged.max_col <= c2
            ):
                unsupported.append("印刷範囲が結合セルの途中を切っています。")
                continue
            spans[(merged.min_row, merged.min_col)] = (merged.max_row, merged.max_col)
            invalid.update(range(merged.min_row, merged.max_row))
            covered.update(
                (r, c)
                for r in range(merged.min_row, merged.max_row + 1)
                for c in range(merged.min_col, merged.max_col + 1)
                if (r, c) != (merged.min_row, merged.min_col)
            )
        cells = []
        for row in rows:
            for col in columns:
                if (row, col) in covered:
                    continue
                cell = sheet.cell(row, col)
                er, ec = spans.get((row, col), (row, col))
                if draft_support and (xs[ec + 1] == xs[col] or ys[er + 1] == ys[row]):
                    continue
                if cell.data_type == "f":
                    unsupported.append("数式セルの表示値の再計算は未対応です。")
                if draft_support and cell.alignment.shrinkToFit:
                    draft_features.append("shrink-to-fit")
                if cell.alignment.textRotation or (
                    cell.alignment.shrinkToFit and not draft_support
                ):
                    unsupported.append("回転文字・セル内縮小は未対応です。")
                text = str(cell.value) if cell.value is not None else ""
                if (
                    cell.value is not None
                    and cell.number_format != "General"
                    and not isinstance(cell.value, str)
                ):
                    unsupported.append("書式付き数値・日付の表示は未対応です。")
                borders = {
                    side: getattr(getattr(cell.border, side), "style", None) is not None
                    for side in ("left", "right", "top", "bottom")
                }
                if not text and not any(borders.values()) and cell.fill.fill_type is None:
                    continue
                color = cell.fill.fgColor
                fill = (
                    f"#{color.rgb[-6:]}"
                    if cell.fill.fill_type and color.type == "rgb"
                    else "#ffffff"
                )
                cells.append(
                    {
                        "sourceRange": f"{get_column_letter(col)}{row}:{get_column_letter(ec)}{er}",
                        "startRow": row,
                        "endRow": er,
                        "startCol": col,
                        "endCol": ec,
                        "x": xs[col],
                        "y": ys[row],
                        "width": xs[ec + 1] - xs[col],
                        "height": ys[er + 1] - ys[row],
                        "text": text[:2000],
                        "fontSize": float(cell.font.sz or 11),
                        "bold": bool(cell.font.bold),
                        "align": cell.alignment.horizontal or "left",
                        "wrap": bool(cell.alignment.wrap_text),
                        **({"shrinkToFit": bool(cell.alignment.shrinkToFit)}
                           if draft_support else {}),
                        "borders": borders,
                        "fill": fill,
                    }
                )
        fields = []
        authored_merges = {m["parentCellId"]: m for m in layout.get("cellMerges", [])}
        for field in layout.get("cells", []):
            merge = authored_merges.get(field["id"])
            if merge:
                # Resolve exact source ranges with the same contiguous-rectangle
                # validator as XLSX output, never from the parent ID or pixels.
                source_parts = SpreadsheetTemplateOutputService.page_merge_sources(sheet, merge)
                fc = min(range_boundaries(p["sourceRange"])[0] for p in source_parts)
                fr = min(range_boundaries(p["sourceRange"])[1] for p in source_parts)
                ec = max(range_boundaries(p["sourceRange"])[2] for p in source_parts)
                er = max(range_boundaries(p["sourceRange"])[3] for p in source_parts)
                if not (r1 <= fr <= er <= r2 and c1 <= fc <= ec <= c2):
                    unsupported.append("印刷範囲外の結合矩形があります。")
                    continue
                if any(xs[c2p + 1] == xs[c1p] or ys[r2p + 1] == ys[r1p]
                       for c1p, r1p, c2p, r2p in
                       (range_boundaries(p["sourceRange"]) for p in source_parts)):
                    unsupported.append("非表示セルに対応する結合矩形があります。")
                    continue
                fields.append({"cellId": field["id"], "role": field["role"],
                               "sourceRange": (f"{get_column_letter(fc)}{fr}:"
                                               f"{get_column_letter(ec)}{er}"),
                               "sourceParts": source_parts,
                               "startRow": fr, "endRow": er, "startCol": fc, "endCol": ec,
                               "x": xs[fc], "y": ys[fr],
                               "width": xs[ec + 1] - xs[fc], "height": ys[er + 1] - ys[fr]})
                invalid.update(range(fr, er))
                continue
            match = re.match(r"^xlsx_([A-Z]{1,3}[1-9]\d*)(?:$|_)", field["id"])
            if not match:
                unsupported.append("元セル番地を確定できない編集矩形があります。")
                continue
            fc, fr, _, _ = range_boundaries(f"{match[1]}:{match[1]}")
            if not (r1 <= fr <= r2 and c1 <= fc <= c2) or (fr, fc) in covered:
                unsupported.append("印刷範囲外または結合内部の編集矩形があります。")
                continue
            er, ec = spans.get((fr, fc), (fr, fc))
            if draft_support and (xs[ec + 1] == xs[fc] or ys[er + 1] == ys[fr]):
                unsupported.append("非表示セルに対応する編集矩形があります。")
                continue
            fields.append(
                {
                    "cellId": field["id"],
                    "role": field["role"],
                    "sourceRange": f"{get_column_letter(fc)}{fr}:{get_column_letter(ec)}{er}",
                    "startRow": fr,
                    "endRow": er,
                    "startCol": fc,
                    "endCol": ec,
                    "x": xs[fc],
                    "y": ys[fr],
                    "width": xs[ec + 1] - xs[fc],
                    "height": ys[er + 1] - ys[fr],
                }
            )
            invalid.update(range(fr, er))
        valid = [r for r in range(r1, r2) if r not in invalid]
        manual = sorted(
            {
                int(b["id"])
                for b in imported["horizontalBreaks"]
                if b.get("man") in {"1", "true"} and r1 <= int(b["id"]) < r2
            }
        )
        warnings = [
            "セル構造からの参考描画です。Excelのフォント・罫線・組版の完全再現ではありません。実OCRには使用できません。"
        ]
        if any(b not in valid for b in manual):
            warnings.append("元の改ページに結合セルとの交差があるため、自動候補から除外しました。")
            manual = [b for b in manual if b in valid]
        return {
            "sheetName": sheet_name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "startRow": r1,
            "endRow": r2,
            "startCol": c1,
            "endCol": c2,
            "startColumn": get_column_letter(c1),
            "endColumn": get_column_letter(c2),
            "width": xs[c2 + 1],
            "height": ys[r2 + 1],
            "rowBoundaries": [{"row": r, "y": y} for r, y in ys.items()],
            "validBreakRows": valid,
            "manualBreakRows": manual,
            "cells": cells,
            "fields": fields,
            "importedSettings": imported,
            "unsupported": list(dict.fromkeys(unsupported)),
            "warnings": warnings,
            **({
                "draftFeatures": sorted(set(draft_features)),
                "hiddenColumns": sorted(hidden_cols), "hiddenRows": sorted(hidden_rows),
            } if draft_support else {}),
        }
    finally:
        workbook.close()
