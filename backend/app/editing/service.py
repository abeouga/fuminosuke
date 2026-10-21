from __future__ import annotations

import copy
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, FieldError
from app.db.models import utc_now
from app.db.mutation_models import ResourceOperation
from app.db.session import Database
from app.editing.catalog import Catalog, CatalogService
from app.editing.commands import CommandCompiler, Plan
from app.editing.contracts import (
    CONTRACT_VERSION,
    Batch,
    CellRename,
    ContextUpdate,
    OcrSetValue,
    SessionCreate,
    TargetSearch,
    Undo,
)
from app.editing.corrections import simple_edit
from app.editing.errors import problem, reject
from app.editing.infrastructure.mutation_core import MutationCore
from app.editing.manual_contracts import ManualEdit, ManualOcrEdit
from app.editing.manual_ocr_compiler import compile_manual_ocr
from app.editing.models import EditingOperation, EditingSession, EditingTurn
from app.editing.record_cells import cell_label
from app.services.mutation_receipt_service import MutationReceiptService
from app.services.ocr_result_service import OcrResultService
from app.services.template_authoring_service import TemplateAuthoringService
from app.storage.local_storage import LocalStorageService

STALE_VIEW = "編集状態が古くなりました。画面を再読み込みしてください。"


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class EditingService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self.database = database
        self.authoring = TemplateAuthoringService(database, storage)
        self.ocr = OcrResultService(database, storage)
        self.catalogs = CatalogService(self.authoring, self.ocr)
        self.compiler = CommandCompiler()
        self.receipts = MutationReceiptService(database)
        self.mutations = MutationCore(self.authoring, self.ocr, self.catalogs, self.compiler)

    @staticmethod
    def _owner(
        session: Session, session_id: str, token: str, *, recover: bool = False
    ) -> EditingSession:
        owner = session.get(EditingSession, session_id)
        if owner is None or not hmac.compare_digest(
            owner.token_hash, hashlib.sha256(token.encode()).hexdigest()
        ):
            raise reject("EDITING_SESSION_INVALID", "編集sessionの所有者が一致しません。", 403)
        if owner.context_json.get("contractVersion") == "editing.v2":
            raise reject("EDITING_CONTRACT_MISMATCH", STALE_VIEW, 409)
        if not recover and (owner.closed_at is not None or aware(owner.expires_at) <= utc_now()):
            raise reject(
                "EDITING_SESSION_EXPIRED",
                "編集sessionが終了しました。新しい画面状態を取得してください。",
            )
        return owner

    @staticmethod
    def _cancel_turns(session: Session, owner: EditingSession) -> None:
        for turn in session.scalars(
            select(EditingTurn).where(
                EditingTurn.session_id == owner.id, EditingTurn.state.in_(["RESOLVING", "READY"])
            )
        ):
            turn.state = "CANCELLED"

    def _advance(self, session: Session, owner: EditingSession, revision: int) -> None:
        owner.context_revision += 1
        owner.context_json = {**owner.context_json, "resourceRevision": revision}
        self._cancel_turns(session, owner)

    def create_session(self, body: SessionCreate) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        with self.database.transaction() as session:
            owner = EditingSession(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                resource_kind=body.resource.kind,
                resource_id=body.resource.id,
                view_instance_id=body.view_instance_id,
                mode=body.mode,
                context_revision=1,
                expires_at=utc_now() + timedelta(minutes=30),
                context_json={
                    "resourceRevision": 0,
                    "pageScope": body.page_scope.model_dump(by_alias=True),
                    "localEditEpoch": body.local_edit_epoch,
                    "selectionEpoch": body.selection_epoch,
                    "selected": [],
                },
            )
            session.add(owner)
            session.flush()
            catalog = self.catalogs.build(session, owner)
            owner.context_json = {**owner.context_json, "resourceRevision": catalog.revision}
            return {"sessionId": owner.id, "token": token, **self._context(session, owner, catalog)}

    def _context(self, session: Session, owner: EditingSession, catalog: Catalog) -> dict[str, Any]:
        selected = {(item["kind"], item["entityId"]) for item in owner.context_json["selected"]}
        projection = self.catalogs.find(owner, catalog, TargetSearch())
        latest = self._latest(session, owner)
        recent = (
            simple_edit(latest, catalog)
            if latest is not None and self._undoable(session, owner, catalog, latest)
            else None
        )
        return {
            "contractVersion": CONTRACT_VERSION,
            "resource": {"kind": owner.resource_kind, "id": owner.resource_id},
            "viewInstanceId": owner.view_instance_id,
            "mode": owner.mode,
            "contextRevision": owner.context_revision,
            "resourceRevision": catalog.revision,
            "catalogRevision": catalog.generation,
            "pageScope": owner.context_json["pageScope"],
            "localEditEpoch": owner.context_json["localEditEpoch"],
            "selectionEpoch": owner.context_json["selectionEpoch"],
            "selectedHandles": [
                t.handle for t in catalog.targets.values() if (t.kind, t.entity_id) in selected
            ],
            "editable": catalog.editable,
            "capabilities": list(catalog.capabilities),
            "retargetPreviousAvailable": recent is not None,
            "recentEdit": recent,
            "recentTable": self._recent_table(owner, catalog),
            "draftState": catalog.draft_state,
            "catalog": projection,
            "history": self._history(session, owner, catalog, 1),
        }

    def context(self, session_id: str, token: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self._owner(session, session_id, token)
            catalog = self.catalogs.build(session, owner)
            if catalog.revision != owner.context_json["resourceRevision"]:
                owner.context_json = {**owner.context_json, "recentTable": None}
                self._advance(session, owner, catalog.revision)
                catalog = self.catalogs.build(session, owner)
            owner.expires_at = utc_now() + timedelta(minutes=30)
            return self._context(session, owner, catalog)

    @staticmethod
    def _recent_table(owner: EditingSession, catalog: Catalog) -> dict[str, Any] | None:
        recent = owner.context_json.get("recentTable")
        if owner.mode != "continuous" or not recent:
            return None
        cell = next(
            (
                t.detail["recordCell"]
                for t in catalog.targets.values()
                if t.detail.get("recordCell")
                and t.detail["recordCell"]["tableId"] == recent["tableId"]
            ),
            None,
        )
        return {"tableId": cell["tableId"], "tableName": cell["tableName"]} if cell else None

    def manual_view(self, session_id: str, token: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self._owner(session, session_id, token)
            if owner.mode != "manual" or owner.resource_kind != "template":
                raise reject("CAPABILITY_UNAVAILABLE", "手動テンプレートsession専用です。")
            catalog = self.catalogs.build(session, owner)
            if catalog.revision != owner.context_json["resourceRevision"]:
                self._advance(session, owner, catalog.revision)
                catalog = self.catalogs.build(session, owner)
            owner.expires_at = utc_now() + timedelta(minutes=30)
            return {
                "context": self._context(session, owner, catalog),
                "draft": {
                    k: copy.deepcopy(catalog.state[k])
                    for k in ("layout", "documentIdentityFields", "documentName")
                },
            }

    def manual_apply(self, session_id: str, token: str, body: ManualEdit) -> dict[str, Any]:
        return self._run(session_id, token, body)

    def manual_receipt(self, session_id: str, token: str, request_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            owner = self._owner(session, session_id, token, recover=True)
            if owner.mode != "manual" or owner.resource_kind != "template":
                raise reject("CAPABILITY_UNAVAILABLE", "手動テンプレートsession専用です。")
        return self.receipt(session_id, token, request_id)

    def update_context(self, session_id: str, token: str, body: ContextUpdate) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self._owner(session, session_id, token)
            catalog = self.catalogs.build(session, owner)
            if body.expected_context_revision != owner.context_revision:
                raise reject("CONTEXT_STALE", "選択状態の世代が変わりました。")
            if body.resource_revision != catalog.revision:
                raise reject(
                    "REVISION_CONFLICT",
                    "別の編集が保存されています。",
                    currentRevision=catalog.revision,
                )
            if (
                body.local_edit_epoch < owner.context_json["localEditEpoch"]
                or body.selection_epoch < owner.context_json["selectionEpoch"]
            ):
                raise reject("CONTEXT_STALE", "古い画面状態に戻すことはできません。")
            selected = []
            for handle in body.selected_handles:
                target = catalog.resolve(handle, ("cell", "field"))
                selected.append({"kind": target.kind, "entityId": target.entity_id})
            if len(body.selected_handles) != len(set(body.selected_handles)):
                raise reject("INVALID_ARGUMENT", "選択対象を重複指定できません。", 422)
            if (
                selected != owner.context_json["selected"]
                and body.selection_epoch == owner.context_json["selectionEpoch"]
            ):
                raise reject("CONTEXT_STALE", "選択変更には新しい選択世代が必要です。")
            owner.context_json = {
                "resourceRevision": catalog.revision,
                "pageScope": body.page_scope.model_dump(by_alias=True),
                "localEditEpoch": body.local_edit_epoch,
                "selectionEpoch": body.selection_epoch,
                "selected": selected,
            }
            self._advance(session, owner, catalog.revision)
            catalog = self.catalogs.build(session, owner)
            visible = {(t.kind, t.entity_id) for t in catalog.targets.values()}
            if any((s["kind"], s["entityId"]) not in visible for s in selected):
                raise reject("PAGE_SCOPE_INVALID", "選択セルが新しいページ範囲にありません。")
            owner.expires_at = utc_now() + timedelta(minutes=30)
            return self._context(session, owner, catalog)

    def find(self, session_id: str, token: str, body: TargetSearch) -> dict[str, Any]:
        with self.database.session() as session:
            owner = self._owner(session, session_id, token)
            catalog = self.catalogs.build(session, owner)
            if owner.context_json["resourceRevision"] != catalog.revision:
                raise reject("CONTEXT_STALE", "最新の編集contextを取得してください。")
            return self.catalogs.find(owner, catalog, body)

    def _check_request(
        self,
        session: Session,
        owner: EditingSession,
        catalog: Catalog,
        body: Batch | Undo | ManualEdit | ManualOcrEdit,
    ) -> EditingTurn | None:
        if (
            body.expected_revision != catalog.revision
            or owner.context_json["resourceRevision"] != catalog.revision
        ):
            raise reject(
                "REVISION_CONFLICT",
                "別の編集が保存されています。",
                currentRevision=catalog.revision,
            )
        if (
            body.context_revision != owner.context_revision
            or body.local_edit_epoch != owner.context_json["localEditEpoch"]
        ):
            raise reject("CONTEXT_STALE", "選択・手動編集の状態が変わりました。")
        if not catalog.editable:
            raise reject("RESOURCE_NOT_EDITABLE", "DRAFTまたは未確定の編集対象を使用してください。")
        if body.origin == "manual":
            if owner.mode != "manual":
                raise reject("LOCAL_SCOPE_MISMATCH", "手動操作には手動sessionを使用してください。")
            return None
        mode = "local" if body.origin == "local_voice" else "continuous"
        if owner.mode != mode:
            raise reject("LOCAL_SCOPE_MISMATCH", "音声取得方法と編集sessionが一致しません。")
        if mode == "continuous":
            from app.assistant.stream_service import assert_no_capture

            assert_no_capture(session, owner)
        turn = session.get(EditingTurn, body.turn_id)
        if (
            turn is None
            or turn.session_id != owner.id
            or turn.context_revision != owner.context_revision
        ):
            raise reject("CONTEXT_STALE", "音声turnが現在の編集状態に属していません。")
        if turn.state == "CONSUMED":
            raise reject("TURN_ALREADY_CONSUMED", "この音声turnは保存済みです。")
        if turn.state != "READY" or aware(turn.expires_at) <= utc_now():
            raise reject("TURN_CANCELLED", "この音声turnは失効または中止されています。")
        binding = {k: v for k, v in turn.binding_json.items() if k != "controller"}
        if binding != owner.context_json:
            raise reject("CONTEXT_STALE", "録音開始後に編集状態が変わりました。")
        execution = turn.binding_json.get("controller")
        if execution is not None and execution.get("commandDigest") != self.receipts.fingerprint(
            body.model_dump(mode="json", by_alias=True)
        ):
            raise reject("TURN_COMMAND_MISMATCH", "この発話で解決した操作と要求が一致しません。")
        return turn

    @staticmethod
    def _local_scope(owner: EditingSession, plan: Plan) -> None:
        return MutationCore.local_scope(owner, plan)

    def _prepare_template(self, owner: EditingSession, catalog: Catalog, plan: Plan) -> None:
        return self.mutations.prepare_template(owner, catalog, plan)

    @staticmethod
    def _page_scope(owner: EditingSession, catalog: Catalog, plan: Plan) -> None:
        return MutationCore.page_scope(owner, catalog, plan)

    def _latest(self, session: Session, owner: EditingSession) -> ResourceOperation | None:
        return self.mutations.latest(session, owner)

    def _undoable(
        self,
        session: Session,
        owner: EditingSession,
        catalog: Catalog,
        record: ResourceOperation | None,
    ) -> bool:
        return self.mutations.undoable(session, owner, catalog, record)

    def _history(
        self, session: Session, owner: EditingSession, catalog: Catalog, limit: int
    ) -> list[dict[str, Any]]:
        records = session.scalars(
            select(ResourceOperation)
            .where(
                ResourceOperation.resource_kind == owner.resource_kind,
                ResourceOperation.resource_id == owner.resource_id,
            )
            .order_by(ResourceOperation.after_revision.desc())
            .limit(limit)
        )
        return [
            {
                "operationId": r.id,
                "operation": r.operation,
                "beforeRevision": r.before_revision,
                "afterRevision": r.after_revision,
                "contractVersion": m.contract_version
                if (m := session.get(EditingOperation, r.id))
                else "legacy",
                "origin": m.origin if m else "legacy",
                "undoOfId": r.undo_of_id,
                "canUndo": self._undoable(session, owner, catalog, r),
                "supersedesOperationId": (r.inverse_json or {}).get("supersedesOperationId"),
            }
            for r in records
        ]

    def history(self, session_id: str, token: str, limit: int = 20) -> dict[str, Any]:
        with self.database.session() as session:
            owner = self._owner(session, session_id, token)
            catalog = self.catalogs.build(session, owner)
            return {
                "resourceRevision": catalog.revision,
                "operations": self._history(session, owner, catalog, limit),
            }

    def apply(self, session_id: str, token: str, body: Batch) -> dict[str, Any]:
        return self._run(session_id, token, body)

    def undo(self, session_id: str, token: str, body: Undo) -> dict[str, Any]:
        return self._run(session_id, token, body)

    def manual_ocr_apply(self, session_id: str, token: str, body: ManualOcrEdit) -> dict[str, Any]:
        return self._run(session_id, token, body)

    def _run(
        self, session_id: str, token: str, body: Batch | Undo | ManualEdit | ManualOcrEdit
    ) -> dict[str, Any]:
        with self.database.session() as session:
            owner = self._owner(session, session_id, token, recover=True)
            resource_kind, resource_id = owner.resource_kind, owner.resource_id
        planned: Plan | None = None
        substitutions: dict[str, str] = {}
        physical_edit = False
        supersedes: str | None = None

        def prepare() -> None:
            nonlocal planned, substitutions, physical_edit, supersedes
            with self.database.session() as session:
                current_owner = self._owner(session, session_id, token)
                catalog = self.catalogs.build(session, current_owner)
                self._check_request(session, current_owner, catalog, body)
                if isinstance(body, ManualOcrEdit):
                    if current_owner.mode != "manual" or current_owner.resource_kind != "ocr":
                        raise reject("CAPABILITY_UNAVAILABLE", "手動OCR session専用です。")
                    planned = compile_manual_ocr(catalog, body.draft)
                elif isinstance(body, ManualEdit):
                    if current_owner.mode != "manual" or current_owner.resource_kind != "template":
                        raise reject("CAPABILITY_UNAVAILABLE", "手動テンプレートsession専用です。")
                    from app.editing.manual_compiler import ManualCompiler

                    try:
                        planned, substitutions, physical_edit = ManualCompiler().compile(
                            catalog, body
                        )
                    except ValidationError as error:
                        raise reject(
                            "INVALID_GRAPH", "手動操作の形式・構造が不正です。", 422
                        ) from error
                elif isinstance(body, Undo):
                    record = self._latest(session, current_owner)
                    if (
                        record is None
                        or record.id != body.operation_id
                        or not self._undoable(session, current_owner, catalog, record)
                    ):
                        raise reject("UNDO_UNAVAILABLE", "現在の直前操作を取り消せません。")
                    inverse = record.inverse_json or {}
                    if inverse.get("contractVersion") != CONTRACT_VERSION:
                        raise reject(
                            "UNDO_UNAVAILABLE", "旧形式の履歴は新しい取り消しで実行できません。"
                        )
                    planned = Plan(copy.deepcopy(catalog.state), copy.deepcopy(inverse["state"]))
                else:
                    try:
                        if body.correction is None:
                            planned = self.compiler.compile(catalog, body)
                        else:
                            planned, supersedes = self._retarget(
                                session, current_owner, catalog, body
                            )
                    except ValidationError as error:
                        raise reject(
                            "INVALID_GRAPH", "意味付けの形式・依存関係が不正です。", 422
                        ) from error
                if body.origin == "local_voice":
                    self._local_scope(current_owner, planned)
                self._page_scope(current_owner, catalog, planned)
                self._prepare_template(current_owner, catalog, planned)

        def persist(session: Session) -> dict[str, Any]:
            current_owner = self._owner(session, session_id, token)
            catalog = self.catalogs.build(session, current_owner)
            turn = self._check_request(session, current_owner, catalog, body)
            if planned is None or planned.before != catalog.state:
                raise reject(
                    "REVISION_CONFLICT",
                    "準備後に保存内容が変わりました。",
                    currentRevision=catalog.revision,
                )
            if isinstance(body, Undo):
                latest = self._latest(session, current_owner)
                if (
                    latest is None
                    or latest.id != body.operation_id
                    or not self._undoable(session, current_owner, catalog, latest)
                ):
                    raise reject("UNDO_UNAVAILABLE", "準備後に直前操作が変わりました。")
            if supersedes is not None:
                latest = self._latest(session, current_owner)
                if (
                    latest is None
                    or latest.id != supersedes
                    or not self._undoable(session, current_owner, catalog, latest)
                ):
                    raise reject("CORRECTION_SCOPE_AMBIGUOUS", "訂正前に別の編集が入りました。")
            record = None
            if planned.changed:
                saved, record = self.mutations.persist(
                    session,
                    current_owner,
                    catalog,
                    planned,
                    request_key=body.request_id,
                    contract_version=CONTRACT_VERSION,
                    origin=body.origin,
                    undo_of=body.operation_id if isinstance(body, Undo) else None,
                    physical_edit=physical_edit,
                    supersedes=supersedes,
                    turn_id=body.turn_id,
                )
                if turn is not None:
                    turn.state, turn.consumed_request_id = "CONSUMED", body.request_id
                session.flush()
                table = None
                if (
                    resource_kind == "ocr"
                    and body.origin == "continuous_voice"
                    and not isinstance(body, Undo)
                ):
                    changed_fields = [
                        t
                        for t in saved.targets.values()
                        if t.kind == "field"
                        and planned.before["fields"][t.entity_id]
                        != planned.after["fields"][t.entity_id]
                    ]
                    cells = [t.detail.get("recordCell") for t in changed_fields]
                    if cells and all(cells) and len({c["tableId"] for c in cells if c}) == 1:
                        cell = cells[0]
                        assert cell is not None
                        table = {"tableId": cell["tableId"], "tableName": cell["tableName"]}
                current_owner.context_json = {**current_owner.context_json, "recentTable": table}
                self._advance(session, current_owner, saved.revision)
            elif turn is not None:
                turn.state, turn.consumed_request_id = "CONSUMED", body.request_id
            current_owner.expires_at = utc_now() + timedelta(minutes=30)
            saved = self.catalogs.build(session, current_owner)
            changed_targets = self._changes(planned, saved)
            notification = "既にその内容です。"
            if planned.changed:
                notification = (
                    "直前の変更を取り消しました。"
                    if isinstance(body, Undo)
                    else (
                        f"{changed_targets[0]['name']}を変更しました。"
                        if len(changed_targets) == 1
                        else f"{len(changed_targets)}項目を変更しました。"
                    )
                )
                if not isinstance(body, Undo) and len(changed_targets) == 1:
                    target = saved.targets[changed_targets[0]["handle"]]
                    if target.detail.get("recordCell"):
                        value = target.detail.get("currentValue")
                        notification = (
                            f"{cell_label(target.public())}を"
                            f"{value if value is not None else '空欄'}に変更しました。"
                        )
            result = {
                "requestId": body.request_id,
                "operationId": record.id if record else None,
                "beforeRevision": catalog.revision,
                "afterRevision": saved.revision,
                "outcome": "CHANGED" if planned.changed else "NO_CHANGE",
                "changedTargets": changed_targets,
                "createdTargets": {
                    local: t.handle
                    for local, (kind, entity) in planned.created.items()
                    for t in saved.targets.values()
                    if t.kind == kind and t.entity_id == entity
                },
                "draftState": saved.draft_state,
                "contextRevision": current_owner.context_revision,
                "canUndo": self._undoable(
                    session, current_owner, saved, record or self._latest(session, current_owner)
                ),
                "notification": notification,
            }
            if isinstance(body, ManualEdit):
                result["substitutions"] = substitutions
            return result

        return self.receipts.run(
            key=body.request_id,
            owner_id=session_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            payload=body.model_dump(mode="json", by_alias=True),
            apply=persist,
            prepare=prepare,
        )

    def _retarget(
        self, session: Session, owner: EditingSession, catalog: Catalog, body: Batch
    ) -> tuple[Plan, str]:
        record = self._latest(session, owner)
        recent = (
            simple_edit(record, catalog)
            if record is not None and self._undoable(session, owner, catalog, record)
            else None
        )
        operation = body.operations[0] if len(body.operations) == 1 else None
        if record is None or recent is None or operation is None or operation.op != recent["op"]:
            raise reject("CORRECTION_SCOPE_AMBIGUOUS", "移す対象が一つの値・名称に定まりません。")
        if isinstance(operation, OcrSetValue):
            destination, value = operation.target, operation.value
        elif isinstance(operation, CellRename):
            destination, value = operation.cell, operation.name
        else:
            raise reject("CORRECTION_SCOPE_AMBIGUOUS", "この操作は対象の言い直しに対応しません。")
        if destination == recent["target"] or value != recent["value"]:
            raise reject(
                "CORRECTION_SCOPE_AMBIGUOUS", "直前と同じ値を別の一欄へ移す指示が必要です。"
            )
        restored = copy.copy(catalog)
        restored.state = copy.deepcopy(record.before_json)
        plan = self.compiler.compile(restored, body.model_copy(update={"correction": None}))
        plan.before = copy.deepcopy(catalog.state)
        plan.touched.append(catalog.targets[recent["target"]])
        return plan, record.id

    @staticmethod
    def _changes(plan: Plan, saved: Catalog) -> list[dict[str, Any]]:
        return MutationCore.changes(plan, saved)

    def receipt(self, session_id: str, token: str, request_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            self._owner(session, session_id, token, recover=True)
        row = self.receipts.get(request_id, session_id)
        if row is None:
            return {"requestId": request_id, "state": "NOT_FOUND", "result": None, "error": None}
        error = row["error"]
        if error is not None:
            error = dict(error)
            error["field_errors"] = [
                FieldError.model_validate(v) for v in error.get("field_errors", [])
            ]
            error = problem(AppError(**error))
        return {
            "requestId": request_id,
            "state": row["state"],
            "result": row["result"],
            "error": error,
        }

    def close(self, session_id: str, token: str) -> dict[str, str]:
        with self.database.transaction() as session:
            owner = self._owner(session, session_id, token, recover=True)
            owner.closed_at = owner.closed_at or utc_now()
            owner.context_json = {**owner.context_json, "recentTable": None}
            self._cancel_turns(session, owner)
        return {"state": "CLOSED"}

    def begin_turn(
        self, session_id: str, token: str, *, expected: dict[str, Any] | None = None
    ) -> str:
        """Trusted audio/controller entry; not exposed as a public ready-turn API."""
        with self.database.transaction() as session:
            owner = self._owner(session, session_id, token)
            if owner.mode == "manual":
                raise reject("CAPABILITY_UNAVAILABLE", "手動sessionに音声turnを作成できません。")
            catalog = self.catalogs.build(session, owner)
            if catalog.revision != owner.context_json["resourceRevision"]:
                raise reject(
                    "CONTEXT_STALE", "別の編集後の発話です。画面を確認して再指示してください。"
                )
            if expected is not None and any(
                expected[k]
                != (owner.context_revision if k == "contextRevision" else owner.context_json[k])
                for k in ("contextRevision", "resourceRevision", "localEditEpoch", "selectionEpoch")
            ):
                raise reject("CONTEXT_STALE", "発話開始時の画面状態が変わりました。")
            if owner.mode == "local" and not owner.context_json["selected"]:
                raise reject("LOCAL_SCOPE_MISMATCH", "局所録音の対象欄を選択してください。")
            if owner.mode == "continuous" and expected and expected.get("audioItemId"):
                from app.assistant.stream_service import validate_audio_binding

                validate_audio_binding(session, owner, expected["audioItemId"])
            sequence = (
                session.scalar(
                    select(func.max(EditingTurn.sequence)).where(EditingTurn.session_id == owner.id)
                )
                or 0
            ) + 1
            turn = EditingTurn(
                session_id=owner.id,
                sequence=sequence,
                context_revision=owner.context_revision,
                binding_json={
                    **copy.deepcopy(owner.context_json),
                    **(
                        {"controller": {"audioItemId": expected.get("audioItemId")}}
                        if expected
                        else {}
                    ),
                },
                expires_at=utc_now() + timedelta(seconds=60),
            )
            session.add(turn)
            session.flush()
            return turn.id

    def turn_context(self, session_id: str, token: str, turn_id: str) -> dict[str, Any]:
        """Read a captured context without advancing to another writer's revision."""
        with self.database.session() as session:
            owner = self._owner(session, session_id, token)
            turn = session.get(EditingTurn, turn_id)
            catalog = self.catalogs.build(session, owner)
            if turn is None or turn.session_id != owner.id:
                raise reject("TURN_CANCELLED", "発話がこのsessionに属していません。")
            if turn.state != "RESOLVING" or aware(turn.expires_at) <= utc_now():
                raise reject("TURN_CANCELLED", "この発話は中止・解決済みまたは期限切れです。")
            if (
                turn.context_revision != owner.context_revision
                or {k: v for k, v in turn.binding_json.items() if k != "controller"}
                != owner.context_json
                or catalog.revision != owner.context_json["resourceRevision"]
            ):
                raise reject(
                    "CONTEXT_STALE", "発話中に編集状態が変わりました。再指示してください。"
                )
            return self._context(session, owner, catalog)

    def authorize_turn(self, session_id: str, token: str, body: Batch | Undo) -> None:
        """Freeze exactly the controller-resolved command; no public ready endpoint."""
        with self.database.transaction() as session:
            owner = self._owner(session, session_id, token)
            turn = session.get(EditingTurn, body.turn_id)
            if turn is None or turn.session_id != owner.id or turn.state != "RESOLVING":
                raise reject("TURN_CANCELLED", "この発話は実行可能になりません。")
            turn.binding_json = {
                **turn.binding_json,
                "controller": {
                    **turn.binding_json.get("controller", {}),
                    "requestId": body.request_id,
                    "commandDigest": self.receipts.fingerprint(
                        body.model_dump(mode="json", by_alias=True)
                    ),
                },
            }
            turn.state = "READY"
            self._check_request(session, owner, self.catalogs.build(session, owner), body)

    def turn_status(
        self, session_id: str, token: str, turn_id: str, *, cancel: bool = False
    ) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self._owner(session, session_id, token, recover=True)
            turn = session.get(EditingTurn, turn_id)
            if turn is None or turn.session_id != owner.id:
                raise reject("TURN_CANCELLED", "発話がこのsessionに属していません。")
            if cancel and turn.state != "CONSUMED":
                turn.state = "CANCELLED"
            request_id = turn.consumed_request_id or turn.binding_json.get("controller", {}).get(
                "requestId"
            )
            state = turn.state
        return {
            "turnId": turn_id,
            "state": state,
            "requestId": request_id,
            "receipt": self.receipt(session_id, token, request_id) if request_id else None,
        }

    def ready_turn(self, session_id: str, token: str, turn_id: str) -> None:
        with self.database.transaction() as session:
            owner = self._owner(session, session_id, token)
            turn = session.get(EditingTurn, turn_id)
            if (
                turn is None
                or turn.session_id != owner.id
                or turn.state != "RESOLVING"
                or turn.context_revision != owner.context_revision
                or aware(turn.expires_at) <= utc_now()
            ):
                raise reject("TURN_CANCELLED", "この音声turnは実行可能になりません。")
            turn.state = "READY"
