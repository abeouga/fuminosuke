from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.api_budget import ApiBudget
from app.assistant.audio import normalize_audio, normalize_speech
from app.assistant.contracts import (
    Apply,
    Operation,
    ProposalCreate,
    SpeechRequest,
    TextTurn,
    TurnStart,
)
from app.assistant.models import AssistantProposal, AssistantSession
from app.assistant.operations import refused
from app.assistant.service import CONTEXT, AssistantService, aware, secret_hash
from app.assistant.voice_models import AssistantTurn
from app.assistant.voice_provider import VoiceProvider
from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import utc_now
from app.services.mutation_receipt_service import MutationReceiptService


def binding(proposal: AssistantProposal, body: Apply) -> dict[str, Any]:
    return {
        "proposalId": proposal.id,
        "diffHash": body.diff_hash,
        "challengeHash": secret_hash(body.challenge),
    }


def consume_approval(
    session: Session, owner: AssistantSession, proposal: AssistantProposal, body: Apply
) -> None:
    row = session.get(AssistantTurn, body.approval_turn_id)
    if (
        row is None
        or row.session_id != owner.id
        or row.purpose != "approval"
        or row.state != "APPROVED"
        or row.context_revision != owner.context_revision
        or row.binding_json != binding(proposal, body)
        or aware(row.expires_at) <= utc_now()
    ):
        raise refused(
            "音声承認が失効しています。案を確認してボタンで適用してください。",
            "VOICE_APPROVAL_INVALID",
        )
    row.state = "CONSUMED"


