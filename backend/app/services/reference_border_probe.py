"""Byte-preserving measurement copies, never replacements for print artifacts.

Only explicitly selected source ranges receive added measurement styles. No
qualification is issued here; source/probe render evidence must be checked by
the owning version-bound geometry verifier.
"""

from __future__ import annotations

import hashlib
import io
from copy import deepcopy
from typing import Any
from zipfile import ZipFile

import cv2
import numpy as np
from openpyxl.utils.cell import get_column_letter, range_boundaries  # type: ignore[import-untyped]
from PIL import Image

from app.services import print_xml
from app.services.paged_workbook import _attribute
from app.services.preserving_print_workbook import S, _one, _sheet_path, inspect

POLICY = "diagnostic-missing-border-probe-v1"
SIDES = ("left", "right", "top", "bottom")


def probe_copy(
    content: bytes, sheet_name: str, selected: list[dict[str, Any]]
) -> tuple[bytes, dict[str, Any]]:
    """Append measurement styles and replace only selected cell style attributes.

    Existing entries, including old styles and all other worksheet bytes, remain
    intact. Missing serialized cells/inherited styles are explicitly unsupported.
    This is NOT the production print adapter or an image of the original form.
    """
    parts = inspect(content)
    book = print_xml.parse(parts["xl/workbook.xml"])
    sheet_path, _ = _sheet_path(parts, book, sheet_name)
    data = parts[sheet_path]
    sheet = print_xml.parse(data)
    sheet_data = _one(sheet, "sheetData")
    assert sheet_data is not None
    cells = {}
    for row in sheet_data.named(S + "row"):
        for cell in row.named(S + "c"):
            coordinate = cell.attrs["r"]
            if coordinate in cells:
                raise ValueError("Duplicate serialized cell")
            cells[coordinate] = cell
    wanted: dict[str, set[str]] = {}
    identities = set()
    if not 1 <= len(selected) <= 20_000:
        raise ValueError("Invalid probe field count")
    for field in selected:
        identity = field["cellId"]
        sides = field["sides"]
        if (
            not isinstance(identity, str)
            or not identity
            or identity in identities
            or not sides
            or len(sides) != len(set(sides))
            or set(sides) - set(SIDES)
        ):
            raise ValueError("Invalid probe field identity or sides")
        identities.add(identity)
        left, top, right, bottom = range_boundaries(field["sourceRange"])
        if (
            not all(type(v) is int for v in (left, top, right, bottom))
            or not 1 <= left <= right <= 16384
            or not 1 <= top <= bottom <= 1048576
            or (right - left + 1) * (bottom - top + 1) > 20_000
        ):
            raise ValueError("Invalid probe source range")
        for side in sides:
            locations = (
                [(left if side == "left" else right, r) for r in range(top, bottom + 1)]
                if side in {"left", "right"}
                else [(c, top if side == "top" else bottom) for c in range(left, right + 1)]
            )
            for column, row_number in locations:
                coordinate = f"{get_column_letter(column)}{row_number}"
                if coordinate not in cells or "s" not in cells[coordinate].attrs:
                    raise ValueError(
                        "Probe requires existing explicitly styled cells: " + coordinate
                    )
                wanted.setdefault(coordinate, set()).add(side)
                if len(wanted) > 20_000:
                    raise ValueError("Probe cell budget exceeded")
    styles_data = parts["xl/styles.xml"]
    styles = print_xml.parse(styles_data)
    borders_node, xfs_node = _one(styles, "borders"), _one(styles, "cellXfs")
    assert borders_node is not None and xfs_node is not None
    borders, xfs = borders_node.named(S + "border"), xfs_node.named(S + "xf")
    for parent, children in ((borders_node, borders), (xfs_node, xfs)):
        if int(parent.attrs.get("count", "-1")) != len(children):
            raise ValueError("Invalid style count")
    border_extra: list[bytes] = []
    xf_extra: list[bytes] = []
    variants = {}
    cell_edits = []
    for coordinate, sides in sorted(wanted.items()):
        cell = cells[coordinate]
        style_index = int(cell.attrs["s"])
        if not 0 <= style_index < len(xfs):
            raise ValueError("Invalid cell style index")
        key = (style_index, tuple(side for side in SIDES if side in sides))
        if key not in variants:
            xf = xfs[style_index]
            border_index = int(xf.attrs.get("borderId", "0"))
            if not 0 <= border_index < len(borders) or xf.attrs.get("applyBorder") == "0":
                raise ValueError("Unsupported source border style")
            border = borders[border_index]
            changes = []
            for side in key[1]:
                node = _one(border, side, required=False)
                # Refuse to invent schema order or namespace inheritance.
                if node is None:
                    raise ValueError("Missing explicit border side")
                prefix = node.prefix.decode()
                new = (
                    f'<{prefix}{side} style="hair"><{prefix}color rgb="FFFF0000"/></{prefix}{side}>'
                ).encode()
                changes.append((node.start - border.start, node.end - border.start, new))
            border_extra.append(print_xml.edits(styles_data[border.start : border.end], changes))
            opening = _attribute(
                styles_data[xf.start : xf.open_end],
                "borderId",
                str(len(borders) + len(border_extra) - 1),
            )
            opening = _attribute(opening, "applyBorder", "1")
            xf_extra.append(opening + styles_data[xf.open_end : xf.end])
            variants[key] = len(xfs) + len(xf_extra) - 1
        opening = _attribute(data[cell.start : cell.open_end], "s", str(variants[key]))
        cell_edits.append((cell.start, cell.open_end, opening))
    style_edits = []
    for node, old, new_nodes in ((borders_node, borders, border_extra), (xfs_node, xfs, xf_extra)):
        if styles_data[node.open_end - 2 : node.open_end] == b"/>":
            raise ValueError("Empty source style collection")
        opening = _attribute(
            styles_data[node.start : node.open_end], "count", str(len(old) + len(new_nodes))
        )
        style_edits.append((node.start, node.open_end, opening))
        style_edits.append(print_xml.insert_children(styles_data, node, b"".join(new_nodes)))
    changed = {
        sheet_path: print_xml.edits(data, cell_edits),
        "xl/styles.xml": print_xml.edits(styles_data, style_edits),
    }
    for value in changed.values():
        print_xml.parse(value)
    output = io.BytesIO()
    with ZipFile(io.BytesIO(content)) as source, ZipFile(output, "w") as target:
        for entry in source.infolist():
            target.writestr(entry, changed.get(entry.filename, parts[entry.filename]))
    result = output.getvalue()
    inspect(result)
    return result, {
        "policy": POLICY,
        "sourceSha256": hashlib.sha256(content).hexdigest(),
        "probeSha256": hashlib.sha256(result).hexdigest(),
        "changedParts": sorted(changed),
        "cellCount": len(wanted),
        "addedStyles": len(xf_extra),
        "selectedFields": deepcopy(selected),
        "productionQualified": False,
    }


def compare_raster(original: bytes, probe: bytes) -> dict[str, Any]:
    arrays = []
    for content in (original, probe):
        with Image.open(io.BytesIO(content)) as image:
            if image.format != "PNG" or image.width * image.height > 20_000_000:
                raise ValueError("Invalid diagnostic page image")
            arrays.append(np.asarray(image.convert("RGB")))
    a, b = arrays
    if a.shape != b.shape:
        raise ValueError("Probe changed page dimensions")
    # A conservative observation, NOT text-placement or visibility proof.
    color = b.astype(np.int16)
    red = (color[:, :, 0] - color[:, :, 1] > 30) & (color[:, :, 0] - color[:, :, 2] > 30)
    neighborhood = cv2.dilate(red.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    changed = np.any(a != b, axis=2)
    outside = changed & ~neighborhood
    ys, xs = np.nonzero(outside)
    return {
        "changedPixels": int(changed.sum()),
        "redPixels": int(red.sum()),
        "changedOutsideRedNeighborhood": int(outside.sum()),
        "originalInkInsideRedNeighborhood": int(((a.min(axis=2) < 250) & neighborhood).sum()),
        "outsideChangeBounds": (
            [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None
        ),
        "textPlacementVerified": False,
        "productionQualified": False,
    }
