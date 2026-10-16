"""Version-neutral, closed operation DTOs shared by the editing adapters."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import to_camel

Name = Annotated[str, Field(min_length=1, max_length=100, strict=True)]
Handle = Annotated[str, Field(pattern=r"^t_[a-f0-9]{64}$", strict=True)]
EntityId = Annotated[str, Field(min_length=1, max_length=100, strict=True)]
LocalId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$", strict=True)]


class Contract(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class LocalRef(Contract):
    local_ref: LocalId


Ref = Handle | LocalRef


class OcrSetValue(Contract):
    op: Literal["ocr.set_value"]
    target: Handle
    value: str | None = Field(max_length=500, strict=True)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Use null for an explicitly blank field")
        return value


class CellSetRole(Contract):
    op: Literal["cell.set_role"]
    cells: list[Handle] = Field(min_length=1, max_length=1000)
    role: Literal[
        "input",
        "label",
        "parent_label",
        "mark",
        "record_key_cell",
        "column_key_cell",
        "ignored",
    ]


class CellRename(Contract):
    op: Literal["cell.rename"]
    cell: Handle
    name: Name | None


class CellSetReading(Contract):
    op: Literal["cell.set_reading"]
    cells: list[Handle] = Field(min_length=1, max_length=1000)
    reading: Literal["text", "sequence"]


class MeaningCreate(Contract):
    op: Literal["meaning.create"]
    local_ref: LocalId
    name: Name
    aliases: list[Name] = Field(max_length=20)


class MeaningUpdate(Contract):
    op: Literal["meaning.update"]
    meaning: Ref
    name: Name
    aliases: list[Name] = Field(max_length=20)


class MeaningDelete(Contract):
    op: Literal["meaning.delete"]
    meaning: Ref


class MeaningBind(Contract):
    op: Literal["meaning.bind"]
    meaning: Ref
    input_cells: list[Handle] = Field(min_length=1, max_length=1000)
    label_cells: list[Handle] = Field(max_length=30)
    axis: Literal["horizontal", "vertical", "direct"]


class MeaningUnbind(Contract):
    op: Literal["meaning.unbind"]
    relations: list[Handle] = Field(min_length=1, max_length=1000)


class HeadingCreate(Contract):
    op: Literal["heading.create_virtual"]
    local_ref: LocalId
    name: Name
    page: Handle | None
    anchor: Ref


class HeadingUpdate(Contract):
    op: Literal["heading.update"]
    heading: Ref
    name: Name


class HeadingAttach(Contract):
    op: Literal["heading.attach"]
    heading: Ref
    children: list[Ref] = Field(min_length=1, max_length=1000)


class HeadingDetach(Contract):
    op: Literal["heading.detach"]
    children: list[Ref] = Field(min_length=1, max_length=1000)


class HeadingDelete(Contract):
    op: Literal["heading.delete_virtual"]
    heading: Ref


class RecordColumn(Contract):
    meaning: Ref
    label_cells: list[Handle] = Field(max_length=30)


class RecordRow(Contract):
    group: Handle | None
    local_ref: LocalId | None
    cells: list[Handle] = Field(min_length=1, max_length=100)
    row_key_cell: Handle | None
    row_key_value: Name | None

    @model_validator(mode="after")
    def require_identity(self) -> RecordRow:
        if (self.group is None) == (self.local_ref is None):
            raise ValueError("Specify exactly one existing group or new localRef")
        return self


class RecordOptions(Contract):
    row_key_strategy: Literal["printed", "position"]
    include_empty_rows: bool = Field(strict=True)


class RecordsDefine(Contract):
    op: Literal["records.define"]
    local_ref: LocalId
    name: Name
    columns: list[RecordColumn] = Field(max_length=100)
    rows: list[RecordRow] = Field(max_length=300)
    options: RecordOptions
    column_labels: list[Handle] | None = Field(default=None, max_length=30)


class RecordsReplaceRows(Contract):
    op: Literal["records.replace_rows"]
    section: Ref
    rows: list[RecordRow] = Field(max_length=300)


class RecordsConfigure(Contract):
    op: Literal["records.configure"]
    section: Ref
    name: Name | None
    options: RecordOptions
    column_labels: list[Handle] | None = Field(default=None, max_length=30)


class RecordsDissolve(Contract):
    op: Literal["records.dissolve"]
    section: Ref


class MarkOptionSpec(Contract):
    option: Handle | None
    local_ref: LocalId | None
    label: Name
    value: Name
    region: Handle | Literal["whole-cell"] | None

    @model_validator(mode="after")
    def require_identity(self) -> MarkOptionSpec:
        if (self.option is None) == (self.local_ref is None):
            raise ValueError("Specify exactly one existing option or new localRef")
        return self


class MarkSetSpec(Contract):
    set: Handle | None
    local_ref: LocalId | None
    meaning: Ref | None
    recognition_mode: Literal["automatic", "manual", "symbol", "checklist"]
    selection_mode: Literal["single", "multiple"]
    options: list[MarkOptionSpec] = Field(max_length=50)

    @model_validator(mode="after")
    def require_identity(self) -> MarkSetSpec:
        if (self.set is None) == (self.local_ref is None):
            raise ValueError("Specify exactly one existing set or new localRef")
        return self


class MarkConfigure(Contract):
    op: Literal["mark.configure"]
    cell: Handle
    sets: list[MarkSetSpec] = Field(min_length=1, max_length=10)


class MarkClear(Contract):
    op: Literal["mark.clear"]
    cell: Handle


class CellMeaningRef(Contract):
    cell: Ref
    meaning: Ref


FieldRef = Handle | CellMeaningRef


class IdentityEntry(Contract):
    field: FieldRef
    value_type: Literal["text", "digits"]
    exact_length: int | None = Field(ge=1, le=100, strict=True)
    match_role: Literal["primary", "confirmation"]
    display_role: Literal["title", "subtitle", "none"]
    group: Ref | None
    group_label: Name | None
    component_order: int = Field(ge=0, le=100, strict=True)


class IdentityConfigure(Contract):
    op: Literal["identity.configure"]
    entries: list[IdentityEntry] = Field(max_length=20)


class NameRule(Contract):
    fields: list[FieldRef] = Field(min_length=1, max_length=20)
    separator: str = Field(max_length=20, strict=True)
    prefix: str = Field(max_length=100, strict=True)
    suffix: str = Field(max_length=100, strict=True)


class DocumentNameConfigure(Contract):
    op: Literal["document_name.configure"]
    rule: NameRule | None


Operation = Annotated[
    OcrSetValue
    | CellSetRole
    | CellRename
    | CellSetReading
    | MeaningCreate
    | MeaningUpdate
    | MeaningDelete
    | MeaningBind
    | MeaningUnbind
    | HeadingCreate
    | HeadingUpdate
    | HeadingAttach
    | HeadingDetach
    | HeadingDelete
    | RecordsDefine
    | RecordsReplaceRows
    | RecordsConfigure
    | RecordsDissolve
    | MarkConfigure
    | MarkClear
    | IdentityConfigure
    | DocumentNameConfigure,
    Field(discriminator="op"),
]
