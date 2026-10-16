"""Physical row-key correspondence shared by analysis and authoring saves."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

CONTACT_PIXELS = 2
MIN_ROW_OVERLAP = 0.5


def _overlap(a: Mapping[str, Any], b: Mapping[str, Any], axis: str, size: str) -> float:
    return float(min(a[axis] + a[size], b[axis] + b[size]) - max(a[axis], b[axis]))


def validate_row_keys(
    cells: Mapping[str, Mapping[str, Any]],
    rows: Sequence[Sequence[str]],
    row_keys: Sequence[str | None],
) -> None:
    """A key must belong to exactly its declared row, without covering body cells."""
    if len(rows) != len(row_keys):
        raise ValueError("RECORD_KEY_ROW_ALIGNMENT")
    keys = [key for key in row_keys if key is not None]
    body = {ref for row in rows for ref in row}
    if (
        len(keys) != len(set(keys)) or body.intersection(keys)
        or not (body | set(keys)) <= cells.keys()
    ):
        raise ValueError("RECORD_KEY_OWNERSHIP")
    for index, key in enumerate(row_keys):
        if key is None:
            continue
        cell = cells[key]
        matches = [
            i for i, row in enumerate(rows)
            if row and all(
                _overlap(cell, cells[ref], "y", "height")
                >= MIN_ROW_OVERLAP * min(cell["height"], cells[ref]["height"])
                for ref in row
            )
        ]
        if matches != [index]:
            raise ValueError("RECORD_KEY_ROW_ALIGNMENT")
        if any(
            _overlap(cell, cells[ref], "x", "width") > CONTACT_PIXELS
            and _overlap(cell, cells[ref], "y", "height") > CONTACT_PIXELS
            for ref in body
        ):
            raise ValueError("RECORD_KEY_BODY_OVERLAP")


def validate_record_row_keys(layout: Mapping[str, Any]) -> None:
    """Validate persisted rows too, including a save with no semantic operations."""
    cells = {cell["id"]: cell for cell in layout["cells"]}
    groups = layout.get("recordGroups", [])
    keys = [group["recordKeyCellId"] for group in groups if group.get("recordKeyCellId")]
    body = {ref for group in groups for ref in group["inputCellIds"]}
    if len(keys) != len(set(keys)) or body.intersection(keys) or any(
        key not in cells or cells[key]["role"] != "record_key_cell" for key in keys
    ):
        raise ValueError("RECORD_KEY_OWNERSHIP")
    for section in layout.get("recordSections", []):
        members = [group for group in groups if group["sectionId"] == section["id"]]
        validate_row_keys(
            cells, [group["inputCellIds"] for group in members],
            [group.get("recordKeyCellId") for group in members],
        )
