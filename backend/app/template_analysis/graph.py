"""Closed semantic proposals, complete coverage and dependency-aware edit units."""

from __future__ import annotations

import copy
import json
from typing import Any

from pydantic import ValidationError

from app.editing.authoring_provenance import facets, key
from app.editing.domain.command import digest
from app.editing.errors import reject
from app.template_analysis.contracts import PageProposal

ROLE_NAMES = (
    "label",
    "parent_label",
    "input",
    "mark",
    "record_key_cell",
    "column_key_cell",
    "ignored",
    "unknown",
)


def invalid() -> None:
    raise reject("PROPOSAL_INVALID", "AI案のセル対応・分類・行列・見出しが不正です。", 422)


def validate_graph(raw: dict[str, Any], page: dict[str, Any], task: str) -> dict[str, Any]:
    try:
        proposal = PageProposal.model_validate(raw).model_dump(mode="json", by_alias=True)
    except ValidationError as error:
        raise reject("PROPOSAL_INVALID", "AI案の形式が不正です。", 422) from error
    refs = page["refs"]
    targets = set(page["targets"])
    flat = [r for ids in proposal["roles"].values() for r in ids]
    if len(flat) != len(set(flat)) or set(flat) != targets:
        invalid()
    roles = {r: role for role, ids in proposal["roles"].items() for r in ids}
    roles.update({r: c["role"] for r, c in refs.items() if r not in targets})
    if any(roles[r] != refs[r]["role"] for r in page["lockedRoles"]):
        invalid()
    issues = {v["cell"]: v["reason"] for v in proposal["unresolved"]}
    if len(issues) != len(proposal["unresolved"]) or not set(issues) <= targets:
        invalid()
    if not set(proposal["roles"]["unknown"]) <= set(issues):
        invalid()
    keys: set[str] = set()
    members: set[str] = set()
    row_key_members: set[str] = set()
    parent_edges = {r: page["parentRefs"].get(r) for r in refs}
    assigned_parents: dict[str, str | None] = {}

    def parent(child: str, ancestor: str | None) -> None:
        if (
            child not in refs
            or ancestor == child
            or (ancestor is not None and roles.get(ancestor) != "parent_label")
        ):
            invalid()
        if child in assigned_parents and assigned_parents[child] != ancestor:
            invalid()
        assigned_parents[child] = ancestor
        parent_edges[child] = ancestor

    def labels(ids: list[str]) -> None:
        if len(ids) != len(set(ids)) or any(roles.get(r) != "label" for r in ids):
            invalid()

    def own(ids: list[str]) -> None:
        if (
            len(ids) != len(set(ids))
            or set(ids) & members
            or not set(ids) <= targets
            or any(roles.get(r) != "input" for r in ids)
        ):
            invalid()
        members.update(ids)

    for h in proposal["headings"]:
        if h["cell"] not in targets or roles.get(h["cell"]) != "parent_label" or h["cell"] in keys:
            invalid()
        keys.add(h["cell"])
        parent(h["cell"], h["parent"])
    for field in proposal["fields"]:
        if field["key"] in keys:
            invalid()
        keys.add(field["key"])
        own([field["cell"]])
        labels(field["labels"])
        if (field["axis"] == "direct") != (not field["labels"]):
            invalid()
        for r in field["labels"] or [field["cell"]]:
            parent(r, field["parent"])
    for table in proposal["tables"]:
        if table["key"] in keys or (
            table["parent"] is not None and roles.get(table["parent"]) != "parent_label"
        ):
            invalid()
        keys.add(table["key"])
        if len({c["key"] for c in table["columns"]}) != len(table["columns"]):
            invalid()
        all_labels = [r for c in table["columns"] for r in c["labels"]]
        labels(all_labels)
        row_keys = []
        for row in table["rows"]:
            if len(row["cells"]) != len(table["columns"]):
                invalid()
            own(row["cells"])
            if row["keyCell"] is not None:
                if (
                    row["keyCell"] not in targets
                    or roles.get(row["keyCell"]) != "record_key_cell"
                    or row["keyCell"] in row_key_members
                ):
                    invalid()
                row_key_members.add(row["keyCell"])
                row_keys.append(row["keyCell"])
            if table["rowKeyStrategy"] == "printed" and (not row["keyCell"] or not row["keyValue"]):
                invalid()
        if len(row_keys) != len(set(row_keys)) or (
            table["rowKeyStrategy"] == "printed"
            and len({r["keyValue"] for r in table["rows"]}) != len(table["rows"])
        ):
            invalid()
    for r in parent_edges:
        seen = set()
        current = r
        while current is not None:
            if current in seen or current not in refs:
                invalid()
            seen.add(current)
            current = parent_edges.get(current)
    if task == "roles" and any(proposal[k] for k in ("headings", "fields", "tables")):
        invalid()
    if task == "full":
        if members & set(issues):
            invalid()
        for r in targets:
            if roles[r] == "input" and r not in members and r not in page["bound"]:
                issues.setdefault(r, "missing_binding")
            if roles[r] == "mark" and not refs[r].get("markSets"):
                issues.setdefault(r, "unsupported_mark_definition")
    proposal["unresolved"] = [{"cell": r, "reason": reason} for r, reason in sorted(issues.items())]
    return proposal


