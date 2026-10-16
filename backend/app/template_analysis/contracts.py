from typing import Annotated, Any, Literal

from pydantic import Field

from app.api.v2.editing_contracts import Binding, Focus, Id, PageScope, Resource, Sha
from app.editing.facet_contracts import FacetTouch
from app.editing.geometry_contracts import Rectangle
from app.editing.manual_draft import ManualDraft
from app.editing.operation_contracts import Contract, Handle

CellRef = Annotated[str, Field(pattern=r"^c[1-9][0-9]{0,3}$", strict=True)]
Key = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$", strict=True)]
Name = Annotated[str, Field(min_length=1, max_length=100, strict=True)]
Cells = Annotated[list[CellRef], Field(max_length=1000)]
Labels = Annotated[list[CellRef], Field(max_length=30)]


class Roles(Contract):
    label: Cells
    parent_label: Cells = Field(alias="parent_label")
    input: Cells
    mark: Cells
    record_key_cell: Cells = Field(alias="record_key_cell")
    column_key_cell: Cells = Field(alias="column_key_cell")
    ignored: Cells
    unknown: Cells


class Heading(Contract):
    cell: CellRef
    parent: CellRef | None


class FieldProposal(Contract):
    key: Key
    cell: CellRef
    name: Name
    labels: Labels
    axis: Literal["horizontal", "vertical", "direct"] = Field(
        description="labelsが空ならdirect。labelsが一つ以上あればhorizontal又はvertical。"
    )
    parent: CellRef | None


class Column(Contract):
    key: Key
    name: Name
    labels: Labels


class Row(Contract):
    cells: Annotated[list[CellRef], Field(min_length=1, max_length=100)]
    key_cell: CellRef | None
    key_value: Name | None


class Table(Contract):
    key: Key
    name: Name
    parent: CellRef | None
    columns: Annotated[list[Column], Field(min_length=1, max_length=100)]
    rows: Annotated[list[Row], Field(min_length=1, max_length=300)]
    row_key_strategy: Literal["printed", "position"]
    include_empty_rows: bool = Field(strict=True)


IssueReason = Literal[
    "ambiguous_role",
    "ambiguous_binding",
    "ambiguous_parent",
    "missing_evidence",
    "missing_binding",
    "unsupported_mark_definition",
    "unsupported_structure",
    "protected_existing",
]


class Issue(Contract):
    cell: CellRef
    reason: IssueReason


class PageProposal(Contract):
    version: Literal["template-analysis.graph.v1"]
    roles: Roles
    headings: Annotated[list[Heading], Field(max_length=1000)]
    fields: Annotated[list[FieldProposal], Field(max_length=500)]
    tables: Annotated[list[Table], Field(max_length=100)]
    unresolved: Annotated[list[Issue], Field(max_length=1000)]


class AnalysisCreate(Contract):
    contract_version: Literal["template-analysis.v3"]
    request_id: Id
    binding: Binding
    draft: ManualDraft
    task: Literal["full", "roles", "structure"]
    page_scope: PageScope
    selected_table_id: str | None = Field(max_length=128)
    release_role_protection: list[Handle] = Field(default_factory=list, max_length=1000)
    manual_touched_facets: list[FacetTouch] = Field(default_factory=list, max_length=5000)
    priority_bounds: (
        Annotated[
            list[Annotated[int, Field(ge=0, le=20000, strict=True)]],
            Field(min_length=4, max_length=4),
        ]
        | None
    ) = None

    region_scope: Rectangle | None = None


class AnalysisPlanRegion(Contract):
    bounds: Rectangle
    cell_ids: list[str]
    state: Literal["ready", "held"]
    reason: str | None


class AnalysisPlanResult(Contract):
    contract_version: Literal["template-analysis.v3"]
    resource: Resource
    base_revision: int
    draft_digest: Sha
    regions: list[AnalysisPlanRegion]


