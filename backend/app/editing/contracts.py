from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import to_camel
from app.editing.operation_contracts import (
    CellMeaningRef as CellMeaningRef,
)
from app.editing.operation_contracts import (
    CellRename as CellRename,
)
from app.editing.operation_contracts import (
    CellSetReading as CellSetReading,
)
from app.editing.operation_contracts import (
    CellSetRole as CellSetRole,
)
from app.editing.operation_contracts import (
    DocumentNameConfigure as DocumentNameConfigure,
)
from app.editing.operation_contracts import (
    FieldRef as FieldRef,
)
from app.editing.operation_contracts import (
    HeadingAttach as HeadingAttach,
)
from app.editing.operation_contracts import (
    HeadingCreate as HeadingCreate,
)
from app.editing.operation_contracts import (
    HeadingDelete as HeadingDelete,
)
from app.editing.operation_contracts import (
    HeadingDetach as HeadingDetach,
)
from app.editing.operation_contracts import (
    HeadingUpdate as HeadingUpdate,
)
from app.editing.operation_contracts import (
    IdentityConfigure as IdentityConfigure,
)
from app.editing.operation_contracts import (
    IdentityEntry as IdentityEntry,
)
from app.editing.operation_contracts import (
    LocalRef as LocalRef,
)
from app.editing.operation_contracts import (
    MarkClear as MarkClear,
)
from app.editing.operation_contracts import (
    MarkConfigure as MarkConfigure,
)
from app.editing.operation_contracts import (
    MarkOptionSpec as MarkOptionSpec,
)
from app.editing.operation_contracts import (
    MarkSetSpec as MarkSetSpec,
)
from app.editing.operation_contracts import (
    MeaningBind as MeaningBind,
)
from app.editing.operation_contracts import (
    MeaningCreate as MeaningCreate,
)
from app.editing.operation_contracts import (
    MeaningDelete as MeaningDelete,
)
from app.editing.operation_contracts import (
    MeaningUnbind as MeaningUnbind,
)
from app.editing.operation_contracts import (
    MeaningUpdate as MeaningUpdate,
)
from app.editing.operation_contracts import (
    NameRule as NameRule,
)
from app.editing.operation_contracts import (
    OcrSetValue as OcrSetValue,
)
from app.editing.operation_contracts import (
    Operation as Operation,
)
from app.editing.operation_contracts import (
    RecordColumn as RecordColumn,
)
from app.editing.operation_contracts import (
    RecordOptions as RecordOptions,
)
from app.editing.operation_contracts import (
    RecordRow as RecordRow,
)
from app.editing.operation_contracts import (
    RecordsConfigure as RecordsConfigure,
)
from app.editing.operation_contracts import (
    RecordsDefine as RecordsDefine,
)
from app.editing.operation_contracts import (
    RecordsDissolve as RecordsDissolve,
)
from app.editing.operation_contracts import (
    RecordsReplaceRows as RecordsReplaceRows,
)
from app.editing.operation_contracts import (
    Ref as Ref,
)
from app.editing.target_contracts import (
    DraftState as DraftState,
)
from app.editing.target_contracts import (
    Position as Position,
)
from app.editing.target_contracts import (
    RecordCell as RecordCell,
)
from app.editing.target_contracts import (
    RecordTable as RecordTable,
)
from app.editing.target_contracts import (
    TargetInfo as TargetInfo,
)

CONTRACT_VERSION = "editing.v1"
TargetKind = Literal[
    "page",
    "cell",
    "meaning",
    "relation",
    "heading",
    "record_section",
    "record_group",
    "mark_set",
    "mark_option",
    "region",
    "field",
    "identity_group",
]
Mode = Literal["manual", "continuous", "local"]
Origin = Literal["manual", "continuous_voice", "local_voice"]
Name = Annotated[str, Field(min_length=1, max_length=100, strict=True)]
Handle = Annotated[str, Field(pattern=r"^t_[a-f0-9]{64}$", strict=True)]
EntityId = Annotated[str, Field(min_length=1, max_length=100, strict=True)]
LocalId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$", strict=True)]


