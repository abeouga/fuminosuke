"""Measure printed label text that spills past its own box on the template image.

Spreadsheet text without a merged range overflows into the empty cells on its right;
the detected boxes then split one printed word. Only left-aligned, single-row label
text is measured, and only into neighbouring boxes that print nothing themselves.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import cv2
import numpy as np

INK = 140


@lru_cache(maxsize=4)
def _gray(data: bytes) -> np.ndarray[Any, Any] | None:
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def spills(image: bytes, cells: list[dict[str, Any]]) -> dict[str, int]:
    """Return {label cell id: pixels of printed text beyond its right edge}."""
    gray = _gray(image)
    if gray is None:
        return {}
    height, width = gray.shape
    result: dict[str, int] = {}
    for cell in cells:
        if (
            cell["role"] not in {"label", "parent_label"}
            or not (cell.get("text") or "").strip()
            or cell.get("virtual")
            or cell.get("shapeType") == "polygon"
            or cell.get("textDirection") == "vertical"
            or cell.get("textAlignment", "left") != "left"
        ):
            continue
        top, bottom = cell["y"] + 3, min(height, cell["y"] + cell["height"] - 3)
        right = cell["x"] + cell["width"]
        if bottom - top < 4 or right >= width:
            continue
        # Overflow stops at the first neighbour that prints its own text.
        limit = width
        for other in cells:
            overlap = min(bottom, other["y"] + other["height"]) - max(top, other["y"])
            if (
                other is not cell
                and other["x"] >= right - 2
                and overlap > 0.5 * (bottom - top)
                and (other.get("text") or "").strip()
            ):
                limit = min(limit, other["x"])
        band = gray[top:bottom, right:limit] < INK
        if band.size == 0:
            continue
        # Horizontal rules crossing the band are lines, not text.
        band = band[band.mean(axis=1) < 0.5]
        edge = gray[top:bottom, max(cell["x"], right - 8) : right - 2] < INK
        edge = edge[edge.mean(axis=1) < 0.5] if edge.size else edge
        edge = edge[:, edge.mean(axis=0) < 0.8] if edge.size else edge
        if band.size == 0 or not edge.size or not edge.any():
            continue  # Text that ends inside its own box does not spill.
        counts = band.sum(axis=0)
        border = counts > 0.8 * band.shape[0]
        allowed = max(6, int(0.4 * cell["height"]))
        last, gap = None, 0
        for column, count in enumerate(counts):
            if border[column]:
                continue
            if count:
                last, gap = column, 0
            else:
                gap += 1
                if gap > allowed:
                    break
        if last is not None and last > 3:
            result[cell["id"]] = int(last + 1)
    return result
