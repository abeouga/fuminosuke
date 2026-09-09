"""Dimensions of the legacy preview's Office import, for newly imported templates.

This snapshot is separate from printLayout geometry and renderer qualification.
It changes coordinates only, preserving the source-derived cell membership/roles.
"""
from __future__ import annotations

import hashlib
import io
import math
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple

from app.domain.print_layout import print_error
from app.layout_analysis.models import LayoutDefinition

MODEL = "spreadsheet-preview-office-v1"


def project_preview_geometry(
    content: bytes, layout: LayoutDefinition, print_cells: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> tuple[LayoutDefinition, list[dict[str, Any]]]:
    if (snapshot.get("model") != MODEL
            or snapshot.get("sourceSha256") != hashlib.sha256(content).hexdigest()):
        raise print_error("保存したプレビュー寸法と原本が一致しません。",
                          "PRINT_GEOMETRY_CHANGED", 409)
    workbook = load_workbook(io.BytesIO(content), data_only=False)
    try:
        sheet = workbook[snapshot["sheetName"]]
        c1, r1, c2, r2 = snapshot["bounds"]
        columns, rows = snapshot["columns"], snapshot["rows"]
        if (len(columns) != c2 or len(rows) != r2
                or any(not math.isfinite(v) or v <= 0 for v in [*columns, *rows])):
            raise print_error("保存したプレビュー寸法が不正です。", "PRINT_GEOMETRY_CHANGED", 409)
        hidden: set[int] = set()
        for dim in sheet.column_dimensions.values():
            if dim.hidden:
                hidden.update(range(dim.min or c1, (dim.max or dim.min or c1) + 1))
        xs, ys = {c1: 0.0}, {r1: 0.0}
        for c in range(c1, c2 + 1):
            xs[c + 1] = xs[c] + (0 if c in hidden else columns[c - 1])
        for r in range(r1, r2 + 1):
            ys[r + 1] = ys[r] + (0 if sheet.row_dimensions[r].hidden else rows[r - 1])
        portrait = sheet.page_setup.orientation != "landscape"
        cw, ch = layout.canonical_width, layout.canonical_height
        dpi_x, dpi_y = cw / (8.27 if portrait else 11.69), ch / (11.69 if portrait else 8.27)
        margins = sheet.page_margins
        left, right = [round(float(v or 0) * dpi_x) for v in (margins.left, margins.right)]
        top, bottom = [round(float(v or 0) * dpi_y) for v in (margins.top, margins.bottom)]
        width, height = xs[c2 + 1], ys[r2 + 1]
        scale = min(max(1, cw - left - right) / max(width, 1),
                    max(1, ch - top - bottom) / max(height, 1))
        ox, oy = float(left), float(top)
        if sheet.print_options.horizontalCentered:
            ox += max(0, (cw - left - right - width * scale) / 2)
        if sheet.print_options.verticalCentered:
            oy += max(0, (ch - top - bottom - height * scale) / 2)
        merges = {(m.min_row, m.min_col): (min(m.max_row, r2), min(m.max_col, c2))
                  for m in sheet.merged_cells.ranges}
        projected = []
        for cell in print_cells:
            row, col = coordinate_to_tuple(cell["id"][5:])
            er, ec = merges.get((row, col), (row, col))
            x, y = round(ox + xs[col] * scale), round(oy + ys[row] * scale)
            projected.append({**cell, "x": x, "y": y,
                              "width": max(1, round(ox + xs[ec + 1] * scale) - x),
                              "height": max(1, round(oy + ys[er + 1] * scale) - y)})
        by_id = {c["id"]: c for c in projected}
        return layout.model_copy(update={"cells": [
            c.model_copy(update={k: by_id[c.id][k] for k in ("x", "y", "width", "height")})
            for c in layout.cells
        ]}), projected
    finally:
        workbook.close()
