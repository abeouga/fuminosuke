"""Validate source-preserving edits before any draft/config write."""

from __future__ import annotations

from typing import Any, NoReturn

from app.domain.print_layout import print_error


def bounds(cells: list[dict[str, Any]]) -> dict[str, Any]:
    x, y = min(c["x"] for c in cells), min(c["y"] for c in cells)
    return {"x": x, "y": y,
            "width": max(c["x"] + c["width"] for c in cells) - x,
            "height": max(c["y"] + c["height"] for c in cells) - y}


def physical_cells(layout: dict[str, Any]) -> list[dict[str, Any]]:
    merges = {m["parentCellId"]: m for m in layout.get("cellMerges", [])}
    return [part["cell"] for cell in layout["cells"] if not is_page_heading(cell)
            for part in (merges[cell["id"]]["parts"] if cell["id"] in merges
                         else [{"cell": cell}])]


def is_page_heading(cell: dict[str, Any]) -> bool:
    return (cell.get("virtual") is True and cell.get("role") == "parent_label"
            and cell.get("source") == "manual" and bool(cell.get("pageRange")))


def validate_page_headings(layout: dict[str, Any], plan: dict[str, Any] | None) -> None:
    """Validate display-only nodes without deriving physical/OCR fields from them."""
    pages = {p["range"]: p for p in (plan or {}).get("pages", [])}
    for cell in layout["cells"]:
        if cell.get("pageRange") is None:
            continue
        page = pages.get(cell["pageRange"])
        if (not is_page_heading(cell) or cell["id"].startswith("xlsx_") or page is None
                or cell["x"] + cell["width"] > page["width"]
                or cell["y"] + cell["height"] > page["height"]):
            raise print_error(
                "仮想見出しの所属ページ・位置が保存ページと一致しません。"
                "ページ設定を変更・解除する場合は、先に対象の仮想見出しを削除してください。",
                "PRINT_VIRTUAL_HEADING_PAGE_INVALID", 409,
            )
    # Virtual nodes cannot impersonate parts of a physical merge.
    if any(is_page_heading(p["cell"]) for m in layout.get("cellMerges", []) for p in m["parts"]):
        raise print_error("仮想見出しはセル結合に使用できません。",
                          "PRINT_AUTHORING_GEOMETRY_CHANGED", 409)


def validate_page_edit(previous: dict[str, Any], proposed: dict[str, Any]) -> None:
    def refuse() -> NoReturn:
        raise print_error("元セルを保持する同一ページ内の結合・解除だけが可能です。",
                          "PRINT_AUTHORING_GEOMETRY_CHANGED", 409)

    originals = {c["id"]: c for c in physical_cells(previous)}
    atoms = physical_cells(proposed)
    if len({c["id"] for c in atoms}) != len(atoms):
        refuse()
    for cell in atoms:
        original = originals.get(cell["id"])
        if original is None or any(cell[k] != original[k]
                                   for k in ("x", "y", "width", "height")):
            refuse()
        if any(cell.get(k) != original.get(k) for k in ("shapeType", "vertices", "virtual")):
            refuse()
    old_merges = {m["parentCellId"]: m for m in previous.get("cellMerges", [])}
    new_merges = {m["parentCellId"]: m for m in proposed.get("cellMerges", [])}
    previous_cells = {c["id"]: c for c in previous["cells"]}
    proposed_cells = {c["id"]: c for c in proposed["cells"]}
    virtual_ids = {c["id"] for c in proposed["cells"] if is_page_heading(c)}
    for cell_id in virtual_ids:
        original = previous_cells.get(cell_id) or originals.get(cell_id)
        if original is not None and (
            not is_page_heading(original)
            or original.get("pageRange") != proposed_cells[cell_id].get("pageRange")
        ):
            refuse()
    for parent_id, merge in new_merges.items():
        parent = proposed_cells.get(parent_id)
        parts = [p["cell"] for p in merge["parts"]]
        if parent is None or any(parent[k] != value for k, value in bounds(parts).items()):
            refuse()
        if parent.get("shapeType") == "polygon" or parent.get("virtual"):
            refuse()
        if parent_id in old_merges:
            if merge != old_merges[parent_id]:
                refuse()
        else:
            if parent_id in originals or parent_id in previous_cells:
                refuse()
            for part in parts:
                if (part["id"] not in previous_cells or part["id"] in old_merges
                        or part.get("shapeType") == "polygon" or part.get("virtual")
                        or part.get("parentLabelCellId") or part["role"] == "mark"
                        or part.get("markSets")):
                    refuse()
    # Display-only page headings do not expand the physical source-cell set.
    restored = {p["cell"]["id"] for parent, merge in old_merges.items()
                if parent not in new_merges for p in merge["parts"]}
    allowed = previous_cells.keys() | new_merges.keys() | restored | virtual_ids
    if not proposed_cells.keys() <= allowed:
        refuse()
