"""Office-imported source dimensions, before print scale or image registration.

FODS lengths are rounded to 0.001 cm by this Office export. Recover the original
integer twips (half the serialization quantum is less than half a twip). This
does not estimate scale from a raster. Legacy geometry is left intact.
"""

from __future__ import annotations

import copy
import hashlib
import io
import math
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from PIL import ImageFont

from app.domain.print_layout import print_error
from app.services.office_reference_renderer import render_office

GEOMETRY_MODEL_VERSION = "lo-import-twip-v1"
LEGACY_GEOMETRY_VERSION = "fixed-7px-plus-5-v1"
NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
}


def normal_font(content: bytes) -> dict[str, Any]:
    with ZipFile(io.BytesIO(content)) as archive:
        raw = archive.read("xl/styles.xml")
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper() or b"\x00" in raw:
            raise print_error("Unsafe styles XML.", "UNSUPPORTED_XLSX_FEATURE")
        styles = ET.fromstring(raw)
    normal = next((s for s in styles.findall("s:cellStyles/s:cellStyle", NS)
                   if s.get("builtinId") == "0"), None)
    xfs = styles.findall("s:cellStyleXfs/s:xf", NS)
    fonts = styles.findall("s:fonts/s:font", NS)
    xf_id = int(normal.get("xfId", "0")) if normal is not None else 0
    font_id = int(xfs[xf_id].get("fontId", "0")) if 0 <= xf_id < len(xfs) else 0
    font = fonts[font_id]
    name, size = font.find("s:name", NS), font.find("s:sz", NS)
    family = name.get("val", "") if name is not None else ""
    point_size = float(size.get("val", "11")) if size is not None else 11.0
    filenames = {"Calibri": "calibri.ttf", "Arial": "arial.ttf", "Meiryo": "meiryo.ttc",
                 "Yu Gothic": "YuGothR.ttc", "Aptos": "aptos.ttf"}
    path = Path("C:/Windows/Fonts") / filenames.get(family, "not-a-supported-font")
    available = path.is_file()
    actual_family = None
    if available:
        actual_family = ImageFont.truetype(str(path), 20).getname()[0]
        available = (actual_family or "").replace(" ", "").casefold() == family.replace(
            " ", "").casefold()
    return {"family": family, "sizePt": point_size, "normalXfId": xf_id, "fontId": font_id,
            "bold": font.find("s:b", NS) is not None,
            "italic": font.find("s:i", NS) is not None,
            "installed": available, "fileFamily": actual_family,
            "fontSha256": hashlib.sha256(path.read_bytes()).hexdigest() if available else None,
            "fallbackVerified": False}


def read_office_dimensions(fods: bytes, sheet_name: str, rows: int, columns: int) -> dict[str, Any]:
    if not 1 <= rows <= 20_000 or not 1 <= columns <= 16_384:
        raise print_error("Geometry prefix expansion exceeds budget.", "UNSUPPORTED_XLSX_FEATURE")
    if (len(fods) > 64 * 1024 * 1024 or b"<!DOCTYPE" in fods.upper()
            or b"<!ENTITY" in fods.upper() or b"\x00" in fods):
        raise print_error("Invalid Office geometry XML.", "UNSUPPORTED_XLSX_FEATURE")
    root = ET.fromstring(fods)
    styles = {node.get(f"{{{NS['style']}}}name"): node for node in
              root.findall(".//style:style", NS)}
    sheet = next((s for s in root.findall(".//table:table", NS)
                  if s.get(f"{{{NS['table']}}}name") == sheet_name), None)
    if sheet is None:
        raise print_error("Office geometry sheet missing.", "PAGE_GEOMETRY_MISMATCH")
    result: dict[str, Any] = {"model": GEOMETRY_MODEL_VERSION, "unit": "pt"}
    for kind, count in (("column", columns), ("row", rows)):
        lengths: list[float] = []
        for node in sheet.findall(f"table:table-{kind}", NS):
            style = styles.get(node.get(f"{{{NS['table']}}}style-name"))
            prop = style.find(f"style:table-{kind}-properties", NS) if style is not None else None
            key = "column-width" if kind == "column" else "row-height"
            raw = prop.get(f"{{{NS['style']}}}{key}", "") if prop is not None else ""
            if not raw.endswith("cm"):
                raise print_error("Missing Office physical dimension.", "PAGE_GEOMETRY_MISMATCH")
            cm = float(raw[:-2])
            if not math.isfinite(cm) or cm <= 0:
                raise print_error("Invalid Office dimension.", "PAGE_GEOMETRY_MISMATCH")
            points = math.floor(cm * 1440 / 2.54 + 0.5) / 20
            repeat = int(node.get(f"{{{NS['table']}}}number-{kind}s-repeated", "1"))
            lengths.extend([points] * min(max(0, count - len(lengths)), repeat))
            if len(lengths) == count:
                break
        if len(lengths) != count:
            raise print_error("Office dimension count mismatch.", "PAGE_GEOMETRY_MISMATCH")
        result[kind + "s"] = lengths
    return result


def office_geometry(content: bytes, source: dict[str, Any]) -> dict[str, Any]:
    first, last = source["startRow"], source["endRow"]
    plan = {"sourceSha256": source["sha256"], "commonScale": 1.0, "paperSize": "A4",
            "orientation": "portrait", "margins": dict(left=10, right=10, top=10, bottom=10),
            "horizontalCentering": False, "verticalCentering": False, "pages": [{
                "sheetName": source["sheetName"],
                "range": f"{source['startColumn']}{first}:{source['endColumn']}{last}"}]}
    identity, outputs = render_office(content, plan, geometry_only=True)
    dimensions = read_office_dimensions(outputs[0], source["sheetName"], last, source["endCol"])
    dimensions["renderer"] = identity
    dimensions["normalFont"] = normal_font(content)
    dimensions["sourceSha256"] = source["sha256"]
    return dimensions


def apply_geometry(source: dict[str, Any], dimensions: dict[str, Any]) -> dict[str, Any]:
    """Rebuild only new-page source coordinates, keeping identity/ranges/bindings."""
    if (dimensions.get("model") != GEOMETRY_MODEL_VERSION
            or len(dimensions["columns"]) < source["endCol"]
            or len(dimensions["rows"]) < source["endRow"]
            or any(not math.isfinite(v) or v <= 0
                   for v in [*dimensions["columns"], *dimensions["rows"]])):
        raise print_error("Invalid source geometry.", "PRINT_GEOMETRY_CHANGED", 409)
    result = copy.deepcopy(source)
    xs, ys = {source["startCol"]: 0.0}, {source["startRow"]: 0.0}
    for col in range(source["startCol"], source["endCol"] + 1):
        xs[col + 1] = xs[col] + dimensions["columns"][col - 1]
    for row in range(source["startRow"], source["endRow"] + 1):
        ys[row + 1] = ys[row] + dimensions["rows"][row - 1]
    for item in [*result["cells"], *result["fields"]]:
        c1, c2 = item["startCol"], item["endCol"]
        r1, r2 = item["startRow"], item["endRow"]
        item.update(x=xs[c1], y=ys[r1], width=xs[c2 + 1] - xs[c1], height=ys[r2 + 1] - ys[r1])
    result.update(width=xs[source["endCol"] + 1], height=ys[source["endRow"] + 1],
                  rowBoundaries=[{"row": r, "y": y} for r, y in ys.items()],
                  columnBoundaries=[{"column": c, "x": x} for c, x in xs.items()],
                  geometryModelVersion=dimensions["model"], geometryEvidence=dimensions)
    return result
