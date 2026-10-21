"""Ordered audio manifests; segment completion never authorizes editing."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

MAX_BYTES = 8_640_000
SEGMENT_BYTES = 1_440_000


class AudioInvalid(Exception):
    pass


_FILLER = re.compile(
    r"(?:え+[ーっ]*と?|あ+ー*|あの+ー*|う+ー*ん|ん+ー*|うーむ|まあ|えーと|えっと|あれ|おっと)+"
)


_MEANINGFUL_SHORT = re.compile(
    r"[0-9一二三四五六七八九十百千万]|取り?消|戻|中止|やめ|キャンセル|違|はい|いいえ|空欄"
)


def is_noise(text: str, *, answering: bool) -> bool:
    """Too short to be an instruction (えーと heard as 「A と」), unless it answers a question
    or is a short command such as 取り消して / 中止."""
    if is_filler(text):
        return True
    core = re.sub(r"[\s、。,.!?…・〜~]", "", unicodedata.normalize("NFKC", text))
    return not answering and len(core) <= 3 and not _MEANINGFUL_SHORT.search(core)


def is_filler(text: str) -> bool:
    """Hesitation only (えー, あのー, うーん). Never sent as an instruction."""
    # NFKC folds full-width punctuation, so one ASCII class covers both widths.
    core = re.sub(r"[\s、。,.!?…・〜~]", "", unicodedata.normalize("NFKC", text))
    return not core.strip("ー") or bool(_FILLER.fullmatch(core))


@dataclass(frozen=True)
class UtteranceBoundaryPolicy:
    silence_ms: int = 1200
    incomplete_ms: int = 3000

    def wait_ms(self, caption: str, *, local: bool = False) -> int:
        if local:
            return 0
        # Classification of a trailing boundary only; no operation/value parser.
        if re.search(r"(?:それから|あと|ではなく|じゃなく|違う|えーと|と、?|を、?)\s*$", caption):
            return self.incomplete_ms
        return self.silence_ms

    def continues(self, caption: str) -> bool:
        return bool(
            re.match(r"\s*(?:違う|訂正|いや|それから|追加で|あと[、\s]|ではなく|じゃなく)", caption)
        )


@dataclass
class AudioSegment:
    segment_id: str
    sequence: int
    byte_count: int = 0
    item_id: str | None = None
    submitted: bool = False
    text: str | None = None


@dataclass
class Utterance:
    utterance_id: str
    binding: dict[str, Any]
    started: float
    segments: list[AudioSegment] = field(default_factory=list)
    byte_count: int = 0
    manifest: tuple[str, ...] | None = None
    text: str | None = None
    transcript_digest: str | None = None
    failed: bool = False

    def segment(self, segment_id: str, sequence: int) -> AudioSegment:
        if (
            self.failed
            or self.manifest is not None
            or sequence != len(self.segments) + 1
            or len(self.segments) >= 180
        ):
            raise AudioInvalid("Segment order/limit is invalid")
        if any(v.segment_id == segment_id for v in self.segments):
            raise AudioInvalid("Duplicate segment")
        if self.segments and not self.segments[-1].submitted:
            raise AudioInvalid("Previous segment has not been committed")
        segment = AudioSegment(segment_id, sequence)
        self.segments.append(segment)
        return segment

    def append(self, data: bytes, now: float) -> None:
        if (
            self.failed
            or not self.segments
            or self.manifest is not None
            or now - self.started > 180
        ):
            raise AudioInvalid("Utterance ended or exceeded its deadline")
        segment = self.segments[-1]
        if segment.submitted or not data or len(data) > 96000 or len(data) % 2:
            raise AudioInvalid("Invalid PCM chunk")
        if (
            self.byte_count + len(data) > MAX_BYTES
            or segment.byte_count + len(data) > SEGMENT_BYTES
        ):
            raise AudioInvalid("PCM limit exceeded")
        segment.byte_count += len(data)
        self.byte_count += len(data)

    def commit(self, segment_id: str) -> None:
        segment = self.segments[-1] if self.segments else None
        if (
            not segment
            or segment.segment_id != segment_id
            or segment.submitted
            or segment.byte_count < 4800
        ):
            raise AudioInvalid("A segment needs at least 100ms of PCM")
        segment.submitted = True

    def committed(self, segment_id: str, item_id: str) -> None:
        segment = next((s for s in self.segments if s.segment_id == segment_id), None)
        if (
            not segment
            or not segment.submitted
            or segment.item_id
            or any(s.item_id == item_id for s in self.segments)
        ):
            raise AudioInvalid("Unexpected provider commit")
        segment.item_id = item_id

    def completed(self, item_id: str, text: str) -> None:
        segment = next((s for s in self.segments if s.item_id == item_id), None)
        # A piece holding only noise or a cough transcribes as empty; it is skipped.
        if not segment or segment.text is not None or len(text) > 20000:
            raise AudioInvalid("Unexpected provider transcript")
        segment.text = text
        self.assemble()

    def end(self, manifest: list[str], now: float) -> None:
        if self.failed or self.manifest is not None or now - self.started > 180:
            raise AudioInvalid("Utterance has expired")
        if (
            not manifest
            or manifest != [s.segment_id for s in self.segments]
            or any(not s.submitted for s in self.segments)
        ):
            raise AudioInvalid("Manifest is incomplete or unordered")
        self.manifest = tuple(manifest)
        self.assemble()

    def assemble(self) -> None:
        if self.manifest is None or any(s.text is None for s in self.segments):
            return
        text = "".join(s.text or "" for s in self.segments)
        if len(text) > 20000:
            raise AudioInvalid("Full transcript is invalid")
        if not text.strip():
            # Nothing intelligible: an empty text is never an instruction.
            self.text, self.transcript_digest = "", None
            return
        self.text = text
        self.transcript_digest = hashlib.sha256(text.encode()).hexdigest()

    def clear(self) -> None:
        self.failed = True
        self.text = None
        for segment in self.segments:
            segment.text = None
