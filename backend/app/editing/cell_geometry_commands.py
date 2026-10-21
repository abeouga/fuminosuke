"""Spoken physical edits: merge adjacent cells into one rectangle, or delete cells.

Only plain rectangles outside tables and mark/composite settings may change. Meanings
bound only to removed cells are removed with them; a merged input keeps the meaning of
its first part in reading order so the saved field path survives.
"""

from __future__ import annotations

import copy
import os
import re
from typing import Any
from uuid import uuid4

from app.editing.errors import reject
from app.editing.geometry_intents import covered, merge_geometry, overlap
from app.layout_analysis.models import LayoutCell

LABEL_ROLES = {"label", "parent_label"}


def _protected(layout: dict[str, Any], state: dict[str, Any], ids: set[str]) -> None:
    cells = {c["id"]: c for c in layout["cells"]}
    for entity in ids:
        cell = cells[entity]
        if (
            cell.get("virtual")
            or cell.get("shapeType") == "polygon"
            or cell.get("markSets")
            or cell.get("compositeSubfields")
        ):
            raise reject(
                "GEOMETRY_PROTECTED",
                f"「{_name(cell)}」は記号・複合欄・多角形の設定があるため結合・削除できません。",
                409,
            )
    table = {
        e
        for g in layout["recordGroups"]
        for e in [*g["inputCellIds"], g.get("recordKeyCellId")]
        if e
    } | {
        e
        for s in layout["recordSections"]
        for e in [*s["recordLabelCellIds"], *s["rowKeyCellIds"], s.get("parentLabelCellId")]
        if e
    }
    if inside := ids & table:
        cell = cells[sorted(inside)[0]]
        raise reject(
            "GEOMETRY_PROTECTED",
            f"「{_name(cell)}」は表の一部です。表を解除してから結合・削除してください。",
            409,
        )


def _name(cell: dict[str, Any]) -> str:
    return cell.get("fieldLabel") or cell.get("text") or "名称未設定の欄"


