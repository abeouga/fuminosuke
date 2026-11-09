"""Wire contracts for structured-document.v1 (confirmed values on the accepted structure)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.editing.operation_contracts import Contract

SCHEMA_VERSION: Literal["structured-document.v1"] = "structured-document.v1"
CONTRACT_VERSION: Literal["structured.v1"] = "structured.v1"

ReadingStatus = Literal["readable", "uncertain", "blank"]
DocumentKind = Literal["single", "paged"]


class Box(Contract):
    """A cell's rectangle on its page image (aligned-image pixels)."""

    x: float
    y: float
    width: float
    height: float


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
    crop_url: str | None = None
    box: Box | None = None
    # A selection box (選択欄): its printed choices are not repeated in the shown name.
    choice: bool = False
    # Its choice names; a repeated one is stored numbered (黒2) and shown plain (黒).
    choice_labels: list[str] | None = None


class ExtraReading(Contract):
    key: str
    label: str
    raw: str | None = None
    normalized: str | None = None
    value: str | None = None
    status: ReadingStatus


class JoinedReading(Contract):
    """Items shown as one value (the template's 「項目のまとめ方」); display only."""

    id: str
    name: str
    section: str | None = None
    value: str | None = None
    status: ReadingStatus
    field_ids: list[str]


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
    # Small image of the (first) page, for lists.
    thumbnail_url: str | None = None


class DocumentPage(Contract):
    page_id: str | None = None
    page_number: int = Field(ge=1)
    image_url: str


class HeadingCell(Contract):
    """A printed heading or item name on the page, and what it names on the shelf."""

    cell_id: str
    kind: Literal["section", "table", "column", "item"]
    box: Box
    page_id: str | None = None
    section: str | None = None
    table_ids: list[str] = Field(default_factory=list)
    column_id: str | None = None
    field_ids: list[str] = Field(default_factory=list)


class ItemGroupRef(Contract):
    """Items under one printed item name (「登録No.」), shown nested."""

    name: str
    field_ids: list[str] = Field(default_factory=list)


class ReviewBlockRef(Contract):
    """A table (``id`` = table id), a heading, or a run of items under no heading."""

    kind: Literal["record", "parent", "independent"]
    id: str | None = None
    field_ids: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    # On a table: its heading's items outside the rows, shown with the table.
    attached_id: str | None = None
    attached_field_ids: list[str] = Field(default_factory=list)
    item_groups: list[ItemGroupRef] = Field(default_factory=list)


class StructuredDocument(DocumentSummary):
    schema_version: Literal["structured-document.v1"] = SCHEMA_VERSION
    pages: list[DocumentPage] = Field(default_factory=list)
    fields: list[StructuredReading]
    tables: list[StructuredTable]
    extra_fields: list[ExtraReading]
    joined_fields: list[JoinedReading] = Field(default_factory=list)
    headings: list[HeadingCell] = Field(default_factory=list)
    # Tables, headings and items under no heading from the top of the form (display order).
    blocks: list[ReviewBlockRef] | None = None


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
    # One of the offered choices, or the user's own words when no choice fits.
    value: str | None = Field(default=None, min_length=1, max_length=300)
    text: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _one_answer(self) -> QuestionReply:
        if (self.value is None) == (self.text is None or not self.text.strip()):
            raise ValueError("value か text のどちらか一方を指定してください。")
        return self


class QuestionRequest(Contract):
    # Empty text is valid only with replyTo (the engine reuses the pending question's text).
    text: str = Field(default="", max_length=2000)
    reply_to: QuestionReply | None = None
    # Documents the user picked on screen; the answer is looked up only among these.
    document_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)
    # The conversation's last answer, to fill in what a follow-up leaves out.
    context: QuestionContext | None = None
    # Where it was sent from in the chat, only for the local chat log (never used to answer).
    client: ChatClient | None = None


class QuestionContext(Contract):
    # The documents the last answer was about (a question naming none is about them).
    document_ids: list[str] = Field(default_factory=list, max_length=20)
    # The question that answer was for.
    previous: str | None = Field(default=None, max_length=2000)
    # That answer's text (what 「それ」 or a complaint is about).
    previous_answer: str | None = Field(default=None, max_length=2000)


class ChatClient(Contract):
    conversation_id: str = Field(min_length=1, max_length=100)
    turn_id: int = Field(ge=0)
    # Sent again on its own after the server forgot the open question.
    recovered: bool = False


class ChatTurnRecord(Contract):
    turn_id: int = Field(ge=0)
    text: str = Field(default="", max_length=2000)
    reply: bool = False
    state: str | None = Field(default=None, max_length=40)
    answer: str | None = Field(default=None, max_length=4000)
    pending: bool = False


