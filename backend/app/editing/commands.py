from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.domain.ocr_models import FieldReading
from app.editing.catalog import Catalog, Target
from app.editing.compilation_limits import AUTHORING_LIMITS, COMMAND_LIMITS, CompilationLimits
from app.editing.errors import reject
from app.editing.operation_contracts import (
    CellDelete,
    CellMerge,
    CellRename,
    CellSetReading,
    CellSetRole,
    LocalRef,
    MeaningBind,
    MeaningCreate,
    MeaningDelete,
    MeaningUnbind,
    MeaningUpdate,
    OcrSetValue,
    Operation,
    Ref,
)
from app.layout_analysis.models import LayoutDefinition
from app.services.template_authoring_service import PreparedAuthoring


@dataclass
class Plan:
    before: dict[str, Any]
    after: dict[str, Any]
    touched: list[Target] = field(default_factory=list)
    created: dict[str, tuple[str, str]] = field(default_factory=dict)
    prepared: PreparedAuthoring | None = None

    @property
    def changed(self) -> bool:
        return self.before != self.after


def name(value: str) -> str:
    if not value.strip() or value != value.strip():
        raise reject("INVALID_ARGUMENT", "名称の前後に空白を含められません。", 422)
    return value


class CommandCompiler:
    def compile(self, catalog: Catalog, body: Any) -> Plan:
        # Legacy transport adapter; v2 calls compile_operations directly.
        if body.correction is not None:
            raise reject("CAPABILITY_UNAVAILABLE", "対象の言い直しは会話制御フェーズで接続します。")
        return self.compile_operations(catalog, body.operations)

    def compile_operations(
        self, catalog: Catalog, operations: list[Operation], *,
        limits: CompilationLimits = COMMAND_LIMITS,
    ) -> Plan:
        if not 1 <= len(operations) <= limits.operations:
            raise reject("EDIT_BATCH_TOO_LARGE", "操作群の件数上限を超えています。", 413)
        plan = Plan(copy.deepcopy(catalog.state), copy.deepcopy(catalog.state))
        from app.editing.semantic_commands import SemanticCommands

        local: dict[str, tuple[str, str]] = {}
        expanded = 0
        physical_targets: set[str] = set()

        def resolve(handle: str, kind: str | tuple[str, ...]) -> Target:
            nonlocal expanded
            target = catalog.resolve(handle, kind)
            if (
                target.kind in {"cell", "heading"}
                and plan.after["kind"] == "template"
                and not any(c["id"] == target.entity_id for c in plan.after["layout"]["cells"])
            ):
                raise reject("TARGET_STALE", "同じ操作群で削除済みの欄です。")
            plan.touched.append(target)
            if target.kind in {"cell", "field"}:
                expanded += 1
                physical_targets.add(target.entity_id)
                if expanded > limits.references or len(physical_targets) > limits.cells:
                    raise reject(
                        "EDIT_BATCH_TOO_LARGE", "一括操作の対象は合計1000欄までです。", 413
                    )
            return target

        def meaning(ref: Ref) -> dict[str, Any]:
            if isinstance(ref, LocalRef):
                binding = local.get(ref.local_ref)
                if binding is None or binding[0] != "meaning":
                    raise reject(
                        "LOCAL_REF_INVALID", "作成前または未定義の意味を参照しています。", 422
                    )
                entity = binding[1]
            else:
                entity = resolve(ref, "meaning").entity_id
            entry: dict[str, Any] | None = next(
                (item for item in plan.after["layout"]["labelPool"] if item["id"] == entity), None
            )
            if entry is None:
                raise reject("LOCAL_REF_INVALID", "削除済みの意味を再利用できません。", 422)
            return entry

        semantics = SemanticCommands(catalog, plan, local, resolve, meaning)
        spill: dict[str, int] | None = None
        for operation in operations:
            if operation.op not in catalog.capabilities:
                raise reject("CAPABILITY_UNAVAILABLE", "この編集対象で使用できない操作です。")
            if semantics.apply(operation):
                continue
            if isinstance(operation, OcrSetValue):
                target = resolve(operation.target, "field")
                if "ocr.set_value" not in target.operations:
                    raise reject("CAPABILITY_UNAVAILABLE", "この欄は文字列として補正できません。")
                before = plan.after["fields"][target.entity_id]
                after = {
                    **before,
                    "raw": operation.value,
                    "normalized": operation.value,
                    "status": "blank" if operation.value is None else "readable",
                    "alternatives": [],
                    "reviewReason": None,
                }
                plan.after["fields"][target.entity_id] = FieldReading.model_validate(
                    after
                ).model_dump(mode="json", by_alias=True)
                continue
            layout = plan.after["layout"]
            cells = {cell["id"]: cell for cell in layout["cells"]}
            if isinstance(operation, CellMerge | CellDelete):
                from app.editing import cell_geometry_commands as geometry

                entities = [resolve(h, "cell").entity_id for h in self._unique(operation.cells)]
                if isinstance(operation, CellMerge):
                    # Shared by every merge of this batch: a merged label keeps its spill.
                    spill = spill if spill is not None else {
                        t.entity_id: t.detail["textSpillsRight"]
                        for t in catalog.targets.values()
                        if t.kind == "cell" and t.detail.get("textSpillsRight")
                    }
                    geometry.merge_spoken(
                        plan.after,
                        entities,
                        operation.role,
                        None if operation.name is None else name(operation.name),
                        spill,
                    )
                else:
                    geometry.delete(plan.after, entities)
            elif isinstance(operation, CellRename):
                target = resolve(operation.cell, "cell")
                cell = cells[target.entity_id]
                if cell.get("virtual"):
                    raise reject("CAPABILITY_UNAVAILABLE", "仮想見出し名は見出し操作で変更します。")
                if operation.name is None:
                    cell.pop("fieldLabel", None)
                else:
                    cell["fieldLabel"] = name(operation.name)
            elif isinstance(operation, CellSetRole):
                for handle in self._unique(operation.cells):
                    cell = cells[resolve(handle, "cell").entity_id]
                    if cell["role"] == operation.role:
                        continue
                    if (
                        cell.get("virtual")
                        or cell.get("markSets")
                        or cell.get("compositeSubfields")
                        or cell.get("readingMode")
                    ):
                        raise reject(
                            "DEPENDENCY_CONFLICT",
                            "読取設定・仮想見出しの依存を先に解決してください。",
                        )
                    cell["role"] = operation.role
            elif isinstance(operation, CellSetReading):
                for handle in self._unique(operation.cells):
                    cell = cells[resolve(handle, "cell").entity_id]
                    if cell["role"] != "input" or cell.get("compositeSubfields"):
                        raise reject(
                            "DEPENDENCY_CONFLICT", "通常の記入欄に読取方法を指定してください。"
                        )
                    if operation.reading == "text":
                        cell.pop("readingMode", None)
                    else:
                        cell["readingMode"] = "number"
                        if not any(r["inputCellId"] == cell["id"] for r in layout["relations"]):
                            entry = self._new_meaning("番号", [])
                            layout["labelPool"].append(entry)
                            layout["relations"].append(
                                self._relation(cell["id"], entry["targetPath"], [], "horizontal")
                            )
            elif isinstance(operation, MeaningCreate):
                if operation.local_ref in local:
                    raise reject("LOCAL_REF_INVALID", "同じlocalRefを二度作成できません。", 422)
                entry = self._new_meaning(name(operation.name), self._aliases(operation.aliases))
                layout["labelPool"].append(entry)
                local[operation.local_ref] = ("meaning", entry["id"])
                plan.created[operation.local_ref] = ("meaning", entry["id"])
            elif isinstance(operation, MeaningUpdate):
                entry = meaning(operation.meaning)
                old_name = entry["label"]
                entry["label"], entry["aliases"] = (
                    name(operation.name),
                    self._aliases(operation.aliases),
                )
                for relation in layout["relations"]:
                    if relation["targetPath"] == entry["targetPath"]:
                        cell = cells[relation["inputCellId"]]
                        if not cell.get("fieldLabel") or cell["fieldLabel"] == old_name:
                            cell["fieldLabel"] = entry["label"]
            elif isinstance(operation, MeaningDelete):
                entry = meaning(operation.meaning)
                if any(r["targetPath"] == entry["targetPath"] for r in layout["relations"]) or any(
                    mark.get("targetPath") == entry["targetPath"]
                    for cell in cells.values()
                    for mark in cell.get("markSets", [])
                ):
                    raise reject(
                        "DEPENDENCY_CONFLICT", "接続を解除してから意味を削除してください。"
                    )
                layout["labelPool"] = [
                    item for item in layout["labelPool"] if item["id"] != entry["id"]
                ]
            elif isinstance(operation, MeaningBind):
                entry = meaning(operation.meaning)
                labels = [resolve(h, "cell").entity_id for h in self._unique(operation.label_cells)]
                if any(cells[c]["role"] != "label" for c in labels):
                    raise reject("DEPENDENCY_CONFLICT", "項目名セルを接続元に指定してください。")
                axis = "horizontal" if operation.axis == "direct" else operation.axis
                if operation.axis == "direct" and labels:
                    raise reject("INVALID_ARGUMENT", "直接割当には項目名セルを指定しません。", 422)
                for handle in self._unique(operation.input_cells):
                    cell = cells[resolve(handle, "cell").entity_id]
                    sets = cell.get("markSets", [])
                    if cell["role"] == "mark" and len(sets) == 1:
                        # Connecting a meaning to a choice box declares where its value is
                        # stored: the single choice group takes that path, whether it was
                        # still empty or pointed elsewhere (e.g. a checklist "□" and its label).
                        sets[0]["targetPath"] = entry["targetPath"]
                    if cell["role"] == "mark" and len(sets) > 1 and not any(
                        m["targetPath"] == entry["targetPath"] for m in sets
                    ):
                        unset = [m for m in sets if not m["targetPath"]]
                        if len(unset) != 1:
                            raise reject(
                                "DEPENDENCY_CONFLICT",
                                "選択肢のまとまりが複数あるため、どれに保存するかを選択欄の設定で決めてください。",
                            )
                        unset[0]["targetPath"] = entry["targetPath"]
                    # A mark box without choices yet keeps the connection as unfinished setup.
                    if cell["role"] not in {"input", "mark"}:
                        raise reject(
                            "DEPENDENCY_CONFLICT",
                            "項目名・見出し・読み取らない枠には意味を接続できません。記入欄にしてから接続してください。",
                        )
                    in_table = any(
                        cell["id"] in group["inputCellIds"]
                        for group in layout.get("recordGroups", [])
                    )
                    existing = [r for r in layout["relations"] if r["inputCellId"] == cell["id"]]
                    if any(r["targetPath"] != entry["targetPath"] for r in existing):
                        if in_table or len(sets) > 1:
                            raise reject(
                                "DEPENDENCY_CONFLICT",
                                "この欄はすでに別の項目名につながっています。"
                                "前の線を外してから、もう一度つないでください。",
                            )
                        # A new line from an item name replaces the field's earlier meaning.
                        replaced = {r["id"] for r in existing}
                        layout["relations"] = [
                            r for r in layout["relations"] if r["id"] not in replaced
                        ]
                        if cell.get("fieldLabel") in {
                            item["label"]
                            for item in layout["labelPool"]
                            if item["targetPath"] in {r["targetPath"] for r in existing}
                        }:
                            cell.pop("fieldLabel", None)
                        existing = []
                    if in_table and existing:
                        # Relabeling a bound column never changes row membership,
                        # field paths or relation identities.
                        for relation in existing:
                            relation.update(labelCellIds=labels[:], axis=axis)
                        continue
                    exact = next(
                        (r for r in existing if r["axis"] == axis and r["labelCellIds"] == labels),
                        None,
                    )
                    if exact is None:
                        layout["relations"].append(
                            self._relation(cell["id"], entry["targetPath"], labels, axis)
                        )
                    if not existing and not cell.get("fieldLabel"):
                        cell["fieldLabel"] = entry["label"]
            elif isinstance(operation, MeaningUnbind):
                ids = {
                    resolve(handle, "relation").entity_id
                    for handle in self._unique(operation.relations)
                }
                layout["relations"] = [r for r in layout["relations"] if r["id"] not in ids]
        if catalog.state["kind"] == "template":
            semantics.finish()
            if limits == AUTHORING_LIMITS:
                for field_name, field_info in LayoutDefinition.model_fields.items():
                    value = plan.after["layout"].get(field_info.alias or field_name)
                    maximum = next(
                        (m.max_length for m in field_info.metadata if hasattr(m, "max_length")),
                        None,
                    )
                    if isinstance(value, list) and maximum is not None and len(value) > maximum:
                        raise reject(
                            "AUTHORING_CAPACITY_EXCEEDED",
                            "保存する意味・表の件数が上限を超えています。",
                            422,
                        )
            self.validate_layout(plan.after["layout"])
        return plan

    @staticmethod
    def _unique(handles: list[str]) -> list[str]:
        if len(handles) != len(set(handles)):
            raise reject("INVALID_ARGUMENT", "同じ対象を配列で重複指定できません。", 422)
        return handles

    @staticmethod
    def _aliases(aliases: list[str]) -> list[str]:
        cleaned = [name(alias) for alias in aliases]
        if len(cleaned) != len(set(cleaned)):
            raise reject("INVALID_ARGUMENT", "別名を重複指定できません。", 422)
        return cleaned

    @staticmethod
    def _new_meaning(label: str, aliases: list[str]) -> dict[str, Any]:
        key = uuid4().hex
        return {
            "id": f"meaning_{key}",
            "label": label,
            "targetPath": f"fields.field_{key}",
            "section": "custom",
            "aliases": aliases,
            "source": "manual",
        }

    @staticmethod
    def _relation(cell: str, path: str, labels: list[str], axis: str) -> dict[str, Any]:
        return {
            "id": f"relation_{uuid4().hex}",
            "inputCellId": cell,
            "labelCellIds": labels,
            "parentLabelCellId": None,
            "axis": axis,
            "order": 1,
            "targetPath": path,
        }

    @staticmethod
    def validate_layout(raw: dict[str, Any]) -> LayoutDefinition:
        try:
            layout = LayoutDefinition.model_validate(raw)
        except ValidationError as error:
            raise reject(
                "INVALID_GRAPH", "意味付けの構造または依存関係が不正です。", 422
            ) from error
        cells = {cell.id: cell for cell in layout.cells}
        paths: dict[str, str] = {}
        for relation in layout.relations:
            if cells[relation.input_cell_id].role not in {"input", "mark"} or any(
                cells[label].role != "label" for label in relation.label_cell_ids
            ):
                raise reject("DEPENDENCY_CONFLICT", "分類変更後にも接続の依存関係が残っています。")
            owner = paths.setdefault(relation.target_path, relation.input_cell_id)
            if owner != relation.input_cell_id:
                raise reject(
                    "DUPLICATE_PATH", "複数の欄に同じ単一値の意味を割り当てられません。", 422
                )
        for cell in layout.cells:
            for mark in cell.mark_sets:
                if (
                    mark.target_path
                    and mark.target_path in paths
                    and paths[mark.target_path] != cell.id
                ):
                    raise reject("DUPLICATE_PATH", "マークと記入欄の保存先が重複しています。", 422)
                if mark.target_path:
                    paths[mark.target_path] = cell.id
        return layout
