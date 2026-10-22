"""The order of the blocks on the result screens (2026-10-23).

A block is a table, a heading with its items, or a run of items under no heading. By
default they follow the form from the top, and items on one line (their heights overlap)
from the left, so a cell a few pixels higher on the right does not jump ahead. Items under
no heading sit where they are on the form; neighbours in that order form one block.

Display only: computed when a result is shown, from the accepted snapshot's structure and
mapping positions. The snapshot (and its hash) is never changed, so earlier results get
the same order. A saved block order (an item-join row of kind ``sections``, one member path
per block) moves the blocks it names; the rest keep their form order after them.

Items linked straight to a table's heading but outside its rows (「導入年月日・金額」 under
「売却子牛一覧」) belong to that table: they are shown with it, not as a second block with the
same heading. Which heading a table has comes from the template version's layout.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

Rect = tuple[float, float, float, float]  # x, y, width, height


def table_headings(layout: object) -> dict[str, str]:
    """Table (record section) ID → its heading cell, from a layout (model or JSON)."""
    sections = getattr(layout, "record_sections", None)
    if sections is not None:
        return {
            str(section.id): str(section.parent_label_cell_id)
            for section in sections
            if section.parent_label_cell_id
        }
    if not isinstance(layout, Mapping):
        return {}
    return {
        str(section["id"]): str(heading)
        for section in _records(layout.get("recordSections"))
        if isinstance(section.get("id"), str)
        and isinstance(heading := section.get("parentLabelCellId"), str)
        and heading
    }


def version_layout(session: Any, version_id: object) -> object:
    """The layout JSON of a template version (fixed once the version is in use), or None."""
    if not isinstance(version_id, str) or session is None:
        return None
    from app.db.models import TemplateVersion

    version = session.get(TemplateVersion, version_id)
    return version.layout_json if version is not None else None


def with_blocks(structure: object, raw_mappings: object, layout: object = None) -> object:
    """The structure with ``blocks`` added and each heading's items in line order."""
    if not isinstance(structure, Mapping):
        return structure
    result = copy.deepcopy(dict(structure))
    result["tableHeadings"] = table_headings(layout)
    rects = _rects(raw_mappings)
    for section in _records(result.get("parentSections")):
        ids = [i for i in section.get("fieldIds") or [] if isinstance(i, str)]
        section["fieldIds"] = _reading_order(ids, lambda i: rects.get(i))
    blocks = review_blocks(result, raw_mappings)
    groups = item_groups(result, layout)
    for block in blocks:
        _nest(block, groups, result)
    result["blocks"] = blocks
    return result


def item_groups(structure: Mapping[str, Any], layout: object) -> dict[str, dict[str, Any]]:
    """Field ID → its item-name group ``{id, name}``: items linked to the same printed item
    name (「登録No.」 for its 区分 and 番号 boxes). Names come from the layout's cells."""
    names: dict[str, str] = {}
    for cell in _records(layout.get("cells")) if isinstance(layout, Mapping) else []:
        text = next(
            (
                str(cell[key]).strip()
                for key in ("printedLabelText", "text", "fieldLabel")
                if isinstance(cell.get(key), str) and cell[key].strip()
            ),
            "",
        )
        if isinstance(cell.get("id"), str) and text:
            names[cell["id"]] = text
    result: dict[str, dict[str, Any]] = {}
    for field in _records(structure.get("fields")):
        group = field.get("groupId")
        if not isinstance(group, str) or not group.startswith("labels:"):
            continue
        cell_ids = group.rsplit(":", 1)[-1].split("|")
        name = "・".join(names[i] for i in cell_ids if i in names)
        if name and isinstance(field.get("id"), str):
            result[field["id"]] = {"id": group, "name": name}
    return result


def _nest(
    block: dict[str, Any], groups: Mapping[str, dict[str, Any]], structure: Mapping[str, Any]
) -> None:
    """``itemGroups`` of a heading or loose block: two or more of its items under one item
    name, kept together at the first one. A group naming the whole block adds nothing."""
    key = "attachedFieldIds" if block["kind"] == "record" else "fieldIds"
    ids = list(block.get(key) or [])
    members: dict[str, list[str]] = {}
    for field_id in ids:
        if field_id in groups:
            members.setdefault(groups[field_id]["id"], []).append(field_id)
    title = next(
        (
            str(section.get("title") or "")
            for section in _records(structure.get("parentSections"))
            if section.get("id") == (block.get("attachedId") or block.get("id"))
        ),
        "",
    )
    nested = [
        {"name": groups[found[0]]["name"], "fieldIds": found}
        for found in members.values()
        if len(found) >= 2
        and len(found) < len(ids)
        and groups[found[0]]["name"] != title
    ]
    if not nested:
        return
    # Members of a group follow its first member, so the group is shown in one place.
    grouped = {field_id: group for group in nested for field_id in group["fieldIds"]}
    order: list[str] = []
    for field_id in ids:
        group = grouped.get(field_id)
        if group is None:
            order.append(field_id)
        elif field_id == group["fieldIds"][0]:
            order.extend(group["fieldIds"])
    block[key] = order
    block["itemGroups"] = nested


