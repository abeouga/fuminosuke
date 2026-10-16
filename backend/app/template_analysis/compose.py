"""Compose hand intent and validated semantic units through existing compilers."""

from __future__ import annotations

import copy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.editing.authoring_provenance import facets
from app.editing.commands import Plan
from app.editing.compilation_limits import AUTHORING_LIMITS
from app.editing.draft_catalog import with_draft_cells
from app.editing.errors import reject
from app.editing.geometry_compiler import GeometryCompiler
from app.editing.geometry_contracts import GeometryOperation
from app.editing.manual_draft import ManualDraft
from app.editing.operation_contracts import Operation
from app.template_analysis.draft import compile_hand
from app.template_analysis.snapshot import physical


def compose(
    runtime: Any,
    owner: Any,
    catalog: Any,
    draft: dict[str, Any],
    units: list[dict[str, Any]],
    request_id: str,
    protected: set[str],
) -> tuple[Any, dict[str, str], dict[str, Any]]:
    hand, identities = compile_hand(
        catalog, SimpleNamespace(request_id=request_id, draft=ManualDraft.model_validate(draft))
    )
    working = with_draft_cells(catalog, hand.after, identities.reverse_references)
    geometry_touched = []
    for unit in units:
        if "geometryPlan" not in unit:
            continue
        from app.template_analysis.structure_compile import context_from

        geometry = copy.deepcopy(unit["geometryPlan"])
        context_value = geometry["context"]
        context_value["refs"] = {
            r: identities.references.get(e, e) for r, e in context_value["refs"].items()
        }
        for name in ("writable", "editable"):
            context_value[name] = [identities.references.get(e, e) for e in context_value[name]]
        stage, intent = GeometryCompiler().apply(
            working,
            TypeAdapter(list[GeometryOperation]).validate_python(geometry["operations"]),
            context_from(context_value),
        )
        working = with_draft_cells(working, stage.state, identities.reverse_references)
        geometry_touched += stage.touched + [
            t
            for t in working.targets.values()
            if t.kind == "cell" and t.entity_id in intent.changed
        ]
    expected_geometry = physical(working.state["layout"])
    expected_order = [c["id"] for c in working.state["layout"]["cells"]]
    operations = [op for unit in units for op in unit["operations"]]
    if operations:
        decoded = TypeAdapter(list[Operation]).validate_python(operations)
        plan = runtime.mutations.core.compiler.compile_operations(
            working, decoded, limits=AUTHORING_LIMITS
        )
    else:
        plan = Plan(copy.deepcopy(hand.before), copy.deepcopy(working.state))
    plan.touched.extend(geometry_touched)
    visible_hand = identities.apply(hand.after, reverse=True)
    plan.before = copy.deepcopy(catalog.state)
    runtime.mutations.core.local_scope(owner, plan)
    runtime.mutations.core.page_scope(owner, catalog, plan)
    runtime.mutations.table_scope(owner, catalog, plan)
    try:
        runtime.mutations.core.prepare_template(owner, catalog, plan)
    except ValidationError as error:
        raise reject(
            "AUTHORING_CAPACITY_EXCEEDED",
            "保存する意味・表の件数又は構造の上限を確認してください。",
            422,
        ) from error
    visible_after = identities.apply(plan.after, reverse=True)
    if (
        physical(plan.after["layout"]) != expected_geometry
        or [c["id"] for c in plan.after["layout"]["cells"]] != expected_order
    ):
        raise reject("SNAPSHOT_STALE", "AI案が物理セルを変更しようとしています。", 409)
    before, after = facets(visible_hand["layout"]), facets(visible_after["layout"])
    if any(before.get(k) != after.get(k) for k in protected):
        raise reject("MANUAL_CONFLICT", "保護された設定へ影響するAI案です。", 409)
    # Return a non-persistable preview. The prepared canonical plan is discarded;
    # explicit manual-authoring will allocate and validate identities at save.
    preview = Plan(
        copy.deepcopy(plan.before),
        visible_after,
        touched=[
            replace(t, entity_id=identities.reverse_references.get(t.entity_id, t.entity_id))
            for t in plan.touched
        ],
    )
    return preview, {}, visible_hand["layout"]


def draft_from(plan: Any) -> dict[str, Any]:
    return {
        k: copy.deepcopy(plan.after[k])
        for k in ("layout", "documentIdentityFields", "documentName")
    }