class Contract(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class ResourceRef(Contract):
    kind: Literal["ocr", "template"]
    id: Annotated[str, Field(min_length=1, max_length=36, strict=True)]


class PageScope(Contract):
    mode: Literal["current", "all"] = "current"
    page_id: EntityId | None = None


class SessionCreate(Contract):
    resource: ResourceRef
    view_instance_id: EntityId
    mode: Mode = "manual"
    page_scope: PageScope = Field(default_factory=PageScope)
    local_edit_epoch: int = Field(default=0, ge=0, strict=True)
    selection_epoch: int = Field(default=0, ge=0, strict=True)


class ContextUpdate(Contract):
    expected_context_revision: int = Field(ge=1, strict=True)
    resource_revision: int = Field(ge=1, strict=True)
    page_scope: PageScope
    local_edit_epoch: int = Field(ge=0, strict=True)
    selection_epoch: int = Field(ge=0, strict=True)
    selected_handles: list[Handle] = Field(max_length=1000)


class TargetSearch(Contract):
    query: str = Field(default="", max_length=300, strict=True)
    kinds: list[TargetKind] = Field(default_factory=list, max_length=12)
    page_scope: PageScope | None = None
    cursor: str | None = Field(default=None, max_length=200, strict=True)
    position: Literal["top", "bottom", "left", "right"] | None = None
    heading: Name | None = None
    row: int | None = Field(default=None, ge=1, strict=True)
    column: int | None = Field(default=None, ge=1, strict=True)
    table_id: EntityId | None = None
    table_name: Name | None = None
    role: (
        Literal[
            "input",
            "label",
            "parent_label",
            "mark",
            "record_key_cell",
            "column_key_cell",
            "ignored",
        ]
        | None
    ) = None


class RetargetPrevious(Contract):
    kind: Literal["retarget_previous"]


class Batch(Contract):
    contract_version: Literal["editing.v1"]
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$", strict=True)]
    expected_revision: int = Field(ge=1, strict=True)
    context_revision: int = Field(ge=1, strict=True)
    local_edit_epoch: int = Field(ge=0, strict=True)
    origin: Origin
    turn_id: Annotated[str, Field(min_length=36, max_length=36, strict=True)] | None
    correction: RetargetPrevious | None
    operations: list[Operation] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def require_turn(self) -> Batch:
        if (self.origin == "manual") != (self.turn_id is None):
            raise ValueError("Manual requests have no turn; voice requests require a bound turn")
        return self


class Undo(Contract):
    contract_version: Literal["editing.v1"]
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$", strict=True)]
    expected_revision: int = Field(ge=1, strict=True)
    context_revision: int = Field(ge=1, strict=True)
    local_edit_epoch: int = Field(ge=0, strict=True)
    origin: Origin
    turn_id: Annotated[str, Field(min_length=36, max_length=36, strict=True)] | None
    operation_id: Annotated[str, Field(min_length=36, max_length=36, strict=True)]

    @model_validator(mode="after")
    def require_turn(self) -> Undo:
        if (self.origin == "manual") != (self.turn_id is None):
            raise ValueError("Manual requests have no turn; voice requests require a bound turn")
        return self


class ToolBatch(Contract):
    correction: RetargetPrevious | None
    operations: list[Operation] = Field(min_length=1, max_length=50)


class EmptyArgs(Contract):
    pass


class ReceiptArgs(Contract):
    request_id: str = Field(min_length=1, max_length=128, strict=True)


class CatalogResult(Contract):
    catalog_revision: str
    resource_revision: int
    targets: list[TargetInfo]
    next_cursor: str | None
    total: int


class HistoryItem(Contract):
    operation_id: str
    operation: str
    before_revision: int
    after_revision: int
    contract_version: str
    origin: str
    undo_of_id: str | None
    can_undo: bool
    supersedes_operation_id: str | None = None


class RecentEdit(Contract):
    operation_id: str
    op: Literal["ocr.set_value", "cell.rename"]
    target: Handle
    value: str | None


class ContextResult(Contract):
    contract_version: Literal["editing.v1"]
    resource: ResourceRef
    view_instance_id: str
    mode: Mode
    context_revision: int
    resource_revision: int
    catalog_revision: str
    page_scope: PageScope
    local_edit_epoch: int
    selection_epoch: int
    selected_handles: list[Handle]
    editable: bool
    capabilities: list[str]
    retarget_previous_available: bool
    draft_state: DraftState
    catalog: CatalogResult
    history: list[HistoryItem]
    recent_edit: RecentEdit | None = None
    recent_table: RecordTable | None = None


class SessionResult(ContextResult):
    session_id: str
    token: str


class ChangedTarget(Contract):
    handle: Handle | None
    kind: TargetKind
    name: str
    page_id: str | None


class BatchResult(Contract):
    request_id: str
    operation_id: str | None
    before_revision: int
    after_revision: int
    outcome: Literal["CHANGED", "NO_CHANGE"]
    changed_targets: list[ChangedTarget]
    created_targets: dict[str, Handle]
    draft_state: DraftState
    context_revision: int
    can_undo: bool
    notification: str


class EditingFieldError(Contract):
    field: str
    message: str


class EditingProblem(Contract):
    code: str
    message: str
    retryable: bool
    field_errors: list[EditingFieldError]
    candidate_handles: list[Handle]
    current_revision: int | None


class ReceiptResult(Contract):
    request_id: str
    state: Literal["NOT_FOUND", "PENDING", "SUCCEEDED", "FAILED"]
    result: BatchResult | None
    error: EditingProblem | None


class HistoryResult(Contract):
    resource_revision: int
    operations: list[HistoryItem]


class EditingStatus(Contract):
    contract_version: Literal["editing.v1"]
    enabled: bool


class CloseResult(Contract):
    state: Literal["CLOSED"]
