"""Facet fingerprints and provenance; never grants permission to alter a layout."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.editing.domain.command import digest
from app.editing.draft_identity import DraftIdentityMap
from app.editing.errors import reject
from app.template_analysis.models import AnalysisMerge, AnalysisRun, AuthoringFacet
from app.template_analysis.runtime_contract import check_visual_contract


def key(entity: str, facet: str) -> str:
    return json.dumps([entity, facet], separators=(",", ":"))


def facets(layout: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    pools = {p["targetPath"]: p for p in layout["labelPool"]}
    for cell in layout["cells"]:
        entity = cell["id"]
        relations = [r for r in layout["relations"] if r["inputCellId"] == entity]
        groups = [g for g in layout["recordGroups"] if entity in g["inputCellIds"]]
        settings = {
            "geometry": {
                k: cell.get(k)
                for k in (
                    "x",
                    "y",
                    "width",
                    "height",
                    "shapeType",
                    "vertices",
                    "pageRange",
                )
            },
            "role": cell["role"],
            "meaning": {
                "fieldLabel": cell.get("fieldLabel"),
                "meanings": [pools.get(r["targetPath"]) for r in relations],
            },
            "binding": relations,
            "parent": cell.get("parentLabelCellId"),
            "records": {
                "groups": groups,
                "sections": [
                    s
                    for s in layout["recordSections"]
                    if any(g["sectionId"] == s["id"] for g in groups)
                ],
            },
            "reading": cell.get("readingMode"),
            "mark": [cell.get("markSets", []), cell.get("compositeSubfields", [])],
        }
        values.update({key(entity, facet): value for facet, value in settings.items()})
    for pool in layout["labelPool"]:
        values[key(pool["id"], "meaning")] = pool
    for section in layout["recordSections"]:
        values[key(section["id"], "records")] = section
    for group in layout["recordGroups"]:
        values[key(group["id"], "records")] = group
    for relation in layout["relations"]:
        values[key(relation["id"], "binding")] = relation
    values[key("@layout", "geometry")] = {
        "cells": [c["id"] for c in layout["cells"]],
        "merges": layout["cellMerges"],
        "size": [layout["canonicalWidth"], layout["canonicalHeight"]],
    }
    return values


def touches(
    entries: list[dict[str, Any]],
    catalog: Any,
    layout: dict[str, Any],
    *,
    deleted_ids: frozenset[str] = frozenset(),
) -> set[str]:
    values = facets(layout)
    result = set()
    for entry in entries:
        target = entry["target"]
        entity = target.get("draftEntityId")
        if target.get("handle"):
            found = catalog.targets.get(target["handle"])
            if found is None:
                raise reject("TARGET_STALE", "手動変更の対象世代が一致しません。")
            entity = found.entity_id
        item = key(entity, entry["facet"])
        if item not in values and not (
            entry["facet"] == "geometry"
            and (
                entity in deleted_ids
                or any(t.entity_id == entity and t.kind == "cell" for t in catalog.targets.values())
            )
        ):
            # An entity drawn or joined and then removed before this save has nothing left to
            # protect; refusing it would stop every later save of the editor.
            continue
        result.add(item)
    return result


def available(session: Session) -> bool:
    return inspect(session.connection()).has_table("template_authoring_facets")


def protection(session: Session, version: str, layout: dict[str, Any]) -> dict[str, str]:
    values = facets(layout)
    known = {
        key(row.entity_id, row.facet): row
        for row in session.scalars(
            select(AuthoringFacet).where(AuthoringFacet.version_id == version)
        )
    }
    return {
        item: row.origin
        if (row := known.get(item)) and row.value_digest == digest(value)
        else "unknown"
        for item, value in values.items()
    }


def seed_detection(session: Session, version: Any) -> None:
    """Only newly detected layouts have known unreviewed roles; never infer legacy provenance."""
    if not available(session):
        return
    initial = facets(version.layout_json)
    session.add_all(
        [
            AuthoringFacet(
                version_id=version.id,
                entity_id=cell["id"],
                facet=facet,
                origin="unreviewed",
                value_digest=digest(initial[key(cell["id"], facet)]),
                revision=version.revision,
            )
            for cell in version.layout_json["cells"]
            for facet in ("role", "geometry")
        ]
    )


def remap(value: Any, substitutions: dict[str, str]) -> Any:
    return DraftIdentityMap(substitutions).apply(value)


def record_save(
    session: Session,
    owner: Any,
    catalog: Any,
    plan: Any,
    value: dict[str, Any],
    substitutions: dict[str, str],
    revision: int,
    resolved_operations: list[dict[str, Any]],
) -> None:
    if plan.after["kind"] != "template" or not available(session):
        return
    before, after = facets(plan.before["layout"]), facets(plan.after["layout"])
    merges = []
    deleted_ids: set[str] = set()
    for merge_id in value.get("analysisMergeIds", []):
        merge = session.get(AnalysisMerge, merge_id)
        run = session.get(AnalysisRun, merge.run_id) if merge else None
        if (
            not merge
            or not run
            or run.session_id != owner.id
            or run.resource_id != owner.resource_id
            or run.base_revision != catalog.revision
            or merge.result_json.get("reverted")
        ):
            raise reject("ANALYSIS_MERGE_INVALID", "AI反映記録が保存対象と一致しません。", 409)
        check_visual_contract(run.snapshot_json)
        merges.append(merge)
        deleted_ids.update(c["id"] for c in merge.result_json["draft"]["layout"]["cells"])
    if merges:
        from app.template_analysis.merge_chain import chain

        for run_id in {m.run_id for m in merges}:
            provided = [m.id for m in merges if m.run_id == run_id]
            if [m.id for m in chain(session, run_id, provided[-1])] != provided:
                raise reject("ANALYSIS_MERGE_INVALID", "保存するAI反映履歴が一致しません。", 409)
    manual = touches(
        value.get("manualTouchedFacets", []),
        catalog,
        value.get("draft", {}).get("layout", plan.before["layout"]),
        deleted_ids=frozenset(deleted_ids),
    )
    manual = {
        key(substitutions.get(entity, entity), facet) for entity, facet in map(json.loads, manual)
    }
    for op in resolved_operations:
        facet = {
            "cell.set_role": "role",
            "cell.rename": "meaning",
            "cell.set_reading": "reading",
            "meaning.update": "meaning",
            "heading.attach": "parent",
            "heading.detach": "parent",
        }.get(op["op"])
        if facet:
            for handle in (
                op.get("cells", []) + op.get("children", []) + [op.get("cell"), op.get("meaning")]
            ):
                if isinstance(handle, str) and handle in catalog.targets:
                    manual.add(key(catalog.targets[handle].entity_id, facet))
    ai: dict[str, str] = {}
    for merge in merges:
        merge_id = merge.id
        expected = facets(remap(merge.result_json["draft"]["layout"], substitutions))
        for item in merge.facet_json:
            entity, facet = json.loads(item)
            mapped = key(substitutions.get(entity, entity), facet)
            if digest(expected.get(mapped)) == digest(after.get(mapped)):
                ai[mapped] = merge_id
    rows = {
        key(r.entity_id, r.facet): r
        for r in session.scalars(
            select(AuthoringFacet).where(AuthoringFacet.version_id == owner.resource_id)
        )
    }
    for item in set(before) | set(after) | manual:
        if item not in after:
            entity, facet = json.loads(item)
            if facet == "geometry":
                row = rows.get(item) or AuthoringFacet(
                    version_id=owner.resource_id,
                    entity_id=entity,
                    facet=facet,
                )
                row.origin = "ai" if item in ai and item not in manual else "manual"
                row.value_digest, row.revision = digest(None), revision
                row.merge_id = ai.get(item) if row.origin == "ai" else None
                session.add(row)
            elif item in rows:
                session.delete(rows[item])
            continue
        if before.get(item) == after[item] and item not in manual:
            continue
        entity, facet = json.loads(item)
        row = rows.get(item) or AuthoringFacet(
            version_id=owner.resource_id, entity_id=entity, facet=facet
        )
        row.origin = "ai" if item in ai and item not in manual else "manual"
        row.value_digest, row.revision = digest(after[item]), revision
        row.merge_id = ai.get(item) if row.origin == "ai" else None
        session.add(row)
    if merges:
        from app.template_analysis.repository import ACTIVE, READABLE, AnalysisRepository

        for run_id in {m.run_id for m in merges}:
            run = session.get(AnalysisRun, run_id)
            if run and run.state in ACTIVE | READABLE:
                run.state = "stale"
                AnalysisRepository.emit(
                    session,
                    run,
                    "stale",
                    {
                        "error": {
                            "code": "SNAPSHOT_STALE",
                            "message": "保存したため旧解析案を終了しました。",
                        }
                    },
                )