def _reading_order(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(cells, key=lambda c: (c["y"], c["x"], c["id"]))


def _drop(
    layout: dict[str, Any], removed: set[str], heading: str | None = None, label: bool = False
) -> list[dict[str, Any]]:
    """Detach removed cells; links to a removed heading move to its merged heading."""
    own = [r for r in layout["relations"] if r["inputCellId"] in removed]
    layout["relations"] = [r for r in layout["relations"] if r["inputCellId"] not in removed]
    for relation in layout["relations"]:
        labels = relation["labelCellIds"]
        kept = [c for c in labels if c not in removed]
        if heading and label and len(kept) != len(labels) and heading not in kept:
            kept.append(heading)
        relation["labelCellIds"] = kept
        if relation.get("parentLabelCellId") in removed:
            relation["parentLabelCellId"] = heading
    for cell in layout["cells"]:
        if cell.get("parentLabelCellId") in removed:
            cell["parentLabelCellId"] = None
    layout["cells"] = [c for c in layout["cells"] if c["id"] not in removed]
    return own


def _prune_meanings(layout: dict[str, Any], state: dict[str, Any], paths: set[str]) -> None:
    """Meanings left without any field are removed unless metadata still uses them."""
    used = {r["targetPath"] for r in layout["relations"]} | {
        m["targetPath"] for c in layout["cells"] for m in c.get("markSets", [])
    }
    orphan = paths - used
    metadata = {e["targetPath"] for e in state.get("documentIdentityFields", [])} | set(
        (state.get("documentName") or {}).get("fieldPaths", [])
    )
    if orphan & metadata:
        raise reject(
            "DEPENDENCY_CONFLICT",
            "書類を見分ける欄・書類名に使っている欄は結合・削除できません。"
            "先に書類の識別・書類名の設定から外してください。",
            409,
        )
    layout["labelPool"] = [m for m in layout["labelPool"] if m["targetPath"] not in orphan]


def merge(
    state: dict[str, Any],
    ids: list[str],
    role: str,
    name: str | None,
    spills: dict[str, int] | None = None,
) -> str:
    layout = state["layout"]
    selected = set(ids)
    _protected(layout, state, selected)
    if sum(any(m["parentCellId"] == i for m in layout["cellMerges"]) for i in selected) > 1:
        raise reject(
            "GEOMETRY_MERGE_INVALID",
            "結合済みの枠どうしは結合できません。結合済みの枠は1つだけにしてください。",
            422,
        )
    parts = _reading_order([c for c in layout["cells"] if c["id"] in selected])
    bounds = dict(x=min(c["x"] for c in parts), y=min(c["y"] for c in parts))
    bounds.update(
        width=max(c["x"] + c["width"] for c in parts) - bounds["x"],
        height=max(c["y"] + c["height"] for c in parts) - bounds["y"],
    )
    others = [c for c in layout["cells"] if c["id"] not in selected and not c.get("virtual")]
    if blocking := [c for c in others if overlap(bounds, c) > 0]:
        raise reject(
            "GEOMETRY_MERGE_INVALID",
            f"結合する範囲に「{_name(blocking[0])}」の枠があります。"
            "間の枠も含めて指示してください。",
            422,
        )
    if covered(bounds, parts) < 0.9 * bounds["width"] * bounds["height"]:
        raise reject("GEOMETRY_MERGE_INVALID", "離れた枠は結合できません。", 422)
    try:
        direction, _ = merge_geometry(parts, layout["canonicalWidth"], layout["canonicalHeight"])
    except Exception:
        # A filled 2D block is still one rectangle; the record only keeps history.
        direction = "horizontal"
    text = "".join(c.get("text") or c.get("printedLabelText") or "" for c in parts)[:300]
    entity = f"manual_{uuid4().hex}"
    cell = LayoutCell(
        id=entity,
        role=role,
        **bounds,
        confidence=1,
        text=text if role in LABEL_ROLES else "",
        printed_label_text=text if role in LABEL_ROLES else "",
        field_label=name,
        source="manual",
        text_direction=parts[0].get("textDirection", "horizontal"),
        text_alignment=parts[0].get("textAlignment", "left"),
        text_vertical_alignment=parts[0].get("textVerticalAlignment"),
    ).model_dump(mode="json", by_alias=True)
    headings = {c["id"] for c in parts if c["role"] in LABEL_ROLES}
    children = {
        c["id"]
        for c in layout["cells"]
        if c.get("parentLabelCellId") in headings and c["id"] not in selected
    }
    order = {c["id"]: i for i, c in enumerate(layout["cells"])}
    own = sorted(
        _drop(
            layout, selected, entity if role in LABEL_ROLES else None, role == "label"
        ),
        key=lambda r: next(i for i, c in enumerate(parts) if c["id"] == r["inputCellId"]),
    )
    # Children and links that pointed at a merged heading point at the merged heading.
    if role in LABEL_ROLES:
        for c in layout["cells"]:
            if c["id"] in children:
                c["parentLabelCellId"] = entity
    position = min(order[c["id"]] for c in parts)
    layout["cells"].insert(min(position, len(layout["cells"])), cell)
    paths = {r["targetPath"] for r in own}
    if role == "input" and own:
        kept = copy.deepcopy(own[0])
        kept.update(inputCellId=entity)
        layout["relations"].append(kept)
        meaning = next(m for m in layout["labelPool"] if m["targetPath"] == kept["targetPath"])
        if name or len(own) > 1:
            # Numbered part names (記入1, 記入2...) describe pieces, not the merged field.
            labels = [
                m["label"]
                for r in own
                for m in layout["labelPool"]
                if m["targetPath"] == r["targetPath"]
            ]
            shared = re.sub(r"[\d\s_-]+$", "", os.path.commonprefix(labels))
            meaning["label"] = name or (shared if len(shared) >= 2 else meaning["label"])
        cell["fieldLabel"] = meaning["label"]
    _prune_meanings(layout, state, paths)
    # Re-merging a merged box records the original sheet cells, never a nested merge.
    history: list[dict[str, Any]] = []
    for part in parts:
        earlier = next((m for m in layout["cellMerges"] if m["parentCellId"] == part["id"]), None)
        if earlier is None:
            history.append(part)
        else:
            history.extend(p["cell"] for p in earlier["parts"])
            layout["cellMerges"].remove(earlier)
    layout["cellMerges"].append(
        dict(
            id=f"merge_{uuid4().hex}",
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
                for c in history
            ],
        )
    )
    if spills is not None and role in LABEL_ROLES:
        # Text measured past a merged label still runs past the new, wider box.
        end = max((c["x"] + c["width"] + spills.get(c["id"], 0) for c in parts), default=0)
        if end > bounds["x"] + bounds["width"] + 3:
            spills[entity] = end - bounds["x"] - bounds["width"]
    return entity


