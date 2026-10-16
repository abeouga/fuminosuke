"""Convert captured hand-editor intent into the same closed semantic commands.

The proposed graph is not a replacement write. Physical edits are separately
allowlisted; meanings/rows/relations are compiled and allocated by CommandCompiler.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Protocol

from pydantic import TypeAdapter

from app.editing.catalog import Catalog
from app.editing.commands import CommandCompiler, Plan
from app.editing.compilation_limits import AUTHORING_LIMITS
from app.editing.errors import reject
from app.editing.geometry_compiler import (
    PHYSICAL_FIELDS as PHYSICAL_FIELDS,
)
from app.editing.geometry_compiler import (
    REFERENCE_FIELDS as REFERENCE_FIELDS,
)
from app.editing.geometry_compiler import (
    SEMANTIC_FIELDS,
    GeometryCompiler,
    indexed,
)
from app.editing.manual_draft import ManualDraft
from app.editing.mark_commands import node_id
from app.editing.operation_contracts import Operation


class ManualInput(Protocol):
    request_id: str
    draft: ManualDraft


class ManualCompiler:
    def compile(self, catalog: Catalog, body: ManualInput) -> tuple[Plan, dict[str, str], bool]:
        if catalog.state["kind"] != "template":
            raise reject("CAPABILITY_UNAVAILABLE", "手動アダプターはテンプレート専用です。")
        desired = body.draft.layout.model_dump(mode="json", by_alias=True)
        before = catalog.state["layout"]
        geometry = GeometryCompiler().prepare(catalog, desired, body.request_id)
        physical, desired = geometry.state, geometry.desired
        stage = physical["layout"]
        old_cells, new_cells = indexed(before["cells"]), indexed(desired["cells"])
        substitutions = geometry.substitutions
        local_entities: dict[str, str] = {}
        operations: list[dict[str, Any]] = []
        touched, targets = geometry.touched, geometry.targets
        handle, target = geometry.handle, geometry.target
        reading_operations: list[dict[str, Any]] = []
        bindings = geometry.bindings
        physical_changed = geometry.changed

        def local(entity: str) -> dict[str, str]:
            key = next((k for k, v in local_entities.items() if v == entity), None)
            if key is None:
                key = f"new_{len(local_entities)}"
                local_entities[key] = entity
            return {"localRef": key}

        staged = replace(catalog, state=physical, targets=targets)
        pools, proposed_pools = indexed(before["labelPool"]), indexed(desired["labelPool"])
        meanings: dict[str, str | dict[str, str]] = {}
        for entity, entry in proposed_pools.items():
            if entity in pools:
                if any(entry[k] != pools[entity][k] for k in ("section", "source")):
                    raise reject(
                        "MANUAL_INTENT_UNSUPPORTED",
                        "意味の分類・検出由来情報は名称変更で変更できません。",
                        422,
                    )
                if entry["targetPath"] != pools[entity]["targetPath"]:
                    raise reject(
                        "MANUAL_INTENT_UNSUPPORTED",
                        "既存の意味の保存先は名称変更で変更できません。",
                        422,
                    )
                ref: str | dict[str, str] = handle("meaning", entity)
                if any(entry[k] != pools[entity][k] for k in ("label", "aliases")):
                    operations.append(
                        {
                            "op": "meaning.update",
                            "meaning": ref,
                            "name": entry["label"],
                            "aliases": entry["aliases"],
                        }
                    )
            else:
                ref = local(entity)
                operations.append(
                    {
                        "op": "meaning.create",
                        **ref,
                        "name": entry["label"],
                        "aliases": entry["aliases"],
                    }
                )
            meanings[entry["targetPath"]] = ref

        def meaning(path: str) -> str | dict[str, str]:
            if path not in meanings:
                raise reject("DEPENDENCY_CONFLICT", "意味の候補を先に登録してください。", 422)
            return meanings[path]

        old_sections, new_sections = (
            indexed(stage["recordSections"]),
            indexed(desired["recordSections"]),
        )
        old_groups, new_groups = indexed(stage["recordGroups"]), indexed(desired["recordGroups"])
        for entity in old_sections.keys() & new_sections.keys():
            old, new = old_sections[entity], new_sections[entity]
            if any(old.get(k) != new.get(k) for k in ("collectionPath", "recordType", "title")):
                raise reject(
                    "MANUAL_INTENT_UNSUPPORTED",
                    "既存の表の所有情報は名称変更で変更できません。",
                    422,
                )
        table_cells = {c for g in old_groups.values() for c in g["inputCellIds"]}
        for entity in old_sections.keys() - new_sections.keys():
            operations.append(
                {"op": "records.dissolve", "section": handle("record_section", entity)}
            )
        old_rel = indexed(stage["relations"])
        new_rel = indexed(desired["relations"])

        def relation_value(r: dict[str, Any]) -> tuple[Any, ...]:
            return r["inputCellId"], r["targetPath"], tuple(r["labelCellIds"]), r["axis"]

        removed_rel = [
            r
            for k, r in old_rel.items()
            if k not in new_rel or relation_value(r) != relation_value(new_rel[k])
        ]
        unbind = [
            handle("relation", r["id"])
            for r in removed_rel
            if r["inputCellId"] not in table_cells
            or not any(
                n["inputCellId"] == r["inputCellId"] and n["targetPath"] == r["targetPath"]
                for n in new_rel.values()
            )
        ]
        if unbind:
            operations.append({"op": "meaning.unbind", "relations": unbind})
        # Physical region edits are staged privately. New option/set IDs remain
        # local references; mark.configure allocates their final identities.
        for entity, cell in new_cells.items():
            old = next(c for c in stage["cells"] if c["id"] == entity)
            old_original = old_cells.get(entity, {**old, "markSets": []})
            if all(cell.get(k) == old_original.get(k) for k in SEMANTIC_FIELDS) and not (
                cell.get("virtual") and cell["text"] != old["text"]
            ):
                continue
            ref = handle("cell", entity)
            if old_original.get("markSets") and not cell.get("markSets"):
                operations.append({"op": "mark.clear", "cell": ref})
            if old.get("readingMode") and (cell["role"] != "input" or not cell.get("readingMode")):
                operations.append({"op": "cell.set_reading", "cells": [ref], "reading": "text"})
            if ("input" if old_original.get("markSets") else old["role"]) != cell[
                "role"
            ] and not cell.get("markSets"):
                operations.append({"op": "cell.set_role", "cells": [ref], "role": cell["role"]})
            if cell.get("markSets") != old_original.get("markSets"):
                specs = []
                existing_sets = indexed(old_original.get("markSets", []))
                for mark in cell.get("markSets", []):
                    whole = mark["recognitionMode"] in {"symbol", "checklist"}
                    previous = existing_sets.get(mark["id"])
                    seed = next((m for m in old["markSets"] if m["id"] == mark["id"]), None)
                    if seed is None:
                        seed = copy.deepcopy(mark)
                        # Whole-cell modes have no option-region geometry to
                        # stage. Seeding their future semantic settings here
                        # would prevent the preceding role command from running.
                        if not whole:
                            old["markSets"].append(seed)
                    previous_options = indexed(previous["options"]) if previous else {}
                    specs_options = []
                    for option in mark["options"]:
                        parts = node_id(entity, mark["id"], option["id"])
                        if not whole:
                            candidate = next(
                                (o for o in seed["options"] if o["id"] == option["id"]), None
                            )
                            if candidate is None:
                                seed["options"].append(copy.deepcopy(option))
                                physical_changed = True
                            else:
                                physical_changed |= any(
                                    candidate[k] != option[k] for k in ("x", "y", "width", "height")
                                )
                                candidate.update(
                                    {k: option[k] for k in ("x", "y", "width", "height")}
                                )
                            target("region", parts, targets[ref].page_id, option["label"])
                            physical_changed |= (
                                previous is None or option["id"] not in previous_options
                            )
                        specs_options.append(
                            {
                                "option": handle("mark_option", parts)
                                if option["id"] in previous_options
                                else None,
                                "localRef": None
                                if option["id"] in previous_options
                                else local(parts)["localRef"],
                                "label": option["label"],
                                "value": option["value"],
                                "region": "whole-cell" if whole else bindings["region", parts],
                            }
                        )
                    specs.append(
                        {
                            "set": handle("mark_set", node_id(entity, mark["id"]))
                            if previous
                            else None,
                            "localRef": None
                            if previous
                            else local(node_id(entity, mark["id"]))["localRef"],
                            "meaning": meaning(mark["targetPath"]) if mark["targetPath"] else None,
                            "recognitionMode": mark["recognitionMode"],
                            "selectionMode": mark["selectionMode"],
                            "options": specs_options,
                        }
                    )
                if specs:
                    if old["role"] not in {"input", "mark"}:
                        operations.append({"op": "cell.set_role", "cells": [ref], "role": "input"})
                    operations.append({"op": "mark.configure", "cell": ref, "sets": specs})
            if cell.get("readingMode") != old.get("readingMode") and cell["role"] == "input":
                reading_operations.append(
                    {
                        "op": "cell.set_reading",
                        "cells": [ref],
                        "reading": "sequence" if cell.get("readingMode") else "text",
                    }
                )
            if cell.get("fieldLabel") != old.get("fieldLabel"):
                operations.append(
                    {"op": "cell.rename", "cell": ref, "name": cell.get("fieldLabel")}
                )
            if cell.get("virtual") and cell["text"] != old["text"]:
                operations.append(
                    {
                        "op": "heading.update",
                        "heading": handle("heading", entity),
                        "name": cell["text"],
                    }
                )
        for key, r in new_rel.items():
            if new_cells[r["inputCellId"]]["role"] not in {"input", "mark"}:
                continue
            if key not in old_rel or relation_value(r) != relation_value(old_rel[key]):
                operations.append(
                    {
                        "op": "meaning.bind",
                        "meaning": meaning(r["targetPath"]),
                        "inputCells": [handle("cell", r["inputCellId"])],
                        "labelCells": [handle("cell", c) for c in r["labelCellIds"]],
                        "axis": r["axis"] if r["labelCellIds"] else "direct",
                    }
                )
        section_refs: dict[str, str | dict[str, str]] = {}
        operations.extend(reading_operations)
        for entity, section in new_sections.items():
            groups = sorted(
                (g for g in new_groups.values() if g["sectionId"] == entity),
                key=lambda g: (g["order"], g["id"]),
            )
            if section == old_sections.get(entity) and groups == sorted(
                (g for g in old_groups.values() if g["sectionId"] == entity),
                key=lambda g: (g["order"], g["id"]),
            ):
                continue
            options = {
                "rowKeyStrategy": section["rowKeyStrategy"],
                "includeEmptyRows": section["includeEmptyRows"],
            }

            def rows(
                groups: list[dict[str, Any]] = groups,
                entity: str = entity,
            ) -> list[dict[str, Any]]:
                result = []
                for group in groups:
                    previous = old_groups.get(group["id"])
                    if previous and previous["inputCellIds"] != group["inputCellIds"]:
                        # The old editor may number rows anew. Find the physical row,
                        # never reuse a row ID merely because its ordinal matches.
                        previous = next(
                            (
                                g
                                for g in old_groups.values()
                                if g["sectionId"] == entity
                                and g["inputCellIds"] == group["inputCellIds"]
                            ),
                            None,
                        )
                    result.append(
                        {
                            "group": handle("record_group", previous["id"]) if previous else None,
                            "localRef": None if previous else local(group["id"])["localRef"],
                            "cells": [handle("cell", c) for c in group["inputCellIds"]],
                            "rowKeyCell": handle("cell", group["recordKeyCellId"])
                            if group.get("recordKeyCellId")
                            else None,
                            "rowKeyValue": group.get("recordKeyValue"),
                        }
                    )
                return result

            if entity not in old_sections:
                columns = []
                for cell_id in groups[0]["inputCellIds"] if groups else []:
                    column_relation = next(
                        (item for item in desired["relations"] if item["inputCellId"] == cell_id),
                        None,
                    )
                    mark = new_cells[cell_id].get("markSets", [])
                    path = (
                        column_relation["targetPath"]
                        if column_relation
                        else mark[0]["targetPath"]
                        if len(mark) == 1
                        else ""
                    )
                    columns.append(
                        {
                            "meaning": meaning(path),
                            "labelCells": [
                                handle("cell", c) for c in column_relation["labelCellIds"]
                            ]
                            if column_relation
                            else [],
                        }
                    )
                new_section_ref = local(entity)
                section_refs[entity] = new_section_ref
                operations.append(
                    {
                        "op": "records.define",
                        **new_section_ref,
                        "name": section.get("displayName") or section["title"],
                        "columns": columns,
                        "rows": rows(),
                        "options": options,
                        "columnLabels": [handle("cell", c) for c in section["recordLabelCellIds"]],
                    }
                )
                if section.get("displayName") is None:
                    operations.append(
                        {
                            "op": "records.configure",
                            "section": new_section_ref,
                            "name": None,
                            "options": options,
                            "columnLabels": None,
                        }
                    )
            else:
                section_refs[entity] = handle("record_section", entity)
                previous_groups = [g for g in old_groups.values() if g["sectionId"] == entity]
                if previous_groups != groups:
                    operations.append(
                        {
                            "op": "records.replace_rows",
                            "section": section_refs[entity],
                            "rows": rows(),
                        }
                    )
                old = old_sections[entity]
                if (
                    any(
                        old.get(k) != section.get(k)
                        for k in ("displayName", "rowKeyStrategy", "includeEmptyRows")
                    )
                    or old["recordLabelCellIds"] != section["recordLabelCellIds"]
                ):
                    operations.append(
                        {
                            "op": "records.configure",
                            "section": section_refs[entity],
                            "name": section.get("displayName"),
                            "options": options,
                            "columnLabels": [
                                handle("cell", c) for c in section["recordLabelCellIds"]
                            ],
                        }
                    )
        for entity, cell in new_cells.items():
            old = next(c for c in stage["cells"] if c["id"] == entity)
            parent = cell.get("parentLabelCellId")
            if parent != old.get("parentLabelCellId"):
                operations.append(
                    {
                        "op": "heading.attach",
                        "heading": handle("heading", parent),
                        "children": [handle("cell", entity)],
                    }
                    if parent
                    else {"op": "heading.detach", "children": [handle("cell", entity)]}
                )
        for entity, section in new_sections.items():
            old_parent = old_sections.get(entity, {}).get("parentLabelCellId")
            parent = section.get("parentLabelCellId")
            if parent != old_parent:
                operations.append(
                    {
                        "op": "heading.attach",
                        "heading": handle("heading", parent),
                        "children": [section_refs[entity]],
                    }
                    if parent
                    else {"op": "heading.detach", "children": [section_refs[entity]]}
                )
        for relation in desired["relations"]:
            previous = old_rel.get(relation["id"])
            parent = relation.get("parentLabelCellId")
            if parent != (previous.get("parentLabelCellId") if previous else None):
                children = relation["labelCellIds"] or [relation["inputCellId"]]
                operations.append(
                    {
                        "op": "heading.attach",
                        "heading": handle("heading", parent),
                        "children": [handle("cell", c) for c in children],
                    }
                    if parent
                    else {"op": "heading.detach", "children": [handle("cell", c) for c in children]}
                )
        for entity in pools.keys() - proposed_pools.keys():
            operations.append({"op": "meaning.delete", "meaning": handle("meaning", entity)})

        def field(path: str) -> str | dict[str, Any]:
            existing = next(
                (t for t in targets.values() if t.kind == "field" and t.binding_path == path), None
            )
            if existing:
                return existing.handle
            relations = [r for r in desired["relations"] if r["targetPath"] == path]
            if len(relations) != 1:
                raise reject("DEPENDENCY_CONFLICT", "識別設定の欄を一意に指定してください。", 422)
            return {"cell": handle("cell", relations[0]["inputCellId"]), "meaning": meaning(path)}

        identity = [
            f.model_dump(mode="json", by_alias=True) for f in body.draft.document_identity_fields
        ]
        if identity != catalog.state["documentIdentityFields"]:
            entries = []
            for entry in identity:
                group = entry.get("matchGroupId")
                group_ref: str | dict[str, str] | None = (
                    bindings.get(("identity_group", group)) if group else None
                )
                if group and not group_ref:
                    group_ref = local(group)
                entries.append(
                    {
                        "field": field(entry["targetPath"]),
                        "valueType": entry["valueType"],
                        "exactLength": entry["exactLength"],
                        "matchRole": entry["matchRole"],
                        "displayRole": entry["displayRole"],
                        "group": group_ref,
                        "groupLabel": entry.get("matchGroupLabel"),
                        "componentOrder": entry.get("componentOrder", 0),
                    }
                )
            operations.append({"op": "identity.configure", "entries": entries})
        document_name = (
            body.draft.document_name.model_dump(mode="json", by_alias=True)
            if body.draft.document_name
            else None
        )
        if document_name != catalog.state["documentName"]:
            operations.append(
                {
                    "op": "document_name.configure",
                    "rule": {
                        **{k: document_name[k] for k in ("separator", "prefix", "suffix")},
                        "fields": [field(p) for p in document_name["fieldPaths"]],
                    }
                    if document_name
                    else None,
                }
            )
        # Draft conversion remains one atomic plan and uses the shared validators.
        # The public operation endpoint retains its smaller command limits.
        if operations:
            decoded = TypeAdapter(list[Operation]).validate_python(operations)
            plan = CommandCompiler().compile_operations(staged, decoded, limits=AUTHORING_LIMITS)
        else:
            plan = Plan(copy.deepcopy(physical), copy.deepcopy(physical))
        plan.before = copy.deepcopy(catalog.state)
        plan.touched.extend(touched)
        for key, (kind, entity) in plan.created.items():
            client = local_entities.get(key)
            if client:
                if kind in {"mark_set", "mark_option"}:
                    import json

                    canonical_parts = json.loads(entity)
                    substitutions[f"{kind}:{client}"] = canonical_parts[-1]
                else:
                    substitutions[client] = entity
                if kind == "meaning":
                    source = proposed_pools[client]["targetPath"]
                    substitutions[source] = next(
                        p["targetPath"]
                        for p in plan.after["layout"]["labelPool"]
                        if p["id"] == entity
                    )
                elif kind == "record_section":
                    substitutions[new_sections[client]["collectionPath"]] = next(
                        s["collectionPath"]
                        for s in plan.after["layout"]["recordSections"]
                        if s["id"] == entity
                    )
        # Canonical relation IDs are allocated in the shared compiler.
        for relation in desired["relations"]:
            path = substitutions.get(relation["targetPath"], relation["targetPath"])
            matching = [
                r
                for r in plan.after["layout"]["relations"]
                if r["inputCellId"] == relation["inputCellId"]
                and r["targetPath"] == path
                and r["labelCellIds"] == relation["labelCellIds"]
            ]
            if len(matching) == 1:
                substitutions[relation["id"]] = matching[0]["id"]
        return plan, substitutions, physical_changed
