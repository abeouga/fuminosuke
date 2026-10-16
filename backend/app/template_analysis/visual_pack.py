"""Compact model input and reference-labelled crops derived from pinned evidence."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.template_analysis.visual_bindings import build_binding_catalog
from app.template_analysis.visual_grid import GridCandidate, regular_grid
from app.template_analysis.visual_input import VisualEvidence

POLICY = "template-analysis.visual-pack.v1"


@dataclass(frozen=True)
class ModelPack:
    metadata_json: str
    sheets: tuple[bytes, ...]
    grid: GridCandidate | None


def reference_sheets(image: bytes, cells: list[dict[str, Any]]) -> tuple[bytes, ...]:
    """Labels occupy a separate band, never overwrite the source crop's ink."""
    width, height, column_width = 1288, 1600, 644
    font = ImageFont.load_default(size=20)
    sheets: list[bytes] = []
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    x = y = line_height = 0

    def finish() -> None:
        buffer = BytesIO()
        canvas.crop((0, 0, width, max(1, y + line_height))).save(buffer, format="PNG")
        sheets.append(buffer.getvalue())

    with Image.open(BytesIO(image)) as source:
        for cell in cells:
            rect = (
                math.floor(cell["x"]),
                math.floor(cell["y"]),
                math.ceil(cell["x"] + cell["width"]),
                math.ceil(cell["y"] + cell["height"]),
            )
            if rect[2] <= rect[0] or rect[3] <= rect[1]:
                raise ValueError("VISUAL_TILE_EMPTY")
            with source.crop(rect).convert("RGB") as tile:
                tile.thumbnail((636, 300), Image.Resampling.LANCZOS)
                tile_height = tile.height + 34
                if x and max(line_height, tile_height) + y > height:
                    x = 0
                    y += line_height
                    line_height = 0
                if not x and y + tile_height > height:
                    finish()
                    if len(sheets) >= 16:
                        raise ValueError("VISUAL_TILE_LIMIT")
                    canvas = Image.new("RGB", (width, height), "white")
                    draw = ImageDraw.Draw(canvas)
                    y = 0
                draw.text((x + 4, y + 2), cell["ref"], fill="#174a8b", font=font)
                canvas.paste(tile, (x + 4, y + 30))
                line_height = max(line_height, tile_height)
                if x:
                    x = 0
                    y += line_height
                    line_height = 0
                else:
                    x = column_width
    if y or line_height:
        finish()
    return tuple(sheets)


def build_model_pack(evidence: VisualEvidence) -> ModelPack:
    meta = json.loads(evidence.metadata_json)
    grid = regular_grid(meta)
    omitted = set(grid.members) - set(grid.headers) if grid else set()
    visible = [c for c in meta["cells"] if c["ref"] not in omitted]
    compact = {
        "policy": POLICY,
        "sourceImageSize": meta["sourceImageSize"],
        "writeRefs": [r for r in meta["writeRefs"] if r not in omitted],
        "readOnlyRefs": meta["readOnlyRefs"],
        "cellColumns": [
            "ref",
            "x",
            "y",
            "width",
            "height",
            "role",
            "locked",
            "bound",
            "parent",
            "protected",
            "textHint",
        ],
        "cells": [
            [
                c["ref"],
                c["x"],
                c["y"],
                c["width"],
                c["height"],
                c["existingRole"],
                c["roleLocked"],
                c["hasMeaning"],
                c["existingParentRef"],
                c["protectedFacets"],
                c["textHint"],
            ]
            for c in visible
        ],
        "grids": [
            {
                "ref": grid.ref,
                "headers": list(grid.headers),
                "rows": len(grid.rows),
                "rule": "各headerの直下にある既存セルだけを同じ列へ展開。行列変更は不可。",
            }
        ]
        if grid
        else [],
    }
    if grid is None:
        compact.update(build_binding_catalog(meta).metadata())
        compact["existingBindings"] = meta.get("existingBindings", [])
        compact["structure"] = bool(meta.get("structure"))
        compact["geometryEditable"] = meta.get("geometryEditable", [])
        compact["geometryBounds"] = meta.get("geometryBounds")
    # Read-only references are useful context too. Very large pages keep the
    # untouched overview/crop and prioritise writable references for tile space.
    tile_cells = [c for c in visible if c["ref"] in meta["writeRefs"]]
    return ModelPack(
        json.dumps(compact, ensure_ascii=False, separators=(",", ":")),
        reference_sheets(evidence.full_image, tile_cells),
        grid,
    )
