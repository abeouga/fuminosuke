"""Quarantine conflicting semantic components without repairing AI declarations.

Scope and protected-role checks cover the entire answer before isolation. Each
component and the assembled result use the same strict converter and validator
as a complete answer. Geometry, evidence and the common save path are unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.core.errors import AppError
from app.template_analysis.visual_answer import VisualAnswer, expand_answer
from app.template_analysis.visual_graph import validate_visual_graph
from app.template_analysis.visual_grid import GridCandidate, regular_grid
from app.template_analysis.visual_input import VisualEvidence

POLICY = "template-analysis.visual-isolation.v2"
_LISTS = (
    "labels", "ignored", "recordKeys", "columnKeys", "headings", "values",
    "tables", "grids", "unresolved",
)
_LOCAL_ERRORS = {
    "VISUAL_ANSWER_CONFLICT", "VISUAL_DOUBLE_OWNER", "VISUAL_GRID_COLUMNS",
    "VISUAL_GRID_HOLD_CONFLICT", "VISUAL_ANSWER_HOLD_CONFLICT",
    "VISUAL_ROLE_DUPLICATE", "VISUAL_TABLE_WIDTH",
}
_MARK_ERRORS = {
    "DUPLICATE_MARK_CELL", "DUPLICATE_MARK_OPTION", "DUPLICATE_EVIDENCE_REF",
    "INVALID_MARK_OPTION", "MISSING_MARK_EVIDENCE", "INVALID_CHECKLIST_OPTION",
}


@dataclass(frozen=True)
class _Declaration:
    field: str
    value: Any
    members: frozenset[str]
    dependencies: frozenset[str]


def _declarations(
    answer: dict[str, Any], meta: dict[str, Any], page: dict[str, Any],
    grid: GridCandidate | None,
    extra_dependencies: dict[str, list[str]],
) -> list[_Declaration]:
    """Check every supplied reference/role, including declarations later held."""
    cells = {c["ref"]: c for c in meta["cells"]}
    targets = set(meta["writeRefs"])
    hidden = set(grid.members) - set(grid.headers) if grid else set()
    readings = {r["key"] for r in answer["readings"]}
    if len(readings) != len(answer["readings"]):
        raise ValueError("VISUAL_READING_DUPLICATE")

    def ref(r: str | None, *, write: bool = False, role: str | None = None) -> None:
        if r is None:
            return
        if r not in cells or (write and r not in targets):
            raise ValueError("VISUAL_ANSWER_REFERENCE")
        if r in hidden:
            raise ValueError("VISUAL_HIDDEN_REFERENCE")
        if role is not None:
            if r not in targets and cells[r]["existingRole"] != role:
                raise ValueError("VISUAL_READONLY_ROLE")
            if cells[r]["roleLocked"] and cells[r]["existingRole"] != role:
                raise ValueError("VISUAL_LOCKED_ROLE")

    def reading(name: str | None) -> None:
        if name is not None and name not in readings:
            raise ValueError("VISUAL_READING_REFERENCE")

    for definition in answer["readings"]:
        for r in definition["evidenceRefs"]:
            ref(r)

    entries: list[_Declaration] = []

    def add(
        field: str, value: Any, refs: list[str], key: str | None = None,
        reads: tuple[str, ...] = (),
    ) -> None:
        # Existing parent chains can connect proposed changes through read-only
        # headings. Follow their writable dependencies without modifying them.
        dependencies = set(refs) | set(reads)
        for r in dependencies.copy():
            ancestor = page["parentRefs"].get(r)
            seen: set[str] = set()
            while ancestor is not None and ancestor not in seen:
                seen.add(ancestor)
                dependencies.add(ancestor)
                ancestor = page["parentRefs"].get(ancestor)
        tokens = {"cell:" + r for r in dependencies & targets}
        if key is not None:
            tokens.add("key:" + key)
        entries.append(_Declaration(
            field, value, frozenset(set(refs) & targets), frozenset(tokens),
        ))

    for field, role in (
        ("labels", "label"), ("ignored", "ignored"),
        ("recordKeys", "record_key_cell"), ("columnKeys", "column_key_cell"),
    ):
        for r in answer[field]:
            ref(r, write=True, role=role)
            add(field, r, [r])
    for heading in answer["headings"]:
        r, parent = heading["cell"], heading["parent"]
        ref(r, write=True, role="parent_label")
        ref(parent)
        add("headings", heading, [r] + ([parent] if parent else []), r)
    for value in answer["values"]:
        ref(value["cell"], write=True, role="mark" if value["reading"] else "input")
        reading(value["reading"])
        ref(value["parent"])
        for r in value["labels"]:
            ref(r, role="label")
        reads = tuple(extra_dependencies.get(value["cell"], []))
        for r in reads:
            ref(r)
        add("values", value, [value["cell"], *value["labels"]]
            + ([value["parent"]] if value["parent"] else []), "value_" + value["cell"], reads)
    for table in answer["tables"]:
        refs = [table["parent"]] if table["parent"] else []
        table_reads = []
        ref(table["parent"])
        for column in table["columns"]:
            reading(column["reading"])
            for r in column["labels"]:
                ref(r, role="label")
                refs.append(r)
        for row in table["rows"]:
            ref(row["keyCell"], write=True)
            if row["keyCell"]:
                refs.append(row["keyCell"])
            for i, r in enumerate(row["cells"]):
                # Even an invalid-width row must not hide a foreign reference.
                cell_role = ("mark" if table["columns"][i]["reading"] else "input") \
                    if i < len(table["columns"]) else None
                ref(r, write=True, role=cell_role)
                refs.append(r)
                for dependency in extra_dependencies.get(r, []):
                    ref(dependency)
                    table_reads.append(dependency)
        add("tables", table, refs, table["key"], tuple(table_reads))
    for selection in answer["grids"]:
        if grid is None or selection["grid"] != grid.ref:
            raise ValueError("VISUAL_GRID_REFERENCE")
        ref(selection["parent"])
        for column in selection["columns"]:
            ref(column["header"])
            reading(column["reading"])
        # Issued grids contain only unprotected writable cells. Body references
        # are expanded by the server; they are never accepted in the wire lists.
        refs = list(grid.members) + [c["header"] for c in selection["columns"]]
        refs += [selection["parent"]] if selection["parent"] else []
        add("grids", selection, refs, grid.ref)
    for issue in answer["unresolved"]:
        ref(issue["cell"], write=True)
        add("unresolved", issue, [issue["cell"]])
    return entries


def _components(entries: list[_Declaration]) -> list[list[int]]:
    parents = list(range(len(entries)))

    def root(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    owners: dict[str, int] = {}
    for i, entry in enumerate(entries):
        for token in entry.dependencies:
            if token in owners:
                parents[root(i)] = root(owners[token])
            owners[token] = i
    groups: dict[int, list[int]] = {}
    for i in range(len(entries)):
        groups.setdefault(root(i), []).append(i)
    return list(groups.values())


def _semantic_error(error: ValueError | AppError) -> bool:
    if isinstance(error, ValidationError):
        return all(
            item["loc"][0] == "marks" and item["type"] == "value_error"
            and str(item.get("ctx", {}).get("error")) in _MARK_ERRORS
            for item in error.errors()
        )
    if isinstance(error, AppError):
        return error.code == "PROPOSAL_INVALID"
    return str(error) in _LOCAL_ERRORS


def isolate_answer(
    raw: dict[str, Any], evidence: VisualEvidence, page: dict[str, Any],
    *, extra_dependencies: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Return validated v2; never publish operations from a rejected component."""
    answer = VisualAnswer.model_validate(raw).model_dump(mode="json", by_alias=True)
    meta = json.loads(evidence.metadata_json)
    grid = regular_grid(meta)
    entries = _declarations(answer, meta, page, grid, extra_dependencies or {})

    def validate(candidate: dict[str, Any]) -> dict[str, Any]:
        expanded = expand_answer(candidate, evidence)
        validate_visual_graph(expanded, page)
        return expanded

    try:
        return validate(answer)
    except (ValueError, AppError) as error:
        if not _semantic_error(error):
            raise

    def subset(indices: set[int]) -> dict[str, Any]:
        result: dict[str, Any] = {field: [] for field in _LISTS}
        result.update(version=answer["version"], readings=answer["readings"])
        for i, entry in enumerate(entries):
            if i in indices:
                result[entry.field].append(entry.value)
        return result

    accepted: set[int] = set()
    held: set[str] = set()
    held_grid = False
    for component in _components(entries):
        indices = set(component)
        try:
            validate(subset(indices))
        except (ValueError, AppError) as error:
            if not _semantic_error(error):
                raise
            for i in component:
                held.update(entries[i].members)
                held_grid |= entries[i].field == "grids"
        else:
            accepted.update(indices)
    result = subset(accepted)
    if held_grid:
        assert grid is not None
        result["grids"].append({
            "grid": grid.ref, "name": None, "parent": None, "columns": [],
            "reason": "ambiguous_binding",
        })
        held.difference_update(grid.members)
    result["unresolved"] += [
        {"cell": r, "reason": "ambiguous_binding"} for r in sorted(held)
    ]
    # Recheck the union, including graph-wide constraints and coverage limits.
    # A residual conflict is a failure, never a reason to drop arbitrary edits.
    return validate(result)
