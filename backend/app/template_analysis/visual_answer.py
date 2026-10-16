"""Model wire format: cohesive values and server-issued regular grid selections."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import Field

from app.editing.operation_contracts import Contract
from app.template_analysis.contracts import (
    CellRef,
    Cells,
    Column,
    Issue,
    IssueReason,
    Key,
    Labels,
    Name,
    Row,
)
from app.template_analysis.graph import ROLE_NAMES
from app.template_analysis.visual_contracts import VisualHeading
from app.template_analysis.visual_grid import regular_grid
from app.template_analysis.visual_input import VisualEvidence


class Reading(Contract):
    key: Key
    kind: Literal["symbol", "checklist"]
    options: Annotated[list[Name], Field(min_length=1, max_length=50)]
    evidence_refs: Labels
    evidence_text: Annotated[str, Field(min_length=1, max_length=300, strict=True)]


class ValueMeaning(Contract):
    cell: CellRef
    name: Name
    parent: CellRef | None
    reading: Key | None


class DirectValue(ValueMeaning):
    labels: Annotated[list[CellRef], Field(max_length=0)]
    axis: Literal["direct"]


class LabelledValue(ValueMeaning):
    labels: Annotated[list[CellRef], Field(min_length=1, max_length=30)]
    axis: Literal["horizontal", "vertical"]


# These cross-field constraints also reach the provider's JSON schema. A local
# validator alone would reject the completed response after a paid generation.
ValueAnswer = DirectValue | LabelledValue


class ReadingColumn(Column):
    reading: Key | None


class ExplicitTable(Contract):
    key: Key
    name: Name
    parent: CellRef | None
    columns: Annotated[list[ReadingColumn], Field(min_length=1, max_length=100)]
    rows: Annotated[list[Row], Field(min_length=1, max_length=300)]
    row_key_strategy: Literal["printed", "position"]
    include_empty_rows: bool = Field(strict=True)


class GridColumn(Contract):
    header: CellRef
    name: Name | None
    reading: Key | None


class HeldGrid(Contract):
    grid: Annotated[str, Field(pattern=r"^g_[a-f0-9]{20}$", strict=True)]
    name: Name | None
    parent: CellRef | None
    columns: Annotated[list[GridColumn], Field(max_length=100)]
    reason: IssueReason


class AcceptedGridColumn(GridColumn):
    name: Name


class AcceptedGrid(Contract):
    grid: Annotated[str, Field(pattern=r"^g_[a-f0-9]{20}$", strict=True)]
    name: Name
    parent: CellRef | None
    columns: Annotated[list[AcceptedGridColumn], Field(min_length=1, max_length=100)]
    reason: None


GridAnswer = AcceptedGrid | HeldGrid


class VisualAnswer(Contract):
    version: Literal["template-analysis.visual.v3"]
    labels: Cells
    ignored: Cells
    record_keys: Cells
    column_keys: Cells
    headings: Annotated[list[VisualHeading], Field(max_length=1000)]
    readings: Annotated[list[Reading], Field(max_length=320)]
    values: Annotated[list[ValueAnswer], Field(max_length=500)]
    tables: Annotated[list[ExplicitTable], Field(max_length=100)]
    grids: Annotated[list[GridAnswer], Field(max_length=1)]
    unresolved: Annotated[list[Issue], Field(max_length=1000)]


def expand_answer(raw: dict[str, Any], evidence: VisualEvidence) -> dict[str, Any]:
    """Expand immutable geometry, never guess missing labels, meanings, or readings."""
    answer = VisualAnswer.model_validate(raw).model_dump(mode="json", by_alias=True)
    meta = json.loads(evidence.metadata_json)
    refs = {c["ref"]: c for c in meta["cells"]}
    targets = set(meta["writeRefs"])
    grid = regular_grid(meta)
    hidden = set(grid.members) - set(grid.headers) if grid else set()
    roles: dict[str, str] = {}
    owners: set[str] = set()
    issues: dict[str, str] = {}
    fields, tables = [], []
    definitions = {r["key"]: r for r in answer["readings"]}
    if len(definitions) != len(answer["readings"]):
        raise ValueError("VISUAL_READING_DUPLICATE")
    marks: dict[str, list[str]] = {}

    def classify(
        ref: str, role: str, *, readonly: bool = False, grid_member: bool = False,
    ) -> None:
        if ref not in refs or (ref not in targets and not readonly):
            raise ValueError("VISUAL_ANSWER_REFERENCE")
        if ref in hidden and not grid_member:
            raise ValueError("VISUAL_HIDDEN_REFERENCE")
        if ref not in targets:
            if refs[ref]["existingRole"] != role:
                raise ValueError("VISUAL_READONLY_ROLE")
            return
        if ref in roles and roles[ref] != role:
            raise ValueError("VISUAL_ANSWER_CONFLICT")
        if refs[ref]["roleLocked"] and refs[ref]["existingRole"] != role:
            raise ValueError("VISUAL_LOCKED_ROLE")
        roles[ref] = role

    def own(ref: str, reading: str | None, *, grid_member: bool = False) -> None:
        if ref in owners:
            raise ValueError("VISUAL_DOUBLE_OWNER")
        owners.add(ref)
        classify(ref, "mark" if reading else "input", grid_member=grid_member)
        if reading is not None:
            if reading not in definitions:
                raise ValueError("VISUAL_READING_REFERENCE")
            marks.setdefault(reading, []).append(ref)

    for field, role in [
        ("labels", "label"), ("ignored", "ignored"),
        ("recordKeys", "record_key_cell"), ("columnKeys", "column_key_cell"),
    ]:
        if len(answer[field]) != len(set(answer[field])):
            raise ValueError("VISUAL_ROLE_DUPLICATE")
        for ref in answer[field]:
            classify(ref, role)
    for heading in answer["headings"]:
        classify(heading["cell"], "parent_label")
    for value in answer["values"]:
        own(value["cell"], value["reading"])
        for ref in value["labels"]:
            classify(ref, "label", readonly=True)
        fields.append({
            k: v for k, v in value.items() if k != "reading"
        } | {"key": "value_" + value["cell"]})
    for table in answer["tables"]:
        columns = table["columns"]
        for column in columns:
            for ref in column["labels"]:
                classify(ref, "label", readonly=True)
        for row in table["rows"]:
            if len(row["cells"]) != len(columns):
                raise ValueError("VISUAL_TABLE_WIDTH")
            for ref, column in zip(row["cells"], columns, strict=True):
                own(ref, column["reading"])
        tables.append(table | {"columns": [
            {k: v for k, v in c.items() if k != "reading"} for c in columns
        ]})
    for selection in answer["grids"]:
        if grid is None or selection["grid"] != grid.ref:
            raise ValueError("VISUAL_GRID_REFERENCE")
        if selection["reason"] is not None:
            # A rejected geometry candidate is not a licence to infer its labels.
            if any(ref in roles for ref in grid.members):
                raise ValueError("VISUAL_GRID_HOLD_CONFLICT")
            for ref in grid.members:
                roles[ref] = "unknown"
                issues[ref] = selection["reason"]
            continue
        columns = {c["header"]: c for c in selection["columns"]}
        if (
            len(columns) != len(selection["columns"]) or set(columns) != set(grid.headers)
            or selection["name"] is None or any(c["name"] is None for c in columns.values())
        ):
            raise ValueError("VISUAL_GRID_COLUMNS")
        for header in grid.headers:
            classify(header, "label")
        for row in grid.rows:
            for header, ref in zip(grid.headers, row, strict=True):
                own(ref, columns[header]["reading"], grid_member=True)
        tables.append({
            "key": grid.ref, "name": selection["name"], "parent": selection["parent"],
            "columns": [{
                "key": header, "name": columns[header]["name"], "labels": [header],
            } for header in grid.headers],
            "rows": [{"cells": list(row), "keyCell": None, "keyValue": None} for row in grid.rows],
            "rowKeyStrategy": "position", "includeEmptyRows": True,
        })
    for issue in answer["unresolved"]:
        ref = issue["cell"]
        if ref not in targets or ref in hidden or ref in roles or ref in issues:
            raise ValueError("VISUAL_ANSWER_HOLD_CONFLICT")
        issues[ref] = issue["reason"]
    for ref in sorted(targets):
        if ref not in roles:
            # An omission is visible as unresolved, never an inferred edit.
            roles[ref] = refs[ref]["existingRole"] if refs[ref]["roleLocked"] else "unknown"
            issues.setdefault(
                ref,
                "protected_existing"
                if refs[ref]["hasMeaning"] or refs[ref]["protectedFacets"] else "missing_binding",
            )
    for definition in definitions.values():
        if not set(definition["evidenceRefs"]) <= set(refs) - hidden:
            raise ValueError("VISUAL_MARK_EVIDENCE_REF")
    return {
        "version": "template-analysis.visual.v2",
        "roles": {
            role: sorted(r for r, value in roles.items() if value == role) for role in ROLE_NAMES
        },
        "headings": answer["headings"], "fields": fields, "tables": tables,
        "marks": [{
            k: v for k, v in definitions[name].items() if k != "key"
        } | {"cells": members} for name, members in marks.items()],
        "unresolved": [{"cell": ref, "reason": reason} for ref, reason in issues.items()],
    }
