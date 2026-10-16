"""Compile outside the write lock; save business state and command receipt together."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter

from app.api.v2.editing_contracts import ManualAuthoring, ManualOcr
from app.db.models import utc_now
from app.db.mutation_models import MutationRequest
from app.editing.catalog import Catalog
from app.editing.commands import Plan
from app.editing.corrections import simple_edit
from app.editing.domain.command import (
    BoundOperation,
    ChangeSet,
    PreparedChange,
    ResolvedInstruction,
    canonical,
    digest,
    transition,
)
from app.editing.errors import reject
from app.editing.infrastructure.command_repository import SqlCommandRepository
from app.editing.infrastructure.mutation_core import MutationCore
from app.editing.manual_compiler import ManualCompiler
from app.editing.manual_ocr_compiler import compile_manual_ocr
from app.editing.models import EditingSession
from app.editing.operation_contracts import Operation
from app.editing.scope import affected_cells
from app.editing.semantic_commands import section_cells


@dataclass
class Work:
    command_id: str
    plan: Plan
    physical: bool = False
    undo_of: str | None = None
    supersedes: str | None = None
    substitutions: dict[str, str] = field(default_factory=dict)


class BusinessMutationUnitOfWork:
    def __init__(self, repository: SqlCommandRepository, core: MutationCore) -> None:
        self.repository, self.core = repository, core
        self.database = repository.database
        self.inputs: dict[str, dict[str, Any]] = {}
        self.prepared: dict[str, Work] = {}

    def register(self, command_id: str, value: dict[str, Any]) -> None:
        self.inputs[command_id] = value

    def forget(self, command_id: str) -> None:
        self.inputs.pop(command_id, None)

    @staticmethod
    def table_scope(owner: EditingSession, catalog: Catalog, plan: Plan) -> None:
        table = owner.context_json.get("selectedTableId")
        if table is None:
            return
        if plan.before["kind"] == "ocr":
            targets = {t.entity_id: t for t in catalog.targets.values() if t.kind == "field"}
            changed = {k for k, v in plan.after["fields"].items() if v != plan.before["fields"][k]}
            if any(
                targets.get(k) is None
                or (targets[k].detail.get("recordCell") or {}).get("tableId") != table
                for k in changed
            ):
                raise reject("TABLE_SCOPE_MISMATCH", "選択した表の外側は変更できません。", 409)
        else:
            cells, unbound = affected_cells(plan)
            allowed = section_cells(plan.before["layout"], table) | section_cells(
                plan.after["layout"], table
            )
            if unbound or not cells <= allowed:
                raise reject("TABLE_SCOPE_MISMATCH", "意味付けが選択表の外側にも影響します。", 409)

    def prepare(
        self,
        command_id: str,
        operations: tuple[BoundOperation, ...],
        resolution: ResolvedInstruction | None = None,
    ) -> PreparedChange:
        value = self.inputs[command_id]
        with self.database.session() as session:
            row, owner, catalog = self.repository.guarded(session, command_id)
            undo_of, physical = None, False
            substitutions: dict[str, str] = {}
            supersedes = None
            undo_id = resolution.undo_operation_id if resolution else None
            if resolution and resolution.retarget_operation_id:
                record = self.core.latest(session, owner)
                recent = (
                    simple_edit(record, catalog)
                    if record and self.core.undoable(session, owner, catalog, record)
                    else None
                )
                if (
                    not recent
                    or record is None
                    or record.id != resolution.retarget_operation_id
                    or (record.inverse_json or {}).get("contractVersion") != "editing.v2"
                    or recent["target"] == resolution.retarget_handle
                ):
                    raise reject(
                        "CORRECTION_SCOPE_AMBIGUOUS",
                        "直前の単一値・名称だけを別の一欄へ移せます。",
                        409,
                    )
                # Same-session receipt and scope, not a different tab's latest edit.
                receipt = self.repository.sessions.result(owner, catalog, session).get(
                    "lastReceipt"
                )
                if not receipt or receipt["operationId"] != record.id or not receipt["canUndo"]:
                    raise reject(
                        "CORRECTION_SCOPE_AMBIGUOUS", "同じ対象範囲の直前の変更が必要です。", 409
                    )
                operation = {
                    "op": recent["op"],
                    "target"
                    if recent["op"] == "ocr.set_value"
                    else "cell": resolution.retarget_handle,
                    "value" if recent["op"] == "ocr.set_value" else "name": recent["value"],
                }
                restored = copy.copy(catalog)
                restored.state = copy.deepcopy(record.before_json)
                decoded = TypeAdapter(list[Operation]).validate_python([operation])
                plan = self.core.compiler.compile_operations(restored, decoded)
                plan.before = copy.deepcopy(catalog.state)
                plan.touched.append(catalog.targets[recent["target"]])
                supersedes = record.id
            elif row.command_kind == "undo" or undo_id:
                record = self.core.latest(session, owner)
                if (
                    not self.core.undoable(session, owner, catalog, record)
                    or record is None
                    or record.id != (undo_id or value["command"]["operationId"])
                    or (record.inverse_json or {}).get("contractVersion") != "editing.v2"
                ):
                    raise reject(
                        "UNDO_UNAVAILABLE", "現在の保存世代で直前のv2変更だけを取り消せます。", 409
                    )
                assert record.inverse_json is not None
                plan = Plan(
                    copy.deepcopy(catalog.state), copy.deepcopy(record.inverse_json["state"])
                )
                undo_of = record.id
            elif row.command_kind == "manual_authoring":
                plan, substitutions, physical = ManualCompiler().compile(
                    catalog, ManualAuthoring.model_validate(value)
                )
            elif row.command_kind == "manual_ocr":
                plan = compile_manual_ocr(catalog, ManualOcr.model_validate(value).draft)
            else:
                decoded = TypeAdapter(list[Operation]).validate_python(
                    [op.value() for op in operations]
                )
                plan = self.core.compiler.compile_operations(catalog, decoded)
            self.core.local_scope(owner, plan)
            self.core.page_scope(owner, catalog, plan)
            self.table_scope(owner, catalog, plan)
            count = (
                sum(v != plan.before["fields"][k] for k, v in plan.after["fields"].items())
                if plan.before["kind"] == "ocr"
                else len(affected_cells(plan)[0])
            )
            if count > 1000:
                raise reject("EDIT_BATCH_TOO_LARGE", "一指示の変更対象は1000欄までです。", 413)
            self.core.prepare_template(owner, catalog, plan)
            token = str(uuid4())
            self.prepared[token] = Work(
                command_id, plan, physical, undo_of, supersedes, substitutions
            )
            return PreparedChange(
                token, ChangeSet(canonical(plan.before), canonical(plan.after), operations)
            )

    @staticmethod
    def focus(owner: EditingSession, saved: Catalog, plan: Plan) -> dict[str, Any]:
        if plan.before["kind"] == "ocr":
            changed = {
                key
                for key, value in plan.after["fields"].items()
                if value != plan.before["fields"][key]
            }
            targets = [
                t for t in saved.targets.values() if t.kind == "field" and t.entity_id in changed
            ]
        else:
            changed, _ = affected_cells(plan)
            targets = [
                t for t in saved.targets.values() if t.kind == "cell" and t.entity_id in changed
            ]
        anchors = []
        for t in sorted(targets, key=lambda t: t.entity_id):
            record = t.detail.get("recordCell") or {}
            if t.kind == "cell":
                tables = [
                    s["id"]
                    for s in plan.after["layout"]["recordSections"]
                    if t.entity_id in section_cells(plan.after["layout"], s["id"])
                ]
                record = {"tableId": tables[0] if len(tables) == 1 else None}
            anchors.append(
                {
                    "anchorId": "a_" + digest([owner.resource_id, t.kind, t.entity_id]),
                    "kind": "review_field" if t.kind == "field" else "template_cell",
                    "resourceId": owner.resource_id,
                    "pageId": t.page_id,
                    "tableId": record.get("tableId"),
                    "fieldId": t.entity_id if t.kind == "field" else None,
                    "cellId": t.entity_id if t.kind == "cell" else None,
                    "rowId": record.get("rowId"),
                    "columnId": record.get("columnId"),
                    "label": t.label[:300] or "欄",
                }
            )
        return {
            "viewRevision": saved.revision,
            "contextRevision": owner.context_revision,
            "interactionEpoch": owner.context_json["interactionEpoch"],
            "anchors": anchors,
            "primaryAnchorId": anchors[0]["anchorId"] if anchors else None,
        }

    def execute(self, command_id: str, fence: int, change: PreparedChange) -> dict[str, Any]:
        work = self.prepared[change.token]
        if (
            work.command_id != command_id
            or canonical(work.plan.before) != change.change_set.before_json
            or canonical(work.plan.after) != change.change_set.after_json
        ):
            raise reject("COMMAND_FENCED", "準備した変更群の所有者が一致しません。", 409)
        plan = work.plan
        with self.database.transaction() as session:
            row, owner, catalog = self.repository.guarded(session, command_id, fence)
            if row.held:
                raise reject("COMMAND_HELD", "続きの発話を確認しています。", 409)
            if catalog.state != plan.before:
                raise reject(
                    "REVISION_CONFLICT",
                    "準備後に保存内容が変わりました。",
                    409,
                    currentRevision=catalog.revision,
                )
            if work.undo_of is not None or work.supersedes is not None:
                latest = self.core.latest(session, owner)
                if (
                    latest is None
                    or latest.id != (work.undo_of or work.supersedes)
                    or not self.core.undoable(session, owner, catalog, latest)
                ):
                    raise reject(
                        "UNDO_UNAVAILABLE", "取り消す変更の後に別の編集が入りました。", 409
                    )
            self.core.local_scope(owner, plan)
            self.core.page_scope(owner, catalog, plan)
            self.table_scope(owner, catalog, plan)
            transition(row.state, "COMMITTING")
            row.state = "COMMITTING"
            request_key = "ev2_" + digest([row.session_id, row.request_id])
            receipt_row = MutationRequest(
                key=request_key,
                owner_id=owner.id,
                resource_kind=owner.resource_kind,
                resource_id=owner.resource_id,
                request_hash=row.input_fingerprint,
                state="PENDING",
            )
            session.add(receipt_row)
            session.flush()
            saved, record = self.core.persist(
                session,
                owner,
                catalog,
                plan,
                request_key=request_key,
                contract_version="editing.v2",
                origin="manual"
                if row.command_kind in {"manual_changes", "manual_authoring", "manual_ocr"}
                else "local_voice"
                if owner.mode == "local"
                else "continuous_voice",
                undo_of=work.undo_of,
                physical_edit=work.physical,
                supersedes=work.supersedes,
            )
            from app.editing.authoring_provenance import record_save

            record_save(
                session, owner, catalog, plan, self.inputs[command_id],
                work.substitutions, saved.revision,
                [op.value() for op in change.change_set.operations],
            )
            session.flush()
            focus = self.focus(owner, saved, plan)
            tables = {a["tableId"] for a in focus["anchors"] if a["tableId"] is not None}
            owner.context_json = {
                **owner.context_json,
                "resourceRevision": saved.revision,
                "currentFocus": focus if record else owner.context_json.get("currentFocus"),
                "recentTableId": next(iter(tables))
                if len(tables) == 1
                else None
                if len(tables) > 1
                else owner.context_json.get("recentTableId"),
            }
            payload: dict[str, Any]
            if record is None:
                payload = {
                    "type": "no_action",
                    "reason": "unchanged",
                    "message": "保存内容に変更はありません。",
                }
                state = "NO_ACTION"
            else:
                names = [c["name"] for c in self.core.changes(plan, saved)]
                notification = "、".join(names)[:960] + (
                    "を取り消しました。" if work.undo_of else "を変更しました。"
                )
                receipt = {
                    "receiptId": request_key,
                    "operationId": record.id,
                    "beforeRevision": catalog.revision,
                    "afterRevision": saved.revision,
                    "notification": notification,
                    "canUndo": work.undo_of is None and not work.physical,
                    "viewRevision": saved.revision,
                    "focus": focus,
                    "substitutions": work.substitutions,
                }
                payload, state = {"type": "applied", "receipt": receipt}, "APPLIED"
            event = self.repository.complete(session, owner, row, state, payload)
            row.receipt_key, row.change_digest = request_key, change.change_set.fingerprint
            receipt_row.state, receipt_row.result_json = "SUCCEEDED", copy.deepcopy(payload)
            receipt_row.completed_at = utc_now()
            session.flush()
            return event

    def discard(self, change: PreparedChange) -> None:
        self.prepared.pop(change.token, None)
