"""Freeze owned page images and existing cell identities before any paid send."""

from __future__ import annotations

import copy
import hashlib
import json
from io import BytesIO
from typing import Any

from PIL import Image

from app.db.models import JobArtifact, TemplateVersion
from app.domain.print_layout import transform_rect
from app.editing.authoring_provenance import facets, key, protection, touches
from app.editing.catalog import CatalogService
from app.editing.domain.command import digest
from app.editing.draft_catalog import with_draft_cells
from app.editing.errors import reject
from app.editing.manual_compiler import PHYSICAL_FIELDS, ManualCompiler
from app.editing.semantic_commands import section_cells

POLICY = "template-analysis.v3.0"
PHYSICAL_POLICY = "template-analysis.physical-values.v1"


def physical(layout: dict[str, Any]) -> str:
    names = PHYSICAL_FIELDS | {"id", "source", "text", "confidence", "virtual"}

    def numeric_values(value: Any) -> Any:
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, list):
            return [numeric_values(v) for v in value]
        if isinstance(value, dict):
            return {k: numeric_values(v) for k, v in value.items()}
        return value

    return digest(
        numeric_values(
            {
                "size": [layout["canonicalWidth"], layout["canonicalHeight"]],
                "cells": sorted(
                    ({k: c.get(k) for k in names} for c in layout["cells"]), key=lambda c: c["id"]
                ),
                "merges": layout["cellMerges"],
            }
        )
    )


