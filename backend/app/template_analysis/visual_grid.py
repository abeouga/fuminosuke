"""Geometry-only table candidates. Semantics require an explicit model answer."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from app.editing.domain.command import digest


@dataclass(frozen=True)
class GridCandidate:
    ref: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    @property
    def members(self) -> tuple[str, ...]:
        return self.headers + tuple(r for row in self.rows for r in row)


def regular_grid(metadata: dict[str, Any]) -> GridCandidate | None:
    """Offer only an entire, unconfigured rectangular component, without repairs."""
    if metadata.get("structureSemantics"):
        return None
    targets = set(metadata["writeRefs"])
    cells = [c for c in metadata["cells"] if c["ref"] in targets]
    if len(cells) < 6 or any(
        c["vertices"] or c["roleLocked"] or c["hasMeaning"] or c["protectedFacets"] for c in cells
    ):
        return None
    bands: list[list[dict[str, Any]]] = []
    for cell in sorted(cells, key=lambda c: (c["y"], c["x"], c["ref"])):
        if not bands or abs(cell["y"] - bands[-1][0]["y"]) > 2:
            bands.append([])
        bands[-1].append(cell)
    if not 3 <= len(bands) <= 301 or not 2 <= len(bands[0]) <= 100:
        return None
    for row in bands:
        row.sort(key=lambda c: (c["x"], c["ref"]))
        if len(row) != len(bands[0]) or any(
            abs(c["y"] + c["height"] - row[0]["y"] - row[0]["height"]) > 2 for c in row
        ):
            return None
        if any(abs(a["x"] + a["width"] - b["x"]) > 2 for a, b in pairwise(row)):
            return None
        if any(
            abs(c["x"] - h["x"]) > 2 or abs(c["x"] + c["width"] - h["x"] - h["width"]) > 2
            for c, h in zip(row, bands[0], strict=True)
        ):
            return None
    if any(abs(a[0]["y"] + a[0]["height"] - b[0]["y"]) > 2 for a, b in pairwise(bands)):
        return None
    rows = tuple(tuple(c["ref"] for c in row) for row in bands)
    return GridCandidate("g_" + digest([metadata["snapshotPin"], rows])[:20], rows[0], rows[1:])
