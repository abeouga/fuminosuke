"""Read modern Excel in-cell checkboxes without evaluating workbook code."""
from __future__ import annotations

import io
import posixpath
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from app.core.errors import AppError

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
FPB = "http://schemas.microsoft.com/office/spreadsheetml/2022/featurepropertybag"
S = "{" + MAIN + "}"


def invalid(detail: str) -> AppError:
    return AppError(status=422, code="XLSX_CHECKLIST_INVALID",
                    title="Excel checklist cannot be read", detail=detail)


def parts(content: bytes) -> dict[str, bytes]:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if (len(entries) > 10000 or sum(e.file_size for e in entries) > 100_000_000
                    or len({e.filename for e in entries}) != len(entries)):
                raise invalid("Excelの展開サイズまたは構成が上限を超えています。")
            return {e.filename: archive.read(e) for e in entries}
    except (BadZipFile, RuntimeError) as exc:
        raise invalid("有効なxlsxファイルを指定してください。") from exc


def xml(data: bytes) -> ET.Element:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise invalid("外部定義を含むXMLは読み取れません。")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise invalid("Excelの内部XMLを読み取れません。") from exc


def sheets(package: dict[str, bytes]) -> dict[str, str]:
    rels = {r.attrib["Id"]: r.attrib for r in xml(package["xl/_rels/workbook.xml.rels"])}
    result = {}
    for sheet in xml(package["xl/workbook.xml"]).findall(f"{S}sheets/{S}sheet"):
        rel = rels[sheet.attrib["{" + REL + "}id"]]
        if rel.get("TargetMode") == "External":
            raise invalid("外部参照のシートは読み取れません。")
        target = rel["Target"]
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        if not path.startswith("xl/") or path not in package:
            raise invalid("シートの参照先が不正です。")
        result[sheet.attrib["name"]] = path
    return result


def _checkbox_cells(content: bytes) -> dict[str, dict[str, bool]]:
    package = parts(content)
    feature_paths = [n for n in package if n.startswith("xl/featurePropertyBag/") and n.endswith(".xml")]
    if "xl/styles.xml" not in package:
        if feature_paths:
            raise invalid("チェックボックスの書式情報がありません。")
        return {}
    styles = xml(package["xl/styles.xml"])
    xfs = styles.find(S + "cellXfs")
    refs = {i: xf.find(".//{" + FPB + "}xfComplement") for i, xf in enumerate(xfs or [])}
    refs = {i: node for i, node in refs.items() if node is not None}
    if not refs:
        return {}
    if len(feature_paths) != 1:
        raise invalid("チェックボックスの表示定義が不足しています。")
    bags = list(xml(package[feature_paths[0]]))
    def bag(index: int, kind: str) -> ET.Element:
        if index < 0 or index >= len(bags) or bags[index].get("type") != kind:
            raise invalid("チェックボックスの表示定義が一致しません。")
        return bags[index]
    roots = [b for b in bags if b.get("type") == "XFComplements"]
    if len(roots) != 1:
        raise invalid("チェックボックスの表示定義を特定できません。")
    array = next((e for e in roots[0] if e.get("k") == "MappedFeaturePropertyBags"), None)
    if array is None:
        raise invalid("チェックボックスの書式参照がありません。")
    indices = [int(e.text or "-1") for e in array]
    def linked(node: ET.Element, key: str, kind: str) -> ET.Element:
        ref = next((e for e in node if e.get("k") == key), None)
        if ref is None:
            raise invalid("チェックボックスの書式参照がありません。")
        return bag(int(ref.text or "-1"), kind)
    checkbox_styles = set()
    for style, ref in refs.items():
        index = int(ref.get("i", "-1"))
        if index < 0 or index >= len(indices):
            raise invalid("チェックボックスの書式番号が範囲外です。")
        complement = bag(indices[index], "XFComplement")
        controls = linked(complement, "XFControls", "XFControls")
        linked(controls, "CellControl", "Checkbox")
        checkbox_styles.add(style)
    result = {}
    for name, path in sheets(package).items():
        found = {}
        for cell in xml(package[path]).iter(S + "c"):
            if int(cell.get("s", "0")) not in checkbox_styles:
                continue
            value = cell.findtext(S + "v")
            if cell.find(S + "f") is not None or cell.get("t") != "b" or value not in {"0", "1"}:
                raise invalid("チェックボックスは確定したTRUE/FALSE値にしてください。数式や混合状態は読み取れません。")
            found[cell.attrib["r"]] = value == "1"
        if found:
            result[name] = found
    return result


def checkbox_cells(content: bytes) -> dict[str, dict[str, bool]]:
    try:
        return _checkbox_cells(content)
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        raise invalid("チェックボックスの内部参照または値が不正です。") from exc


def preview_bytes(content: bytes) -> bytes:
    """Change only checkbox cell values in a disposable rendering copy."""
    package = parts(content)
    if any(n.startswith(("xl/activeX/", "xl/ctrlProps/")) for n in package) or any(
        n.endswith(".vml") and b'ObjectType="Checkbox"' in value
        for n, value in package.items()
    ):
        raise invalid("旧フォーム/ActiveXのチェックボックスは画像にして読み込んでください。セル内チェックボックス形式は利用できます。")
    cells = checkbox_cells(content)
    if not cells:
        return content
    from app.services.paged_workbook import _write_cells
    paths = sheets(package)
    changed = {paths[name]: _write_cells(package[paths[name]], {
        coordinate: "☑" if value else "☐" for coordinate, value in values.items()
    }) for name, values in cells.items()}
    output = io.BytesIO()
    with ZipFile(io.BytesIO(content)) as original, ZipFile(output, "w") as target:
        for entry in original.infolist():
            target.writestr(entry, changed.get(entry.filename, package[entry.filename]))
    return output.getvalue()


def write_values(content: bytes, sheet_name: str, values: dict[str, Any]) -> bytes:
    """Preserve checkbox controls/styles and all other OOXML parts on export."""
    from app.services import print_xml
    from app.services.paged_workbook import _attribute, _write_cells
    from app.services.preserving_print_workbook import S as PS
    package = parts(content)
    path = sheets(package)[sheet_name]
    checks = checkbox_cells(content).get(sheet_name, {})
    data = _write_cells(package[path], {k: v for k, v in values.items() if k not in checks})
    changes = []
    table = print_xml.parse(data).named(PS + "sheetData")[0]
    for cell in (cell for row in table.named(PS + "row") for cell in row.named(PS + "c")):
        coordinate = cell.attrs.get("r")
        if coordinate not in checks or coordinate not in values:
            continue
        if not isinstance(values[coordinate], bool):
            raise invalid("チェックボックスへ保存する値は選択済み・未選択のいずれかにしてください。")
        opening = _attribute(data[cell.start:cell.open_end], "t", "b").removesuffix(b"/>").removesuffix(b">") + b">"
        payload = b"<" + cell.prefix + b"v>" + (b"1" if values[coordinate] else b"0") + b"</" + cell.prefix + b"v>"
        changes.append((cell.start, cell.end, opening + payload + b"</" + cell.qname + b">"))
    data = print_xml.edits(data, changes)
    output = io.BytesIO()
    with ZipFile(io.BytesIO(content)) as original, ZipFile(output, "w") as target:
        for entry in original.infolist():
            target.writestr(entry, data if entry.filename == path else package[entry.filename])
    return output.getvalue()

