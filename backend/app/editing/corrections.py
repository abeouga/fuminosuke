"""Recognize the narrow, persisted edits that can be retargeted atomically."""

from __future__ import annotations

import copy
from typing import Any

from app.db.mutation_models import ResourceOperation
from app.editing.catalog import Catalog


def simple_edit(record: ResourceOperation, catalog: Catalog) -> dict[str, Any] | None:
    before, after = record.before_json, record.after_json
    if before.get("kind") != after.get("kind"):
        return None
    if before["kind"] == "ocr":
        if before["fields"].keys() != after["fields"].keys():
            return None
        changed = [key for key in before["fields"] if before["fields"][key] != after["fields"][key]]
        if len(changed) != 1:
            return None
        entity = changed[0]
        old, new = before["fields"][entity], after["fields"][entity]
        mutable = {"raw", "normalized", "status", "alternatives", "reviewReason"}
        if {k: v for k, v in old.items() if k not in mutable} != {
            k: v for k, v in new.items() if k not in mutable
        }:
            return None
        kind, operation = "field", "ocr.set_value"
        value = new.get("normalized") if new.get("normalized") is not None else new.get("raw")
    else:
        old_cells = {c["id"]: c for c in before["layout"]["cells"]}
        new_cells = {c["id"]: c for c in after["layout"]["cells"]}
        if old_cells.keys() != new_cells.keys():
            return None
        changed = [key for key in old_cells if old_cells[key] != new_cells[key]]
        if len(changed) != 1:
            return None
        entity = changed[0]
        expected = copy.deepcopy(before)
        expected["layout"]["cells"] = copy.deepcopy(after["layout"]["cells"])
        # Authoring derives identity display labels from the renamed mapping.
        for state in (expected,):
            for entry in state["documentIdentityFields"]:
                entry.pop("label", None)
        compared = copy.deepcopy(after)
        for entry in compared["documentIdentityFields"]:
            entry.pop("label", None)
        if expected != compared or {
            k: v for k, v in old_cells[entity].items() if k != "fieldLabel"
        } != {k: v for k, v in new_cells[entity].items() if k != "fieldLabel"}:
            return None
        kind, operation, value = "cell", "cell.rename", new_cells[entity].get("fieldLabel")
    target = next(
        (t for t in catalog.targets.values() if t.kind == kind and t.entity_id == entity), None
    )
    if target is None or operation not in target.operations:
        return None
    return {"operationId": record.id, "op": operation, "target": target.handle, "value": value}
