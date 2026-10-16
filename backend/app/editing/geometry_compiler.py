"""Common allowlisted physical draft compilation for hand and AI adapters."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.editing.catalog import Catalog, CatalogService, Target
from app.editing.errors import reject
from app.layout_analysis.models import LayoutCell, LayoutDefinition

if TYPE_CHECKING:
    from app.editing.geometry_contracts import GeometryOperation
    from app.editing.geometry_intents import GeometryContext, GeometryIntentResult

PHYSICAL_FIELDS = {
    "x",
    "y",
    "width",
    "height",
    "shapeType",
    "vertices",
    "pageRange",
    "printedLabelText",
    "textAlignment",
    "textVerticalAlignment",
    "textDirection",
    "compositeSubfields",
}
REFERENCE_FIELDS = {
    "id",
    "inputCellId",
    "parentLabelCellId",
    "sectionId",
    "parentCellId",
    "recordKeyCellId",
    "labelCellIds",
    "inputCellIds",
    "recordLabelCellIds",
    "rowKeyCellIds",
    "collectionPath",
}
SEMANTIC_FIELDS = {"role", "fieldLabel", "readingMode", "markSets", "parentLabelCellId"}


def indexed(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


@dataclass
class GeometryStage:
    state: dict[str, Any]
    desired: dict[str, Any]
    targets: dict[str, Target]
    request_id: str
    substitutions: dict[str, str] = field(default_factory=dict)
    touched: list[Target] = field(default_factory=list)
    changed: bool = False
    bindings: dict[tuple[str, str], str] = field(init=False)

    def __post_init__(self) -> None:
        self.bindings = {(t.kind, t.entity_id): t.handle for t in self.targets.values()}

    def handle(self, kind: str, entity: str) -> str:
        bindings = self.bindings
        found = bindings.get((kind, entity))
        cells = indexed(self.desired["cells"])
        if (
            found is None
            and kind == "heading"
            and cells.get(entity, {}).get("role") in {"label", "parent_label"}
        ):
            found = bindings.get(("cell", entity))
        if found is None:
            raise reject("TARGET_STALE", "手動操作の対象が現在のページにありません。")
        self.touched.append(self.targets[found])
        return found

    def target(self, kind: str, entity: str, page: str | None, label: str) -> str:
        handle = "t_" + hashlib.sha256(f"{self.request_id}:{kind}:{entity}".encode()).hexdigest()
        self.targets[handle] = Target(handle, kind, entity, page, label)
        self.bindings[kind, entity] = handle
        return handle


class GeometryCompiler:
    def apply(
        self,
        catalog: Catalog,
        operations: list[GeometryOperation],
        context: GeometryContext,
    ) -> tuple[GeometryStage, GeometryIntentResult]:
        from app.editing.geometry_intents import compile_intents

        intent = compile_intents(catalog, operations, context)
        stage = self.prepare(
            catalog,
            intent.desired,
            context.namespace,
            canonical_ids={entity: entity for entity in intent.aliases.values()},
        )
        return stage, intent

    def prepare(
        self,
        catalog: Catalog,
        desired: dict[str, Any],
        request_id: str,
        *,
        canonical_ids: dict[str, str] | None = None,
    ) -> GeometryStage:
        desired = LayoutDefinition.model_validate(desired).model_dump(mode="json", by_alias=True)
        before = catalog.state["layout"]
        physical = copy.deepcopy(catalog.state)
        stage = physical["layout"]
        old_cells, new_cells = indexed(before["cells"]), indexed(desired["cells"])
        result = GeometryStage(physical, desired, dict(catalog.targets), request_id)
        substitutions = result.substitutions
        handle, target = result.handle, result.target
        removed = old_cells.keys() - new_cells.keys()
        # Existing cells retain identity. Only explicit physical properties enter
        # the manual authoring stage. Classification and links use shared commands.
        for entity in (r for r in old_cells if r in new_cells):
            old, new = old_cells[entity], new_cells[entity]
            changes = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
            unsupported = changes - PHYSICAL_FIELDS - SEMANTIC_FIELDS - {"text"}
            if "source" in unsupported and new["source"] == "manual":
                unsupported.remove("source")
                # Keep accepted physical provenance, as the shared command does.
            if unsupported:
                raise reject(
                    "MANUAL_INTENT_UNSUPPORTED", "検出由来の情報は手動保存で変更できません。", 422
                )
            if changes & PHYSICAL_FIELDS:
                handle("cell", entity)
                current = next(c for c in stage["cells"] if c["id"] == entity)
                for key in changes & PHYSICAL_FIELDS:
                    if key in new:
                        current[key] = copy.deepcopy(new[key])
                    else:
                        current.pop(key, None)
            if old.get("text") != new.get("text") and not old.get("virtual"):
                # Printed candidate correction is a hand-only authoring operation;
                # the AI heading command only changes the display override.
                handle("cell", entity)
                next(c for c in stage["cells"] if c["id"] == entity)["text"] = new["text"]
        for entity in removed:
            handle("cell", entity)
        stage["cells"] = [c for c in stage["cells"] if c["id"] not in removed]
        restorable = {p["cell"]["id"]: p["cell"] for m in before["cellMerges"] for p in m["parts"]}
        for entity in (r for r in new_cells if r not in old_cells):
            cell = copy.deepcopy(new_cells[entity])
            if entity in restorable and any(
                cell.get(k) != restorable[entity].get(k)
                for k in cell.keys() | restorable[entity].keys()
                if k not in PHYSICAL_FIELDS | SEMANTIC_FIELDS | {"text"}
            ):
                raise reject(
                    "MANUAL_INTENT_UNSUPPORTED", "復元する物理セルの由来情報が一致しません。", 422
                )
            if cell["source"] != "manual" and entity not in restorable:
                raise reject(
                    "MANUAL_INTENT_UNSUPPORTED", "新しい枠は手動で描いた枠だけを保存します。", 422
                )
            canonical = (
                entity
                if entity in restorable
                else (canonical_ids or {}).get(
                    entity,
                    f"manual_{uuid4().hex}",
                )
            )
            substitutions[entity] = canonical
            cell.update(
                id=canonical, markSets=[], readingMode=None, fieldLabel=None, parentLabelCellId=None
            )
            stage["cells"].append(cell)
            page = CatalogService.cell_page(
                LayoutDefinition.model_validate(desired),
                LayoutCell.model_validate(new_cells[entity]),
                catalog.pages,
            )
            if catalog.pages and page is None:
                raise reject("PAGE_SCOPE_INVALID", "手動枠のページを一意に指定してください。")
            scope_pages = {t.page_id for t in catalog.targets.values() if t.kind == "cell"}
            if page is not None and page not in scope_pages:
                raise reject("PAGE_SCOPE_INVALID", "現在のページ外へ枠を作成できません。")
            target("cell", canonical, page, cell["text"] or "手動枠")
            if cell["role"] in {"label", "parent_label"}:
                target("heading", canonical, page, cell["text"])

        def remap(value: Any, key: str = "") -> Any:
            if isinstance(value, str):
                return substitutions.get(value, value) if key in REFERENCE_FIELDS else value
            if isinstance(value, list):
                return [remap(v, key) for v in value]
            if isinstance(value, dict):
                return {k: remap(v, k) for k, v in value.items()}
            return value

        desired = remap(desired)
        new_cells = indexed(desired["cells"])
        # Deleting a physical frame deletes only its owned/dependent links. The
        # remaining graph is still validated by the normal authoring service.
        stage["relations"] = [
            r
            for r in stage["relations"]
            if r["inputCellId"] not in removed and not set(r["labelCellIds"]) & removed
        ]
        stage["recordGroups"] = [
            g for g in stage["recordGroups"] if not set(g["inputCellIds"]) & removed
        ]
        stage["recordSections"] = [
            s for s in stage["recordSections"] if not set(s["recordLabelCellIds"]) & removed
        ]
        stage["recordGroups"] = [
            g
            for g in stage["recordGroups"]
            if g["sectionId"] in {s["id"] for s in stage["recordSections"]}
        ]
        for cell in stage["cells"]:
            if cell.get("parentLabelCellId") in removed:
                cell["parentLabelCellId"] = None
        if before["cellMerges"] != desired["cellMerges"]:
            # Merge parts are physical history, not a source of semantic mappings.
            # Existing merge records must be preserved unless explicitly unmerged.
            affected = set(removed) | (new_cells.keys() - old_cells.keys())
            old_merges, new_merges = indexed(before["cellMerges"]), indexed(desired["cellMerges"])
            for merge_id, merge in new_merges.items():
                previous = old_merges.get(merge_id)
                sources = (
                    {p["cell"]["id"]: p["cell"] for p in previous["parts"]}
                    if previous
                    else old_cells
                )
                if any(p["cell"] != sources.get(p["cell"]["id"]) for p in merge["parts"]):
                    raise reject(
                        "MANUAL_INTENT_UNSUPPORTED", "結合前の物理セル履歴を変更できません。", 422
                    )
                if previous is None:
                    from app.editing.geometry_intents import merge_geometry, overlap

                    parts = [p["cell"] for p in merge["parts"]]
                    _, bounds = merge_geometry(
                        parts,
                        before["canonicalWidth"],
                        before["canonicalHeight"],
                    )
                    parent = new_cells.get(merge["parentCellId"])
                    selected = {c["id"] for c in parts}
                    if (
                        parent is None
                        or any(parent[k] != v for k, v in bounds.items())
                        or any(
                            overlap(bounds, c) > 0
                            for entity, c in old_cells.items()
                            if entity not in selected and entity not in removed
                        )
                    ):
                        raise reject("GEOMETRY_MERGE_INVALID", "結合の範囲が一致しません。", 422)
            for key in old_merges.keys() | new_merges.keys():
                if old_merges.get(key) != new_merges.get(key):
                    merge = new_merges.get(key) or old_merges[key]
                    affected.add(merge["parentCellId"])
                    affected.update(p["cell"]["id"] for p in merge["parts"])
            if any(
                c not in old_cells
                and c not in new_cells
                and c not in {p["cell"]["id"] for m in before["cellMerges"] for p in m["parts"]}
                for c in affected
            ):
                raise reject("MANUAL_INTENT_UNSUPPORTED", "結合の物理セルが一致しません。", 422)
            stage["cellMerges"] = copy.deepcopy(desired["cellMerges"])
        if before["allowCrossAxisLabels"] != desired["allowCrossAxisLabels"]:
            if before["relations"] == desired["relations"]:
                raise reject(
                    "MANUAL_INTENT_UNSUPPORTED", "接続のない状態で検証条件を変更できません。", 422
                )
            stage["allowCrossAxisLabels"] = desired["allowCrossAxisLabels"]
        # All other top-level data is server-owned. Even a detector flag cannot be
        # used to suppress validation or to declare the layout verified.
        allowed = {
            "cells",
            "cellMerges",
            "relations",
            "labelPool",
            "recordSections",
            "recordGroups",
            "recordStructureVerified",
            "allowCrossAxisLabels",
        }
        if any(
            before.get(k) != desired.get(k)
            for k in before.keys() | desired.keys()
            if k not in allowed
        ):
            raise reject(
                "MANUAL_INTENT_UNSUPPORTED", "検出設定・品質情報は手動保存で変更できません。", 422
            )
        by_id = indexed(stage["cells"])
        stage["cells"] = [by_id[c["id"]] for c in desired["cells"]]
        result.desired = desired
        result.changed = physical != catalog.state
        return result
