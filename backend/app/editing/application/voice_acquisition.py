"""One volatile owner of bounded utterances, shared by continuous/local capture."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.editing.domain.utterance import AudioInvalid, Utterance
from app.editing.errors import reject


@dataclass
class Captured:
    session_id: str
    utterance: Utterance
    expires: float


class VoiceAcquisition:
    def __init__(self) -> None:
        self.items: dict[str, Captured] = {}

    def prune(self) -> None:
        for key, value in list(self.items.items()):
            if value.expires <= time.monotonic():
                value.utterance.clear()
                self.items.pop(key)

    def begin(self, session_id: str, utterance_id: str, binding: dict[str, Any]) -> Utterance:
        self.prune()
        if (
            utterance_id in self.items
            or len(self.items) >= 32
            or sum(v.session_id == session_id for v in self.items.values()) >= 3
        ):
            raise AudioInvalid("Utterance capacity/identity is invalid")
        utterance = Utterance(utterance_id, dict(binding), time.monotonic())
        self.items[utterance_id] = Captured(session_id, utterance, time.monotonic() + 900)
        return utterance

    def text(self, session_id: str, binding: dict[str, Any], source: dict[str, Any]) -> str:
        self.prune()
        item = self.items.get(source["utteranceId"])
        if (
            not item
            or item.session_id != session_id
            or item.utterance.failed
            or not item.utterance.text
        ):
            raise reject("VOICE_TRANSCRIPT_UNAVAILABLE", "音声指示が未完了・失効しています。", 409)
        utterance = item.utterance
        assert utterance.text is not None
        if (
            utterance.binding != binding
            or utterance.transcript_digest != source["transcriptDigest"]
        ):
            raise reject("VOICE_BINDING_STALE", "音声の対象世代又は全文digestが一致しません。", 409)
        return utterance.text

    def clear(self, session_id: str | None = None) -> None:
        for key, item in list(self.items.items()):
            if session_id is None or item.session_id == session_id:
                item.utterance.clear()
                self.items.pop(key)

    def release(self, utterance_id: str) -> None:
        item = self.items.pop(utterance_id, None)
        if item:
            item.utterance.clear()
