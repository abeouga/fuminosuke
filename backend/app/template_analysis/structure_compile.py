"""Checked physical staging, then the same issued-binding and semantic compiler."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, overload

if TYPE_CHECKING:
    from app.template_analysis.visual_compile import VisualCompilation

from app.editing.authoring_provenance import facets, key
from app.editing.domain.command import digest
from app.editing.draft_catalog import with_draft_cells
from app.editing.geometry_compiler import GeometryCompiler
from app.editing.geometry_intents import GeometryContext
from app.editing.manual_draft import ManualDraft
from app.template_analysis.draft import compile_hand
from app.template_analysis.region_contracts import RegionAnswer
from app.template_analysis.snapshot import physical
from app.template_analysis.structure_contracts import StructureAnswer
from app.template_analysis.visual_bindings import build_binding_catalog
from app.template_analysis.visual_input import (
    build_visual_evidence,
    capture_visual_page,
    plan_visual_units,
)


def context_from(value: dict[str, Any]) -> GeometryContext:
    return GeometryContext(
        refs=value["refs"],
        writable=frozenset(value["writable"]),
        editable=frozenset(value["editable"]),
        bounds=tuple(value["bounds"]),
        namespace=value["namespace"],
        enabled=value["enabled"],
    )


def compile_structure(
    raw: dict[str, Any], source: Any, plan: Any, evidence: Any, catalog: Any
) -> VisualCompilation:
    from app.template_analysis.visual_compile import VisualCompilation, compile_visual_proposal

    is_region = raw.get("version") == "template-analysis.region.v1"
    answer = RegionAnswer.model_validate(raw) if is_region else StructureAnswer.model_validate(raw)
    snapshot, page = source.unpack(source.pin)
    if not page.get("structure"):
        raise ValueError("STRUCTURE_CAPABILITY_UNAVAILABLE")
    if physical(catalog.state["layout"]) != snapshot.get(
        "savedPhysicalDigest", snapshot["physicalDigest"]
    ):
        raise ValueError("VISUAL_PHYSICAL_STALE")
    hand, identities = compile_hand(
        catalog,
        SimpleNamespace(
            request_id="structure_" + source.pin[:24],
            draft=ManualDraft.model_validate(snapshot["draft"]),
        ),
    )
    baseline = identities.apply(hand.after, reverse=True)
    working = with_draft_cells(catalog, baseline)
    for ref, handle in page["handles"].items():
        target = working.resolve(handle, "cell")
        if target.entity_id != page["refs"][ref]["id"] or target.page_id != page["pageId"]:
            raise ValueError("VISUAL_HANDLE_STALE")
    meta = json.loads(evidence.metadata_json)
    issued = build_binding_catalog(meta)
    if answer.catalog_digest != issued.digest:
        raise ValueError("VISUAL_BINDING_CATALOG_STALE")
    namespace = digest([source.pin, evidence.unit_id, "geometry"])
    geometry_bounds = tuple(meta["geometryBounds"])
    if is_region:
        owned_rects = [c for c in meta["cells"] if c["ref"] in meta["writeRefs"]]
        x, y, w, h = geometry_bounds
        left = min([x, *[c["x"] for c in owned_rects]])
        top = min([y, *[c["y"] for c in owned_rects]])
        right = max([x + w, *[c["x"] + c["width"] for c in owned_rects]])
        bottom = max([y + h, *[c["y"] + c["height"] for c in owned_rects]])
        geometry_bounds = (left, top, right - left, bottom - top)
    context = GeometryContext(
        refs={r: c["id"] for r, c in page["refs"].items()},
        writable=frozenset(page["refs"][r]["id"] for r in meta["writeRefs"]),
        editable=frozenset(
            page["refs"][r]["id"] for r in page["geometryEditable"] if r in meta["writeRefs"]
        ),
        bounds=geometry_bounds,
        namespace=namespace,
        enabled=True,
    )
    stage, intent = GeometryCompiler().apply(working, answer.geometry, context)
    if is_region:
        from app.template_analysis.region_compile import check_geometry

        check_geometry(
            meta,
            baseline["layout"],
            stage.state["layout"],
            intent,
            context.writable,
            answer.geometry,
        )
    staged = with_draft_cells(working, stage.state)
    after_cells = {c["id"]: c for c in stage.state["layout"]["cells"]}
    # Preserve old c references. New references are assigned by the server only.
    refs = {
        r: copy.deepcopy(after_cells[c["id"]])
        for r, c in page["refs"].items()
        if c["id"] in after_cells
    }
    next_ref = max((int(r[1:]) for r in page["refs"]), default=0) + 1
    new_refs = {}
    for n, entity in intent.aliases.items():
        ref = f"c{next_ref}"
        next_ref += 1
        refs[ref] = copy.deepcopy(after_cells[entity])
        new_refs[n] = ref
    if len(refs) > 1000 or next_ref > 10000:
        raise ValueError("VISUAL_REFERENCE_LIMIT")
    reverse = {c["id"]: r for r, c in refs.items()}
    targets = [r for r in meta["writeRefs"] if r in refs] + list(new_refs.values())
    handles = {
        r: next(
            t.handle for t in staged.targets.values() if t.kind == "cell" and t.entity_id == c["id"]
        )
        for r, c in refs.items()
    }
    inner = copy.deepcopy(snapshot)
    inner["draft"] = {
        k: copy.deepcopy(stage.state[k])
        for k in ("layout", "documentIdentityFields", "documentName")
    }
    inner["draftDigest"] = digest(inner["draft"])
    inner["physicalDigest"] = inner["savedPhysicalDigest"] = physical(stage.state["layout"])
    inner_page = copy.deepcopy(page)
    inner_page.update(
        refs=refs,
        targets=targets,
        handles=handles,
        structureSemantics=True,
        geometryBounds=meta["geometryBounds"],
        lockedRoles=[r for r in page["lockedRoles"] if r in refs],
        parentRefs={r: reverse.get(c.get("parentLabelCellId")) for r, c in refs.items()},
        bound=[r for r in page["bound"] if r in refs],
        providerCells=[
            dict(
                ref=r,
                **{k: c[k] for k in ("x", "y", "width", "height", "vertices")},
                text=c.get("printedLabelText") or c.get("text", ""),
                role=c["role"],
                target=r in targets,
                roleLocked=r in page["lockedRoles"],
                hasMeaning=r in page["bound"],
                virtual=c.get("virtual", False),
            )
            for r, c in refs.items()
        ],
    )
    inner["pages"] = [inner_page]
    inner_source = capture_visual_page(inner, source.page_key, source.image)
    inner_plan = plan_visual_units(inner_source, inner_source.pin)
    inner_evidence = build_visual_evidence(
        inner_source, inner_source.pin, inner_plan, inner_plan.units[0].unit_id
    )
    new_meta = json.loads(inner_evidence.metadata_json)
    new_catalog = build_binding_catalog(new_meta)
    pairs = {(b.input, b.label, b.axis): b.ref for b in new_catalog.links}
    originals = {b.ref: b for b in issued.links}
    affected = set(intent.changed) | set(intent.removed)

    @overload
    def region(ref: str) -> str: ...

    @overload
    def region(ref: None) -> None: ...

    def region(ref: str | None) -> str | None:
        if ref is None:
            return None
        mapped = new_refs.get(ref, ref)
        if mapped not in refs:
            raise ValueError("STRUCTURE_REFERENCE_INVALID")
        return mapped

    def binding(value: dict[str, Any]) -> dict[str, Any]:
        if value["kind"] == "unlabelled":
            return dict(
                kind="unlabelled",
                cell=region(value["cell"]),
                evidence={
                    **value["evidence"],
                    "refs": [region(r) for r in value["evidence"]["refs"]],
                },
            )
        if value["kind"] == "linked":
            selected = []
            for ref in value["links"]:
                if ref not in originals:
                    raise ValueError("VISUAL_BINDING_REFERENCE")
                old = originals[ref]
                if any(page["refs"][r]["id"] in affected for r in (old.input, old.label)):
                    raise ValueError("STRUCTURE_OLD_LINK_INVALID")
                selected.append((region(old.input), region(old.label), old.axis))
        else:
            input_ref = region(value["input"])
            labels = [region(r) for r in value["labels"]]
            if not is_region and not any(refs[r]["id"] in affected for r in [input_ref, *labels]):
                raise ValueError("STRUCTURE_DERIVED_LINK_UNCHANGED")
            if len(labels) != len(set(labels)):
                raise ValueError("VISUAL_BINDING_REFERENCE")
            selected = [(input_ref, label, value["axis"]) for label in labels]
        if any(pair not in pairs for pair in selected):
            raise ValueError("STRUCTURE_DERIVED_LINK_UNISSUED")
        return dict(kind="linked", links=[pairs[pair] for pair in selected])

    semantic = answer.semantics.model_dump(mode="json", by_alias=True)
    problems = []
    if is_region:
        for item in semantic["unresolved"]:
            if item["cell"] is None:
                from app.editing.geometry_intents import covered

                x, y, w, h = meta["geometryBounds"]
                if covered(item["bounds"], [dict(x=x, y=y, width=w, height=h)]) == 0:
                    raise ValueError("REGION_ISSUE_OUTSIDE")
                problems.append(dict(cellId="", bounds=item["bounds"], reason=item["reason"]))
            else:
                r = region(item["cell"])
                problems.append(dict(cell=r, cellId=refs[r]["id"], reason=item["reason"]))
        semantic["unresolved"] = [
            dict(
                cell=i["cell"],
                reason=i["reason"]
                if i["reason"] not in {"manual_required", "cross_region"}
                else "unsupported_structure",
            )
            for i in semantic["unresolved"]
            if i["cell"] is not None
        ]
    declared = set()
    for group in semantic["groups"]:
        group["parent"] = region(group["parent"])
        for field in group["fields"]:
            field["binding"] = binding(field["binding"])
            b = field["binding"]
            declared.add(
                b["cell"]
                if b["kind"] == "unlabelled"
                else next(x.input for x in new_catalog.links if x.ref == b["links"][0])
            )
    for table in semantic["tables"]:
        table["parent"] = region(table["parent"])
        for column in table["columns"]:
            column["binding"] = binding(column["binding"])
        for row in table["rows"]:
            row["cells"] = [region(r) for r in row["cells"]]
            declared.update(row["cells"])
            row["keyCell"] = region(row["keyCell"])
    for heading in semantic["headings"]:
        heading["cell"] = region(heading["cell"])
        heading["parent"] = region(heading["parent"])
    for reading in semantic["readings"]:
        reading["evidenceRefs"] = [region(r) for r in reading["evidenceRefs"]]
    for item in semantic["annotations"] + semantic["unresolved"]:
        item["cell"] = region(item["cell"])
    semantic.update(version="template-analysis.form.v2", catalogDigest=new_catalog.digest)
    compiled = compile_visual_proposal(
        semantic, inner_source, inner_source.pin, inner_plan, inner_evidence, staged
    )
    if not answer.geometry:
        if is_region:
            from app.template_analysis.region_compile import atomic_region

            return atomic_region(compiled, source, evidence, problems)
        return compiled
    dependent: list[dict[str, Any]] = []
    independent: list[dict[str, Any]] = []
    for unit in compiled.units:
        dependencies = set(unit["cellIds"]) | {json.loads(k)[0] for k in unit["readSet"]}
        (dependent if dependencies & affected else independent).append(unit)
    held = any(u["state"] != "mergeable" for u in dependent) or any(
        refs[i["cell"]]["id"] in affected and i["cell"] in declared for i in compiled.unresolved
    )
    prior = facets(baseline["layout"])
    reads = {
        k: digest(v)
        for k, v in prior.items()
        if json.loads(k)[1] == "geometry" or json.loads(k)[0] in affected
    }
    writes = {key(e, "geometry") for e in affected} | {key("@layout", "geometry")}
    for unit in dependent:
        reads.update({k: digest(prior.get(k)) for k in unit["readSet"] if k in prior})
        writes.update(unit["writeSet"])
    context_value = asdict(context)
    context_value.update(writable=sorted(context.writable), editable=sorted(context.editable))
    changed_ids = sorted(intent.changed - set(intent.aliases.values()))
    delta = dict(
        before=[
            {k: c[k] for k in ("id", "x", "y", "width", "height")}
            for c in baseline["layout"]["cells"]
            if c["id"] in affected
        ],
        after=[
            {k: c[k] for k in ("id", "x", "y", "width", "height")}
            for c in stage.state["layout"]["cells"]
            if c["id"] in affected
        ],
        addedIds=list(intent.aliases.values()),
        removedIds=sorted(intent.removed),
        changedIds=changed_ids,
        referenceMap=intent.aliases,
    )
    unit = dict(
        unitId="u_" + digest([namespace, delta])[:24],
        pageId=page["pageId"],
        kind="structure",
        cellIds=sorted(affected | {c for u in dependent for c in u["cellIds"]}),
        name="矩形と意味付け",
        state="protected" if held else "mergeable",
        reason="dependent_semantics_unresolved" if held else None,
        dependencies=[],
        roleChanges=[v for u in dependent for v in u["roleChanges"]],
        details=[
            f"矩形: 追加{len(intent.aliases)}・変更{len(changed_ids)}・退役{len(intent.removed)}"
        ]
        + [d for u in dependent for d in u["details"]],
        geometry=delta,
        operations=[op for u in dependent for op in u["operations"]],
        readSet=reads,
        writeSet=sorted(writes),
        geometryPlan=dict(
            operations=[op.model_dump(mode="json", by_alias=True) for op in answer.geometry],
            context=context_value,
        ),
    )
    # Unknowns retain original reference ownership for runner publication.
    unresolved = [
        dict(
            i,
            cellId=refs[i["cell"]]["id"],
            cell=next(
                (r for r, c in page["refs"].items() if c["id"] == refs[i["cell"]]["id"]), i["cell"]
            ),
        )
        for i in compiled.unresolved
    ]
    result = VisualCompilation(
        [unit, *independent], unresolved, compiled.layout, compiled.operations
    )
    if is_region:
        from app.template_analysis.region_compile import atomic_region

        return atomic_region(result, source, evidence, problems)
    return result
