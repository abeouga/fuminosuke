from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.assistant.contracts import (
    Apply,
    AssistantContext,
    ContextUpdate,
    OcrContext,
    Presentation,
    ProposalCreate,
    TemplateContext,
    UndoProposalCreate,
)
from app.assistant.models import AssistantProposal, AssistantSession
from app.assistant.operations import OperationRegistry, PlannedChange, refused
from app.assistant.undo import compile_undo, resource
from app.core.errors import AppError
from app.db.models import utc_now
from app.db.mutation_models import MutationRequest, ResourceOperation
from app.db.session import Database
from app.services.mutation_receipt_service import MutationReceiptService
from app.services.ocr_result_service import OcrResultService
from app.services.template_authoring_service import TemplateAuthoringService
from app.storage.local_storage import LocalStorageService

CONTEXT: TypeAdapter[OcrContext | TemplateContext] = TypeAdapter(AssistantContext)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AssistantService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self.database = database
        self.receipts = MutationReceiptService(database)
        self.registry = OperationRegistry(
            TemplateAuthoringService(database, storage), OcrResultService(database, storage)
        )

    @staticmethod
    def _session(session: Session, token: str, *, allow_expired: bool = False) -> AssistantSession:
        row = session.scalar(
            select(AssistantSession).where(AssistantSession.token_hash == secret_hash(token))
        )
        if row is None:
            raise refused(
                "操作sessionが見つかりません。パネルを開き直してください。",
                "ASSISTANT_SESSION_INVALID",
                401,
            )
        if not allow_expired and aware(row.expires_at) <= utc_now():
            raise refused(
                "操作sessionの期限が切れました。パネルを開き直してください。",
                "ASSISTANT_SESSION_EXPIRED",
            )
        return row

    @staticmethod
    def _context(row: AssistantSession, revision: int) -> None:
        if row.context_revision != revision:
            raise refused(
                "選択対象または編集状態が変わりました。変更案を作り直してください。",
                "ASSISTANT_CONTEXT_CHANGED",
            )

    @staticmethod
    def _proposal(
        session: Session,
        owner: AssistantSession,
        proposal_id: str,
        context_revision: int | None = None,
    ) -> AssistantProposal:
        row = session.get(AssistantProposal, proposal_id)
        if row is None or row.session_id != owner.id:
            raise refused(
                "このsessionに属する変更案がありません。", "ASSISTANT_PROPOSAL_NOT_FOUND", 404
            )
        if context_revision is not None:
            AssistantService._context(owner, context_revision)
            if row.context_revision != context_revision or row.context_json != owner.context_json:
                raise refused("変更案の選択対象が失効しています。", "ASSISTANT_CONTEXT_CHANGED")
        return row

    @staticmethod
    def _active(row: AssistantProposal) -> None:
        if aware(row.expires_at) <= utc_now():
            raise refused(
                "変更案の期限が切れました。変更案を作り直してください。",
                "ASSISTANT_PROPOSAL_EXPIRED",
            )
        if row.state not in {"PROPOSED", "PRESENTED"}:
            raise refused(
                "この変更案は取り消し・失効・適用済みです。", "ASSISTANT_PROPOSAL_INACTIVE"
            )

    @staticmethod
    def _view(row: AssistantProposal) -> dict[str, Any]:
        plan = row.plan_json
        return {
            "id": row.id,
            "contractVersion": 1,
            "contextRevision": row.context_revision,
            "diffHash": row.diff_hash,
            "operation": plan["operation"],
            "undoOfId": plan.get("undoOfId"),
            "target": plan["target"],
            "before": plan["before"],
            "after": plan["after"],
            "effects": plan["effects"],
            "expiresAt": aware(row.expires_at).isoformat(),
            "state": "EXPIRED"
            if aware(row.expires_at) <= utc_now() and row.state in {"PROPOSED", "PRESENTED"}
            else row.state,
        }

    def create_session(self, context: AssistantContext) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        with self.database.transaction() as session:
            row = AssistantSession(
                token_hash=secret_hash(token),
                context_json=context.model_dump(mode="json", by_alias=True),
                expires_at=utc_now() + timedelta(minutes=30),
            )
            session.add(row)
            session.flush()
            return {"sessionId": row.id, "token": token, "contextRevision": row.context_revision}

    def update_context(self, token: str, body: ContextUpdate) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self._session(session, token)
            self._context(owner, body.expected_context_revision)
            incoming = body.context.model_dump(mode="json", by_alias=True)
            if incoming != owner.context_json:
                session.execute(
                    update(AssistantProposal)
                    .where(
                        AssistantProposal.session_id == owner.id,
                        AssistantProposal.state.in_(["PROPOSED", "PRESENTED"]),
                    )
                    .values(state="SUPERSEDED", challenge_hash=None)
                )
                owner.context_revision += 1
                owner.context_json = incoming
            owner.expires_at = utc_now() + timedelta(minutes=30)
            return {"contextRevision": owner.context_revision}

    def propose(self, token: str, body: ProposalCreate) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self._session(session, token)
            return self.propose_in_transaction(session, owner, body)

    def propose_in_transaction(
        self, session: Session, owner: AssistantSession, body: ProposalCreate
    ) -> dict[str, Any]:
        self._context(owner, body.context_revision)
        context = CONTEXT.validate_python(owner.context_json)
        planned = self.registry.compile(session, context, body.operation, body.value)
        session.execute(update(AssistantProposal).where(
            AssistantProposal.session_id == owner.id,
            AssistantProposal.state.in_(["PROPOSED", "PRESENTED"]),
        ).values(state="SUPERSEDED", challenge_hash=None))
        row = AssistantProposal(
            session_id=owner.id, context_revision=owner.context_revision,
            context_json=owner.context_json, plan_json=planned.data, diff_hash=planned.digest,
            expires_at=utc_now() + timedelta(seconds=120),
        )
        session.add(row)
        owner.expires_at = utc_now() + timedelta(minutes=30)
        session.flush()
        return self._view(row)

    def present(self, token: str, proposal_id: str, body: Presentation) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self._session(session, token)
            row = self._proposal(session, owner, proposal_id, body.context_revision)
            self._active(row)
            if row.diff_hash != body.diff_hash:
                raise refused(
                    "表示した変更内容が保存された案と一致しません。", "ASSISTANT_DIFF_MISMATCH"
                )
            # Also reject a resource changed since generation, before enabling approval.
            planned = self._compile(session, owner, row)
            if planned.digest != row.diff_hash:
                raise refused(
                    "正本の値が変わりました。変更案を作り直してください。", "REVISION_CONFLICT"
                )
            challenge = secrets.token_urlsafe(32)
            row.challenge_hash, row.presented_at, row.state = (
                secret_hash(challenge),
                utc_now(),
                "PRESENTED",
            )
            owner.expires_at = utc_now() + timedelta(minutes=30)
            return {"challenge": challenge, "expiresAt": aware(row.expires_at).isoformat()}

    def apply(self, token: str, proposal_id: str, key: str, body: Apply) -> dict[str, Any]:
        # Authentication and resource lookup establish receipt ownership only.
        # Expiry, proposal state and resource revision are checked inside its callback.
        with self.database.session() as session:
            owner = self._session(session, token, allow_expired=True)
            row = self._proposal(session, owner, proposal_id)
            owner_id, kind, resource_id = (
                owner.id,
                row.plan_json["resourceKind"],
                row.plan_json["resourceId"],
            )

        def apply_once(session: Session) -> dict[str, Any]:
            active = self._session(session, token)
            proposal = self._proposal(session, active, proposal_id, body.context_revision)
            self._active(proposal)
            if (
                proposal.state != "PRESENTED"
                or proposal.presented_at is None
                or proposal.challenge_hash is None
            ):
                raise refused("変更案を表示してから承認してください。", "ASSISTANT_NOT_PRESENTED")
            if proposal.diff_hash != body.diff_hash or not secrets.compare_digest(
                proposal.challenge_hash, secret_hash(body.challenge)
            ):
                raise refused("承認情報がこの変更案と一致しません。", "ASSISTANT_APPROVAL_INVALID")
            if body.approval_turn_id:
                from app.assistant.voice_service import consume_approval
                consume_approval(session, active, proposal, body)
            context = CONTEXT.validate_python(proposal.context_json)
            planned = self._compile(session, active, proposal)
            if planned.digest != proposal.diff_hash:
                raise refused(
                    "表示後に正本が変わりました。変更案を作り直してください。", "REVISION_CONFLICT"
                )
            plan = planned.data
            if plan.get("undoOfId"):
                context = CONTEXT.validate_python(plan["restoreContext"])
            after_revision = self.registry.persist(session, context, planned)
            operation = self.receipts.record_operation(
                session,
                request_key=key,
                resource_kind=kind,
                resource_id=resource_id,
                operation="assistant.undo" if plan.get("undoOfId") else plan["operation"],
                before_revision=plan["revision"],
                after_revision=after_revision,
                before={"target": plan["target"], "data": plan["beforeData"]},
                after={"target": plan["target"], "data": plan["afterData"]},
                inverse=None
                if plan.get("undoOfId")
                else {
                    "contractVersion": 1,
                    "context": proposal.context_json,
                    "operation": plan["operation"],
                    "restore": plan["beforeData"],
                },
                undo_of_id=plan.get("undoOfId"),
            )
            proposal.state, proposal.challenge_hash, proposal.consumed_request_key = (
                "CONSUMED",
                None,
                key,
            )
            active.expires_at = utc_now() + timedelta(minutes=30)
            session.flush()
            return {
                "requestKey": key,
                "proposalId": proposal_id,
                "operationId": operation.id,
                "undoOfId": plan.get("undoOfId"),
                "resourceKind": kind,
                "resourceId": resource_id,
                "revision": after_revision,
                "target": plan["target"],
                "after": plan["after"],
                "saved": True,
            }

        return self.receipts.run(
            key=key,
            owner_id=owner_id,
            resource_kind=kind,
            resource_id=resource_id,
            payload={
                "proposalId": proposal_id,
                **body.model_dump(mode="json", by_alias=True, exclude_none=True),
            },
            apply=apply_once,
        )

    def cancel(self, token: str, proposal_id: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self._session(session, token, allow_expired=True)
            row = self._proposal(session, owner, proposal_id)
            if row.state == "CONSUMED":
                return {
                    "state": "CONSUMED",
                    "receipt": self.receipts.get(str(row.consumed_request_key), owner.id),
                }
            if row.state in {"PROPOSED", "PRESENTED"}:
                row.state, row.challenge_hash = "CANCELLED", None
            return {"state": row.state}

    def request(self, token: str, key: str) -> dict[str, Any]:
        with self.database.session() as session:
            owner = self._session(session, token, allow_expired=True)
            result = self.receipts.get(key, owner.id)
        if result is None:
            raise refused(
                "このsessionの操作結果は見つかりません。", "ASSISTANT_REQUEST_NOT_FOUND", 404
            )
        return result

    @staticmethod
    def _operation(
        session: Session, owner: AssistantSession, operation_id: str
    ) -> ResourceOperation:
        record = session.get(ResourceOperation, operation_id)
        context = CONTEXT.validate_python(owner.context_json)
        if record is None or resource(context) != (record.resource_kind, record.resource_id):
            raise refused(
                "この書類・テンプレートの操作がありません。", "ASSISTANT_OPERATION_NOT_FOUND", 404
            )
        return record

    def _compile(
        self, session: Session, owner: AssistantSession, proposal: AssistantProposal
    ) -> PlannedChange:
        context = CONTEXT.validate_python(owner.context_json)
        if operation_id := proposal.plan_json.get("undoOfId"):
            return compile_undo(
                self.registry, session, context, self._operation(session, owner, operation_id)
            )
        return self.registry.compile(
            session, context, proposal.plan_json["operation"], proposal.plan_json["value"]
        )

    def propose_undo(
        self, token: str, operation_id: str, body: UndoProposalCreate
    ) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self._session(session, token)
            self._context(owner, body.context_revision)
            planned = compile_undo(
                self.registry,
                session,
                CONTEXT.validate_python(owner.context_json),
                self._operation(session, owner, operation_id),
            )
            session.execute(
                update(AssistantProposal)
                .where(
                    AssistantProposal.session_id == owner.id,
                    AssistantProposal.state.in_(["PROPOSED", "PRESENTED"]),
                )
                .values(state="SUPERSEDED", challenge_hash=None)
            )
            row = AssistantProposal(
                session_id=owner.id,
                context_revision=owner.context_revision,
                context_json=owner.context_json,
                plan_json=planned.data,
                diff_hash=planned.digest,
                expires_at=utc_now() + timedelta(seconds=120),
            )
            session.add(row)
            session.flush()
            return self._view(row)

    def history(self, token: str) -> dict[str, Any]:
        # This loopback application has no multi-user ACL. History belongs to the
        # resource, while proposal/challenge and request replay remain session-owned.
        with self.database.session() as session:
            owner = self._session(session, token, allow_expired=True)
            context = CONTEXT.validate_python(owner.context_json)
            kind, resource_id = resource(context)
            records = session.scalars(
                select(ResourceOperation)
                .where(
                    ResourceOperation.resource_kind == kind,
                    ResourceOperation.resource_id == resource_id,
                )
                .order_by(ResourceOperation.after_revision.desc())
                .limit(20)
            ).all()
            items = []
            for record in records:
                request = session.get(MutationRequest, record.request_key)
                reason = None
                try:
                    compile_undo(self.registry, session, context, record)
                except AppError as error:
                    reason = error.detail
                items.append(
                    {
                        "operationId": record.id,
                        "operation": record.operation,
                        "undoOfId": record.undo_of_id,
                        "revision": record.after_revision,
                        "createdAt": aware(record.created_at).isoformat(),
                        "target": record.after_json["target"],
                        "after": (request.result_json or {}).get("after") if request else None,
                        "canUndo": reason is None,
                        "reason": reason,
                    }
                )
            return {"items": items}
