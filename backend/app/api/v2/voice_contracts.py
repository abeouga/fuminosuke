"""Browser capture controls. Neither captions nor client text authorize a voice edit."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from app.api.v2.editing_contracts import Binding, Id
from app.editing.operation_contracts import Contract


class Begin(Contract):
    type: Literal["utterance.begin"]
    utterance_id: Id
    binding: Binding


class SegmentBegin(Contract):
    type: Literal["segment.begin"]
    segment_id: Id
    sequence: int = Field(ge=1, le=180, strict=True)


class SegmentEnd(Contract):
    type: Literal["segment.end"]
    segment_id: Id


class End(Contract):
    type: Literal["utterance.end"]
    manifest: list[Id] = Field(min_length=1, max_length=180)
    reason: Literal["silence", "local_stop"]
    silence_ms: int = Field(ge=0, le=30000, strict=True)


class Simple(Contract):
    type: Literal["speech.stopped", "utterance.cancel", "capture.stop"]


VoiceControl = Annotated[
    Begin | SegmentBegin | SegmentEnd | End | Simple, Field(discriminator="type")
]
Control: TypeAdapter[VoiceControl] = TypeAdapter(VoiceControl)
