"""Exact arithmetic for LO 7.3.7.2's integer-point PDF emission path.

This is not a general PDF writer model or an admission/qualification decision.
Callers must establish that the Calc integer grid and this emission path apply.
No saved rectangle, crop, legacy geometry, or raster image is changed here.
See docs/verification/calc-pdf-coordinate-precision-20260914.md.
"""
from __future__ import annotations

from fractions import Fraction

POLICY = "calc-integer-point-pdf-millipoint-v1"


def round_away(value: Fraction) -> int:
    """C++ integer unit conversion: nearest, with exact half ties away from zero."""
    if not isinstance(value, Fraction):
        raise ValueError("An exact rational coordinate is required")
    numerator = abs(value.numerator)
    rounded = (2 * numerator + value.denominator) // (2 * value.denominator)
    return -rounded if value < 0 else rounded


def point_millipoints(x_mm100: int, y_mm100: int, zoom_percent: int,
                     page_height_pt: Fraction) -> tuple[int, int]:
    """Predict encoded PDF integers (each unit is 0.001 pt), not raster pixels.

    x/y are page-relative, unscaled Calc integer-grid coordinates. PDF's origin
    is bottom-left; the integer appendPoint path truncates page height before
    subtracting the rounded y. Other paths (B2D/rect/CTM/UserUnit) are not covered.
    """
    if (any(type(v) is not int for v in (x_mm100, y_mm100, zoom_percent))
            or not 10 <= zoom_percent <= 100
            or not isinstance(page_height_pt, Fraction) or page_height_pt <= 0):
        raise ValueError("Invalid integer-point coordinate input")
    factor = Fraction(zoom_percent * 72 * 1000, 100 * 2540)
    height = int(page_height_pt * 1000)
    if not 0 < height < 2**31:
        raise ValueError("PDF page height exceeds the signed integer range")
    x = round_away(x_mm100 * factor)
    y = height - round_away(y_mm100 * factor)
    if not all(-(2**31) <= value < 2**31 for value in (x, y)):
        raise ValueError("Encoded PDF coordinate exceeds the signed integer range")
    return x, y


def representation_bounds(dpi: int) -> dict[str, Fraction]:
    """Source-derived bounds only; they do not certify geometric/crop safety.

    Coordinate conversion contributes <=0.0005 pt. Positive page-height
    truncation adds <0.001 pt only on y. Integer saved rectangle edges contribute
    <=0.5 px unless a collapsed extent is expanded by the planner's min-width
    rule, which a caller must detect separately.
    """
    if type(dpi) is not int or dpi not in {96, 150, 300}:
        raise ValueError("Unsupported raster DPI")
    coordinate = Fraction(dpi, 144_000)
    height = Fraction(dpi, 72_000)
    return {"integerEdgePx": Fraction(1, 2), "coordinatePx": coordinate,
            "pageHeightPxExclusive": height,
            "xTotalPx": Fraction(1, 2) + coordinate,
            "yTotalPxExclusive": Fraction(1, 2) + coordinate + height}
