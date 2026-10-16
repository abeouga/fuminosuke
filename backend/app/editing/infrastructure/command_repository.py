"""Durable command admission and fences. User instructions remain in RAM."""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v2.editing_contracts import Event
from app.db.models import utc_now
from app.editing.catalog import Catalog
from app.editing.domain.command import ACTIVE, digest, transition
from app.editing.errors import reject
from app.editing.infrastructure.command_models import CommandRequest
from app.editing.infrastructure.sessions import Sessions, aware
from app.editing.models import EditingSession


class SqlCommandRepository:
    def __init__(self, sessions: Sessions) -> None:
        self.sessions = sessions
        self.database = sessions.database
        self.worker_id = str(uuid4())
        self.instruction_guard: Callable[[], None] | None = None
        with self.database.transaction() as session:
            for row in session.scalars(
                select(CommandRequest).where(
                    CommandRequest.state.in_([*ACTIVE, "WAITING_INFORMATION"])
                )
            ):
                owner = session.get(EditingSession, row.session_id)
                if owner is not None:
                    row.fence += 1
                    self.complete(
                        session,
                        owner,
                        row,
                        "INTERRUPTED",
                        {
                            "type": "failed",
                            "error": {
                                "code": "COMMAND_INTERRUPTED",
                                "message": "再起動で未保存の指示を中止しました。",
                                "retryable": True,
                                "currentRevision": None,
                            },
                        },
                    )

    def complete(
        self,
        session: Session,
        owner: EditingSession,
        row: CommandRequest,
        state: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        transition(row.state, state)
        event = Event.model_validate(self.sessions.event(session, owner, row, payload)).model_dump(
            mode="json", by_alias=True
        )
        row.state, row.final_event_json = state, event
        return event

    @staticmethod
    def result(row: CommandRequest) -> dict[str, Any]:
        return {
            "contractVersion": "editing.v2",
            "commandId": row.id,
            "instructionId": row.instruction_id,
            "requestId": row.request_id,
            "state": row.state,
            "acceptedEvent": copy.deepcopy(row.accepted_event_json),
            "finalEvent": copy.deepcopy(row.final_event_json),
        }

    def binding(
        self,
        session: Session,
        owner: EditingSession,
        catalog: Catalog,
        value: dict[str, Any],
        *,
        admission: bool = False,
    ) -> None:
        bound = value["binding"]
        current = self.sessions.snapshot(owner, catalog)
        if not catalog.editable:
            raise reject("RESOURCE_NOT_EDITABLE", "確定済み・公開済みの対象は編集できません。", 409)
        if any(
            bound[key] != current[key]
            for key in ("contextRevision", "interactionEpoch", "scopeDigest")
        ):
            raise reject("CONTEXT_STALE", "対象範囲又は手動編集の状態が変わりました。", 409)
        after = value.get("afterCommandId")
        if after is not None:
            prior = session.get(CommandRequest, after)
            if prior is None or prior.session_id != owner.id or prior.id == value.get("commandId"):
                raise reject(
                    "COMMAND_DEPENDENCY_INVALID", "同じsessionの直前の変更を指定してください。", 409
                )
            # A predecessor may explain only its own revision advance. The original
            # signed catalog and scope must be the same, never an arbitrary rebase.
            if prior.binding_json != bound:
                raise reject(
                    "COMMAND_DEPENDENCY_INVALID", "直前の指示と対象世代が一致しません。", 409
                )
            if admission and prior.state in ACTIVE:
                return
            event = prior.final_event_json or {}
            payload = event.get("payload", {})
            if prior.state != "APPLIED" or payload.get("type") != "applied":
                raise reject("COMMAND_DEPENDENCY_FAILED", "直前の変更が保存されていません。", 409)
            if catalog.revision != payload["receipt"]["afterRevision"]:
                raise reject(
                    "REVISION_CONFLICT",
                    "直前の変更後に別の編集が入りました。",
                    409,
                    currentRevision=catalog.revision,
                )
        elif (
            bound["expectedRevision"] != catalog.revision
            or bound["catalogRevision"] != catalog.generation
        ):
            raise reject(
                "REVISION_CONFLICT",
                "対象の保存世代が変わりました。",
                409,
                currentRevision=catalog.revision,
            )

    def admit(
        self,
        session_id: str,
        token: str,
        value: dict[str, Any],
        *,
        continuation_of: str | None = None,
        scope_rebinding: bool = False,
        fingerprint: str | None = None,
        provider_free: bool = False,
    ) -> dict[str, Any]:
        fingerprint = fingerprint or digest(value)
        # Expiry must progress even when the client posts a new instruction
        # without polling context or command status first.
        self.expire_questions(session_id, token)
        with self.database.transaction() as session:
            owner = self.sessions.owner(session, session_id, token, recover=True)
            prior = session.scalar(
                select(CommandRequest).where(
                    CommandRequest.session_id == session_id,
                    CommandRequest.request_id == value["requestId"],
                )
            )
            if prior is not None:
                if prior.input_fingerprint != fingerprint:
                    raise reject(
                        "IDEMPOTENCY_CONFLICT", "同一requestIdに異なる内容は指定できません。", 409
                    )
                return {"new": False, **self.result(prior)}
            self.sessions.owner(session, session_id, token)
            kind = value.get("command", {}).get("kind", value.get("kind", "manual_authoring"))
            reply = value.get("command", {}).get("replyTo")
            waiting = list(
                session.scalars(
                    select(CommandRequest).where(
                        CommandRequest.session_id == session_id,
                        CommandRequest.state == "WAITING_INFORMATION",
                    )
                )
            )
            answered = None
            if kind == "instruction" and reply:
                answered = next(
                    (
                        r
                        for r in waiting
                        if (
                            (r.final_event_json or {})
                            .get("payload", {})
                            .get("question", {})
                            .get("questionId")
                            == reply["questionId"]
                            and (r.final_event_json or {})["payload"]["question"]["version"]
                            == reply["version"]
                        )
                    ),
                    None,
                )
                if answered is None:
                    raise reject("QUESTION_STALE", "この質問は終了・失効しています。", 409)
                question = answered.final_event_json["payload"]["question"]  # type: ignore[index]
                if (
                    datetime.fromisoformat(question["expiresAt"]) <= utc_now()
                    or aware(answered.expires_at) <= utc_now()
                ):
                    raise reject(
                        "QUESTION_EXPIRED",
                        "質問の期限が切れました。元の指示から再開してください。",
                        409,
                    )
            elif kind != "cancel" and waiting:
                raise reject(
                    "INSTRUCTION_PENDING",
                    "保留中の質問に回答するか、未保存の指示を中止してください。",
                    409,
                )
            catalog = self.sessions.catalogs.build(session, owner)
            continued = session.get(CommandRequest, continuation_of) if continuation_of else None
            if continuation_of and (
                continued is None
                or continued.session_id != session_id
                or continued.state != "CANCELLED"
                or (not scope_rebinding and continued.binding_json != value["binding"])
                or (
                    scope_rebinding
                    and (
                        continued.resource_kind != owner.resource_kind
                        or continued.resource_id != owner.resource_id
                        or continued.binding_json["interactionEpoch"]
                        != value["binding"]["interactionEpoch"]
                        or continued.binding_json["expectedRevision"]
                        != value["binding"]["expectedRevision"]
                        or continued.binding_json["contextRevision"] + 1
                        != value["binding"]["contextRevision"]
                    )
                )
                or aware(continued.expires_at) <= utc_now()
            ):
                raise reject("CONTINUATION_UNAVAILABLE", "元の指示の世代が失効しています。", 409)
            if kind != "cancel":
                self.binding(session, owner, catalog, value, admission=True)
                if value.get("afterCommandId") is not None and kind != "instruction":
                    raise reject(
                        "COMMAND_DEPENDENCY_INVALID",
                        "世代を引き継ぐ指示は音声・文章の指示だけです。",
                        422,
                    )
                if (
                    kind in {"manual_changes", "manual_authoring", "manual_ocr"}
                    and owner.mode != "manual"
                ):
                    raise reject("MODE_MISMATCH", "手動変更はmanual modeで行ってください。", 409)
            pending = list(
                session.scalars(
                    select(CommandRequest).where(
                        CommandRequest.session_id == session_id, CommandRequest.state.in_(ACTIVE)
                    )
                )
            )
            if kind != "cancel" and len(pending) >= 3:
                raise reject(
                    "COMMAND_QUEUE_FULL", "処理中一件と待機二件の上限に達しています。", 429
                )
            if kind == "instruction" and not provider_free and self.instruction_guard is not None:
                self.instruction_guard()
            row = CommandRequest(
                id=str(uuid4()),
                session_id=session_id,
                request_id=value["requestId"],
                instruction_id=answered.instruction_id
                if answered
                else continued.instruction_id
                if continued
                else str(uuid4()),
                input_fingerprint=fingerprint,
                command_kind=kind,
                resource_kind=owner.resource_kind,
                resource_id=owner.resource_id,
                binding_json=copy.deepcopy(value["binding"]),
                after_command_id=value.get("afterCommandId"),
                state="ACCEPTED",
                fence=1,
                worker_id=self.worker_id,
                accepted_event_json={},
                expires_at=answered.expires_at
                if answered
                else continued.expires_at
                if continued
                else utc_now() + timedelta(minutes=15),
            )
            session.add(row)
            if answered:
                answered.fence += 1
                self.complete(
                    session,
                    owner,
                    answered,
                    "CANCELLED",
                    {
                        "type": "cancelled",
                        "message": "質問への回答を同じ指示として受け付けました。",
                    },
                )
            row.accepted_event_json = Event.model_validate(
                self.sessions.event(
                    session, owner, row, {"type": "accepted", "queuePosition": min(len(pending), 2)}
                )
            ).model_dump(mode="json", by_alias=True)
            if kind == "cancel":
                target = session.get(CommandRequest, value["command"]["commandId"])
                if target is None or target.session_id != session_id:
                    raise reject(
                        "COMMAND_NOT_FOUND", "中止する指示がこのsessionにありません。", 404
                    )
                row.state = "VALIDATING"
                if target.state in ACTIVE or target.state == "WAITING_INFORMATION":
                    target.fence += 1
                    target.state = "CANCELLED"
                    target.final_event_json = Event.model_validate(
                        self.sessions.event(
                            session,
                            owner,
                            target,
                            {"type": "cancelled", "message": "未保存の指示を中止しました。"},
                        )
                    ).model_dump(mode="json", by_alias=True)
                self.complete(
                    session,
                    owner,
                    row,
                    "NO_ACTION",
                    {
                        "type": "no_action",
                        "reason": "not_an_edit",
                        "message": "中止要求を処理しました。保存済みの変更は取り消されません。",
                    },
                )
            session.flush()
            return {"new": kind != "cancel", **self.result(row)}

    def guarded(
        self, session: Session, command_id: str, fence: int | None = None
    ) -> tuple[CommandRequest, EditingSession, Catalog]:
        row = session.get(CommandRequest, command_id)
        if (
            row is None
            or row.worker_id != self.worker_id
            or row.state not in ACTIVE
            or (fence is not None and row.fence != fence)
        ):
            raise reject("COMMAND_FENCED", "この指示は中止又は終了しています。", 409)
        owner = session.get(EditingSession, row.session_id)
        if (
            owner is None
            or owner.closed_at
            or aware(owner.expires_at) <= utc_now()
            or aware(row.expires_at) <= utc_now()
        ):
            raise reject("EDITING_SESSION_EXPIRED", "編集session又は指示の期限が切れました。", 409)
        catalog = self.sessions.catalogs.build(session, owner)
        self.binding(
            session,
            owner,
            catalog,
            {
                "binding": row.binding_json,
                "afterCommandId": row.after_command_id,
                "commandId": row.id,
            },
        )
        return row, owner, catalog

    def claim(self, command_id: str) -> dict[str, Any] | None:
        with self.database.transaction() as session:
            row = session.get(CommandRequest, command_id)
            if row is None or row.state != "ACCEPTED":
                return None
            self.guarded(session, command_id)
            state = "PLANNING" if row.command_kind == "instruction" else "VALIDATING"
            transition(row.state, state)
            row.state = state
            return {
                "fence": row.fence,
                "kind": row.command_kind,
                "instructionId": row.instruction_id,
                "remainingSeconds": max(0, (aware(row.expires_at) - utc_now()).total_seconds()),
            }

    def stage(self, command_id: str, fence: int, state: str) -> None:
        with self.database.transaction() as session:
            row, _, _ = self.guarded(session, command_id, fence)
            transition(row.state, state)
            row.state = state

    def hold(self, session_id: str, token: str, binding: dict[str, Any], held: bool) -> list[str]:
        with self.database.transaction() as session:
            owner = self.sessions.owner(session, session_id, token)
            catalog = self.sessions.catalogs.build(session, owner)
            self.binding(session, owner, catalog, {"binding": binding, "afterCommandId": None})
            rows = list(
                session.scalars(
                    select(CommandRequest).where(
                        CommandRequest.session_id == session_id, CommandRequest.state.in_(ACTIVE)
                    )
                )
            )
            for row in rows:
                if row.binding_json != binding:
                    raise reject("CONTEXT_STALE", "取得中の指示と処理中の世代が一致しません。", 409)
                row.held = int(held)
            return [r.id for r in rows]

    def hold_status(self, command_id: str, fence: int) -> bool:
        with self.database.session() as session:
            row, _, _ = self.guarded(session, command_id, fence)
            return bool(row.held)

    def finish(self, command_id: str, fence: int, state: str, payload: dict[str, Any]) -> bool:
        with self.database.transaction() as session:
            row = session.get(CommandRequest, command_id)
            if (
                row is None
                or row.worker_id != self.worker_id
                or row.fence != fence
                or row.state not in ACTIVE
            ):
                return False
            owner = session.get(EditingSession, row.session_id)
            if owner is not None:
                if state == "WAITING_INFORMATION":
                    payload = copy.deepcopy(payload)
                    payload["question"]["expiresAt"] = min(
                        datetime.fromisoformat(payload["question"]["expiresAt"]),
                        aware(row.expires_at),
                    ).isoformat()
                self.complete(session, owner, row, state, payload)
                return True
            return False

    def scope_replay(
        self, session_id: str, token: str, request_id: str, fingerprint: str
    ) -> dict[str, Any] | None:
        with self.database.session() as session:
            self.sessions.owner(session, session_id, token, recover=True)
            row = session.scalar(
                select(CommandRequest).where(
                    CommandRequest.session_id == session_id, CommandRequest.request_id == request_id
                )
            )
            if row:
                if row.input_fingerprint != fingerprint:
                    raise reject(
                        "IDEMPOTENCY_CONFLICT",
                        "同じrequestIdに異なる対象範囲は指定できません。",
                        409,
                    )
                return self.result(row)
            return None

    def rebind_context(
        self, session_id: str, token: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        return self.sessions.update(session_id, token, context)

    def input_context(self, session_id: str, token: str) -> dict[str, Any]:
        return self.sessions.context(session_id, token)

    def expire_questions(self, session_id: str, token: str) -> None:
        with self.database.transaction() as session:
            owner = self.sessions.owner(session, session_id, token, recover=True)
            for row in session.scalars(
                select(CommandRequest).where(
                    CommandRequest.session_id == session_id,
                    CommandRequest.state == "WAITING_INFORMATION",
                )
            ):
                question = (row.final_event_json or {}).get("payload", {}).get("question", {})
                if (
                    aware(row.expires_at) <= utc_now()
                    or datetime.fromisoformat(question["expiresAt"]) <= utc_now()
                ):
                    row.fence += 1
                    self.complete(
                        session,
                        owner,
                        row,
                        "EXPIRED",
                        {"type": "expired", "message": "未保存の指示又は質問の期限が切れました。"},
                    )

    def context_for_command(self, command_id: str, fence: int) -> dict[str, Any]:
        with self.database.session() as session:
            _, owner, catalog = self.guarded(session, command_id, fence)
            result = self.sessions.result(owner, catalog, session)
            focus = result["scopeContext"].get("currentFocus") or {}
            # Private identity projection, not additional authority from a model or browser.
            result["recentTargetHandles"] = [
                t.handle
                for t in catalog.targets.values()
                if any(
                    (t.kind == "field" and t.entity_id == a.get("fieldId"))
                    or (t.kind == "cell" and t.entity_id == a.get("cellId"))
                    for a in focus.get("anchors", [])
                )
            ]
            return result

    def query(self, session_id: str, token: str, request_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            self.sessions.owner(session, session_id, token, recover=True)
            row = session.scalar(
                select(CommandRequest).where(
                    CommandRequest.session_id == session_id, CommandRequest.request_id == request_id
                )
            )
            if row is None:
                raise reject("COMMAND_NOT_FOUND", "このsessionの指示がありません。", 404)
            return self.result(row)

    def stop(self) -> None:
        with self.database.transaction() as session:
            for row in session.scalars(
                select(CommandRequest).where(
                    CommandRequest.worker_id == self.worker_id, CommandRequest.state.in_(ACTIVE)
                )
            ):
                owner = session.get(EditingSession, row.session_id)
                if owner is not None:
                    row.fence += 1
                    self.complete(
                        session,
                        owner,
                        row,
                        "INTERRUPTED",
                        {
                            "type": "failed",
                            "error": {
                                "code": "COMMAND_INTERRUPTED",
                                "message": "終了時に未保存の指示を中止しました。",
                                "retryable": True,
                                "currentRevision": None,
                            },
                        },
                    )

    def replay(self, session_id: str, token: str, cursor: str | None) -> list[dict[str, Any]]:
        with self.database.session() as session:
            owner = self.sessions.owner(session, session_id, token, recover=True)
            sequence = 0
            if cursor is not None:
                prefix, separator, suffix = cursor.rpartition("_")
                if not separator or prefix != owner.id or not suffix.isdecimal():
                    raise reject(
                        "EVENT_CURSOR_INVALID", "このsessionのeventIdを指定してください。", 422
                    )
                sequence = int(suffix)
                if sequence < 1 or sequence > owner.context_json.get("eventSequence", 0):
                    raise reject("EVENT_CURSOR_INVALID", "eventIdが記録範囲外です。", 422)
            # Events contain only identifiers, status and saved receipts. No raw
            # instruction is persisted or replayed after a restart.
            events = [
                e
                for row in session.scalars(
                    select(CommandRequest).where(CommandRequest.session_id == session_id)
                )
                for e in (row.accepted_event_json, row.final_event_json)
                if e and e["sequence"] > sequence
            ]
            return sorted(events, key=lambda e: e["sequence"])[:100]