def delete(state: dict[str, Any], ids: list[str]) -> None:
    layout = state["layout"]
    removed = set(ids)
    _protected(layout, state, removed)
    own = _drop(layout, removed)
    _prune_meanings(layout, state, {r["targetPath"] for r in own})


def merge_spoken(
    state: dict[str, Any],
    ids: list[str],
    role: str,
    name: str | None,
    spills: dict[str, int],
) -> None:
    """Boxes covered by a neighbour label's spilled text join that label first.

    "運転免許証" printed in one narrow spreadsheet cell runs over the next two empty
    cells; merging every small box right of it into one input would put printed text
    inside the field. Those boxes become part of the label and the rest is merged.
    """
    layout = state["layout"]
    cells = {c["id"]: c for c in layout["cells"]}
    absorbed: list[str] = []
    ordered = _reading_order([cells[i] for i in ids])
    head = ordered[0]
    if (
        role in LABEL_ROLES
        and spills.get(head["id"])
        and all(not (c.get("text") or "").strip() for c in ordered[1:])
        and ordered[-1]["x"] >= head["x"] + head["width"] + spills[head["id"]] + 3
    ):
        # "運転免許証…を直して" may arrive as one label over every box; boxes past the
        # printed text are write-in space, so they stay a field instead of joining the label.
        merge_spoken(state, ids, "input", None, spills)
        return
    if role == "input":
        printed = [
            i for i in ids if cells[i]["role"] in LABEL_ROLES and (cells[i].get("text") or "")
        ]
        first = _reading_order([cells[i] for i in ids])[0]["id"]
        if printed and printed != [first]:
            raise reject(
                "GEOMETRY_MERGE_INVALID",
                f"記入欄に印字の項目名「{_name(cells[printed[-1]])}」を含められません。",
                422,
            )
        if printed:
            # "運転免許証の右の枠" sometimes arrives with the label itself; keep it a label.
            ids = [i for i in ids if i != first]
        candidates = {first: spills.get(first, 0)} if printed else dict(spills)
        for label_id, spill in candidates.items():
            label = cells.get(label_id)
            if label is None or label_id in ids:
                continue
            right = label["x"] + label["width"]
            row = [
                cells[i]
                for i in ids
                if min(label["y"] + label["height"], cells[i]["y"] + cells[i]["height"])
                - max(label["y"], cells[i]["y"])
                > 0.5 * min(label["height"], cells[i]["height"])
            ]
            edge = right
            for part in sorted(row, key=lambda c: c["x"]):
                if abs(part["x"] - edge) > 3 or part["x"] >= right + spill - 3:
                    break
                absorbed.append(part["id"])
                edge = part["x"] + part["width"]
            if absorbed:
                merge(
                    state, [label_id, *absorbed], label["role"], label.get("fieldLabel"), spills
                )
                break
        if printed and not absorbed and len(ids) < 2:
            raise reject("GEOMETRY_MERGE_INVALID", "結合する記入欄を2つ以上指定してください。", 422)
    rest = [i for i in ids if i not in absorbed]
    if len(rest) >= 2:
        merge(state, rest, role, name, spills)
    elif rest and absorbed:
        # One box remains: it already is the field; only apply the requested name.
        cell = next(c for c in layout["cells"] if c["id"] == rest[0])
        if name:
            cell["fieldLabel"] = name
    elif not absorbed:
        raise reject("GEOMETRY_MERGE_INVALID", "結合する枠を2つ以上指定してください。", 422)
