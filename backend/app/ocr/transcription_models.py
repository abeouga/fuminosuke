from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import to_camel
from app.domain.ocr_models import ReadingStatus


class TranscriptionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class TranscriptionItem(TranscriptionModel):
    mapping_id: str = Field(min_length=1, max_length=100)
    raw: str | None = Field(default=None, max_length=500)
    status: ReadingStatus
    alternatives: list[str] = Field(default_factory=list, max_length=10)
    review_reason: str | None = Field(default=None, max_length=100)
    matched_candidate: str | None = Field(default=None, max_length=120)
    is_ditto: bool = False

    @model_validator(mode="after")
    def normalize_status(self) -> TranscriptionItem:
        if self.status is ReadingStatus.BLANK:
            self.raw = None
        elif self.status is ReadingStatus.READABLE and not self.raw:
            self.status = ReadingStatus.UNCERTAIN
        return self


class TranscriptionBatch(TranscriptionModel):
    items: list[TranscriptionItem]


class CompactTranscriptionItem(BaseModel):
    """Token-efficient wire model used only for OpenAI structured output."""

    model_config = ConfigDict(extra="forbid")

    i: str = Field(min_length=1, max_length=12)
    v: str | None = Field(max_length=500)
    s: Literal["r", "b", "u"]
    c: str | None = Field(max_length=120)


class CompactTranscriptionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CompactTranscriptionItem]
