"""Canonical polygon geometry shared by layout and immutable OCR inputs."""

from pydantic import BaseModel, ConfigDict, Field


class PolygonPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int = Field(ge=0)
    y: int = Field(ge=0)


def validate_polygon(
    points: list[PolygonPoint], x: int, y: int, width: int, height: int,
) -> None:
    if not points:
        return
    pairs = [(point.x, point.y) for point in points]
    if len(pairs) < 3 or len(set(pairs)) != len(pairs):
        raise ValueError("Polygon requires at least three distinct vertices")
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    if (min(xs), min(ys), max(xs), max(ys)) != (x, y, x + width, y + height):
        raise ValueError("Polygon bounds must match the cell rectangle")

    def cross(a: tuple[int, int], b: tuple[int, int], c: tuple[int, int]) -> int:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def intersects(
        a: tuple[int, int], b: tuple[int, int], c: tuple[int, int], d: tuple[int, int],
    ) -> bool:
        if (max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0])
                or max(a[1], b[1]) < min(c[1], d[1])
                or max(c[1], d[1]) < min(a[1], b[1])):
            return False
        return cross(a, b, c) * cross(a, b, d) <= 0 and cross(c, d, a) * cross(c, d, b) <= 0

    area = 0
    for i, a in enumerate(pairs):
        b = pairs[(i + 1) % len(pairs)]
        area += a[0] * b[1] - a[1] * b[0]
        for j in range(i + 1, len(pairs)):
            if j == i + 1 or (i == 0 and j == len(pairs) - 1):
                continue
            if intersects(a, b, pairs[j], pairs[(j + 1) % len(pairs)]):
                raise ValueError("Polygon must not intersect itself")
    if area == 0:
        raise ValueError("Polygon must have a nonzero area")
