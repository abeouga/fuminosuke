"""Validate AI physical intent against trusted scope, provenance and geometry."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from app.editing.catalog import Catalog
from app.editing.domain.command import digest
from app.editing.errors import reject
from app.editing.geometry_contracts import (
    Add,
    GeometryOperation,
    Merge,
    Rectangle,
    RemoveDuplicate,
    Resize,
    Split,
)
from app.layout_analysis.models import LayoutCell

POLICY = "geometry-intent.v1"


@dataclass(frozen=True)
class GeometryContext:
    refs: dict[str, str]
    writable: frozenset[str]
    editable: frozenset[str]
    bounds: tuple[int, int, int, int]
    namespace: str
    enabled: bool


@dataclass(frozen=True)
class GeometryIntentResult:
    desired: dict[str, Any]
    aliases: dict[str, str]
    changed: frozenset[str]
    removed: frozenset[str]


def overlap(a: dict[str, Any], b: dict[str, Any]) -> int:
    return int(
        max(0, min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"]))
        * max(0, min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"]))
    )


def covered(rect: dict[str, Any], others: list[dict[str, Any]]) -> int:
    """Exact union of clipped rectangles, never sum overlapping areas twice."""
    clips = []
    for cell in others:
        x = max(rect["x"], cell["x"])
        right = min(rect["x"] + rect["width"], cell["x"] + cell["width"])
        y = max(rect["y"], cell["y"])
        bottom = min(rect["y"] + rect["height"], cell["y"] + cell["height"])
        if right > x and bottom > y:
            clips.append((x, right, y, bottom))
    area = 0
    for left, right in pairwise(sorted({v for c in clips for v in c[:2]})):
        intervals = sorted((c[2], c[3]) for c in clips if c[0] < right and c[1] > left)
        end = -1
        length = 0
        for top, bottom in intervals:
            length += max(0, bottom - max(top, end))
            end = max(end, bottom)
        area += (right - left) * length
    return area


def merge_geometry(
    cells: list[dict[str, Any]], width: int, height: int
) -> tuple[str, dict[str, int]]:
    bounds = dict(x=min(c["x"] for c in cells), y=min(c["y"] for c in cells))
    bounds.update(
        width=max(c["x"] + c["width"] for c in cells) - bounds["x"],
        height=max(c["y"] + c["height"] for c in cells) - bounds["y"],
    )
    gap = max(8, min(width, height) * 0.012)

    def aligned(axis: str, size: str, across: str, breadth: str) -> bool:
        ordered = sorted(cells, key=lambda c: (c[axis], c[across], c["id"]))
        return all(
            min(a[across] + a[breadth], b[across] + b[breadth]) - max(a[across], b[across])
            >= 0.25 * min(a[breadth], b[breadth])
            and b[axis] - a[axis] - a[size] <= gap
            for a, b in pairwise(ordered)
        )

    horizontal = aligned("x", "width", "y", "height")
    vertical = aligned("y", "height", "x", "width")
    if not horizontal and not vertical:
        raise reject("GEOMETRY_MERGE_INVALID", "離れた枠を結合できません。", 422)
    direction = (
        "horizontal"
        if horizontal and (not vertical or bounds["width"] >= bounds["height"])
        else "vertical"
    )
    return direction, bounds


def compile_intents(
    catalog: Catalog,
    operations: list[GeometryOperation],
    context: GeometryContext,
) -> GeometryIntentResult:
    if not context.enabled or catalog.pages or catalog.state["kind"] != "template":
        raise reject("GEOMETRY_UNAVAILABLE", "この対象でAIの矩形編集は利用できません。", 422)
    desired = copy.deepcopy(catalog.state["layout"])
    original: dict[str, dict[str, Any]] = {c["id"]: c for c in desired["cells"]}
    if not context.writable <= original.keys() or not context.editable <= context.writable:
        raise reject("GEOMETRY_CONTEXT_INVALID", "矩形の編集権限が一致しません。", 409)
    cells = dict(original)
    aliases = {}
    changed = set()
    removed = set()
    owned = set()
    bound = {
        e
        for r in desired["relations"]
        for e in [r["inputCellId"], *r["labelCellIds"], r.get("parentLabelCellId")]
        if e is not None
    }
    bound.update(
        e
        for g in desired["recordGroups"]
        for e in [*g["inputCellIds"], g.get("recordKeyCellId")]
        if e is not None
    )
    bound.update(
        e
        for s in desired["recordSections"]
        for e in [*s["recordLabelCellIds"], *s["rowKeyCellIds"], s.get("parentLabelCellId")]
        if e is not None
    )

    def existing(ref: str) -> dict[str, Any]:
        entity = context.refs.get(ref)
        if entity not in cells or entity not in context.editable or entity in owned:
            raise reject("GEOMETRY_PROTECTED", "変更できない枠又は重複した操作です。", 409)
        cell = cells[entity]
        if (
            entity in bound
            or any(
                cell.get(k)
                for k in (
                    "fieldLabel",
                    "parentLabelCellId",
                    "markSets",
                    "readingMode",
                    "compositeSubfields",
                    "virtual",
                )
            )
            or cell.get("shapeType") == "polygon"
        ):
            raise reject("GEOMETRY_PROTECTED", "意味設定又は保護された形状を変更できません。", 409)
        owned.add(entity)
        return cell

    def rect(value: Rectangle) -> dict[str, int]:
        r = value.model_dump()
        x, y, w, h = context.bounds
        if r["x"] < x or r["y"] < y or r["x"] + r["width"] > x + w or r["y"] + r["height"] > y + h:
            raise reject(
                "GEOMETRY_SCOPE_INVALID", "矩形が所有する画像・編集範囲を越えています。", 422
            )
        return r

    def add(ref: str, r: dict[str, int]) -> str:
        if ref in aliases:
            raise reject("GEOMETRY_REFERENCE_DUPLICATE", "新規矩形の参照が重複しています。", 422)
        entity = "manual_" + digest([POLICY, context.namespace, ref])[:32]
        if entity in cells:
            raise reject("GEOMETRY_REFERENCE_DUPLICATE", "新規矩形のIDが重複しています。", 422)
        aliases[ref] = entity
        changed.add(entity)
        cells[entity] = LayoutCell(
            id=entity, **r, role="ignored", source="manual", text="", confidence=1
        ).model_dump(mode="json", by_alias=True)
        return entity

    duplicate_checks = []
    for operation in operations:
        if isinstance(operation, Add):
            add(operation.ref, rect(operation.rect))
        elif isinstance(operation, Resize):
            cell = existing(operation.cell)
            cells[cell["id"]] = {**cell, **rect(operation.rect)}
            changed.add(cell["id"])
        elif isinstance(operation, Split):
            cell = existing(operation.cell)
            split_rects = [rect(p.rect) for p in operation.parts]
            if any(covered(part, [cell]) != part["width"] * part["height"] for part in split_rects):
                raise reject(
                    "GEOMETRY_SPLIT_INVALID", "分割した枠は元の枠の内側に限定します。", 422
                )
            for part, r in zip(operation.parts, split_rects, strict=True):
                add(part.ref, r)
            del cells[cell["id"]]
            removed.add(cell["id"])
        elif isinstance(operation, Merge):
            parts = [existing(r) for r in operation.cells]
            direction, bounds = merge_geometry(
                parts, desired["canonicalWidth"], desired["canonicalHeight"]
            )
            rect(Rectangle.model_validate(bounds))
            selected = {c["id"] for c in parts}
            if any(overlap(bounds, c) > 0 for key, c in cells.items() if key not in selected):
                raise reject("GEOMETRY_MERGE_INVALID", "結合範囲に別の枠があります。", 422)
            entity = add(operation.ref, bounds)
            desired["cellMerges"].append(
                dict(
                    id="merge_" + digest([context.namespace, operation.ref])[:32],
                    parentCellId=entity,
                    direction=direction,
                    parts=[
                        dict(
                            cell=copy.deepcopy(c),
                            partType="literal"
                            if c.get("text") or c.get("printedLabelText")
                            else "input"
                            if c["role"] == "input"
                            else "ignored",
                            literal=c.get("text") or c.get("printedLabelText") or "",
                        )
                        for c in parts
                    ],
                )
            )
            for cell in parts:
                del cells[cell["id"]]
                removed.add(cell["id"])
        elif isinstance(operation, RemoveDuplicate):
            cell = existing(operation.cell)
            duplicate_checks.append((cell, operation.survivors))
            del cells[cell["id"]]
            removed.add(cell["id"])
    for cell, refs in duplicate_checks:
        ids = [aliases.get(r) if r.startswith("n") else context.refs.get(r) for r in refs]
        if len(ids) != len(set(ids)) or any(i not in cells for i in ids):
            raise reject("GEOMETRY_DUPLICATE_INVALID", "重複除去後の残存枠を確認できません。", 422)
        if (
            covered(cell, [cells[i] for i in ids if i is not None])
            < 0.95 * cell["width"] * cell["height"]
        ):
            raise reject("GEOMETRY_DUPLICATE_INVALID", "残す枠が元の枠を十分に覆いません。", 422)
    for entity in changed:
        if entity not in cells:
            continue
        if any(overlap(cells[entity], cell) > 0 for key, cell in cells.items() if key != entity):
            raise reject("GEOMETRY_OVERLAP", "独立した矩形の内部が重なっています。", 422)
    desired["cells"] = [cells[c["id"]] for c in desired["cells"] if c["id"] in cells]
    desired["cells"] += [cells[e] for e in aliases.values()]
    return GeometryIntentResult(desired, aliases, frozenset(changed), frozenset(removed))
