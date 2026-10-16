"""Scope-check visual proposals and dry-run the existing authoring compiler.

No new save path. The caller must validate live revision/protection/cancellation
again at publication and merge. This synchronous adapter cannot certify live
state; the analysis runner owns those checks.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from pydantic import TypeAdapter

from app.editing.authoring_provenance import facets
from app.editing.catalog import Catalog
from app.editing.commands import CommandCompiler
from app.editing.compilation_limits import AUTHORING_LIMITS
from app.editing.domain.command import digest
from app.editing.draft_catalog import with_draft_cells
from app.editing.manual_draft import ManualDraft
from app.editing.operation_contracts import Operation
from app.template_analysis.draft import compile_hand
from app.template_analysis.graph import build_units, validate_graph
from app.template_analysis.snapshot import physical
from app.template_analysis.visual_bindings import build_binding_catalog
from app.template_analysis.visual_form_answer import FormVisualAnswer
from app.template_analysis.visual_graph import build_visual_units
from app.template_analysis.visual_input import (
    VisualEvidence,
    VisualPage,
    VisualPlan,
    check_evidence,
)
from app.template_analysis.visual_isolation import isolate_answer


@dataclass(frozen=True)
class VisualCompilation:
    units: list[dict[str, Any]]
    unresolved: list[dict[str, Any]]
    layout: dict[str, Any]
    operations: list[dict[str, Any]]


def _local_namespace(operations: list[dict[str, Any]], unit_id: str) -> list[dict[str, Any]]:
    """Independent components share a page, but must not share local aliases."""
    namespace = "v" + digest(unit_id)[:12]
    names: dict[str, str] = {}

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(v) for v in value]
        if isinstance(value, dict):
            return {
                k: names.setdefault(v, f"{namespace}_{len(names)}") if k == "localRef" else visit(v)
                for k, v in value.items()
            }
        return value

    result: list[dict[str, Any]] = visit(operations)
    return result


def compile_visual_proposal(
    raw: dict[str, Any],
    source: VisualPage,
    current_snapshot_pin: str,
    plan: VisualPlan,
    evidence: VisualEvidence,
    catalog: Catalog,
) -> VisualCompilation:
    check_evidence(source, current_snapshot_pin, plan, evidence)
    snapshot, page = source.unpack(current_snapshot_pin)
    if (
        not catalog.editable
        or catalog.revision != snapshot["baseRevision"]
        or catalog.generation != snapshot["binding"]["catalogRevision"]
    ):
        raise ValueError("VISUAL_CATALOG_STALE")
    if physical(catalog.state["layout"]) != snapshot.get(
        "savedPhysicalDigest", snapshot["physicalDigest"]
    ):
        raise ValueError("VISUAL_PHYSICAL_STALE")
    if raw.get("version") in {"template-analysis.structure.v1", "template-analysis.region.v1"}:
        from app.template_analysis.structure_compile import compile_structure

        return compile_structure(raw, source, plan, evidence, catalog)
    # Admitted unsaved hand intent is part of the snapshot. Compose it through
    # the common manual compiler instead of requiring an otherwise needless save.
    hand, identities = compile_hand(
        catalog,
        SimpleNamespace(
            request_id="visual_" + current_snapshot_pin[:24],
            draft=ManualDraft.model_validate(snapshot["draft"]),
        ),
    )
    draft = snapshot["draft"]
    working = with_draft_cells(catalog, identities.apply(hand.after, reverse=True))
    # Resolve every issued handle, including read-only context, against the
    # actual common catalog. Provider strings can never create new handles.
    for ref, handle in page["handles"].items():
        target = working.resolve(handle, "cell")
        if target.entity_id != page["refs"][ref]["id"] or target.page_id != page["pageId"]:
            raise ValueError("VISUAL_HANDLE_STALE")
    unit = next(u for u in plan.units if u.unit_id == evidence.unit_id)
    scoped = copy.deepcopy(page)
    scoped["targets"] = list(unit.write_refs)
    scoped["lockedRoles"] = [r for r in page["lockedRoles"] if r in unit.write_refs]
    notes: dict[str, dict[str, Any]] = {}
    if raw.get("version") == "template-analysis.form.v1":
        raise ValueError("VISUAL_FORM_VERSION")
    if raw.get("version") == "template-analysis.form.v2":
        metadata = json.loads(evidence.metadata_json)
        expanded = FormVisualAnswer.model_validate(raw).expand(
            build_binding_catalog(metadata),
            metadata,
        )
        raw, notes = expanded.answer, expanded.notes
    if raw.get("version") == "template-analysis.visual.v3":
        raw = isolate_answer(
            raw,
            evidence,
            scoped,
            extra_dependencies={r: n["refs"] for r, n in notes.items()},
        )
    if raw.get("version") == "template-analysis.visual.v2":
        units, unresolved = build_visual_units(raw, scoped, draft["layout"], notes=notes)
    else:
        # Explicit v1 replay support, never a fallback after a v2 rejection.
        proposal = validate_graph(raw, scoped, "full")
        units = build_units(proposal, scoped, draft["layout"], "full")
        unresolved = proposal["unresolved"]
    operations = []
    for edit in units:
        edit["operations"] = _local_namespace(edit["operations"], edit["unitId"])
        if edit["state"] == "mergeable":
            operations.extend(edit["operations"])
    result = copy.deepcopy(draft["layout"])
    if operations:
        decoded = TypeAdapter(list[Operation]).validate_python(operations)
        compiled = CommandCompiler().compile_operations(working, decoded, limits=AUTHORING_LIMITS)
        result = compiled.after["layout"]
    if physical(result) != physical(draft["layout"]):
        raise ValueError("VISUAL_PHYSICAL_CHANGED")
    before, after = facets(draft["layout"]), facets(result)
    protected = set(page["protectedFacets"])
    read_ids = {page["refs"][r]["id"] for r in unit.read_refs}
    # Compare actual compiled facets, not just declared write sets. A heading
    # attachment or table operation may indirectly change a read-only label.
    protected.update(k for k in before if json.loads(k)[0] in read_ids)
    if any(before.get(k) != after.get(k) for k in protected):
        raise ValueError("VISUAL_PROTECTED_WRITE")
    return VisualCompilation(units, unresolved, result, operations)
