from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.editing.contracts import (
    Batch,
    ContextResult,
    Contract,
    Handle,
    ReceiptResult,
    TargetInfo,
    Undo,
)

TurnId = Annotated[str, Field(pattern=r"^[a-f0-9-]{36}$", strict=True)]


class ConversationStart(Contract):
    context_revision: int = Field(ge=1, strict=True)
    resource_revision: int = Field(ge=1, strict=True)
    local_edit_epoch: int = Field(ge=0, strict=True)
    selection_epoch: int = Field(ge=0, strict=True)
    audio_item_id: str | None = Field(default=None, max_length=100, strict=True)


class StartedTurn(Contract):
    turn_id: TurnId
    sequence: int
    context: ContextResult


class ResolveTurn(Contract):
    text: str = Field(min_length=1, max_length=2000, strict=True)
    reply_to: TurnId | None = None


class BatchCommand(Contract):
    kind: Literal["batch"]
    body: Batch


class UndoCommand(Contract):
    kind: Literal["undo"]
    body: Undo


Command = Annotated[BatchCommand | UndoCommand, Field(discriminator="kind")]


class TurnResolution(Contract):
    turn_id: TurnId
    state: Literal["READY", "CLARIFYING", "NO_ACTION"]
    command: Command | None = None
    message: str
    candidates: list[TargetInfo] = Field(default_factory=list, max_length=50)


class TurnStatus(Contract):
    turn_id: TurnId
    state: Literal["RESOLVING", "READY", "CANCELLED", "CONSUMED"]
    request_id: str | None
    receipt: ReceiptResult | None


class DialogueReply(Contract):
    """Non-mutating model output. The model cannot assert a saved result."""

    kind: Literal["clarify", "no_action"]
    missing: Literal["target", "value", "structure", "instruction"]
    candidate_handles: list[Handle] = Field(max_length=50)
