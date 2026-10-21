from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.editing.facet_contracts import FacetTouch
from app.editing.manual_draft import ManualDraft, ManualOcrDraft
from app.editing.operation_contracts import Contract, Handle, Operation
from app.editing.target_contracts import DraftState, TargetInfo

Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$", strict=True)]
Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", strict=True)]
Entity = Annotated[str, Field(min_length=1, max_length=128, strict=True)]


class Resource(Contract):
    kind: Literal["ocr", "template"]
    id: Entity


class PageScope(Contract):
    mode: Literal["current", "all"]
    page_id: Entity | None


class SessionCreate(Contract):
    contract_version: Literal["editing.v2"]
    resource: Resource
    view_instance_id: Entity
    mode: Literal["manual", "continuous", "local"]
    page_scope: PageScope


class Binding(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    context_revision: int = Field(ge=1, strict=True)
    interaction_epoch: int = Field(ge=0, strict=True)
    catalog_revision: Sha
    scope_digest: Sha


class ContextUpdate(Contract):
    contract_version: Literal["editing.v2"]
    kind: Literal["context_update"]
    expected_revision: int = Field(ge=1, strict=True)
    expected_context_revision: int = Field(ge=1, strict=True)
    interaction_epoch: int = Field(ge=0, strict=True)
    resource: Resource
    mode: Literal["manual", "continuous", "local"]
    page_scope: PageScope
    selected_table_id: Entity | None
    selected_handles: list[Handle] = Field(max_length=1000)
    local_target_handle: Handle | None


class FocusAnchor(Contract):
    anchor_id: Id
    kind: Literal["review_field", "template_cell"]
    resource_id: Entity
    page_id: Entity | None
    table_id: Entity | None
    field_id: Entity | None
    cell_id: Entity | None
    row_id: Entity | None
    column_id: Entity | None
    label: str = Field(min_length=1, max_length=300, strict=True)


class Focus(Contract):
    view_revision: int = Field(ge=1, strict=True)
    context_revision: int = Field(ge=1, strict=True)
    interaction_epoch: int = Field(ge=0, strict=True)
    anchors: list[FocusAnchor] = Field(max_length=1000)
    primary_anchor_id: Id | None


class ScopeSnapshot(Contract):
    contract_version: Literal["editing.v2"]
    kind: Literal["scope_snapshot"]
    resource_revision: int = Field(ge=1, strict=True)
    context_revision: int = Field(ge=1, strict=True)
    interaction_epoch: int = Field(ge=0, strict=True)
    resource: Resource
    mode: Literal["manual", "continuous", "local"]
    page_scope: PageScope
    selected_table_id: Entity | None
    selected_handles: list[Handle] = Field(max_length=1000)
    local_target_handle: Handle | None
    recent_table_id: Entity | None
    catalog_revision: Sha
    scope_digest: Sha
    current_focus: Focus | None


class TextInput(Contract):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=20000, strict=True)


class VoiceInput(Contract):
    kind: Literal["voice"]
    utterance_id: Id
    transcript_digest: Sha


class ChoiceInput(Contract):
    kind: Literal["choice"]
    choice_id: Id


class ReplyTo(Contract):
    question_id: Id
    version: int = Field(ge=1, strict=True)


class ScopeAnswer(Contract):
    contract_version: Literal["editing.v2"]
    request_id: Id
    reply_to: ReplyTo
    context: ContextUpdate


class Instruction(Contract):
    kind: Literal["instruction"]
    input: Annotated[TextInput | VoiceInput | ChoiceInput, Field(discriminator="kind")]
    reply_to: ReplyTo | None


class ManualChanges(Contract):
    kind: Literal["manual_changes"]
    operations: list[Operation] = Field(min_length=1, max_length=50)


class Undo(Contract):
    kind: Literal["undo"]
    operation_id: Id


class Cancel(Contract):
    kind: Literal["cancel"]
    command_id: Id


class CommandRequest(Contract):
    contract_version: Literal["editing.v2"]
    request_id: Id
    binding: Binding
    after_command_id: Id | None
    command: Annotated[Instruction | ManualChanges | Undo | Cancel, Field(discriminator="kind")]


class ManualAuthoring(Contract):
    contract_version: Literal["editing.v2"]
    request_id: Id
    binding: Binding
    after_command_id: None
    draft: ManualDraft
    analysis_merge_ids: list[Id] = Field(default_factory=list, max_length=1000)
    manual_touched_facets: list[FacetTouch] = Field(default_factory=list, max_length=5000)