class ChatEvent(Contract):
    """Something done in the chat that the server does not otherwise see (for the local log).

    `rating`: the user's 👍/👎 on an answer (None = taken back); with 👎 the reason picked and,
    for "other", what they said or typed.
    """

    type: Literal["remove", "rating"]
    conversation_id: str = Field(min_length=1, max_length=100)
    turns: list[ChatTurnRecord] = Field(default_factory=list, max_length=60)
    rating: Literal["good", "bad"] | None = None
    reason: Literal["wrong_value", "not_wanted", "unclear", "other"] | None = None
    comment: str | None = Field(default=None, max_length=1000)
    comment_by: Literal["voice", "text"] | None = None


class QuestionChoice(Contract):
    value: str
    label: str
    # A document choice also says which form it is and when it was confirmed, with its picture.
    detail: str | None = None
    thumbnail_url: str | None = None


class Clarification(Contract):
    question_id: str
    kind: str
    message: str
    choices: list[QuestionChoice]
    # True when an answer is already given and the choices only offer a different reading.
    optional: bool = False


class AnswerRowSource(Contract):
    job_id: str
    field_id: str | None = None


class AnswerTable(Contract):
    columns: list[str]
    rows: list[list[str | None]]
    # The cell each row was read from (same order as rows).
    sources: list[AnswerRowSource] = Field(default_factory=list)


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
    # How the question was read (field, calculation, documents) when it was not certain.
    notes: list[str] = Field(default_factory=list)
    question: Clarification | None = None
    model_calls: int = Field(ge=0)
    documents: list[DocumentRef]
    # A general answer to a question the documents cannot answer (only values put in from them).
    general: bool = False
    # The document the user asked to see (「はじめを出して」): opened beside the chat.
    open: str | None = None


class ConversationSave(Contract):
    """A chat conversation as the chat page keeps it, saved so the user's other devices see it.

    The turns are the page's own records (questions, answers, ratings); the server keeps them
    as they are and only checks their size.
    """

    title: str = Field(max_length=200)
    updated_at: int = Field(ge=0)
    turns: list[dict[str, Any]] = Field(max_length=60)
    images: list[str] = Field(default_factory=list, max_length=100)


# --- Confirmed documents filtered and aggregated (for the MCP server and other callers) ---

AggregationOperation = Literal["sum", "avg", "count", "max", "min"]


class FieldCondition(Contract):
    """A field outside any table whose value equals ``value`` (compared as the chat does)."""

    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=200)


class DocumentFilter(Contract):
    """Which confirmed documents (newest version of each, none from the trash) to look at."""

    template_id: str | None = Field(default=None, max_length=100)
    document_ids: list[str] | None = Field(default=None, max_length=200)
    # A document's name or identity value, as the list's ``q``.
    query: str | None = Field(default=None, max_length=200)
    field_equals: list[FieldCondition] = Field(default_factory=list, max_length=3)
    # The confirmed date in Japan time, both ends included.
    confirmed_from: date | None = None
    confirmed_to: date | None = None


class DocumentSearch(DocumentFilter):
    limit: int = Field(default=20, ge=1, le=200)


class DocumentSearchItem(DocumentSummary):
    # The fields outside the tables that met ``field_equals``, in condition order (empty
    # without conditions).
    matched_field_ids: list[str] = Field(default_factory=list)


class DocumentSearchResult(Contract):
    contract_version: Literal["structured.v1"] = CONTRACT_VERSION
    # The list's summaries, newest first, at most ``limit``.
    items: list[DocumentSearchItem]
    # How many matched (before ``limit``).
    total: int = Field(ge=0)
    # More documents than are read (DOCUMENT_LIMIT) matched the template, name, ids and dates.
    truncated: bool


class FieldSelector(Contract):
    """A field outside any table (``section`` narrows it to one heading), or with
    ``table_title`` a column of that table."""

    label: str = Field(min_length=1, max_length=100)
    section: str | None = Field(default=None, max_length=100)
    table_title: str | None = Field(default=None, max_length=100)


class AggregationRequest(DocumentFilter):
    field: FieldSelector
    operation: AggregationOperation


class AggregationItem(Contract):
    job_id: str
    document_name: str | None = None
    field_id: str | None = None
    label: str
    section: str | None = None
    table_title: str | None = None
    row_number: int | None = None
    page_id: str | None = None
    value: str | None = None
    # The number the value was read as (a string: no float rounding).
    number: str | None = None
    # How it was read when not plainly: 「.」 as a thousands separator, as a decimal point,
    # or not a number at all.
    note: str | None = None


class AggregationResult(Contract):
    contract_version: Literal["structured.v1"] = CONTRACT_VERSION
    operation: AggregationOperation
    field: FieldSelector
    # Null when no value could be read as a number (sum, avg, max, min).
    result: str | None
    count_used: int = Field(ge=0)
    blank: int = Field(ge=0)
    # The values used (for count: every written value); at most ITEM_LIMIT of each.
    used: list[AggregationItem]
    excluded: list[AggregationItem]
    # For max / min: the values that are the result.
    at: list[AggregationItem] = Field(default_factory=list)
    documents: int = Field(ge=0)
    # More documents matched than are read (DOCUMENT_LIMIT), or more items than are listed.
    truncated: bool