class VoiceService:
    def __init__(
        self, assistant: AssistantService, settings: Settings, provider: VoiceProvider | None = None
    ) -> None:
        self.assistant, self.database, self.settings = assistant, assistant.database, settings
        self.budget = ApiBudget(self.database)
        self.provider = provider or VoiceProvider(settings, self.budget)

    def status(self) -> dict[str, Any]:
        configured = self.settings.voice.provider == "openai" and bool(
            self.settings.ocr.openai_api_key
        )
        return {
            "configured": configured,
            "recordingAvailable": configured and bool(self.settings.voice.ffmpeg_path),
            "maxAudioBytes": self.settings.voice.max_audio_bytes,
            "maxRecordSeconds": self.settings.voice.max_record_seconds,
            "budget": self.budget.status(),
        }

    def _selected(self, session: Session, owner: AssistantSession) -> dict[str, Any]:
        context = CONTEXT.validate_python(owner.context_json)
        possible: list[Operation] = (
            ["ocr.set_value"]
            if context.resource_kind == "ocr"
            else ["template.rename_cell", "template.change_category", "template.rename_choice"]
        )
        allowed: list[str] = []
        selected: dict[str, Any] = {}
        for operation in possible:
            for value in (
                ["input", "label", "parent_label", "ignored"]
                if operation == "template.change_category"
                else ["仮の名称"]
            ):
                try:
                    plan = self.assistant.registry.compile(session, context, operation, value).data
                    selected.setdefault("label", plan["target"]["label"])
                    selected.setdefault("currentValue", plan["before"])
                    allowed.append(operation)
                    break
                except AppError:
                    continue
        if not allowed:
            raise refused("この選択対象には音声で変更できる操作がありません。")
        selected["allowedOperations"] = allowed
        return selected

    def _presented(
        self, session: Session, token: str, proposal_id: str | None, body: Apply | None
    ) -> tuple[AssistantSession, AssistantProposal]:
        if not proposal_id or body is None or body.approval_turn_id:
            raise refused(
                "提示済みの変更案を指定してください。", "VOICE_PRESENTATION_REQUIRED", 422
            )
        owner = self.assistant._session(session, token)
        proposal = self.assistant._proposal(session, owner, proposal_id, body.context_revision)
        self.assistant._active(proposal)
        if (
            proposal.state != "PRESENTED"
            or proposal.diff_hash != body.diff_hash
            or proposal.challenge_hash != secret_hash(body.challenge)
        ):
            raise refused("表示済みの案と承認情報が一致しません。", "ASSISTANT_APPROVAL_INVALID")
        if self.assistant._compile(session, owner, proposal).digest != proposal.diff_hash:
            raise refused("正本が変わりました。変更案を作り直してください。", "REVISION_CONFLICT")
        return owner, proposal

    def _turn(
        self, session: Session, token: str, turn_id: str, *, active: bool = True
    ) -> tuple[AssistantSession, AssistantTurn]:
        owner = self.assistant._session(session, token, allow_expired=not active)
        row = session.get(AssistantTurn, turn_id)
        if row is None or row.session_id != owner.id:
            raise refused("このsessionの録音が見つかりません。", "VOICE_TURN_NOT_FOUND", 404)
        if active:
            self.assistant._context(owner, row.context_revision)
            if aware(row.expires_at) <= utc_now():
                raise refused("録音要求の期限が切れました。", "VOICE_TURN_EXPIRED")
        return owner, row

    def start(self, token: str, key: str, body: TurnStart) -> dict[str, Any]:
        digest = MutationReceiptService.fingerprint(body.model_dump(mode="json", by_alias=True))
        with self.database.transaction() as session:
            owner = self.assistant._session(session, token)
            existing = session.scalar(select(AssistantTurn).where(AssistantTurn.request_key == key))
            if existing:
                if existing.session_id != owner.id or existing.fingerprint != digest:
                    raise refused(
                        "同じ要求IDが別の音声要求に使用されています。", "VOICE_REQUEST_CONFLICT"
                    )
                return {"turnId": existing.id, "state": existing.state}
            self.assistant._context(owner, body.context_revision)
            target: dict[str, Any] = {}
            if body.purpose == "command":
                if body.presentation or body.proposal_id or body.speech_id:
                    raise refused("変更指示に承認情報を含めることはできません。", status=422)
                self._selected(session, owner)
            else:
                _, proposal = self._presented(session, token, body.proposal_id, body.presentation)
                speech = session.get(AssistantTurn, body.speech_id) if body.speech_id else None
                target = binding(proposal, body.presentation)  # type: ignore[arg-type]
                if (
                    speech is None
                    or speech.session_id != owner.id
                    or speech.purpose != "speech"
                    or speech.state != "PLAYED"
                    or speech.binding_json != target
                    or speech.context_revision != owner.context_revision
                    or aware(speech.expires_at) <= utc_now()
                ):
                    raise refused(
                        "変更案の読み上げが完了してから、別の録音で承認してください。",
                        "VOICE_PLAYBACK_REQUIRED",
                    )
            row = AssistantTurn(
                request_key=key,
                session_id=owner.id,
                context_revision=owner.context_revision,
                purpose=body.purpose,
                binding_json=target,
                fingerprint=digest,
                expires_at=utc_now() + timedelta(seconds=75),
            )
            session.add(row)
            session.flush()
            return {"turnId": row.id, "state": row.state}

    def _claim(self, token: str, turn_id: str, digest: str) -> dict[str, Any] | None:
        with self.database.transaction() as session:
            _, row = self._turn(session, token, turn_id)
            if row.purpose not in {"command", "approval"}:
                raise refused("録音用途が一致しません。", status=422)
            if row.input_hash is not None and row.input_hash != digest:
                raise refused(
                    "同じ録音要求に別の内容が送信されています。", "VOICE_REQUEST_CONFLICT"
                )
            if row.state in {"READY", "APPROVED"}:
                return self._outcome(session, row)
            if row.state != "RECORDING":
                raise refused(
                    "この音声要求は処理中・取消・失敗済みです。自動再送はしません。",
                    "VOICE_TURN_SETTLED",
                )
            row.input_hash, row.state = digest, "PROCESSING"
            row.expires_at = utc_now() + timedelta(seconds=120)
        return None

    def _outcome(self, session: Session, row: AssistantTurn) -> dict[str, Any]:
        result = dict(row.result_json or {})
        if result.get("proposalId"):
            proposal = session.get(AssistantProposal, result["proposalId"])
            result["proposal"] = self.assistant._view(proposal) if proposal else None
        return {"turnId": row.id, **result}

    async def audio(self, token: str, turn_id: str, data: bytes, mime: str) -> dict[str, Any]:
        replay = self._claim(token, turn_id, hashlib.sha256(data).hexdigest())
        if replay is not None:
            return replay
        try:
            wav = await asyncio.to_thread(normalize_audio, data, mime, self.settings.voice)
            with self.database.session() as session:
                _, active = self._turn(session, token, turn_id)
                if active.state != "PROCESSING":
                    raise refused("録音要求が取り消されています。", "VOICE_TURN_CANCELLED")
            text = await self.provider.transcribe(wav)
            return await self._interpret(token, turn_id, text)
        except BaseException:
            self._fail(token, turn_id)
            raise

    async def text(self, token: str, key: str, body: TextTurn) -> dict[str, Any]:
        if body.purpose != "command":
            raise refused("承認は音声録音または適用ボタンを使用してください。", status=422)
        turn = self.start(token, key, body)
        replay = self._claim(token, turn["turnId"], hashlib.sha256(body.text.encode()).hexdigest())
        if replay is not None:
            return replay
        try:
            return await self._interpret(token, turn["turnId"], body.text)
        except BaseException:
            self._fail(token, turn["turnId"])
            raise

    async def _interpret(self, token: str, turn_id: str, text: str) -> dict[str, Any]:
        if not text.strip() or len(text) > 1000:
            raise refused(
                "変更指示を認識できません。短い指示で録り直してください。",
                "VOICE_TRANSCRIPT_INVALID",
                422,
            )
        with self.database.session() as session:
            owner, row = self._turn(session, token, turn_id)
            if row.state != "PROCESSING":
                raise refused("録音要求が取り消されています。", "VOICE_TURN_CANCELLED")
            purpose = row.purpose
            selected = self._selected(session, owner) if purpose == "command" else {}
        intent = await self.provider.intent(text, selected) if purpose == "command" else None
        with self.database.transaction() as session:
            owner, row = self._turn(session, token, turn_id)
            if row.state != "PROCESSING":
                raise refused("録音要求が取り消されています。", "VOICE_TURN_CANCELLED")
            if purpose == "approval":
                proposal = self.assistant._proposal(
                    session, owner, row.binding_json["proposalId"], row.context_revision
                )
                self.assistant._active(proposal)
                expected = "取り消しを適用" if proposal.plan_json.get("undoOfId") else "変更を適用"
                spoken = re.sub(r"[\s。、.!\uFF01?\uFF1F]", "", text)
                approved = (
                    spoken == expected
                    and proposal.state == "PRESENTED"
                    and proposal.challenge_hash == row.binding_json["challengeHash"]
                    and proposal.diff_hash == row.binding_json["diffHash"]
                )
                row.state = "APPROVED" if approved else "READY"
                row.result_json = {
                    "approved": approved,
                    "message": "承認を認識しました。"
                    if approved
                    else f"承認を認識できません。「{expected}」と話してください。"
                    "適用ボタンも使用できます。",
                }
            elif (
                intent is None
                or intent.operation is None
                or intent.operation not in selected["allowedOperations"]
            ):
                row.state = "READY"
                row.result_json = {
                    "message": "選択した対象をどう変更するか、1操作と変更値を指定してください。"
                }
            else:
                proposal_view = self.assistant.propose_in_transaction(
                    session,
                    owner,
                    ProposalCreate(
                        context_revision=row.context_revision,
                        operation=intent.operation,
                        value=intent.value,
                    ),
                )
                row.state, row.result_json = "READY", {"proposalId": proposal_view["id"]}
            return self._outcome(session, row)

    def _fail(self, token: str, turn_id: str) -> None:
        with self.database.transaction() as session:
            _, row = self._turn(session, token, turn_id, active=False)
            if row.state == "PROCESSING":
                row.state = "FAILED"

    def cancel(self, token: str, turn_id: str) -> dict[str, str]:
        with self.database.transaction() as session:
            _, row = self._turn(session, token, turn_id, active=False)
            if row.state not in {"CONSUMED", "PLAYED"}:
                row.state = "CANCELLED"
            return {"state": row.state}

    async def speech(self, token: str, key: str, body: SpeechRequest) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner, proposal = self._presented(session, token, body.proposal_id, body.presentation)
            existing = session.scalar(select(AssistantTurn).where(AssistantTurn.request_key == key))
            if existing:
                raise refused(
                    "この読み上げ要求は処理済みです。音声を再送せず、適用ボタンを使用してください。",
                    "VOICE_TURN_SETTLED",
                )
            label = proposal.plan_json["target"]["label"]
            after = proposal.plan_json["after"]
            text = (
                f"対象は{label}。変更後は{after if after is not None else '空欄'}。"
                "画面で変更内容を確認してから適用してください。"
            )
            row = AssistantTurn(
                request_key=key,
                session_id=owner.id,
                context_revision=owner.context_revision,
                purpose="speech",
                binding_json=binding(proposal, body.presentation),
                fingerprint=MutationReceiptService.fingerprint(body.model_dump(mode="json")),
                state="PROCESSING",
                expires_at=utc_now() + timedelta(seconds=120),
            )
            session.add(row)
            session.flush()
            turn_id = row.id
        try:
            data, duration = normalize_speech(await self.provider.speech(text))
            with self.database.transaction() as session:
                _, row = self._turn(session, token, turn_id)
                self._presented(session, token, body.proposal_id, body.presentation)
                if row.state != "PROCESSING":
                    raise refused("読み上げが取り消されています。", "VOICE_TURN_CANCELLED")
                row.state, row.result_json = (
                    "READY",
                    {"notBefore": (utc_now() + timedelta(seconds=duration)).isoformat()},
                )
            return {
                "speechId": turn_id,
                "mime": "audio/wav",
                "audio": base64.b64encode(data).decode(),
                "text": text,
            }
        except BaseException:
            self._fail(token, turn_id)
            raise

    def playback_end(self, token: str, turn_id: str, completed: bool) -> dict[str, str]:
        with self.database.transaction() as session:
            owner, row = self._turn(session, token, turn_id)
            if row.purpose not in {"speech", "receipt-speech"} or row.state != "READY":
                raise refused("読み上げ状態が一致しません。", "VOICE_PLAYBACK_INVALID")
            if row.purpose == "speech":
                proposal = self.assistant._proposal(
                    session, owner, row.binding_json["proposalId"], row.context_revision
                )
                self.assistant._active(proposal)
            if completed:
                from datetime import datetime

                if utc_now() < datetime.fromisoformat((row.result_json or {})["notBefore"]):
                    raise refused("読み上げが完了していません。", "VOICE_PLAYBACK_REQUIRED")
            row.state = "PLAYED" if completed else "CANCELLED"
            return {"state": row.state}

    async def receipt_speech(self, token: str, key: str, receipt_key: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self.assistant._session(session, token)
            receipt = self.assistant.receipts.get(receipt_key, owner.id)
            if not receipt or receipt["state"] != "SUCCEEDED" or not receipt["result"]:
                raise refused(
                    "保存成功が確認された操作だけを読み上げます。", "VOICE_RECEIPT_REQUIRED"
                )
            if session.scalar(select(AssistantTurn).where(AssistantTurn.request_key == key)):
                raise refused("この読み上げ要求は処理済みです。", "VOICE_TURN_SETTLED")
            result = receipt["result"]
            text = f"対象は{result['target']['label']}。保存が完了しました。"
            row = AssistantTurn(
                request_key=key,
                session_id=owner.id,
                context_revision=owner.context_revision,
                purpose="receipt-speech",
                binding_json={"requestKey": receipt_key},
                fingerprint=secret_hash(receipt_key),
                state="PROCESSING",
                expires_at=utc_now() + timedelta(seconds=120),
            )
            session.add(row)
            session.flush()
            turn_id = row.id
        try:
            data, duration = normalize_speech(await self.provider.speech(text))
            with self.database.transaction() as session:
                _, row = self._turn(session, token, turn_id)
                if row.state != "PROCESSING":
                    raise refused("読み上げが取り消されています。", "VOICE_TURN_CANCELLED")
                row.state, row.result_json = (
                    "READY",
                    {"notBefore": (utc_now() + timedelta(seconds=duration)).isoformat()},
                )
            return {
                "speechId": turn_id,
                "mime": "audio/wav",
                "audio": base64.b64encode(data).decode(),
            }
        except BaseException:
            self._fail(token, turn_id)
            raise
