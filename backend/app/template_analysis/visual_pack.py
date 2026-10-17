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
# A printed cell this many times wider than tall may hold a caption and writing space.
CAPTION_ASPECT = 3
# A grid block needs this many rows to be offered as a table candidate.
GRID_MIN_ROWS = 3
GRID_MIN_COLUMNS = 3
# Ink darker than this level; blank share right/below of the ink, or a blank run
# between printed parts, that leaves room to write inside a printed cell.
INK_LEVEL = 140
SPACE_RATIO = 0.35
RUN_RATIO = 0.25


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


def _writing_space(page: Image.Image, cell: dict[str, Any]) -> bool:
    """Whether a printed cell leaves room to write: blank right or below its ink,
    or a wide blank run between printed parts (as between units like 年 月 日)."""
    inset = max(3, round(0.08 * min(cell["width"], cell["height"])))
    box = (
        round(cell["x"]) + inset,
        round(cell["y"]) + inset,
        round(cell["x"] + cell["width"]) - inset,
        round(cell["y"] + cell["height"]) - inset,
    )
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        return False
    with page.crop(box) as inner:
        width, height = inner.size
        pixels = inner.load()
        ink_columns = [any(pixels[x, y] < INK_LEVEL for y in range(height)) for x in range(width)]
        ink_rows = [any(pixels[x, y] < INK_LEVEL for x in range(width)) for y in range(height)]
    if not any(ink_columns):
        return True
    right = max(i for i, v in enumerate(ink_columns) if v)
    bottom = max(i for i, v in enumerate(ink_rows) if v)
    run = longest = 0
    for value in ink_columns[ink_columns.index(True) : right + 1]:
        run = 0 if value else run + 1
        longest = max(longest, run)
    return bool(
        (width - 1 - right) >= SPACE_RATIO * width
        or (height - 1 - bottom) >= SPACE_RATIO * height
        or longest >= RUN_RATIO * width
    )


