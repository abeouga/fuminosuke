from __future__ import annotations

import copy
import math
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.api_budget import ApiBudget
from app.assistant.stream_models import VoiceAudioItem, VoiceStream
from app.db.models import utc_now
from app.db.mutation_models import ResourceOperation
from app.editing.errors import reject
from app.editing.models import EditingOperation, EditingSession, EditingTurn
from app.editing.service import EditingService, aware

CONNECTION_SECONDS = 300
TOTAL_SECONDS = 1200
SAMPLE_RATE = 24000
MAX_SAMPLES = SAMPLE_RATE * 30


class StreamService:
    def __init__(self, editing: EditingService) -> None:
        self.editing = editing
        self.database = editing.database
        self.budget = ApiBudget(self.database)

    def status(self) -> dict[str, Any]:
        with self.database.session() as session:
            charged = session.scalar(select(func.sum(VoiceStream.charged_seconds))) or 0
            samples = session.scalar(select(func.sum(VoiceAudioItem.sample_count))) or 0
        return {
            "calls": self.budget.status(),
            "secondsRemaining": max(0, TOTAL_SECONDS - charged),
            "connectionSeconds": CONNECTION_SECONDS,
            "utteranceSeconds": 30,
            "pendingItems": 2,
            "sentAudioSeconds": samples / SAMPLE_RATE,
        }

    def create(self, session_id: str, token: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            owner = self.editing._owner(session, session_id, token)
            if owner.mode != "continuous":
                raise reject("LOCAL_SCOPE_MISMATCH", "連続音声session専用です。")
            used = session.scalar(select(func.sum(VoiceStream.charged_seconds))) or 0
            seconds = min(CONNECTION_SECONDS, TOTAL_SECONDS - used)
            if seconds < 1:
                raise reject("VOICE_TIME_EXHAUSTED", "連続音声の累計20分枠に達しました。", 429)
            # A previous disconnected/crashed reservation remains charged in full.
            row = VoiceStream(
                session_id=owner.id,
                reserved_seconds=seconds,
                charged_seconds=seconds,
                expires_at=utc_now() + timedelta(seconds=seconds),
            )
            session.add(row)
            session.flush()
            return {"streamId": row.id, "seconds": seconds}

    def _stream(self, session: Session, session_id: str, token: str, stream_id: str) -> VoiceStream:
        self.editing._owner(session, session_id, token)
        row = session.get(VoiceStream, stream_id)
        if (
            row is None
            or row.session_id != session_id
            or row.state == "CLOSED"
            or aware(row.expires_at) <= utc_now()
        ):
            raise reject("VOICE_STREAM_EXPIRED", "音声接続が終了または期限切れです。")
        return row

    def connect(self, session_id: str, token: str, stream_id: str) -> float:
        with self.database.transaction() as session:
            row = self._stream(session, session_id, token, stream_id)
            if row.state != "CREATED":
                raise reject("VOICE_STREAM_REUSED", "同じ音声接続は再使用できません。")
            row.connect_call_id = self.budget.reserve_in(session, "voice.ws.connect")
            row.state, row.connected_at = "CONNECTED", utc_now()
            return (aware(row.expires_at) - utc_now()).total_seconds()

    def transcription_keywords(self, session_id: str, token: str, stream_id: str) -> list[str]:
        """Owned field names only; never current OCR values or conversation text."""
        with self.database.session() as session:
            self._stream(session, session_id, token, stream_id)
            owner = self.editing._owner(session, session_id, token)
            catalog = self.editing.catalogs.build(session, owner)
            words: list[str] = []
            for target in catalog.targets.values():
                if target.kind not in {"field", "cell", "heading", "meaning", "record"}:
                    continue
                for label in (target.label, *target.aliases):
                    if (
                        label and len(label) <= 100 and label not in words
                        and not any(char in label for char in "<>\r\n")
                    ):
                        if sum(len(word) for word in words) + len(label) > 4000:
                            continue
                        words.append(label)
                        if len(words) >= 100:
                            return words
            return words

    def begin(self, session_id: str, token: str, stream_id: str, audio_id: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            row = self._stream(session, session_id, token, stream_id)
            if row.state != "CONNECTED":
                raise reject("VOICE_STREAM_EXPIRED", "接続の準備が完了していません。")
            pending = list(
                session.scalars(
                    select(VoiceAudioItem).where(
                        VoiceAudioItem.stream_id == row.id,
                        VoiceAudioItem.state.in_(["RECORDING", "SUBMITTED", "COMPLETED"]),
                    )
                )
            )
            if len(pending) >= 2 or any(item.state == "RECORDING" for item in pending):
                raise reject(
                    "VOICE_BUFFER_FULL", "前の発話が未処理です。音声取得を停止しました。", 429
                )
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(VoiceAudioItem)
                    .where(VoiceAudioItem.stream_id == row.id)
                )
                or 0
            )
            if count >= 100:
                raise reject("VOICE_CAPACITY_REACHED", "音声接続の発話数上限に達しました。", 429)
            if session.scalar(
                select(VoiceAudioItem.id).where(
                    VoiceAudioItem.stream_id == row.id, VoiceAudioItem.audio_id == audio_id
                )
            ):
                raise reject("VOICE_AUDIO_REUSED", "同じ発話IDは再使用できません。")
            owner = self.editing._owner(session, session_id, token)
            item = VoiceAudioItem(
                stream_id=row.id,
                audio_id=audio_id,
                sequence=(
                    session.scalar(
                        select(func.max(VoiceAudioItem.sequence)).where(
                            VoiceAudioItem.stream_id == row.id
                        )
                    )
                    or 0
                )
                + 1,
                binding_json=copy.deepcopy(owner.context_json),
            )
            session.add(item)
            session.flush()
            return {"type": "audio.started", "audioId": audio_id, "sequence": item.sequence}

    def _item(self, session: Session, stream_id: str, audio_id: str) -> VoiceAudioItem:
        item = session.scalar(
            select(VoiceAudioItem).where(
                VoiceAudioItem.stream_id == stream_id, VoiceAudioItem.audio_id == audio_id
            )
        )
        if item is None:
            raise reject("VOICE_AUDIO_UNKNOWN", "音声発話IDが一致しません。")
        return item

    def append(
        self, session_id: str, token: str, stream_id: str, audio_id: str, data: bytes
    ) -> None:
        if not data or len(data) > 24576 or len(data) % 2:
            raise reject("VOICE_AUDIO_INVALID", "音声frameの形式またはサイズが不正です。", 422)
        with self.database.transaction() as session:
            self._stream(session, session_id, token, stream_id)
            item = self._item(session, stream_id, audio_id)
            if (
                item.state != "RECORDING"
                or item.sample_count + len(data) // 2 > MAX_SAMPLES
                or (utc_now() - aware(item.created_at)).total_seconds() > 31
            ):
                raise reject("VOICE_DURATION_INVALID", "1発話は30秒以内にしてください。", 422)
            if item.call_id is None:
                item.call_id = self.budget.reserve_in(session, "voice.ws.audio")
            item.sample_count += len(data) // 2

    def control(
        self, session_id: str, token: str, stream_id: str, audio_id: str, action: str
    ) -> None:
        with self.database.transaction() as session:
            self._stream(session, session_id, token, stream_id)
            item = self._item(session, stream_id, audio_id)
            if action == "audio.end":
                if item.state != "RECORDING" or item.sample_count < SAMPLE_RATE * 0.1:
                    raise reject("VOICE_DURATION_INVALID", "発話が短すぎるか終了済みです。", 422)
                item.state = "SUBMITTED"
            elif action == "audio.cancel":
                item.state, item.held = "CANCELLED", 0
            elif action == "audio.release":
                if item.state != "COMPLETED":
                    raise reject("VOICE_AUDIO_PENDING", "認識完了前の発話は実行できません。")
                item.held = 0
            elif action == "audio.done":
                if item.state != "COMPLETED" or item.held:
                    raise reject("VOICE_AUDIO_PENDING", "未処理の発話を解決してください。")
                # Text never persisted; COMPLETED items remain pending until the
                # browser has resolved/cancelled the associated business turn.
                item.state = "CANCELLED"

    def committed(self, stream_id: str, audio_id: str, item_id: str) -> None:
        with self.database.transaction() as session:
            item = self._item(session, stream_id, audio_id)
            if item.state != "SUBMITTED" or item.upstream_item_id is not None:
                raise reject("VOICE_ORDER_INVALID", "音声commitの対応が不正です。", 502)
            item.upstream_item_id = item_id

    def check_deadlines(self, session_id: str, token: str, stream_id: str) -> None:
        with self.database.session() as session:
            self._stream(session, session_id, token, stream_id)
            for item in session.scalars(
                select(VoiceAudioItem).where(
                    VoiceAudioItem.stream_id == stream_id,
                    VoiceAudioItem.state.in_(["RECORDING", "SUBMITTED", "COMPLETED"]),
                )
            ):
                elapsed = (utc_now() - aware(item.created_at)).total_seconds()
                if (item.state == "RECORDING" and elapsed > 31) or elapsed > 60:
                    raise reject(
                        "VOICE_AUDIO_EXPIRED",
                        "発話の取得・処理期限を超えました。再指示してください。",
                    )

    def completed(self, stream_id: str, item_id: str) -> dict[str, Any] | None:
        with self.database.transaction() as session:
            item = session.scalar(
                select(VoiceAudioItem).where(
                    VoiceAudioItem.stream_id == stream_id,
                    VoiceAudioItem.upstream_item_id == item_id,
                )
            )
            if item is None:
                raise reject("VOICE_ORDER_INVALID", "認識結果の音声IDが一致しません。", 502)
            if item.state == "CANCELLED":
                return None
            if item.state != "SUBMITTED":
                raise reject("VOICE_ORDER_INVALID", "認識結果が重複または順序不正です。", 502)
            item.state = "COMPLETED"
            return {"audioId": item.audio_id, "sequence": item.sequence}

    def close(self, stream_id: str) -> None:
        with self.database.transaction() as session:
            row = session.get(VoiceStream, stream_id)
            if row is None or row.state == "CLOSED":
                return
            row.state, row.closed_at = "CLOSED", utc_now()
            # Only a clean server-observed disconnect settles unused time.
            if row.connected_at is not None:
                row.charged_seconds = min(
                    row.reserved_seconds,
                    max(1, math.ceil((utc_now() - aware(row.connected_at)).total_seconds())),
                )
            for item in session.scalars(
                select(VoiceAudioItem).where(VoiceAudioItem.stream_id == stream_id)
            ):
                item.held = 0
                if item.state in {"RECORDING", "SUBMITTED"}:
                    item.state = "FAILED"


