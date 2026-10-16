"""A region is an atomic proposal with scoped ownership and physical collision checks."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.template_analysis.visual_compile import VisualCompilation

from app.editing.authoring_provenance import facets
from app.editing.domain.command import digest
from app.editing.geometry_intents import covered


def check_geometry(
    meta: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    intent: Any,
    owned: frozenset[str],
    operations: Any,
) -> None:
    x, y, w, h = meta["geometryBounds"]
    nominal = dict(x=x, y=y, width=w, height=h)
    old = {c["id"]: c for c in before["cells"]}
    domains = [nominal, *[old[e] for e in owned]]
    add_ids = {intent.aliases[op.ref] for op in operations if op.op == "add"}
    for c in after["cells"]:
        if c["id"] not in intent.changed:
            continue
        domain = [nominal] if c["id"] in add_ids else domains
        if covered(c, domain) != c["width"] * c["height"]:
            raise ValueError("REGION_GEOMETRY_OWNERSHIP")


def atomic_region(
    compiled: Any, source: Any, evidence: Any, problems: list[dict[str, Any]]
) -> VisualCompilation:
    from app.template_analysis.visual_compile import VisualCompilation

    snapshot, page = source.unpack(source.pin)
    meta = json.loads(evidence.metadata_json)
    units = copy.deepcopy(compiled.units)
    explicit_ids = {i["cellId"] for i in problems if i.get("cellId")}
    unresolved = [
        i
        for i in compiled.unresolved
        if (i.get("cellId") or page["refs"].get(i.get("cell"), {}).get("id")) not in explicit_ids
    ] + problems
    if not units:
        return VisualCompilation([], unresolved, compiled.layout, compiled.operations)
    original = facets(snapshot["draft"]["layout"])
    reads = {k: v for u in units for k, v in u["readSet"].items()}
    owned = {page["refs"][r]["id"] for r in meta["writeRefs"]}
    semantic_ids = {
        json.loads(k)[0] for u in units for k in u["readSet"] if json.loads(k)[1] != "geometry"
    }
    reads = {
        k: v
        for k, v in reads.items()
        if json.loads(k)[1] != "geometry" or json.loads(k)[0] in owned | semantic_ids
    }
    # Outside geometry is checked against the whole current draft again by the common compiler.
    reads.update(
        {
            k: digest(v)
            for k, v in original.items()
            if json.loads(k)[0] in owned and json.loads(k)[1] == "geometry"
        }
    )
    held = any(u["state"] != "mergeable" for u in units)
    geometries = [u for u in units if "geometryPlan" in u]
    if len(geometries) > 1:
        raise ValueError("REGION_GEOMETRY_MULTIPLE")
    merged = dict(
        unitId=evidence.unit_id,
        pageId=page["pageId"],
        kind="structure" if geometries else "field",
        regionId=evidence.unit_id,
        proposalDigest=digest([evidence.digest, units, problems]),
        cellIds=sorted({c for u in units for c in u["cellIds"]}),
        name="領域の編集案",
        state="protected" if held else "mergeable",
        reason="dependent_semantics_unresolved" if held else None,
        dependencies=[],
        roleChanges=[r for u in units for r in u["roleChanges"]],
        details=[s for u in units for s in u["details"]],
        operations=[op for u in units for op in u["operations"]],
        readSet=reads,
        writeSet=sorted({k for u in units for k in u["writeSet"]}),
        geometry=None,
    )
    if geometries:
        merged.update(
            geometry=geometries[0]["geometry"], geometryPlan=geometries[0]["geometryPlan"]
        )
    return VisualCompilation([merged], unresolved, compiled.layout, merged["operations"])