def draft_hints(
    cells: list[dict[str, Any]], writable: set[str], image: bytes | None = None
) -> tuple[list[str], list[list[str]], list[str]]:
    """Inputs outside grids, the top row of each grid block, and printed cells that
    may hold a caption and its writing space in one frame, as draft hints.

    A grid cell sits in a two-by-two block: a same-height neighbour beside it, and
    stacked partners of both that are side by side too. Detected roles inside a
    table are unreliable, so every writable cell takes part in the test. These
    are hints in the request only; the answer is checked as before.
    """
    pool = [c for c in cells if c["ref"] in writable]

    def stacked(a: dict[str, Any], b: dict[str, Any]) -> bool:
        overlap = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
        gap = abs(b["y"] - a["y"] - a["height"])
        return bool(
            gap <= 0.4 * min(a["height"], b["height"])
            and overlap >= 0.8 * max(a["width"], b["width"])
        )

    def beside(a: dict[str, Any], b: dict[str, Any]) -> bool:
        tolerance = 0.4 * min(a["height"], b["height"])
        gap = min(abs(b["x"] - a["x"] - a["width"]), abs(a["x"] - b["x"] - b["width"]))
        return bool(
            gap <= max(4, 0.2 * min(a["width"], b["width"]))
            and abs(a["y"] - b["y"]) <= tolerance
            and abs(a["height"] - b["height"]) <= tolerance
        )

    above = {a["ref"]: [c for c in pool if stacked(c, a)] for a in pool}
    below = {a["ref"]: [c for c in pool if stacked(a, c)] for a in pool}

    def square(a: dict[str, Any], b: dict[str, Any]) -> bool:
        # a and b side by side, with stacked partners on the same side also side by side.
        return any(
            beside(c, d) for side in (above, below) for c in side[a["ref"]] for d in side[b["ref"]]
        )

    paired = {
        a["ref"] for a in pool if any(b is not a and beside(a, b) and square(a, b) for b in pool)
    }
    members = [c for c in pool if c["ref"] in paired]
    block = {c["ref"]: c["ref"] for c in members}

    def root(r: str) -> str:
        while block[r] != r:
            r = block[r]
        return r

    for a in members:
        for b in members:
            if a is not b and (beside(a, b) or stacked(a, b)):
                block[root(a["ref"])] = root(b["ref"])
    blocks: dict[str, list[dict[str, Any]]] = {}
    for c in members:
        blocks.setdefault(root(c["ref"]), []).append(c)

    def row_count(found: list[dict[str, Any]]) -> int:
        rows: list[dict[str, Any]] = []
        for c in sorted(found, key=lambda c: c["y"]):
            last = rows[-1] if rows else None
            if last is None or c["y"] - last["y"] > 0.4 * min(c["height"], last["height"]):
                rows.append(c)
        return len(rows)

    def top_row(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
        top = min(c["y"] for c in found)
        first = [c for c in found if c["y"] - top <= 0.4 * c["height"]]
        return sorted(first, key=lambda c: c["x"])

    def column_count(found: list[dict[str, Any]]) -> int:
        columns: list[dict[str, Any]] = []
        for c in sorted(found, key=lambda c: c["x"]):
            last = columns[-1] if columns else None
            if last is None or c["x"] - last["x"] > 0.4 * min(c["width"], last["width"]):
                columns.append(c)
        return len(columns)

    # A block of three rows or more under a printed header row is a table
    # candidate, two columns included. Without a header row, three columns or
    # more is still table structure (a table continued below a note); two
    # columns are caption/value pairs and stay ordinary fields.
    tall = [found for found in blocks.values() if row_count(found) >= GRID_MIN_ROWS]
    tables = [f for f in tall if all(c["existingRole"] == "label" for c in top_row(f))]
    grids = tables + [f for f in tall if f not in tables and column_count(f) >= GRID_MIN_COLUMNS]
    grid = {c["ref"] for found in grids for c in found}
    single = [c["ref"] for c in pool if c["existingRole"] == "input" and c["ref"] not in grid]
    # A caption and its writing space in one frame is detected as one printed cell.
    printed = [c for c in pool if c["existingRole"] == "label" and c["ref"] not in grid]
    page = Image.open(BytesIO(image)).convert("L") if image else None
    captioned = [
        c["ref"]
        for c in printed
        if c["width"] >= CAPTION_ASPECT * c["height"]
        or (page is not None and _writing_space(page, c))
    ]
    tops = [[c["ref"] for c in top_row(found)] for found in tables]
    return single, sorted(tops, key=lambda t: t[0]), captioned


def build_model_pack(evidence: VisualEvidence) -> ModelPack:
    meta = json.loads(evidence.metadata_json)
    grid = regular_grid(meta)
    omitted = set(grid.members) - set(grid.headers) if grid else set()
    visible = [c for c in meta["cells"] if c["ref"] not in omitted]
    if meta.get("draft"):
        # A draft band sees its own cells and the printed cells they may link to;
        # the rest of the page stays in the overview image only.
        reachable = set(meta["writeRefs"]) | {b.label for b in build_binding_catalog(meta).links}
        reachable |= {
            r for b in meta.get("existingBindings", []) for r in [b["input"], *b["labels"]]
        }
        visible = [c for c in visible if c["ref"] in reachable]
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
        if meta.get("draft"):
            # An explicit list of the band's detected inputs outside repeated rows;
            # omissions vary otherwise. Table rows are covered through their headers.
            single, tops, captioned = draft_hints(
                visible, set(meta["writeRefs"]), evidence.full_image
            )
            compact["mustCover"] = single
            compact["tableCandidates"] = tops
            compact["captionCandidates"] = captioned
    # Read-only references are useful context too. Very large pages keep the
    # untouched overview/crop and prioritise writable references for tile space.
    tile_cells = [c for c in visible if c["ref"] in meta["writeRefs"]]
    return ModelPack(
        json.dumps(compact, ensure_ascii=False, separators=(",", ":")),
        reference_sheets(evidence.full_image, tile_cells),
        grid,
    )