def assert_no_capture(session: Session, owner: EditingSession) -> None:
    held = session.scalar(
        select(VoiceAudioItem.id)
        .join(VoiceStream)
        .where(
            VoiceStream.session_id == owner.id,
            VoiceStream.state == "CONNECTED",
            VoiceStream.expires_at > utc_now(),
            VoiceAudioItem.held == 1,
        )
    )
    if held is not None:
        raise reject("VOICE_CAPTURE_ACTIVE", "新しい発話の終了を待っています。")


def validate_audio_binding(session: Session, owner: EditingSession, audio_id: str) -> None:
    if session.scalar(
        select(EditingTurn.id).where(
            EditingTurn.session_id == owner.id,
            EditingTurn.binding_json["controller"]["audioItemId"].as_string() == audio_id,
        )
    ):
        raise reject("VOICE_AUDIO_REUSED", "同じ発話を複数の編集turnに使用できません。")
    item = session.scalar(
        select(VoiceAudioItem)
        .join(VoiceStream)
        .where(VoiceStream.session_id == owner.id, VoiceAudioItem.audio_id == audio_id)
    )
    if (
        item is None
        or item.state != "COMPLETED"
        or item.held
        or (utc_now() - aware(item.created_at)).total_seconds() > 60
    ):
        raise reject("VOICE_AUDIO_PENDING", "この発話は未完了・中止済みまたは期限切れです。")
    captured = item.binding_json
    if any(
        captured[key] != owner.context_json[key]
        for key in ("pageScope", "localEditEpoch", "selectionEpoch", "selected")
    ):
        raise reject("CONTEXT_STALE", "発話中にページ・選択・手動編集が変わりました。")
    revision = captured["resourceRevision"]
    # Only this voice session's explicit earlier successful queue entries may
    # advance a not-yet-interpreted utterance. Any intervening writer blocks it.
    for operation, turn in session.execute(
        select(ResourceOperation, EditingTurn)
        .join(EditingOperation, EditingOperation.operation_id == ResourceOperation.id)
        .join(EditingTurn, EditingOperation.turn_id == EditingTurn.id)
        .where(
            ResourceOperation.resource_kind == owner.resource_kind,
            ResourceOperation.resource_id == owner.resource_id,
            ResourceOperation.before_revision >= revision,
            ResourceOperation.after_revision <= owner.context_json["resourceRevision"],
        )
        .order_by(ResourceOperation.before_revision)
    ):
        if operation.before_revision != revision or turn.session_id != owner.id:
            break
        revision = operation.after_revision
    if revision != owner.context_json["resourceRevision"]:
        raise reject("CONTEXT_STALE", "別の保存後に古い発話を再実行できません。")
