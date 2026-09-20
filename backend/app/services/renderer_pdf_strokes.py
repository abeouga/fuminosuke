"""Bounded straight-path inventory for an isolated, already validated PDF stream.

This does not parse files, prove final visibility, infer cell identities, or issue
a geometry certificate. The glyph/PDF validator must validate resources and text
before its operations are passed here. Coordinates are default PDF page points.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

POLICY = "wsl-pdf-stroke-inventory-v2"
MAX_OPERATIONS = 500_000
MAX_SEGMENTS = 100_000
MAX_COORDINATE = 1_000_000.0
TEXT_OPERATORS = {
    b"BT", b"ET", b"Tf", b"Tr", b"Tc", b"Tw", b"Tz", b"TL", b"Ts", b"Td", b"TD",
    b"Tm", b"T*", b"Tj", b"TJ", b"'", b'"',
}
Point = tuple[float, float]
Matrix = tuple[float, float, float, float, float, float]


def numbers(values: list[Any], count: int) -> list[float]:
    if (len(values) != count or any(isinstance(v, bool) or not isinstance(v, int | float)
            or abs(v) > MAX_COORDINATE or not math.isfinite(v) for v in values)):
        raise ValueError("Invalid PDF stroke numeric operands")
    return [float(v) for v in values]


def point(matrix: Matrix, x: float, y: float) -> Point:
    a, b, c, d, e, f = matrix
    result = (a * x + c * y + e, b * x + d * y + f)
    numbers(list(result), 2)
    return result


def concatenate(current: Matrix, operand: list[float]) -> Matrix:
    a, b, c, d, e, f = current
    aa, bb, cc, dd, ee, ff = operand
    result = (a * aa + c * bb, b * aa + d * bb, a * cc + c * dd, b * cc + d * dd,
              a * ee + c * ff + e, b * ee + d * ff + f)
    numbers(list(result), 6)
    if abs(result[0] * result[3] - result[1] * result[2]) < 1e-12:
        raise ValueError("Degenerate PDF stroke transform")
    return result


@dataclass
class State:
    matrix: Matrix = (1, 0, 0, 1, 0, 0)
    width: float = 1
    cap: int = 0
    join: int = 0
    miter: float = 10
    dash: tuple[float, ...] = ()
    phase: float = 0
    color: tuple[float, ...] = (0,)
    color_space: str = "DeviceGray"
    clipped: bool = False


def inventory(operations: list[tuple[list[Any], bytes]]) -> dict[str, Any]:
    if not 0 < len(operations) <= MAX_OPERATIONS:
        raise ValueError("PDF stroke operation budget exceeded")
    state = State()
    stack: list[State] = []
    segments: list[tuple[Point, Point, float | None]] = []
    current: Point | None = None
    start: Point | None = None
    curved = False
    pending_clip = False
    subpath_distance: float | None = 0
    result: list[dict[str, Any]] = []
    total_segments = 0
    paint_operations = 0
    curve_strokes = 0

    def line(end: Point) -> None:
        nonlocal current, total_segments, subpath_distance
        if current is None:
            raise ValueError("PDF line has no current point")
        total_segments += 1
        if total_segments > MAX_SEGMENTS:
            raise ValueError("PDF stroke segment budget exceeded")
        segments.append((current, end, subpath_distance))
        if subpath_distance is not None:
            a, b, c, d, _, _ = state.matrix
            determinant = a * d - b * c
            dx, dy = end[0] - current[0], end[1] - current[1]
            source_length = math.hypot((d * dx - c * dy) / determinant,
                                       (a * dy - b * dx) / determinant)
            subpath_distance += source_length
            numbers([subpath_distance], 1)
        current = end

    def close() -> None:
        if start is None or current is None:
            raise ValueError("PDF close path has no current subpath")
        if current != start:
            line(start)

    for ordinal, (args, operator) in enumerate(operations, 1):
        if not isinstance(args, list) or not isinstance(operator, bytes):
            raise ValueError("Invalid PDF stroke operation")
        if operator in TEXT_OPERATORS:
            if current is not None or pending_clip:
                raise ValueError("Text interrupted a PDF path")
            if operator in {b"Tj", b"TJ", b"'", b'"'}:
                paint_operations += 1
            continue  # Text/font syntax is validated by the caller, not here.
        if operator == b"m":
            x, y = numbers(args, 2)
            current = start = point(state.matrix, x, y)
            subpath_distance = 0
        elif operator == b"l":
            x, y = numbers(args, 2)
            line(point(state.matrix, x, y))
        elif operator == b"re":
            x, y, w, h = numbers(args, 4)
            current = start = point(state.matrix, x, y)
            subpath_distance = 0
            for xx, yy in ((x + w, y), (x + w, y + h), (x, y + h), (x, y)):
                line(point(state.matrix, xx, yy))
        elif operator == b"h":
            numbers(args, 0)
            close()
        elif operator in {b"c", b"v", b"y"}:
            values = numbers(args, 6 if operator == b"c" else 4)
            if current is None:
                raise ValueError("PDF curve has no current point")
            # Never approximate a Bezier curve by its endpoint chord.
            current = point(state.matrix, values[-2], values[-1])
            curved = True
            subpath_distance = None
        elif operator in {b"W", b"W*"}:
            numbers(args, 0)
            if current is None or pending_clip:
                raise ValueError("Invalid PDF clipping path")
            pending_clip = True
        elif operator in {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"n"}:
            numbers(args, 0)
            if operator in {b"s", b"b", b"b*"}:
                close()
            stroked = operator in {b"S", b"s", b"B", b"B*", b"b", b"b*"}
            if operator != b"n":
                paint_operations += 1
            if stroked and curved:
                curve_strokes += 1
            if stroked:
                for left, right, distance in segments:
                    result.append({"from": list(left), "to": list(right),
                                   "operation": ordinal, "width": state.width,
                                   "strokeMatrix": list(state.matrix),
                                   "lineCap": state.cap, "lineJoin": state.join,
                                   "miterLimit": state.miter, "dash": list(state.dash),
                                   "dashPhase": state.phase, "colorSpace": state.color_space,
                                   "subpathDistance": distance,
                                   "color": list(state.color),
                                   "clippingUnverified": state.clipped or pending_clip,
                                   "sharesPathWithCurve": curved})
            if pending_clip:
                state = replace(state, clipped=True)
            segments, current, start, curved, pending_clip = [], None, None, False, False
        else:
            if current is not None or pending_clip:
                raise ValueError("PDF graphics state interrupted a path")
            if operator == b"q":
                numbers(args, 0)
                if len(stack) >= 256:
                    raise ValueError("PDF stroke graphics stack budget exceeded")
                stack.append(replace(state))
            elif operator == b"Q":
                numbers(args, 0)
                if not stack:
                    raise ValueError("Unbalanced PDF stroke graphics state")
                state = stack.pop()
            elif operator == b"cm":
                state.matrix = concatenate(state.matrix, numbers(args, 6))
            elif operator in {b"w", b"J", b"j", b"M", b"i"}:
                value = numbers(args, 1)[0]
                if value < 0 or (operator in {b"J", b"j"} and value not in {0, 1, 2}):
                    raise ValueError("Invalid PDF stroke style")
                if operator == b"w":
                    state.width = value
                elif operator == b"J":
                    state.cap = int(value)
                elif operator == b"j":
                    state.join = int(value)
                elif operator == b"M":
                    if value < 1:
                        raise ValueError("Invalid PDF miter limit")
                    state.miter = value
                elif value > 100:
                    raise ValueError("Invalid PDF flatness")
            elif operator == b"d":
                if len(args) != 2 or not isinstance(args[0], list) or len(args[0]) > 256:
                    raise ValueError("Invalid PDF dash array")
                dash = numbers(args[0], len(args[0]))
                phase = numbers([args[1]], 1)[0]
                if any(v < 0 for v in dash) or (dash and not any(dash)) or phase < 0:
                    raise ValueError("Invalid PDF dash values")
                state.dash, state.phase = tuple(dash), phase
            elif operator in {b"G", b"g", b"RG", b"rg", b"K", b"k"}:
                size = {b"G": 1, b"RG": 3, b"K": 4}[operator.upper()]
                color = numbers(args, size)
                if any(not 0 <= v <= 1 for v in color):
                    raise ValueError("Invalid PDF device color")
                if operator.isupper():
                    state.color, state.color_space = tuple(color), {
                        1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}[size]
            elif operator == b"ri" and len(args) == 1 and str(args[0]) in {
                "/AbsoluteColorimetric", "/RelativeColorimetric", "/Saturation", "/Perceptual",
            }:
                pass
            else:
                raise ValueError("Unsupported PDF stroke operation")
    if stack or current is not None or pending_clip:
        raise ValueError("Unfinished PDF stroke graphics state")
    return {"policy": POLICY, "segments": result, "paintOperations": paint_operations,
            "curvedStrokePaths": curve_strokes, "coordinateSpace": "default-page-points",
            "visibilityVerified": False, "productionQualified": False}
