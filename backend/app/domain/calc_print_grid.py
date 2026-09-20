"""Calc's integer print grid, separate from historical saved geometry.

Predict from physical dimensions, never from raster positions or field labels.
The explicit authoring adapter uses this model; it is not a qualification certificate.
Qualification requires the fixed inch FODS export, source identity, and absence
of overflow in the UNO dimension export. Small wrapped values cannot be
distinguished from genuine small dimensions using FODS alone; an authoring
candidate computed here does not prove those qualification conditions.
"""
from __future__ import annotations

import math
from typing import Any

POLICY = "calc-print-grid-mm100-v1"
MODEL = "wsl-calc-print-grid-v1"
MAX_DIMENSIONS = 20_000
# LibreOffice 7.3.7.2 cellsuno.cxx / sax converter.cxx export route:
# twip -> nearest mm100 -> uint16 -> four-decimal inches.
# Only the non-overflowing subset is invertible.
MAX_EXPORT_TWIPS = 37153
# The total half-quantum is strictly less than half a twip (0.025pt).
EXPORT_HALF_QUANTUM_PT = 36 / 2540 + .00005 * 72


def exported_twips(points: float) -> int:
    """Invert only values within the bounded fixed-export serialization error."""
    if (isinstance(points, bool) or not isinstance(points, int | float)
            or not math.isfinite(points) or points < 0):
        raise ValueError("Invalid exported physical length")
    twips = math.floor(points * 20 + .5)
    if (not 0 <= twips <= MAX_EXPORT_TWIPS
            or abs(points - twips / 20) > EXPORT_HALF_QUANTUM_PT + 1e-10):
        raise ValueError("Exported length does not identify an integer twip")
    mm100 = (twips * 127 + 36) // 72
    inch_quanta = (mm100 * 10000 + 1270) // 2540
    if abs(points - inch_quanta * 72 / 10000) > 1e-10:
        raise ValueError("Length is not a fixed inch export of the candidate twip")
    return twips


def grid_step(twips: int, *, hidden: bool = False) -> int:
    """FillInfo clamps each visible row/column before accumulating its size."""
    if type(twips) is not int or not 0 <= twips <= 65535 or type(hidden) is not bool:
        raise ValueError("Invalid Calc grid dimension")
    return 0 if hidden else int(min(65535, max(1, twips * (127 / 72))))


def dimension_grid(lengths: list[float], hidden: list[int]) -> dict[str, Any]:
    """Retain one explicit entry per source dimension, including hidden zeros."""
    if (not isinstance(lengths, list) or not 1 <= len(lengths) <= MAX_DIMENSIONS
            or not isinstance(hidden, list)
            or any(type(i) is not int or not 1 <= i <= len(lengths) for i in hidden)
            or hidden != sorted(set(hidden))):
        raise ValueError("Invalid dimension grid inventory")
    concealed = set(hidden)
    twips, steps, boundaries = [], [], [0]
    for index, points in enumerate(lengths, 1):
        value = exported_twips(points)
        if (points == 0) != (index in concealed) or (value == 0 and index not in concealed):
            raise ValueError("Dimension visibility or positive extent changed")
        step = grid_step(value, hidden=index in concealed)
        twips.append(value)
        steps.append(step)
        boundaries.append(boundaries[-1] + step)
    return {"policy": POLICY, "twips": twips, "stepsMm100": steps,
            "boundariesMm100": boundaries, "productionQualified": False}


def frame_origin(paper_twips: int, margin_before: int, margin_after: int,
                 content_twips: int, zoom_percent: int, *, centered: bool) -> int:
    """PrintPage's inclusive page rectangle, then DrawFrame's one-unit offset.

    Inputs are verified page-style twips. The result is unscaled mm100 and can
    be negative at zero margins; never clamp that clipping evidence away.
    """
    if (any(type(v) is not int for v in
            (paper_twips, margin_before, margin_after, content_twips, zoom_percent))
            or not 1 <= paper_twips <= 2_000_000
            or min(margin_before, margin_after, content_twips) < 0
            or margin_before + margin_after >= paper_twips
            or not 10 <= zoom_percent <= 100 or type(centered) is not bool):
        raise ValueError("Invalid Calc page origin inputs")
    first = margin_before * 100 // zoom_percent
    last = (paper_twips - 1 - margin_after) * 100 // zoom_percent
    available = last - first + 1
    if content_twips > available:
        raise ValueError("Printed content exceeds the nominal page extent")
    if centered:
        first += (available - content_twips) // 2
    return math.floor(first * (127 / 72)) - 1