def build_units(
    proposal: dict[str, Any], page: dict[str, Any], base: dict[str, Any], task: str
) -> list[dict[str, Any]]:
    roles = {r: role for role, refs in proposal["roles"].items() for r in refs}
    cells, handles = page["refs"], page["handles"]
    baseline = facets(base)
    units: list[dict[str, Any]] = []
    consumed: set[str] = set()

    def unit(
        kind: str, refs: list[str], operations: list[dict[str, Any]], writes: set[str], name: str
    ) -> None:
        unique = set(refs)
        affected = [cells[r]["id"] for r in sorted(unique)]
        role_ops = []
        for r in sorted(unique):
            if r in roles and roles[r] != "unknown" and roles[r] != cells[r]["role"]:
                role_ops.append({"op": "cell.set_role", "cells": [handles[r]], "role": roles[r]})
                writes.add(key(cells[r]["id"], "role"))
                consumed.add(r)
        read_set = {k: digest(v) for k, v in baseline.items() if json.loads(k)[0] in affected}
        protected = bool(writes & set(page["protectedFacets"]))
        state = "protected" if protected else "mergeable"
        if any(key(cells[r]["id"], "role") in writes and r in page["lockedRoles"] for r in unique):
            state = "protected"
        if not role_ops and not operations:
            return
        units.append(
            {
                "unitId": "u_" + digest([page["pageId"], kind, affected, name])[:24],
                "pageId": page["pageId"],
                "kind": kind,
                "cellIds": affected,
                "name": name,
                "state": state,
                "reason": "protected_existing" if protected else None,
                "dependencies": [],
                "roleChanges": [
                    {"cellId": cells[r]["id"], "before": cells[r]["role"], "after": roles[r]}
                    for r in sorted(unique)
                    if r in roles and roles[r] != "unknown" and roles[r] != cells[r]["role"]
                ],
                "details": [
                    f"項目名: {op['name']}" for op in operations if op["op"] == "meaning.create"
                ]
                + [
                    f"{len(op['rows'])}行 / {len(op['columns'])}列"
                    for op in operations
                    if op["op"] == "records.define"
                ],
                "operations": role_ops + operations,
                "readSet": read_set,
                "writeSet": sorted(writes),
            }
        )

    for i, field in enumerate(proposal["fields"]):
        local = f"p{digest(page['pageId'])[:6]}_field_{i}"
        r = field["cell"]
        refs = [r, *field["labels"]] + ([field["parent"]] if field["parent"] else [])
        operations = [
            {"op": "meaning.create", "localRef": local, "name": field["name"], "aliases": []},
            {
                "op": "meaning.bind",
                "meaning": {"localRef": local},
                "inputCells": [handles[r]],
                "labelCells": [handles[v] for v in field["labels"]],
                "axis": field["axis"],
            },
        ]
        writes = {key(cells[r]["id"], f) for f in ("meaning", "binding")}
        if field["parent"]:
            children = field["labels"] or [r]
            operations.append(
                {
                    "op": "heading.attach",
                    "heading": handles[field["parent"]],
                    "children": [handles[v] for v in children],
                }
            )
            writes.update(key(cells[v]["id"], "parent") for v in children)
        unit("field", refs, operations, writes, field["name"])
    for i, table in enumerate(proposal["tables"]):
        prefix = f"p{digest(page['pageId'])[:6]}"
        local = f"{prefix}_table_{i}"
        refs = [r for row in table["rows"] for r in row["cells"]]
        refs += [r for c in table["columns"] for r in c["labels"]]
        refs += [row["keyCell"] for row in table["rows"] if row["keyCell"]]
        if table["parent"]:
            refs.append(table["parent"])
        operations = [
            {
                "op": "meaning.create",
                "localRef": f"{prefix}_col_{i}_{j}",
                "name": c["name"],
                "aliases": [],
            }
            for j, c in enumerate(table["columns"])
        ]
        operations.append(
            {
                "op": "records.define",
                "localRef": local,
                "name": table["name"],
                "columns": [
                    {
                        "meaning": {"localRef": f"{prefix}_col_{i}_{j}"},
                        "labelCells": [handles[r] for r in c["labels"]],
                    }
                    for j, c in enumerate(table["columns"])
                ],
                "rows": [
                    {
                        "group": None,
                        "localRef": f"{prefix}_row_{i}_{j}",
                        "cells": [handles[r] for r in row["cells"]],
                        "rowKeyCell": handles[row["keyCell"]] if row["keyCell"] else None,
                        "rowKeyValue": row["keyValue"],
                    }
                    for j, row in enumerate(table["rows"])
                ],
                "options": {
                    "rowKeyStrategy": table["rowKeyStrategy"],
                    "includeEmptyRows": table["includeEmptyRows"],
                },
                "columnLabels": None,
            }
        )
        if table["parent"]:
            operations.append(
                {
                    "op": "heading.attach",
                    "heading": handles[table["parent"]],
                    "children": [{"localRef": local}],
                }
            )
        writes = {
            key(cells[r]["id"], f)
            for row in table["rows"]
            for r in row["cells"]
            for f in ("records", "meaning", "binding")
        }
        unit("table", refs, operations, writes, table["name"])
    for h in proposal["headings"]:
        r, p = h["cell"], h["parent"]
        ops = []
        if page["parentRefs"].get(r) != p:
            ops = (
                [{"op": "heading.attach", "heading": handles[p], "children": [handles[r]]}]
                if p
                else [{"op": "heading.detach", "children": [handles[r]]}]
            )
        unit(
            "heading",
            [r] + ([p] if p else []),
            ops,
            {key(cells[r]["id"], "parent")} if ops else set(),
            cells[r].get("text") or "見出し",
        )
    for r, role in roles.items():
        if r not in consumed and role != "unknown" and role != cells[r]["role"]:
            unit("role", [r], [], set(), cells[r].get("text") or "分類")
    # A selected unit cannot omit another unit that writes one of its inputs.
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(units):
            for j in range(i + 1, len(units)):
                b = units[j]
                if set(a["writeSet"]) & (set(b["readSet"]) | set(b["writeSet"])) or set(
                    b["writeSet"]
                ) & set(a["readSet"]):
                    a["operations"] += b["operations"]
                    a["roleChanges"] = list(
                        {
                            change["cellId"]: change
                            for change in a["roleChanges"] + b["roleChanges"]
                        }.values()
                    )
                    a["details"] += b["details"]
                    a["readSet"].update(b["readSet"])
                    a["writeSet"] = sorted(set(a["writeSet"]) | set(b["writeSet"]))
                    a["cellIds"] = sorted(set(a["cellIds"]) | set(b["cellIds"]))
                    a["name"] = (a["name"] + "、" + b["name"])[:300]
                    if b["state"] != "mergeable":
                        a["state"], a["reason"] = b["state"], b["reason"]
                    a["unitId"] = "u_" + digest([page["pageId"], a["cellIds"], a["writeSet"]])[:24]
                    units.pop(j)
                    changed = True
                    break
            if changed:
                break
    return units


def public_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            k: copy.deepcopy(v)
            for k, v in u.items()
            if k not in {"operations", "readSet", "writeSet", "geometryPlan"}
        }
        for u in units
    ]
