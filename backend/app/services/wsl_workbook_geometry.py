"""Measured WSL Office dimensions; separate from all historical geometry models."""

from __future__ import annotations

import copy
import math
import re
from typing import Any
from xml.etree import ElementTree as ET

from app.domain.print_layout import print_error
from app.services.preserving_print_workbook import ADAPTER
from app.services.print_xml import parse

MODEL = "wsl-fods-physical-length-v1"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"


def apply_dimensions(source: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Project only print coordinates; explicit source identities/bindings are preserved."""
    try:
        if (
            evidence["model"] != MODEL
            or evidence["sourceSha256"] != source["sha256"]
            or len(evidence["columns"]) != source["endCol"]
            or len(evidence["rows"]) != source["endRow"]
            or any(
                not isinstance(v, int | float)
                or isinstance(v, bool)
                or not math.isfinite(v)
                or v < 0
                for v in [*evidence["columns"], *evidence["rows"]]
            )
        ):
            raise ValueError("Geometry identity or dimensions changed")
        if any(
            (evidence["columns"][c - 1] == 0) != (c in source.get("hiddenColumns", []))
            for c in range(source["startCol"], source["endCol"] + 1)
        ) or any(
            (evidence["rows"][r - 1] == 0) != (r in source.get("hiddenRows", []))
            for r in range(source["startRow"], source["endRow"] + 1)
        ):
            raise ValueError("Office visibility differs from source dimensions")
        result = copy.deepcopy(source)
        xs, ys = {source["startCol"]: 0.0}, {source["startRow"]: 0.0}
        for column in range(source["startCol"], source["endCol"] + 1):
            xs[column + 1] = xs[column] + evidence["columns"][column - 1]
        for row in range(source["startRow"], source["endRow"] + 1):
            ys[row + 1] = ys[row] + evidence["rows"][row - 1]
        for item in [*result["cells"], *result["fields"]]:
            c1, c2, r1, r2 = (item[key] for key in ("startCol", "endCol", "startRow", "endRow"))
            width, height = xs[c2 + 1] - xs[c1], ys[r2 + 1] - ys[r1]
            if width <= 0 or height <= 0:
                raise ValueError("A retained source cell has no printed extent")
            item.update(x=xs[c1], y=ys[r1], width=width, height=height)
        result.update(
            width=xs[source["endCol"] + 1],
            height=ys[source["endRow"] + 1],
            rowBoundaries=[{"row": r, "y": y} for r, y in ys.items()],
            columnBoundaries=[{"column": c, "x": x} for c, x in xs.items()],
            geometryModelVersion=MODEL,
            geometryEvidence=copy.deepcopy(evidence),
            printScalePolicy="integer-percent-v1",
            printAdapter=ADAPTER,
        )
        if result["width"] <= 0 or result["height"] <= 0:
            raise ValueError("Empty printed source")
        return result
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise print_error(
            "保存した印刷寸法と元セルの対応を確認できません。", "PRINT_GEOMETRY_CHANGED", 409
        ) from exc


def _points(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(cm|mm|in|pt)", value)
    if not match:
        raise ValueError("Unsupported Office dimension unit")
    factors = {"cm": 72 / 2.54, "mm": 72 / 25.4, "in": 72, "pt": 1}
    points = float(match[1]) * factors[match[2]]
    if not math.isfinite(points) or not 0 < points <= 100_000:
        raise ValueError("Invalid Office dimension")
    # Do not reuse the Windows renderer's twip reconstruction contract: this
    # Office build exports inch lengths. Keep the measured lengths unrounded;
    # raster/border calibration must establish their error bounds separately.
    return points


def dimensions(fods: bytes, sheet_name: str, rows: int, columns: int) -> dict[str, Any]:
    """Return point lengths by source row/column; hidden dimensions have zero extent."""
    if not 1 <= rows <= 20_000 or not 1 <= columns <= 16_384:
        raise print_error("Geometry prefix exceeds budget.", "PAGE_GEOMETRY_MISMATCH")
    try:
        parse(fods)  # DTD/entity, encoding, byte, depth and node-count limits.
        root = ET.fromstring(fods)
        styles: dict[str, ET.Element] = {}
        for node in root.iter(STYLE + "style"):
            name = node.get(STYLE + "name", "")
            if not name or name in styles:
                raise ValueError("Duplicate or missing Office style identity")
            styles[name] = node
        sheets = [s for s in root.iter(TABLE + "table") if s.get(TABLE + "name") == sheet_name]
        if len(sheets) != 1:
            raise ValueError("Missing or ambiguous Office sheet")
        result: dict[str, Any] = {"model": MODEL, "unit": "pt"}
        for kind, count in (("column", columns), ("row", rows)):
            lengths: list[float] = []
            hidden: list[int] = []
            nodes: list[ET.Element] = []

            def collect(parent: ET.Element, dimension_kind: str, found: list[ET.Element]) -> None:
                for child in parent:
                    if child.tag == TABLE + "table-" + dimension_kind:
                        found.append(child)
                    elif child.tag in {
                        TABLE + "table-" + dimension_kind + "-group",
                        TABLE + "table-" + dimension_kind + "s",
                        TABLE + "table-header-" + dimension_kind + "s",
                    }:
                        collect(child, dimension_kind, found)

            collect(sheets[0], kind, nodes)
            for node in nodes:
                style = styles.get(node.get(TABLE + "style-name", ""))
                prop = (
                    style.find(STYLE + "table-" + kind + "-properties")
                    if style is not None
                    else None
                )
                if prop is None:
                    raise ValueError("Missing Office dimension style")
                value = prop.get(STYLE + ("column-width" if kind == "column" else "row-height"), "")
                points = _points(value)
                repeat = int(node.get(TABLE + "number-" + kind + "s-repeated", "1"))
                if not 1 <= repeat <= (1_048_576 if kind == "row" else 16_384):
                    raise ValueError("Invalid Office repeat count")
                visibility = node.get(TABLE + "visibility", "visible")
                if visibility not in {"visible", "collapse", "filter"}:
                    raise ValueError("Unknown Office visibility")
                used = min(count - len(lengths), repeat)
                if visibility != "visible":
                    hidden.extend(range(len(lengths) + 1, len(lengths) + used + 1))
                    points = 0.0
                lengths.extend([points] * used)
                if len(lengths) == count:
                    break
            if len(lengths) != count:
                raise ValueError("Incomplete Office geometry prefix")
            result[kind + "s"] = lengths
            result["hidden" + kind.title() + "s"] = hidden
        return result
    except (ValueError, ET.ParseError) as exc:
        raise print_error(
            "Office行高・列幅の測定結果を確定できません。", "PAGE_GEOMETRY_MISMATCH"
        ) from exc
