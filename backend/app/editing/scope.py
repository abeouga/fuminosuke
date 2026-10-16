"""Compute semantic effects once for page and selected-cell boundaries."""

from __future__ import annotations

from typing import Any

from app.editing.commands import Plan


def changed_ids(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> set[str]:
    old, new = ({v["id"]: v for v in values} for values in (before, after))
    return {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}


def affected_cells(plan: Plan) -> tuple[set[str], bool]:
    from app.editing.semantic_commands import section_cells

    before, after = plan.before["layout"], plan.after["layout"]
    changed = changed_ids(before["cells"], after["cells"])
    relations = changed_ids(before["relations"], after["relations"])
    meanings = changed_ids(before["labelPool"], after["labelPool"])
    sections = changed_ids(before["recordSections"], after["recordSections"])
    groups = changed_ids(before["recordGroups"], after["recordGroups"])
    paths = {
        p["targetPath"]
        for graph in (before, after)
        for p in graph["labelPool"]
        if p["id"] in meanings
    }
    bound_paths: set[str] = set()
    for graph in (before, after):
        for relation in graph["relations"]:
            if relation["id"] in relations or relation["targetPath"] in paths:
                changed.add(relation["inputCellId"])
            if relation["targetPath"] in paths:
                bound_paths.add(relation["targetPath"])
        for cell in graph["cells"]:
            for mark in cell.get("markSets", []):
                if mark["targetPath"] in paths:
                    changed.add(cell["id"])
                    bound_paths.add(mark["targetPath"])
        for section in graph["recordSections"]:
            if section["id"] in sections or any(
                g["id"] in groups and g["sectionId"] == section["id"] for g in graph["recordGroups"]
            ):
                changed.update(section_cells(graph, section["id"]))
    metadata_paths: set[str] = set()
    if plan.before["documentIdentityFields"] != plan.after["documentIdentityFields"]:
        old = {e["targetPath"]: e for e in plan.before["documentIdentityFields"]}
        new = {e["targetPath"]: e for e in plan.after["documentIdentityFields"]}
        metadata_paths.update(p for p in old.keys() | new.keys() if old.get(p) != new.get(p))
    if plan.before["documentName"] != plan.after["documentName"]:
        metadata_paths.update(
            p
            for state in (plan.before, plan.after)
            for p in (state["documentName"] or {}).get("fieldPaths", [])
        )
    for graph in (before, after):
        changed.update(
            r["inputCellId"] for r in graph["relations"] if r["targetPath"] in metadata_paths
        )
        changed.update(
            c["id"]
            for c in graph["cells"]
            if any(m["targetPath"] in metadata_paths for m in c.get("markSets", []))
        )
    return changed, bool(paths - bound_paths)
