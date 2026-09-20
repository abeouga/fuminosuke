"""Versioned page planning. Units are points; raster transforms explicitly carry DPI.

This module does not mutate workbooks or legacy OCR rectangles.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field

from app.core.errors import AppError
from app.domain.calc_print_grid import MODEL as CALC_GEOMETRY_MODEL
from app.domain.calc_print_grid import frame_origin
from app.layout_analysis.models import LayoutModel

MIN_SAFE_PRINT_SCALE = 0.70
MAX_PAGES = 50
PAPER_MM = {"A4": (210.0, 297.0), "A3": (297.0, 420.0), "Letter": (215.9, 279.4)}
RENDER_DPI = 150


class PrintMargins(LayoutModel):
    left: float = Field(default=10, ge=0, le=100, allow_inf_nan=False)
    right: float = Field(default=10, ge=0, le=100, allow_inf_nan=False)
    top: float = Field(default=10, ge=0, le=100, allow_inf_nan=False)
    bottom: float = Field(default=10, ge=0, le=100, allow_inf_nan=False)


class PrintLayoutRequest(LayoutModel):
    base_revision: str = Field(min_length=64, max_length=64)
    paper_size: Literal["A4", "A3", "Letter"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    margins: PrintMargins = Field(default_factory=PrintMargins)
    horizontal_centering: bool = False
    vertical_centering: bool = False
    target_pages_wide: Literal[1] = 1
    target_pages_tall: int | None = Field(default=None, ge=1, le=MAX_PAGES)
    # Inclusive end rows, excluding the final row. null requests candidates;
    # [] explicitly requests one page. Saved ranges are never silently replaced.
    break_after_rows: list[int] | None = Field(default=None, max_length=MAX_PAGES - 1)
    auto_arrange: bool = False


def print_error(detail: str, code: str = "PRINT_LAYOUT_INVALID", status: int = 422) -> AppError:
    return AppError(status=status, code=code, title="Print layout cannot be used", detail=detail)


def require_legacy_ocr(quality: dict[str, Any]) -> None:
    if quality.get("printLayout") is not None:
        raise print_error(
            "印刷ページ設定は下書き保存までの対応です。ページ別の実OCR・補正・確定は未対応のため、"
            "有効化・OCR実行はできません。旧方式を使う場合は印刷ページ設定を解除してください。",
            "PRINT_LAYOUT_OCR_NOT_AVAILABLE",
            409,
        )


def page_plan(
    source: dict[str, Any],
    request: PrintLayoutRequest,
    saved: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if source["unsupported"]:
        raise print_error(" / ".join(source["unsupported"]), "PRINT_LAYOUT_UNSUPPORTED")
    first, last = source["startRow"], source["endRow"]
    boundaries = {item["row"]: item["y"] for item in source["rowBoundaries"]}
    calc_grid = source.get("geometryModelVersion") == CALC_GEOMETRY_MODEL
    fitting = ({item["row"]: item["y"] for item in source["nominalRowBoundaries"]}
               if calc_grid else boundaries)
    fit_width = source["nominalWidth"] if calc_grid else source["width"]
    fit_height = source["nominalHeight"] if calc_grid else source["height"]
    valid = set(source["validBreakRows"])
    paper_w, paper_h = PAPER_MM[request.paper_size]
    if request.orientation == "landscape":
        paper_w, paper_h = paper_h, paper_w
    pt = 72 / 25.4
    width, height = paper_w * pt, paper_h * pt
    margins = request.margins
    available_w = width - (margins.left + margins.right) * pt
    available_h = height - (margins.top + margins.bottom) * pt
    if available_w <= 0 or available_h <= 0:
        raise print_error("余白が用紙の印刷可能範囲を超えています。")
    breaks = request.break_after_rows
    origin = "user"
    if breaks is None and saved and not request.auto_arrange:
        breaks = [p["endRow"] for p in saved["pages"][:-1]]
        origin = saved["source"]
    if breaks is None and not request.auto_arrange and source["manualBreakRows"]:
        breaks = source["manualBreakRows"]
        origin = "xlsx-manual"
    if breaks is None:
        origin = "auto"
        ordered = sorted(valid)
        breaks = []
        if request.target_pages_tall:
            count = request.target_pages_tall
            if count > len(ordered) + 1:
                raise print_error("結合セル・OCR範囲を分断せずに指定ページ数へ分割できません。")
            for index in range(1, count):
                remaining = count - index - 1
                choices = [b for b in ordered if not breaks or b > breaks[-1]]
                if remaining:
                    choices = choices[:-remaining]
                desired = fit_height * index / count
                breaks.append(min(choices, key=lambda b: (abs(fitting[b + 1] - desired), b)))
        else:
            width_scale = min(1.0, available_w / fit_width)
            capacity = available_h / width_scale
            start = 0.0
            while fit_height - start > capacity + 0.001:
                choices = [b for b in ordered if fitting[b + 1] > start]
                if not choices:
                    break
                fits = [b for b in choices if fitting[b + 1] - start <= capacity]
                boundary = max(fits) if fits else choices[0]
                breaks.append(boundary)
                start = fitting[boundary + 1]
                if len(breaks) >= MAX_PAGES:
                    raise print_error("ページ数の上限50ページを超えます。")
    if breaks != sorted(set(breaks)) or any(b not in valid for b in breaks):
        raise print_error(
            "改ページは結合セル・OCR範囲を横切らない、重複のない行境界で指定してください。"
        )
    starts = [first, *[b + 1 for b in breaks]]
    ends = [*breaks, last]
    heights = [fitting[b + 1] - fitting[a] for a, b in zip(starts, ends, strict=True)]
    if any(h <= 0 for h in heights):
        raise print_error("空のページは作成できません。")
    scale = min(1.0, available_w / fit_width, *[available_h / h for h in heights])
    integer_scale = source.get("printScalePolicy") == "integer-percent-v1"
    if integer_scale:
        scale = math.floor(scale * 100 + 1e-9) / 100
        if scale < 0.10:
            raise print_error("印刷倍率がExcelの対応下限10%を下回ります。")
    raster = RENDER_DPI / 72
    warnings = list(source["warnings"])
    if scale < MIN_SAFE_PRINT_SCALE:
        warnings.append(
            f"共通縮小率は{scale:.1%}です。安全下限{MIN_SAFE_PRINT_SCALE:.0%}未満です。"
        )
    if request.target_pages_tall and request.target_pages_tall != len(starts):
        warnings.append(
            "保存済み・指定済みページ範囲を維持しています。目標ページ数は自動配置時だけ使用します。"
        )
    pages = []
    for number, (a, b, h) in enumerate(zip(starts, ends, heights, strict=True), 1):
        x_offset = margins.left * pt
        y_offset = margins.top * pt
        if request.horizontal_centering:
            x_offset += (available_w - source["width"] * scale) / 2
        if request.vertical_centering:
            y_offset += (available_h - h * scale) / 2
        if calc_grid:
            grid = source["printGrid"]
            zoom = round(scale * 100)
            try:
                x_offset = frame_origin(
                    round(paper_w * 1440 / 25.4), round(margins.left * 1440 / 25.4),
                    round(margins.right * 1440 / 25.4),
                    sum(grid["columns"][source["startCol"] - 1:source["endCol"]]), zoom,
                    centered=request.horizontal_centering,
                ) * scale * 72 / 2540
                y_offset = frame_origin(
                    round(paper_h * 1440 / 25.4), round(margins.top * 1440 / 25.4),
                    round(margins.bottom * 1440 / 25.4), sum(grid["rows"][a - 1:b]), zoom,
                    centered=request.vertical_centering,
                ) * scale * 72 / 2540
            except ValueError as exc:
                raise print_error("Calcの整数印刷範囲内にページを配置できません。",
                                  "PAGE_GEOMETRY_MISMATCH") from exc
        transform = {
            "scale": scale * raster,
            "offsetX": x_offset * raster,
            "offsetY": y_offset * raster,
            "originX": 0.0,
            "originY": boundaries[a],
        }
        cells = []
        for cell in source["cells"]:
            if cell["startRow"] >= a and cell["endRow"] <= b:
                cells.append({**cell, "rectangle": transform_rect(cell, transform)})
        fields = [
            {**field, "templatePage": number, "rectangle": transform_rect(field, transform)}
            for field in source["fields"]
            if field["startRow"] >= a and field["endRow"] <= b
        ]
        pages.append(
            {
                "page": number,
                "sheetName": source["sheetName"],
                "startRow": a,
                "endRow": b,
                "startCol": source["startCol"],
                "endCol": source["endCol"],
                "range": f"{source['startColumn']}{a}:{source['endColumn']}{b}",
                "width": math.ceil(width * raster),
                "height": math.ceil(height * raster),
                "transform": transform,
                "cells": cells,
                "fields": fields,
            }
        )
    if sum(len(p["fields"]) for p in pages) != len(source["fields"]):
        raise print_error("すべてのOCR範囲を一意のページへ割り当てられません。")
    return {
        "schemaVersion": 1,
        "geometryModelVersion": source.get("geometryModelVersion", "fixed-7px-plus-5-v1"),
        "geometryEvidence": source.get("geometryEvidence"),
        "rendererVersion": "cell-plan-v1",
        "unit": "pt",
        "dpi": RENDER_DPI,
        "source": origin,
        "sourceSha256": source["sha256"],
        "sheetName": source["sheetName"],
        "paperSize": request.paper_size,
        "orientation": request.orientation,
        "margins": margins.model_dump(),
        "horizontalCentering": request.horizontal_centering,
        "verticalCentering": request.vertical_centering,
        "targetPagesWide": 1,
        "targetPagesTall": request.target_pages_tall,
        "commonScale": scale,
        **({"printScalePolicy": "integer-percent-v1", "printAdapter": source["printAdapter"]}
           if integer_scale else {}),
        "pages": pages,
        "warnings": warnings,
        "importedSettings": source["importedSettings"],
        "ocrReady": False,
    }


def transform_rect(cell: dict[str, Any], transform: dict[str, float]) -> dict[str, int]:
    s = transform["scale"]
    left = transform["offsetX"] + (cell["x"] - transform["originX"]) * s
    top = transform["offsetY"] + (cell["y"] - transform["originY"]) * s
    right, bottom = left + cell["width"] * s, top + cell["height"] * s
    return {
        "x": round(left),
        "y": round(top),
        "width": max(1, round(right) - round(left)),
        "height": max(1, round(bottom) - round(top)),
    }
