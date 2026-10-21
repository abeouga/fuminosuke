from __future__ import annotations

import copy
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import utc_now
from app.db.session import Database
from app.editing.catalog import Catalog, CatalogService
from app.editing.domain.command import ACTIVE, digest
from app.editing.errors import reject
from app.editing.infrastructure.command_models import CommandRequest
from app.editing.models import EditingSession

STALE_VIEW = "編集状態が古くなりました。画面を再読み込みしてください。"


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class Sessions:
    def __init__(self, database: Database, catalogs: CatalogService) -> None:
        self.database, self.catalogs = database, catalogs

    @staticmethod
    def owner(
        session: Session, session_id: str, token: str, *, recover: bool = False
    ) -> EditingSession:
        owner = session.get(EditingSession, session_id)
        if owner is None or not hmac.compare_digest(
            owner.token_hash, hashlib.sha256(token.encode()).hexdigest()
        ):
            raise reject("EDITING_SESSION_INVALID", "編集sessionの所有者が一致しません。", 403)
        if owner.context_json.get("contractVersion") != "editing.v2":
            raise reject("EDITING_CONTRACT_MISMATCH", STALE_VIEW, 409)
        if not recover and (owner.closed_at or aware(owner.expires_at) <= utc_now()):
            raise reject("EDITING_SESSION_EXPIRED", "編集sessionが終了しました。", 409)
        return owner

    @staticmethod
    def scope_digest(owner: EditingSession) -> str:
        state = owner.context_json
        return digest(
            {
                "resource": [owner.resource_kind, owner.resource_id],
                "mode": owner.mode,
                "page": state["pageScope"],
                "table": state.get("selectedTableId"),
                "selected": state["selected"]
                if owner.mode == "local" or owner.resource_kind == "template"
                else [],
                "localTarget": state.get("localTarget"),
            }
        )

    @staticmethod
    def tables(catalog: Catalog) -> set[str]:
        return {
            t.detail["recordCell"]["tableId"]
            for t in catalog.targets.values()
            if t.detail.get("recordCell")
        } | {t.entity_id for t in catalog.targets.values() if t.kind == "record_section"}

    def snapshot(self, owner: EditingSession, catalog: Catalog) -> dict[str, Any]:
        selected = {(v["kind"], v["entityId"]) for v in owner.context_json["selected"]}
        local = owner.context_json.get("localTarget")
        focus = owner.context_json.get("currentFocus")
        if focus and focus["viewRevision"] != catalog.revision:
            focus = None
        return {
            "contractVersion": "editing.v2",
            "kind": "scope_snapshot",
            "resourceRevision": catalog.revision,
            "contextRevision": owner.context_revision,
            "interactionEpoch": owner.context_json["interactionEpoch"],
            "resource": {"kind": owner.resource_kind, "id": owner.resource_id},
            "mode": owner.mode,
            "pageScope": owner.context_json["pageScope"],
            "selectedTableId": owner.context_json.get("selectedTableId"),
            "selectedHandles": [
                t.handle for t in catalog.targets.values() if (t.kind, t.entity_id) in selected
            ],
            "localTargetHandle": next(
                (
                    t.handle
                    for t in catalog.targets.values()
                    if local == {"kind": t.kind, "entityId": t.entity_id}
                ),
                None,
            ),
            "recentTableId": owner.context_json.get("recentTableId"),
            "catalogRevision": catalog.generation,
            "scopeDigest": self.scope_digest(owner),
            "currentFocus": copy.deepcopy(focus),
        }

    def result(self, owner: EditingSession, catalog: Catalog, session: Session) -> dict[str, Any]:
        row = session.scalar(
            select(CommandRequest)
            .where(CommandRequest.session_id == owner.id, CommandRequest.state == "APPLIED")
            .order_by(CommandRequest.created_at.desc())
            .limit(1)
        )
        receipt = copy.deepcopy((row.final_event_json or {})["payload"]["receipt"]) if row else None
        if receipt is not None and receipt["afterRevision"] != catalog.revision:
            receipt["canUndo"] = False
        return {
            "contractVersion": "editing.v2",
            "scopeContext": self.snapshot(owner, catalog),
            "editable": catalog.editable,
            "capabilities": list(catalog.capabilities),
            "catalog": {
                "complete": True,
                "targets": [t.public() for t in catalog.targets.values()],
            },
            "draftState": catalog.draft_state,
            "lastReceipt": receipt,
            "uiBindings": [
                {"handle": t.handle, "kind": t.kind, "entityId": t.entity_id, "pageId": t.page_id}
                for t in catalog.targets.values()
                if t.kind in {"field", "cell", "heading", "record_section"}
            ],
            "limits": {
                "operations": 50,
                "queuedCommands": 2,
                "instructionCharacters": 20000,
                "utteranceSeconds": 180,
            },
        }

    def create(self, value: dict[str, Any]) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        with self.database.transaction() as session:
            owner = EditingSession(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                resource_kind=value["resource"]["kind"],
                resource_id=value["resource"]["id"],
                view_instance_id=value["viewInstanceId"],
                mode=value["mode"],
                context_revision=1,
                expires_at=utc_now() + timedelta(minutes=30),
                context_json={
                    "contractVersion": "editing.v2",
                    "resourceRevision": 0,
                    "pageScope": value["pageScope"],
                    "interactionEpoch": 0,
                    "localEditEpoch": 0,
                    "selectionEpoch": 0,
                    "selected": [],
                    "selectedTableId": None,
                    "localTarget": None,
                    "recentTableId": None,
                    "eventSequence": 0,
                    "currentFocus": None,
                },
            )
            session.add(owner)
            session.flush()
            catalog = self.catalogs.build(session, owner)
            owner.context_json = {**owner.context_json, "resourceRevision": catalog.revision}
            return {"sessionId": owner.id, "token": token, **self.result(owner, catalog, session)}

    def context(self, session_id: str, token: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self.owner(session, session_id, token)
            catalog = self.catalogs.build(session, owner)
            if catalog.revision != owner.context_json["resourceRevision"]:
                owner.context_revision += 1
                owner.context_json = {
                    **owner.context_json,
                    "resourceRevision": catalog.revision,
                    "recentTableId": None,
                    "currentFocus": None,
                }
                self.invalidate(session, owner)
                catalog = self.catalogs.build(session, owner)
            owner.expires_at = utc_now() + timedelta(minutes=30)
            return self.result(owner, catalog, session)

    def update(self, session_id: str, token: str, value: dict[str, Any]) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self.owner(session, session_id, token)
            catalog = self.catalogs.build(session, owner)
            if value["resource"] != {"kind": owner.resource_kind, "id": owner.resource_id}:
                raise reject("RESOURCE_MISMATCH", "sessionの編集対象は変更できません。", 409)
            if value["expectedContextRevision"] != owner.context_revision:
                raise reject("CONTEXT_STALE", "画面状態の世代が変わりました。", 409)
            if catalog.revision != owner.context_json["resourceRevision"]:
                raise reject(
                    "CONTEXT_STALE", "別の編集が入りました。先に画面状態を取得してください。", 409
                )
            if (
                value["expectedRevision"] != catalog.revision
                or value["interactionEpoch"] < owner.context_json["interactionEpoch"]
            ):
                raise reject(
                    "REVISION_CONFLICT",
                    "古い画面状態に戻すことはできません。",
                    409,
                    currentRevision=catalog.revision,
                )
            if len(set(value["selectedHandles"])) != len(value["selectedHandles"]):
                raise reject("INVALID_ARGUMENT", "選択対象が重複しています。", 422)
            selected = [
                catalog.resolve(handle, tuple({t.kind for t in catalog.targets.values()}))
                for handle in value["selectedHandles"]
            ]
            local = catalog.targets.get(value["localTargetHandle"])
            if value["mode"] == "local" and (
                local is None
                or len(selected) != 1
                or local not in selected
                or local.kind not in {"cell", "field"}
            ):
                raise reject(
                    "LOCAL_SCOPE_MISMATCH", "局所録音には選択欄を一つ指定してください。", 422
                )
            if value["mode"] != "local" and value["localTargetHandle"] is not None:
                raise reject("LOCAL_SCOPE_MISMATCH", "局所対象はlocal modeだけで指定します。", 422)
            table = value["selectedTableId"]
            if table is not None and table not in self.tables(catalog):
                raise reject("TABLE_SCOPE_INVALID", "選択表が現在の対象範囲にありません。", 422)
            old_scope, old_epoch = self.scope_digest(owner), owner.context_json["interactionEpoch"]
            old_page = owner.context_json["pageScope"]
            old_table = owner.context_json.get("selectedTableId")
            owner.mode = value["mode"]
            owner.context_json = {
                **owner.context_json,
                "pageScope": value["pageScope"],
                "selected": [{"kind": t.kind, "entityId": t.entity_id} for t in selected],
                "localTarget": {"kind": local.kind, "entityId": local.entity_id} if local else None,
                "selectedTableId": table,
                "interactionEpoch": value["interactionEpoch"],
                "localEditEpoch": value["interactionEpoch"],
            }
            # Validate the proposed page against the saved layout before accepting it.
            catalog = self.catalogs.build(session, owner)
            if local is not None and not any(
                t.kind == local.kind and t.entity_id == local.entity_id
                for t in catalog.targets.values()
            ):
                raise reject(
                    "LOCAL_SCOPE_MISMATCH", "局所対象が新しいページ範囲にありません。", 422
                )
            if table is not None and table not in self.tables(catalog):
                raise reject("TABLE_SCOPE_INVALID", "選択表が新しいページ範囲にありません。", 422)
            if old_scope != self.scope_digest(owner) or old_epoch != value["interactionEpoch"]:
                recent = None
                if (
                    old_page == value["pageScope"]
                    and old_epoch == value["interactionEpoch"]
                    and old_table is not None
                    and table is None
                ):
                    row = session.scalar(
                        select(CommandRequest)
                        .where(
                            CommandRequest.session_id == owner.id, CommandRequest.state == "APPLIED"
                        )
                        .order_by(CommandRequest.created_at.desc())
                        .limit(1)
                    )
                    receipt = (row.final_event_json or {})["payload"]["receipt"] if row else None
                    if receipt and receipt["afterRevision"] == catalog.revision:
                        ids = {a["tableId"] for a in receipt["focus"]["anchors"] if a["tableId"]}
                        if ids == {old_table}:
                            recent = old_table
                owner.context_revision += 1
                owner.context_json = {
                    **owner.context_json,
                    "currentFocus": None,
                    "recentTableId": recent,
                }
                self.invalidate(session, owner)
                catalog = self.catalogs.build(session, owner)
            owner.context_json = {**owner.context_json, "resourceRevision": catalog.revision}
            return self.result(owner, catalog, session)

    @staticmethod
    def event(
        session: Session, owner: EditingSession, row: CommandRequest, payload: dict[str, Any]
    ) -> dict[str, Any]:
        sequence = owner.context_json.get("eventSequence", 0) + 1
        owner.context_json = {**owner.context_json, "eventSequence": sequence}
        return {
            "contractVersion": "editing.v2",
            "eventId": f"{owner.id}_{sequence}",
            "sessionId": owner.id,
            "commandId": row.id,
            "instructionId": row.instruction_id,
            "requestId": row.request_id,
            "sequence": sequence,
            "emittedAt": utc_now().isoformat(),
            "payload": payload,
        }

    def invalidate(self, session: Session, owner: EditingSession) -> None:
        for row in session.scalars(
            select(CommandRequest).where(
                CommandRequest.session_id == owner.id,
                CommandRequest.state.in_([*ACTIVE, "WAITING_INFORMATION"]),
            )
        ):
            row.fence += 1
            row.state = "CANCELLED"
            row.final_event_json = self.event(
                session,
                owner,
                row,
                {
                    "type": "cancelled",
                    "message": "対象範囲又は手動編集が変わったため中止しました。",
                },
            )

    def close(self, session_id: str, token: str) -> dict[str, str]:
        with self.database.transaction() as session:
            owner = self.owner(session, session_id, token, recover=True)
            owner.closed_at = utc_now()
            self.invalidate(session, owner)
            owner.context_json = {**owner.context_json, "currentFocus": None, "recentTableId": None}
        return {"contractVersion": "editing.v2", "sessionId": session_id, "state": "CLOSED"}

    def owner_probe(self, session_id: str, token: str) -> None:
        with self.database.session() as session:
            self.owner(session, session_id, token)
