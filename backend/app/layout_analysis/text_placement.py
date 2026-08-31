from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

TextDirection = Literal["horizontal", "vertical"]
TextAlignment = Literal["left", "center", "right"]
TextBounds = tuple[float, float, float, float]


def infer_text_placement(
    *,
    cell_width: int,
    cell_height: int,
    text: str,
    bounds: Iterable[TextBounds],
    origin_x: float = 0,
) -> tuple[TextDirection, TextAlignment]:
    normalized_bounds = list(bounds)
    compact_text = "".join(text.split())
    if normalized_bounds:
        left = min(item[0] for item in normalized_bounds)
        top = min(item[1] for item in normalized_bounds)
        right = max(item[2] for item in normalized_bounds)
        bottom = max(item[3] for item in normalized_bounds)
        ink_width = max(1.0, right - left)
        ink_height = max(1.0, bottom - top)
    else:
        left = origin_x
        right = origin_x + cell_width
        ink_width = float(cell_width)
        ink_height = float(cell_height)

    vertical = len(compact_text) >= 2 and (
        cell_height >= cell_width * 1.4 or ink_height >= ink_width * 1.35
    )
    direction: TextDirection = "vertical" if vertical else "horizontal"
    if not normalized_bounds:
        return direction, "center" if vertical else "left"

    left_margin = max(0.0, left - origin_x)
    right_margin = max(0.0, origin_x + cell_width - right)
    tolerance = max(3.0, cell_width * 0.12)
    if left_margin + tolerance < right_margin:
        alignment: TextAlignment = "left"
    elif right_margin + tolerance < left_margin:
        alignment = "right"
    else:
        alignment = "center"
    return direction, alignment
