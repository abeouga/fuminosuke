from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.editing.operation_contracts import Contract, EntityId, Handle

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


class DraftState(Contract):
    state: Literal["complete", "incomplete"]
    reasons: list[str]


class Position(Contract):
    x: float
    y: float


class Size(Contract):
    width: float
    height: float


class RecordTable(Contract):
    table_id: EntityId
    table_name: str


class RecordCell(RecordTable):
    table_title: str
    column_label_ambiguous: bool = False
    row_id: EntityId
    row_number: int = Field(ge=1)
    column_id: EntityId
    column_number: int = Field(ge=1)
    column_name: str


class TargetInfo(Contract):
    handle: Handle
    kind: TargetKind
    page_id: str | None
    name: str
    aliases: list[str]
    allowed_operations: list[str]
    score: int | None = None
    printed_name: str | None = None
    role: str | None = None
    heading_path: list[str] | None = None
    position: Position | None = None
    size: Size | None = None
    sheet_cell: str | None = None
    text_spills_right: int | None = None
    unavailable_reason: str | None = None
    virtual: bool | None = None
    input_cell_name: str | None = None
    mapping_type: str | None = None
    current_value: str | None = None
    member_handles: list[Handle] | None = None
    order: int | None = None
    recognition_mode: str | None = None
    selection_mode: str | None = None
    option_value: str | None = None
    owner_handle: Handle | None = None
    meaning_handle: Handle | None = None
    parent_handle: Handle | None = None
    row_key_handle: Handle | None = None
    row_key_value: str | None = None
    record_cell: RecordCell | None = None
