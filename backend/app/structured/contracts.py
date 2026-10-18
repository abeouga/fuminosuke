"""Wire contracts for structured-document.v1 (confirmed values on the accepted structure)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.editing.operation_contracts import Contract

SCHEMA_VERSION: Literal["structured-document.v1"] = "structured-document.v1"
CONTRACT_VERSION: Literal["structured.v1"] = "structured.v1"

ReadingStatus = Literal["readable", "uncertain", "blank"]
DocumentKind = Literal["single", "paged"]


class StructuredReading(Contract):
    field_id: str
    logical_field_id: str | None = None
    key: str
    target_path: str
    label: str
    section: str | None = None
    page_id: str | None = None
    table_id: str | None = None
    row_number: int | None = Field(default=None, ge=1)
    column_id: str | None = None
    raw: str | None = None
    normalized: str | None = None
    value: str | None = None
    status: ReadingStatus
    review_reason: str | None = None


class ExtraReading(Contract):
    key: str
    label: str
    raw: str | None = None
    normalized: str | None = None
    value: str | None = None
    status: ReadingStatus


class IdentityValue(Contract):
    label: str
    field_id: str | None = None
    target_path: str
    value: str | None = None
    status: ReadingStatus | None = None
    group_label: str | None = None


class TableColumn(Contract):
    id: str
    label: str


class TableCell(Contract):
    column_id: str
    field_id: str | None = None


class TableRow(Contract):
    id: str
    row_number: int = Field(ge=1)
    cells: list[TableCell]


class StructuredTable(Contract):
    id: str
    title: str
    collection_path: str | None = None
    columns: list[TableColumn]
    rows: list[TableRow]


class DocumentSummary(Contract):
    job_id: str
    kind: DocumentKind
    template_id: str
    template_version_id: str | None = None
    template_name: str
    document_name: str | None = None
    confirmed_at: datetime
    confirmed_revision: int = Field(ge=1)
    identity: list[IdentityValue]
    # The document series this job belongs to (its own id when never updated) and its version.
    series_id: str | None = None
    series_version: int = Field(default=1, ge=1)


class StructuredDocument(DocumentSummary):
    schema_version: Literal["structured-document.v1"] = SCHEMA_VERSION
    fields: list[StructuredReading]
    tables: list[StructuredTable]
    extra_fields: list[ExtraReading]


class DocumentList(Contract):
    contract_version: Literal["structured.v1"] = CONTRACT_VERSION
    items: list[DocumentSummary]


class StructuredStatus(Contract):
    contract_version: Literal["structured.v1"] = CONTRACT_VERSION
    enabled: bool
    qa_connected: bool = False
    voice_connected: bool = False


class QuestionReply(Contract):
    question_id: str = Field(min_length=3, max_length=100)
    value: str = Field(min_length=1, max_length=300)


class QuestionRequest(Contract):
    # Empty text is valid only with replyTo (the engine reuses the pending question's text).
    text: str = Field(default="", max_length=2000)
    reply_to: QuestionReply | None = None
    # Documents the user picked on screen; the answer is looked up only among these.
    document_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)


class QuestionChoice(Contract):
    value: str
    label: str


class Clarification(Contract):
    question_id: str
    kind: str
    message: str
    choices: list[QuestionChoice]


class AnswerTable(Contract):
    columns: list[str]
    rows: list[list[str | None]]


class Evidence(Contract):
    job_id: str
    document_name: str | None = None
    field_id: str | None = None
    target_path: str | None = None
    label: str
    raw: str | None = None
    value: str | None = None
    status: ReadingStatus
    interpreted_as: str | None = None
    crop_url: str | None = None


class AnswerStep(Contract):
    label: str
    value: str


class FieldName(Contract):
    label: str
    kind: Literal["field", "column"]
    table_title: str | None = None


class FieldNameList(Contract):
    contract_version: Literal["structured.v1"] = CONTRACT_VERSION
    items: list[FieldName]


class DocumentRef(Contract):
    job_id: str
    document_name: str | None = None


class QuestionResult(Contract):
    contract_version: Literal["structured.v1"] = CONTRACT_VERSION
    state: Literal["answered", "clarifying", "unanswerable"]
    answer: str
    table: AnswerTable | None = None
    steps: list[AnswerStep] = Field(default_factory=list)
    evidence: list[Evidence]
    caveats: list[str]
    question: Clarification | None = None
    model_calls: int = Field(ge=0)
    documents: list[DocumentRef]