class ManualOcr(Contract):
    contract_version: Literal["editing.v2"]
    kind: Literal["manual_ocr"]
    request_id: Id
    binding: Binding
    after_command_id: None
    draft: ManualOcrDraft


class Receipt(Contract):
    receipt_id: Id
    operation_id: Id | None
    before_revision: int = Field(ge=1, strict=True)
    after_revision: int = Field(ge=1, strict=True)
    notification: str = Field(min_length=1, max_length=1000, strict=True)
    can_undo: bool
    view_revision: int = Field(ge=1, strict=True)
    focus: Focus
    substitutions: dict[str, str] = Field(default_factory=dict)


class Accepted(Contract):
    type: Literal["accepted"]
    queue_position: int = Field(ge=0, le=2, strict=True)


class TargetFocus(Contract):
    type: Literal["target_focus"]
    phase: Literal["candidates", "resolved"]
    candidate_set_id: Id | None
    focus: Focus


class Applied(Contract):
    type: Literal["applied"]
    receipt: Receipt


class Choice(Contract):
    choice_id: Id
    label: str = Field(min_length=1, max_length=200)
    # The target this choice would edit, for highlighting it on screen (added 2026-10-18).
    handle: str | None = Field(default=None, max_length=200)


class Question(Contract):
    question_id: Id
    version: int = Field(ge=1, strict=True)
    missing_kind: Literal[
        "target", "table", "row", "column", "value", "meaning", "name", "operation", "scope"
    ]
    message: str = Field(min_length=1, max_length=1000)
    # Up to 12 so a "which row" question can list a whole short table.
    choices: list[Choice] = Field(max_length=12)
    expires_at: str = Field(min_length=1, max_length=40)


class Clarification(Contract):
    type: Literal["clarification_required"]
    question: Question


class NoAction(Contract):
    type: Literal["no_action"]
    reason: Literal["not_an_edit", "unchanged"]
    message: str = Field(min_length=1, max_length=1000)


class Error(Contract):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool
    current_revision: int | None = Field(ge=1, strict=True)


class Failure(Contract):
    type: Literal["conflict", "failed"]
    error: Error


class Ended(Contract):
    type: Literal["cancelled", "expired"]
    message: str = Field(min_length=1, max_length=1000)


class Event(Contract):
    contract_version: Literal["editing.v2"]
    event_id: Id
    session_id: Id
    command_id: Id
    instruction_id: Id
    request_id: Id
    sequence: int = Field(ge=1, strict=True)
    emitted_at: str = Field(min_length=1, max_length=40)
    payload: Accepted | TargetFocus | Applied | Clarification | NoAction | Failure | Ended


class CommandResult(Contract):
    contract_version: Literal["editing.v2"]
    command_id: Id
    instruction_id: Id
    request_id: Id
    state: Literal[
        "ACCEPTED",
        "PLANNING",
        "VALIDATING",
        "COMMITTING",
        "APPLIED",
        "WAITING_INFORMATION",
        "NO_ACTION",
        "CONFLICT",
        "FAILED",
        "CANCELLED",
        "EXPIRED",
        "INTERRUPTED",
    ]
    accepted_event: Event
    final_event: Event | None


class CatalogView(Contract):
    complete: Literal[True]
    targets: list[TargetInfo]


class Limits(Contract):
    operations: Literal[50]
    queued_commands: Literal[2]
    instruction_characters: Literal[20000]
    utterance_seconds: Literal[180]


class UiBinding(Contract):
    handle: Handle
    kind: Literal["field", "cell", "heading", "record_section"]
    entity_id: Entity
    page_id: Entity | None


class ContextResult(Contract):
    contract_version: Literal["editing.v2"]
    scope_context: ScopeSnapshot
    editable: bool
    capabilities: list[str]
    catalog: CatalogView
    draft_state: DraftState
    limits: Limits
    last_receipt: Receipt | None
    ui_bindings: list[UiBinding]


class SessionResult(ContextResult):
    session_id: Id
    token: str = Field(min_length=32, max_length=100, strict=True)


class CloseResult(Contract):
    contract_version: Literal["editing.v2"]
    session_id: Id
    state: Literal["CLOSED"]
