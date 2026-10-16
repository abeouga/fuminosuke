"""Extend checked semantic units with whole-cell readings and heading names.

All declarations are validated before any unit can be published. Mark cells are
temporarily represented as input while the existing graph validator checks value
ownership. The emitted commands use the common input binding followed by mark
configuration, preserving a separate meaning for every physical row.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from app.editing.authoring_provenance import facets, key
from app.editing.domain.command import digest
from app.template_analysis.graph import build_units, validate_graph
from app.template_analysis.visual_contracts import VisualProposal


def _merge_dependencies(units: list[dict[str, Any]], page_id: str | None) -> None:
    """New reading/heading writes can connect previously independent units."""
    while True:
        pair = next((
            (i, j) for i, a in enumerate(units) for j, b in enumerate(units)
            if i < j and (
                set(a["writeSet"]) & (set(b["readSet"]) | set(b["writeSet"]))
                or set(b["writeSet"]) & set(a["readSet"])
            )
        ), None)
        if pair is None:
            return
        i, j = pair
        a, b = units[i], units.pop(j)
        a["operations"] += b["operations"]
        a["roleChanges"] = list({
            c["cellId"]: c for c in a["roleChanges"] + b["roleChanges"]
        }.values())
        a["details"] += b["details"]
        a["readSet"].update(b["readSet"])
        a["writeSet"] = sorted(set(a["writeSet"]) | set(b["writeSet"]))
        a["cellIds"] = sorted(set(a["cellIds"]) | set(b["cellIds"]))
        a["name"] = (a["name"] + "、" + b["name"])[:300]
        a["unitId"] = "u_" + digest([page_id, a["cellIds"], a["writeSet"]])[:24]


def validate_visual_graph(
    raw: dict[str, Any], page: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """Validate without generating commands; also used for conflict isolation."""
    proposal = VisualProposal.model_validate(raw).model_dump(mode="json", by_alias=True)
    refs = page["refs"]
    targets = set(page["targets"])
    mark_by_ref: dict[str, dict[str, Any]] = {}
    final_roles = {r: role for role, rs in proposal["roles"].items() for r in rs}
    # Check original roles/claims before the internal graph representation.
    flat = [r for rs in proposal["roles"].values() for r in rs]
    if len(flat) != len(set(flat)) or set(flat) != targets:
        raise ValueError("VISUAL_ROLE_COVERAGE")
    if any(final_roles[r] != refs[r]["role"] for r in page["lockedRoles"]):
        raise ValueError("VISUAL_LOCKED_ROLE")
    owners = [f["cell"] for f in proposal["fields"]]
    owners += [r for t in proposal["tables"] for row in t["rows"] for r in row["cells"]]
    for definition in proposal["marks"]:
        if not set(definition["evidenceRefs"]) <= set(refs):
            raise ValueError("VISUAL_MARK_EVIDENCE_REF")
        if page["pageId"] is not None and definition["kind"] == "symbol":
            raise ValueError("VISUAL_PAGED_SYMBOL_UNAVAILABLE")
        for r in definition["cells"]:
            if r not in targets or final_roles.get(r) != "mark":
                raise ValueError("VISUAL_MARK_ROLE")
            if r in mark_by_ref or owners.count(r) != 1:
                raise ValueError("VISUAL_MARK_OWNERSHIP")
            mark_by_ref[r] = definition
    surrogate = copy.deepcopy(proposal)
    surrogate.pop("marks")
    surrogate["version"] = "template-analysis.graph.v1"
    for heading in surrogate["headings"]:
        heading.pop("name")
    surrogate["roles"]["mark"] = [r for r in surrogate["roles"]["mark"] if r not in mark_by_ref]
    surrogate["roles"]["input"] += list(mark_by_ref)
    checked_page = copy.deepcopy(page)
    for r in mark_by_ref:
        # Only the validation copy changes. No original text, geometry, or
        # saved role is edited to make a mark look like an ordinary text field.
        if r in checked_page["lockedRoles"]:
            checked_page["refs"][r]["role"] = "input"
    validated = validate_graph(surrogate, checked_page, "full")
    return proposal, validated, mark_by_ref


def build_visual_units(
    raw: dict[str, Any], page: dict[str, Any], base: dict[str, Any],
    *, notes: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    proposal, validated, mark_by_ref = validate_visual_graph(raw, page)
    refs, handles = page["refs"], page["handles"]
    final_roles = {r: role for role, rs in proposal["roles"].items() for r in rs}
    units = build_units(validated, page, base, "full")
    reverse = {h: r for r, h in handles.items()}
    baseline = facets(base)

    def configure(r: str, meaning: dict[str, str], local: str) -> dict[str, Any]:
        reading = mark_by_ref[r]
        return {
            "op": "mark.configure", "cell": handles[r], "sets": [{
                "set": None, "localRef": local + "s", "meaning": meaning,
                "recognitionMode": reading["kind"],
                "selectionMode": "single" if reading["kind"] == "symbol" else "multiple",
                "options": [{
                    "option": None, "localRef": local + f"o{i}", "label": value,
                    "value": value, "region": "whole-cell",
                } for i, value in enumerate(reading["options"])],
            }],
        }

    for edit in units:
        rewritten = []
        configured = set()
        for operation in edit["operations"]:
            if operation["op"] == "records.define":
                name_by_local = {
                    op["localRef"]: op["name"] for op in edit["operations"]
                    if op["op"] == "meaning.create"
                }
                for row in operation["rows"]:
                    for column, handle in zip(operation["columns"], row["cells"], strict=True):
                        r = reverse[handle]
                        if r not in mark_by_ref:
                            continue
                        # A distinct meaning per physical marked cell prevents
                        # multiple rows from sharing one mark's output path.
                        local = "vm_" + digest([edit["unitId"], r])[:20]
                        meaning = {"localRef": local}
                        rewritten.extend([
                            {"op": "meaning.create", "localRef": local,
                             "name": name_by_local[column["meaning"]["localRef"]], "aliases": []},
                            {"op": "meaning.bind", "meaning": meaning, "inputCells": [handle],
                             "labelCells": column["labelCells"],
                             "axis": "vertical" if column["labelCells"] else "direct"},
                            configure(r, meaning, local),
                        ])
                        configured.add(r)
            rewritten.append(operation)
            if operation["op"] == "meaning.bind":
                for handle in operation["inputCells"]:
                    r = reverse[handle]
                    if r in mark_by_ref:
                        local = "vm_" + digest([edit["unitId"], r])[:20]
                        rewritten.append(configure(r, operation["meaning"], local))
                        configured.add(r)
        edit["operations"] = rewritten
        writes = set(edit["writeSet"])
        for r in configured:
            writes.add(key(refs[r]["id"], "mark"))
            writes.add(key(refs[r]["id"], "reading"))
            for evidence_ref in mark_by_ref[r]["evidenceRefs"]:
                evidence_id = refs[evidence_ref]["id"]
                edit["readSet"].update({
                    k: digest(v) for k, v in baseline.items() if json.loads(k)[0] == evidence_id
                })
            edit["details"].append(
                f"{'記号' if mark_by_ref[r]['kind'] == 'symbol' else 'チェック'}: "
                + " / ".join(mark_by_ref[r]["options"])
            )
        # Report/protect the final role rather than the temporary input state.
        changes = []
        for r, role in final_roles.items():
            if refs[r]["id"] not in edit["cellIds"]:
                continue
            item = key(refs[r]["id"], "role")
            writes.discard(item)
            if role != "unknown" and role != refs[r]["role"]:
                writes.add(item)
                changes.append({"cellId": refs[r]["id"], "before": refs[r]["role"], "after": role})
        edit["writeSet"], edit["roleChanges"] = sorted(writes), changes

    for heading in proposal["headings"]:
        if heading["name"] is None:
            continue
        r = heading["cell"]
        entity = refs[r]["id"]
        if refs[r].get("virtual"):
            raise ValueError("VISUAL_VIRTUAL_HEADING_WRITE")
        units.append({
            "unitId": "u_" + digest([page["pageId"], "heading-name", entity])[:24],
            "pageId": page["pageId"], "kind": "heading", "cellIds": [entity],
            "name": heading["name"], "state": "mergeable", "reason": None,
            "dependencies": [], "roleChanges": [], "details": [f"見出し名: {heading['name']}"],
            "operations": [{
                "op": "heading.update", "heading": handles[r], "name": heading["name"],
            }],
            "readSet": {k: digest(v) for k, v in baseline.items() if json.loads(k)[0] == entity},
            "writeSet": [key(entity, "meaning")],
        })
    for r, note in (notes or {}).items():
        for edit in units:
            if not any(
                (op["op"] == "meaning.bind" and handles[r] in op["inputCells"])
                or (op["op"] == "records.define" and any(
                    handles[r] in row["cells"] for row in op["rows"]
                ))
                for op in edit["operations"]
            ):
                continue
            edit["details"].append("見出し接続なし: " + note["text"])
            evidence_ids = {refs[ref]["id"] for ref in note["refs"]}
            edit["readSet"].update({
                k: digest(v) for k, v in baseline.items() if json.loads(k)[0] in evidence_ids
            })
    _merge_dependencies(units, page["pageId"])
    for edit in units:
        protected = bool(set(edit["writeSet"]) & set(page["protectedFacets"]))
        edit["state"] = "protected" if protected else "mergeable"
        edit["reason"] = "protected_existing" if protected else None
    return units, validated["unresolved"]
