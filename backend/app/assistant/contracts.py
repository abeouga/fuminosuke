from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import to_camel

Operation = Literal[
    "ocr.set_value", "template.rename_cell", "template.change_category", "template.rename_choice"
]


class Contract(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class Interaction(Contract):
    view_instance_id: str = Field(min_length=1, max_length=100)
    selection_epoch: int = Field(ge=0, strict=True)
    local_edit_epoch: int = Field(ge=0, strict=True)
    resource_revision: int | None = Field(ge=1, strict=True)


class OcrContext(Interaction):
    resource_kind: Literal["ocr"]
    job_id: str = Field(min_length=1, max_length=36)
    mapping_id: str | None = Field(max_length=100)
    target_path: str | None = Field(max_length=200)


class TemplateContext(Interaction):
    resource_kind: Literal["template"]
    version_id: str = Field(min_length=1, max_length=36)
    page_id: str | None = Field(max_length=100)
    cell_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(max_length=1000)
    mark_set_id: str | None = Field(max_length=100)
    choice_id: str | None = Field(max_length=100)


AssistantContext = Annotated[OcrContext | TemplateContext, Field(discriminator="resource_kind")]


class SessionCreate(Contract):
    context: AssistantContext


class ContextUpdate(SessionCreate):
    expected_context_revision: int = Field(ge=1, strict=True)


class ProposalCreate(Contract):
    context_revision: int = Field(ge=1, strict=True)
    operation: Operation
    value: str | None = Field(max_length=500)


class UndoProposalCreate(Contract):
    context_revision: int = Field(ge=1, strict=True)


class Presentation(Contract):
    context_revision: int = Field(ge=1, strict=True)
    diff_hash: str = Field(min_length=64, max_length=64)


class Apply(Presentation):
    challenge: str = Field(min_length=32, max_length=100)
    approval_turn_id: str | None = Field(default=None, min_length=36, max_length=36)


class TurnStart(Contract):
    context_revision: int = Field(ge=1, strict=True)
    purpose: Literal["command", "approval"] = "command"
    presentation: Apply | None = None
    proposal_id: str | None = Field(default=None, min_length=36, max_length=36)
    speech_id: str | None = Field(default=None, min_length=36, max_length=36)


class TextTurn(TurnStart):
    text: str = Field(min_length=1, max_length=1000)


class SpeechRequest(Contract):
    proposal_id: str = Field(min_length=36, max_length=36)
    presentation: Apply


class PlaybackEnd(Contract):
    completed: bool


class ReceiptSpeechRequest(Contract):
    request_key: str = Field(min_length=1, max_length=128)
