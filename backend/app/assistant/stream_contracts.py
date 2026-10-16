from typing import Literal

from pydantic import Field

from app.editing.contracts import Contract


class AudioBegin(Contract):
    type: Literal["audio.begin"]
    audio_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class AudioControl(Contract):
    type: Literal["audio.end", "audio.cancel", "audio.release", "audio.done"]
    audio_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class SpeechRequest(Contract):
    request_id: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=36)


class UiTargets(Contract):
    context_revision: int = Field(ge=1, strict=True)
    resource_revision: int = Field(ge=1, strict=True)
    entity_ids: list[str] = Field(default_factory=list, max_length=100)
    handles: list[str] = Field(default_factory=list, max_length=100)
