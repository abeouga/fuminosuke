"""Deterministic pixel coverage and single-owner region planning."""

from __future__ import annotations

from typing import Any

from app.editing.domain.command import digest

POLICY = "template-analysis.region-plan.v2"
SOFT_CELLS = 24
MAX_REGIONS = 16


def complete_partition(
    leaves: list[tuple[tuple[int, ...], list[dict[str, Any]], str]],
    cells: list[dict[str, Any]],
    groups: list[set[str]],
) -> bool:
    """Check writable ownership; external protected context is not an owner."""
    owners = {c["ref"]: i for i, (_, members, _) in enumerate(leaves) for c in members}
    if len(owners) != sum(len(members) for _, members, _ in leaves) or set(owners) != {
        c["ref"] for c in cells
    }:
        return False
    for (x, y, w, h), members, _ in leaves:
        if any(
            c["x"] < x - 2
            or c["y"] < y - 2
            or c["x"] + c["width"] > x + w + 2
            or c["y"] + c["height"] > y + h + 2
            for c in members
        ):
            return False
    return all(len({owners[ref] for ref in group if ref in owners}) <= 1 for group in groups)


def repeated_component(cells: list[dict[str, Any]], groups: list[set[str]]) -> set[str]:
    """Conservative dependency hint, never a claim that these cells form a table."""
    bands: list[list[dict[str, Any]]] = []
    for c in sorted(cells, key=lambda c: (c["y"], c["x"], c["ref"])):
        if not bands or abs(c["y"] - bands[-1][0]["y"]) > 2:
            bands.append([])
        bands[-1].append(c)

    def same(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
        return len(a) == len(b) and all(
            all(abs(x[k] - y[k]) <= 2 for k in ["x", "width", "height"])
            for x, y in zip(a, b, strict=True)
        )

    candidates = []
    for band in bands:
        if len(band) < 3:
            continue
        matches = [other for other in bands if same(band, other)]
        if len(matches) < 3:
            continue
        members = {c["ref"] for row in matches for c in row}
        changed = True
        while changed:
            previous = set(members)
            selected = [c for c in cells if c["ref"] in members]
            left = min(c["x"] for c in selected)
            top = min(c["y"] for c in selected)
            right = max(c["x"] + c["width"] for c in selected)
            bottom = max(c["y"] + c["height"] for c in selected)
            for c in cells:
                xo = min(right, c["x"] + c["width"]) - max(left, c["x"])
                yo = min(bottom, c["y"] + c["height"]) - max(top, c["y"])
                if xo > 2 and (yo > 2 or abs(c["y"] + c["height"] - top) <= 2):
                    members.add(c["ref"])
            for group in groups:
                if group & members:
                    members.update(group & {c["ref"] for c in cells})
            changed = members != previous
        selected = [c for c in cells if c["ref"] in members]
        area = (max(c["x"] + c["width"] for c in selected) - min(c["x"] for c in selected)) * (
            max(c["y"] + c["height"] for c in selected) - min(c["y"] for c in selected)
        )
        candidates.append((area, tuple(sorted(members))))
    return set(max(candidates)[1]) if candidates else set()


def plan_regions(source: Any) -> tuple[Any, ...]:
    from app.template_analysis.visual_grid import regular_grid
    from app.template_analysis.visual_input import VisualUnit

    snapshot, page = source.unpack(source.pin)
    cells = [c for c in page["providerCells"] if c["ref"] in page["targets"]]
    bounds = tuple(page["geometryBounds"])
    refs = {c["id"]: r for r, c in page["refs"].items()}
    groups = []
    layout = snapshot["draft"]["layout"]
    for relation in layout.get("relations", []):
        groups.append(
            {
                refs[e]
                for e in [
                    relation["inputCellId"],
                    *relation["labelCellIds"],
                    relation.get("parentLabelCellId"),
                ]
                if e in refs
            }
        )
    from app.editing.semantic_commands import section_cells

    for section in layout.get("recordSections", []):
        groups.append({refs[e] for e in section_cells(layout, section["id"]) if e in refs})
    for merge in layout.get("cellMerges", []):
        groups.append(
            {
                refs[e]
                for e in [merge["parentCellId"], *[p["cell"]["id"] for p in merge["parts"]]]
                if e in refs
            }
        )
    # Mutable shared parents are indivisible. Protected external context stays read-only.
    by_parent: dict[str, set[str]] = {}
    for c in cells:
        parent = page["parentRefs"].get(c["ref"])
        if parent in page["targets"]:
            by_parent.setdefault(parent, {parent}).add(c["ref"])
    groups += list(by_parent.values())
    grid_meta = {
        "snapshotPin": source.pin,
        "writeRefs": page["targets"],
        "cells": [
            dict(
                c,
                roleLocked=c.get("roleLocked", False),
                hasMeaning=c.get("hasMeaning", False),
                protectedFacets=[
                    k for k in page["protectedFacets"] if page["refs"][c["ref"]]["id"] in k
                ],
            )
            for c in cells
        ],
    }
    grid = regular_grid(grid_meta)
    leaves: list[tuple[tuple[int, ...], list[dict[str, Any]], str]] = []
    if grid is not None:
        x = min(c["x"] for c in cells)
        y = min(c["y"] for c in cells)
        right = max(c["x"] + c["width"] for c in cells)
        bottom = max(c["y"] + c["height"] for c in cells)
        # A grid owns its table only. The surrounding undetected space remains work.
        gx, gy, gw, gh = bounds
        leaves.append(((x, y, right - x, bottom - y), cells, "grid"))
        for margin_bounds in [
            (gx, gy, gw, y - gy),
            (gx, bottom, gw, gy + gh - bottom),
            (gx, y, x - gx, bottom - y),
            (right, y, gx + gw - right, bottom - y),
        ]:
            if margin_bounds[2] > 0 and margin_bounds[3] > 0:
                leaves.append((margin_bounds, [], "region"))
    else:
        component = repeated_component(cells, groups)
        if component:
            groups.append(component)
            selected = [c for c in cells if c["ref"] in component]
            x = min(c["x"] for c in selected)
            y = min(c["y"] for c in selected)
            right = max(c["x"] + c["width"] for c in selected)
            bottom = max(c["y"] + c["height"] for c in selected)
            gx, gy, gw, gh = bounds
            seed_boxes = [
                (x, y, right - x, bottom - y),
                (gx, gy, gw, y - gy),
                (gx, bottom, gw, gy + gh - bottom),
                (gx, y, x - gx, bottom - y),
                (right, y, gx + gw - right, bottom - y),
            ]
            for box in seed_boxes:
                bx, by, bw, bh = box
                if bw <= 0 or bh <= 0:
                    continue
                members = [
                    c
                    for c in cells
                    if bx <= c["x"] + c["width"] / 2 < bx + bw
                    and by <= c["y"] + c["height"] / 2 < by + bh
                ]
                leaves.append((tuple(box), members, "region"))
            if not complete_partition(leaves, cells, groups):
                # L-shaped dependencies cannot be repaired by overlapping seed boxes.
                # Repartition the full page with the same dependency-aware cuts below.
                leaves = [(bounds, cells, "region")]
        else:
            leaves = [(bounds, cells, "region")]
        while len(leaves) < MAX_REGIONS:
            choices = []
            for index, (b, members, _) in enumerate(leaves):
                if len(members) <= SOFT_CELLS:
                    continue
                x, y, w, h = b
                for axis, size, low, high in [("x", "width", x, x + w), ("y", "height", y, y + h)]:
                    for cut in sorted(
                        {c[axis] for c in members} | {c[axis] + c[size] for c in members}
                    ):
                        if not low + 32 <= cut <= high - 32 or any(
                            c[axis] + 2 < cut < c[axis] + c[size] - 2 for c in members
                        ):
                            continue
                        a = [c for c in members if c[axis] + c[size] / 2 <= cut]
                        ar = {c["ref"] for c in a}
                        br = {c["ref"] for c in members} - ar
                        if not ar or not br or any(g & ar and g & br for g in groups):
                            continue
                        bb = [c for c in members if c["ref"] in br]
                        boxes = (
                            ((x, y, cut - x, h), (cut, y, x + w - cut, h))
                            if axis == "x"
                            else ((x, y, w, cut - y), (x, cut, w, y + h - cut))
                        )
                        choices.append(
                            (-len(members), abs(len(a) - len(bb)), axis, cut, index, boxes, a, bb)
                        )
            if not choices:
                break
            _, _, _, _, index, boxes, left_cells, right_cells = min(choices, key=lambda c: c[:5])
            leaves[index : index + 1] = [
                (boxes[0], left_cells, "region"),
                (boxes[1], right_cells, "region"),
            ]
    if not complete_partition(leaves, cells, groups):
        from app.editing.errors import reject

        raise reject("REGION_PLAN_INVALID", "依存する欄の範囲を安全に分割できません。", 422)
    units = []
    for b, members, kind in leaves:
        writes = tuple(sorted(c["ref"] for c in members))
        held = len(writes) > 320
        units.append(
            VisualUnit(
                "r_" + digest([POLICY, source.pin, b, writes, kind])[:24],
                writes,
                tuple(sorted(set(page["refs"]) - set(writes))),
                "held" if held else "ready",
                "DEPENDENCY_COMPONENT_LIMIT" if held else None,
                tuple(b),
                kind,
            )
        )
    priority = snapshot.get("priorityBounds")

    def order(u: Any) -> tuple[Any, ...]:
        x, y, w, h = u.bounds
        overlap = 0
        if priority:
            px, py, pw, ph = priority
            overlap = max(0, min(x + w, px + pw) - max(x, px)) * max(
                0, min(y + h, py + ph) - max(y, py)
            )
        return (-overlap, y, x, u.unit_id)

    ordered = tuple(sorted(units, key=order))
    if snapshot.get("regionScope"):
        from app.editing.errors import reject

        scope = snapshot["regionScope"]
        bounds = tuple(scope[k] for k in ["x", "y", "width", "height"])
        scoped_units = tuple(u for u in ordered if u.bounds == bounds)
        if len(scoped_units) != 1:
            raise reject(
                "REGION_SCOPE_INVALID",
                "領域計画と指定範囲が一致しません。計画を再取得してください。",
                422,
            )
        return scoped_units
    return ordered