def capture(session: Any, owner: Any, catalog: Any, body: Any, storage: Any) -> dict[str, Any]:
    from app.template_analysis.contracts import PageProposal
    from app.template_analysis.provider import INSTRUCTIONS, OpenAIAnalysisProvider

    if owner.resource_kind != "template" or not catalog.editable:
        raise reject("RESOURCE_NOT_EDITABLE", "下書きテンプレートだけを解析できます。", 409)
    if body.page_scope.model_dump(by_alias=True) != owner.context_json[
        "pageScope"
    ] or body.selected_table_id != owner.context_json.get("selectedTableId"):
        raise reject("SCOPE_CHANGED", "解析範囲と編集画面の対象が一致しません。", 409)
    current = body.draft.model_dump(mode="json", by_alias=True)
    version = session.get(TemplateVersion, owner.resource_id)
    geometry_enabled = version.mode == "SINGLE_IMAGE_EXPERIMENTAL" and not catalog.pages
    # Creating physical identities cannot widen an existing local/table selection.
    structure_enabled = geometry_enabled and owner.mode != "local" and not body.selected_table_id
    saved_physical = physical(catalog.state["layout"])
    if physical(current["layout"]) != saved_physical and not (
        geometry_enabled and body.task in {"full", "structure"}
    ):
        raise reject("PHYSICAL_DRAFT_UNSAVED", "枠の変更を保存してから解析してください。", 409)
    # Validate captured hand intent without saving or allocating business mappings.
    if body.task in {"full", "structure", "draft"}:
        from app.template_analysis.draft import compile_hand

        compile_hand(catalog, body)
    else:
        ManualCompiler().compile(catalog, body)
    if (
        current["documentIdentityFields"] != catalog.state["documentIdentityFields"]
        or current["documentName"] != catalog.state["documentName"]
    ):
        raise reject("DEPENDENCY_CONFLICT", "書類情報の変更を保存してから解析してください。", 409)
    layout = current["layout"]
    values, saved = facets(layout), facets(catalog.state["layout"])
    origin = protection(session, owner.resource_id, layout)
    touched = touches(
        [v.model_dump(mode="json", by_alias=True) for v in body.manual_touched_facets],
        catalog,
        layout,
    )

    def meaningful(item: str, value: Any) -> bool:
        _, facet = json.loads(item)
        if item in saved or facet in {"role", "geometry"}:
            return True
        if facet == "meaning":
            return bool(value["fieldLabel"] or value["meanings"])
        if facet == "records":
            return bool(value["groups"] or value["sections"])
        if facet == "mark":
            return any(value)
        return bool(value)

    touched |= {k for k, v in values.items() if saved.get(k) != v and meaningful(k, v)}
    released = set()
    for handle in body.release_role_protection:
        target = catalog.resolve(handle, "cell")
        released.add(target.entity_id)
    if len(released) != len(body.release_role_protection):
        raise reject("INVALID_ARGUMENT", "分類の見直し対象が重複しています。", 422)
    draft_catalog = with_draft_cells(catalog, {**catalog.state, "layout": layout})
    target_ids = {t.entity_id for t in draft_catalog.targets.values() if t.kind == "cell"}
    if body.selected_table_id:
        if not any(s["id"] == body.selected_table_id for s in layout["recordSections"]):
            raise reject("TABLE_SCOPE_INVALID", "表の所有を確認できません。", 422)
        target_ids &= section_cells(layout, body.selected_table_id)
    if owner.mode == "local":
        target_ids &= {v["entityId"] for v in owner.context_json["selected"] if v["kind"] == "cell"}
    cells = {c["id"]: c for c in layout["cells"]}
    model_cells = {c.id: c for c in body.draft.layout.cells}
    cell_pages = {
        cell_id: CatalogService.cell_page(body.draft.layout, cell, catalog.pages)
        for cell_id, cell in model_cells.items()
    }
    cell_handles = {
        t.entity_id: t.handle for t in draft_catalog.targets.values() if t.kind == "cell"
    }
    target_ids = {c for c in target_ids if not cells[c].get("virtual")}
    empty_structure = structure_enabled and body.task in {"full", "structure"} and not cells
    if (not target_ids and not empty_structure) or not released <= target_ids:
        raise reject("PAGE_SCOPE_INVALID", "解析対象の物理セルを指定してください。", 422)
    protected = set(touched) - {key("@layout", "geometry")}
    bound = {r["inputCellId"] for r in layout["relations"]}
    geometry_bound = (
        set(bound)
        | {
            e
            for r in layout["relations"]
            for e in [*r["labelCellIds"], r.get("parentLabelCellId")]
            if e is not None
        }
        | {
            e
            for s in layout["recordSections"]
            for e in [*s["recordLabelCellIds"], *s["rowKeyCellIds"], s.get("parentLabelCellId")]
            if e is not None
        }
        | {
            e
            for g in layout["recordGroups"]
            for e in [*g["inputCellIds"], g.get("recordKeyCellId")]
            if e is not None
        }
    )
    geometry_editable = set()
    for cell_id, cell in cells.items():
        role_key = key(cell_id, "role")
        if (
            origin.get(role_key) not in {"unreviewed", "ai"} and cell_id not in released
        ) or role_key in touched:
            protected.add(role_key)
        for facet in ("meaning", "binding", "parent", "records", "reading", "mark"):
            item = key(cell_id, facet)
            value = values[item]
            nonempty = (
                bool(value["fieldLabel"] or value["meanings"])
                if facet == "meaning"
                else bool(value["groups"])
                if facet == "records"
                else any(value)
                if facet == "mark"
                else bool(value)
            )
            if nonempty or origin.get(item) == "manual":
                protected.add(item)
        if (
            cell.get("readingMode")
            or cell.get("markSets")
            or cell.get("compositeSubfields")
            or cell.get("virtual")
            or cell_id in bound
        ):
            protected.add(role_key)
        geometry_key = key(cell_id, "geometry")
        if (
            origin.get(geometry_key) in {"unreviewed", "ai"}
            and geometry_key not in touched
            and not (
                key(cell_id, "role") in protected
                or any(
                    key(cell_id, f) in protected
                    for f in ("meaning", "binding", "parent", "records", "reading", "mark")
                )
                or cell.get("shapeType") == "polygon"
                or cell_id in geometry_bound
            )
        ):
            geometry_editable.add(cell_id)
        else:
            protected.add(geometry_key)
    manifest = []
    physical_count = 0
    for saved_page in catalog.pages or [None]:
        page_id = f"{saved_page['page']}:{saved_page['range']}" if saved_page else None
        ids = [cell_id for cell_id in sorted(target_ids) if cell_pages[cell_id] == page_id]
        if not ids and not empty_structure:
            continue
        # Read-only context includes headings and labels on this page.
        page_cells = [c for c in layout["cells"] if cell_pages[c["id"]] == page_id]
        refs = {
            f"c{i + 1}": copy.deepcopy(c)
            for i, c in enumerate(sorted(page_cells, key=lambda c: c["id"]))
        }
        reverse = {c["id"]: r for r, c in refs.items()}
        targets = [reverse[c] for c in ids]
        role_locks = [r for r in targets if key(refs[r]["id"], "role") in protected]
        useful = [
            r
            for r in targets
            if r not in role_locks
            or (
                body.task in {"full", "structure", "draft"}
                and refs[r]["role"] == "input"
                and key(refs[r]["id"], "binding") not in protected
                and key(refs[r]["id"], "meaning") not in protected
            )
        ]
        if not useful and not structure_enabled:
            continue
        image_ref = saved_page["referenceImage"] if saved_page else None
        artifact_id = image_ref["artifactId"] if image_ref else version.clean_template_artifact_id
        artifact = session.get(JobArtifact, artifact_id)
        if (
            not artifact
            or artifact.job_id != version.source_job_id
            or (
                image_ref
                and (
                    image_ref["sha256"] != artifact.sha256
                    or image_ref["relativePath"] != artifact.relative_path
                )
            )
        ):
            raise reject("SNAPSHOT_STALE", "版が所有する基準画像を確認できません。", 409)
        image = storage.read_bytes(artifact.relative_path)
        if hashlib.sha256(image).hexdigest() != artifact.sha256:
            raise reject("SNAPSHOT_STALE", "基準画像の内容が変わりました。", 409)
        try:
            with Image.open(BytesIO(image)) as header:
                media_type = Image.MIME.get(header.format)
                if media_type not in {"image/png", "image/jpeg", "image/webp"}:
                    raise ValueError("Unsupported reference image")
                image_size = header.size
                if len(image) > 20_000_000 or max(image_size) > 20000:
                    raise ValueError("Reference image exceeds limits")
                header.verify()
        except (OSError, ValueError) as error:
            raise reject("REFERENCE_IMAGE_INVALID", "基準画像を検証できません。", 422) from error
        handles = {r: cell_handles[c["id"]] for r, c in refs.items() if c["id"] in cell_handles}
        # Context is limited to the catalog's authorized page; never manufacture a handle.
        if not set(refs) <= set(handles):
            raise reject("PAGE_SCOPE_INVALID", "ページ参照の編集所有を確認できません。", 409)
        provider_cells = []
        for r, c in refs.items():
            geometry = (
                transform_rect(c, saved_page["transform"])
                if saved_page and not c.get("virtual")
                else {k: c[k] for k in ("x", "y", "width", "height")}
            )
            vertices = c.get("vertices") or []
            if saved_page and vertices:
                t = saved_page["transform"]
                vertices = [
                    {
                        "x": t["offsetX"] + (v["x"] - t["originX"]) * t["scale"],
                        "y": t["offsetY"] + (v["y"] - t["originY"]) * t["scale"],
                    }
                    for v in vertices
                ]
            if not c.get("virtual") and (
                geometry["x"] < 0
                or geometry["y"] < 0
                or geometry["x"] + geometry["width"] > image_size[0]
                or geometry["y"] + geometry["height"] > image_size[1]
            ):
                raise reject("REFERENCE_IMAGE_INVALID", "セルと基準画像の範囲が一致しません。", 422)
            provider_cells.append(
                {
                    "ref": r,
                    **geometry,
                    "vertices": vertices,
                    "text": c.get("printedLabelText") or c.get("text", ""),
                    "role": c["role"],
                    "target": r in targets,
                    "roleLocked": r in role_locks,
                    "hasMeaning": c["id"] in bound,
                    "virtual": c.get("virtual", False),
                }
            )
        count = (
            1
            if body.task in {"full", "structure", "draft"}
            else (len([r for r in targets if r not in role_locks]) + 199) // 200
        )
        manifest.append(
            {
                "pageId": page_id,
                "pageKey": page_id or "legacy",
                "artifactId": artifact.id,
                "imagePath": artifact.relative_path,
                "imageSha256": artifact.sha256,
                "mediaType": media_type,
                "refs": refs,
                "targets": targets,
                "handles": handles,
                "lockedRoles": role_locks,
                "protectedFacets": sorted(protected),
                "bound": [reverse[c] for c in bound if c in reverse],
                "parentRefs": {r: reverse.get(c.get("parentLabelCellId")) for r, c in refs.items()},
                "providerCells": provider_cells,
                "plannedCalls": count,
                "structure": structure_enabled and body.task in {"full", "structure"},
                # A draft covers the page in one unit; no issued grid candidate or region split.
                "wholePage": body.task == "draft",
                "structureSemantics": body.task == "draft",
                "geometryEditable": [r for r, c in refs.items() if c["id"] in geometry_editable],
                "geometryBounds": [0, 0, *image_size]
                if owner.mode != "local" and not body.selected_table_id
                else [
                    min(refs[r]["x"] for r in targets),
                    min(refs[r]["y"] for r in targets),
                    max(refs[r]["x"] + refs[r]["width"] for r in targets)
                    - min(refs[r]["x"] for r in targets),
                    max(refs[r]["y"] + refs[r]["height"] for r in targets)
                    - min(refs[r]["y"] for r in targets),
                ],
            }
        )
        physical_count += len(targets)
    if physical_count > 1000:
        raise reject("EDIT_BATCH_TOO_LARGE", "解析対象は1000セルまでです。", 413)
    return {
        "policy": POLICY,
        "physicalPolicy": PHYSICAL_POLICY,
        "regionScope": body.region_scope.model_dump() if body.region_scope else None,
        "priorityBounds": body.priority_bounds,
        "provider": {
            "model": OpenAIAnalysisProvider.model,
            "instructionsDigest": digest(INSTRUCTIONS),
            "schemaDigest": digest(PageProposal.model_json_schema(by_alias=True)),
            "rolesPolicy": "eight-choices-confidence-0.70-v1",
        },
        "binding": body.binding.model_dump(by_alias=True),
        "resourceId": owner.resource_id,
        "baseRevision": catalog.revision,
        "draft": current,
        "draftDigest": digest(current),
        "physicalDigest": physical(layout),
        "savedPhysicalDigest": saved_physical,
        "protectionDigest": digest([origin, sorted(protected)]),
        "pages": manifest,
    }