def review_blocks(structure: Mapping[str, Any], raw_mappings: object) -> list[dict[str, Any]]:
    """Blocks in form order: ``{kind, id?, fieldIds, paths}`` (kind record/parent/independent).

    A table block may carry ``attachedId``/``attachedFieldIds``: the items of its heading
    that lie outside its rows, shown after the table.
    """
    rects = _rects(raw_mappings)
    headings = structure.get("tableHeadings")
    headings = headings if isinstance(headings, Mapping) else {}
    parents = {
        str(section.get("id")): section for section in _records(structure.get("parentSections"))
    }
    path_by_id = {
        str(field["id"]): str(field.get("targetPath") or "")
        for field in _records(structure.get("fields"))
        if isinstance(field.get("id"), str)
    }
    units: list[dict[str, Any]] = []
    placed: set[str] = set()
    attached_ids: set[str] = set()
    for section in _records(structure.get("recordSections")):
        ids = [
            cell["fieldId"]
            for row in _records(section.get("rows"))
            for cell in _records(row.get("cells"))
            if isinstance(cell.get("fieldId"), str)
        ]
        placed.update(ids)
        unit: dict[str, Any] = {"kind": "record", "id": section.get("id"), "fieldIds": ids}
        heading = headings.get(str(section.get("id")))
        attached = parents.get(str(heading)) if heading else None
        if attached is not None and str(heading) not in attached_ids:
            # The first table under the heading takes its other items (shown after it).
            attached_ids.add(str(heading))
            extra = [i for i in attached.get("fieldIds") or [] if isinstance(i, str)]
            placed.update(extra)
            unit.update(attachedId=attached.get("id"), attachedFieldIds=extra)
        units.append(unit)
    for section in _records(structure.get("parentSections")):
        if str(section.get("id")) in attached_ids:
            continue
        ids = [i for i in section.get("fieldIds") or [] if isinstance(i, str)]
        placed.update(ids)
        units.append({"kind": "parent", "id": section.get("id"), "fieldIds": ids})
    loose = [
        i for i in structure.get("independentFieldIds") or [] if isinstance(i, str)
    ] + [i for i in path_by_id if i not in placed]
    for field_id in dict.fromkeys(loose):
        if field_id not in placed:
            placed.add(field_id)
            units.append({"kind": "independent", "id": None, "fieldIds": [field_id]})

    def anchor(unit: dict[str, Any]) -> Rect | None:
        ids = [*unit["fieldIds"], *unit.get("attachedFieldIds", [])]
        found = [rects[i] for i in ids if i in rects]
        return min(found, key=lambda rect: (rect[1], rect[0])) if found else None

    ordered = [
        units[index] for index in _reading_order(range(len(units)), lambda i: anchor(units[i]))
    ]
    blocks: list[dict[str, Any]] = []
    for unit in ordered:
        if unit["kind"] == "independent" and blocks and blocks[-1]["kind"] == "independent":
            blocks[-1]["fieldIds"].extend(unit["fieldIds"])
            continue
        blocks.append({**unit, "fieldIds": list(unit["fieldIds"])})
    for block in blocks:
        ids = [*block["fieldIds"], *block.get("attachedFieldIds", [])]
        block["paths"] = [path_by_id[i] for i in ids if path_by_id.get(i)]
    return blocks


def ordered_blocks(
    blocks: Sequence[Mapping[str, Any]], joins: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """``blocks`` in the saved block order; blocks it does not name keep their place after."""
    order = next((join for join in joins if join.get("kind") == "sections"), None)
    if order is None:
        return list(blocks)
    position = {
        str(member.get("targetPath")): index
        for index, member in enumerate(order.get("members") or [])
        if isinstance(member, Mapping)
    }
    fallback = len(position)
    return sorted(
        blocks,
        key=lambda block: min(
            (position[path] for path in block.get("paths") or [] if path in position),
            default=fallback,
        ),
    )


def _reading_order(keys: Sequence[Any] | range, rect_of: Any) -> list[Any]:
    """Keys by their rectangles: lines from the top (heights overlapping by half), each
    left to right; keys with no rectangle stay last in their given order."""
    with_rect = [(key, rect) for key in keys if (rect := rect_of(key)) is not None]
    without = [key for key in keys if rect_of(key) is None]
    lines: list[list[tuple[Any, Rect]]] = []
    for key, rect in sorted(with_rect, key=lambda item: item[1][1] + item[1][3] / 2):
        line = lines[-1] if lines else None
        if line is not None:
            members = [r for _, r in line] + [rect]
            top = max(r[1] for r in members)
            bottom = min(r[1] + r[3] for r in members)
            if bottom - top >= 0.5 * min(r[3] for r in members):
                line.append((key, rect))
                continue
        lines.append([(key, rect)])
    return [key for line in lines for key, _ in sorted(line, key=lambda item: item[1][0])] + without


def _rects(raw_mappings: object) -> dict[str, Rect]:
    if not isinstance(raw_mappings, Sequence) or isinstance(raw_mappings, str | bytes):
        return {}
    result: dict[str, Rect] = {}
    for mapping in raw_mappings:
        if not isinstance(mapping, Mapping) or not isinstance(mapping.get("mappingId"), str):
            continue
        values = [mapping.get(key) for key in ("x", "y", "width", "height")]
        if all(isinstance(v, int | float) and not isinstance(v, bool) for v in values):
            x, y, width, height = (float(v) for v in values)  # type: ignore[arg-type]
            result[str(mapping["mappingId"])] = (x, y, max(width, 1.0), max(height, 1.0))
    return result


def _records(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