class AnalysisMergeRequest(Contract):
    contract_version: Literal["template-analysis.v3"]
    request_id: Id
    binding: Binding
    draft: ManualDraft
    selected_unit_ids: list[Id] = Field(min_length=1, max_length=1000)
    manual_touched_facets: list[FacetTouch] = Field(default_factory=list, max_length=5000)
    parent_merge_id: Id | None
    unit_digests: dict[str, Sha]


class AnalysisRevertRequest(Contract):
    contract_version: Literal["template-analysis.v3"]
    request_id: Id
    binding: Binding


class AnalysisError(Contract):
    code: str
    message: str


class RolePreview(Contract):
    cell_id: str
    before: str
    after: str


class GeometryCell(Contract):
    id: str
    x: int
    y: int
    width: int
    height: int


class GeometryPreview(Contract):
    before: list[GeometryCell]
    after: list[GeometryCell]
    added_ids: list[str]
    removed_ids: list[str]
    changed_ids: list[str]
    reference_map: dict[str, str]


class EditUnit(Contract):
    unit_id: Id
    page_id: str | None
    kind: Literal["role", "field", "table", "heading", "structure"]
    cell_ids: list[str]
    name: str
    state: Literal["mergeable", "protected"]
    reason: str | None
    dependencies: list[Id]
    role_changes: list[RolePreview] = Field(default_factory=list)
    details: list[str] = Field(default_factory=list)
    geometry: GeometryPreview | None = None
    region_id: str | None = None
    proposal_digest: Sha | None = None
    display_order: int | None = Field(default=None, ge=0)


class UnresolvedCell(Contract):
    page_id: str | None
    cell_id: str
    reason: str
    bounds: GeometryCell | None = None


class PagePreview(Contract):
    units: list[EditUnit]
    unresolved: list[UnresolvedCell]
    coverage: dict[str, int]
    focus: Focus | None


class AnalysisProposal(PagePreview):
    proposal_digest: Sha


class PageResult(Contract):
    page_id: str | None
    state: Literal["queued", "running", "ready", "partial", "failed"]
    planned_calls: int
    reserved_calls: int
    completed_calls: int
    metrics: dict[str, Any]
    error: AnalysisError | None
    proposal: PagePreview | None


class AnalysisResult(Contract):
    contract_version: Literal["template-analysis.v3"]
    region_scope: Rectangle | None = None
    analysis_id: Id
    request_id: Id
    resource: Resource
    base_revision: int
    snapshot_digest: Sha
    draft_digest: Sha
    scope_digest: Sha
    state: Literal[
        "queued",
        "running",
        "validating",
        "ready",
        "partial",
        "failed",
        "cancelled",
        "expired",
        "stale",
    ]
    pages: list[PageResult]
    planned_calls: int
    reserved_calls: int
    completed_calls: int
    expires_at: str
    last_sequence: int
    proposal: AnalysisProposal | None
    error: AnalysisError | None


class MergeConflict(Contract):
    unit_id: Id
    code: Literal["MANUAL_CONFLICT"]


class MergeResult(Contract):
    contract_version: Literal["template-analysis.v3"]
    analysis_id: Id
    merge_id: Id
    parent_merge_id: Id | None = None
    base_revision: int
    input_draft_digest: Sha
    output_draft_digest: Sha
    draft: ManualDraft
    applied_unit_ids: list[Id]
    conflicts: list[MergeConflict]
    unresolved: list[UnresolvedCell]
    focus: Focus | None
    substitutions: dict[str, str]


class AnalysisEventResult(Contract):
    contract_version: Literal["template-analysis.v3"]
    analysis_id: Id
    snapshot_digest: Sha
    sequence: int
    type: Literal[
        "accepted",
        "page_started",
        "page_validated",
        "region_validated",
        "progress",
        "ready",
        "partial",
        "failed",
        "cancelled",
        "expired",
        "stale",
    ]
    emitted_at: str
    planned_calls: int | None = None
    page_id: str | None = None
    state: str | None = None
    unit_count: int | None = None
    unresolved_count: int | None = None
    proposal_digest: Sha | None = None
    error: AnalysisError | None = None
