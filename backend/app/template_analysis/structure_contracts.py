"""One closed response for physical intent and dependent semantic intent."""

from typing import Annotated, Literal

from pydantic import Field

from app.api.v2.editing_contracts import Sha
from app.editing.geometry_contracts import GeometryOperations, RegionRef
from app.editing.operation_contracts import Contract
from app.template_analysis.binding_resolution import LinkedBinding
from app.template_analysis.contracts import IssueReason, Key, Name

POLICY = "template-analysis.structure-answer.v1"


class RegionEvidence(Contract):
    basis: Literal["free_entry", "in_cell_caption"]
    text: Annotated[str, Field(min_length=1, max_length=300, strict=True)]
    refs: Annotated[list[RegionRef], Field(min_length=1, max_length=30)]


class RegionUnlabelled(Contract):
    kind: Literal["unlabelled"]
    cell: RegionRef
    evidence: RegionEvidence


class DerivedLink(Contract):
    kind: Literal["derived_link"]
    input: RegionRef
    labels: Annotated[list[RegionRef], Field(min_length=1, max_length=30)]
    axis: Literal["horizontal", "vertical"]


RegionBinding = LinkedBinding | RegionUnlabelled | DerivedLink


class RegionField(Contract):
    name: Name
    binding: RegionBinding


class RegionGroup(Contract):
    parent: RegionRef | None
    reading: Key | None
    fields: Annotated[list[RegionField], Field(min_length=1, max_length=320)]


class RegionColumn(Contract):
    key: Key
    name: Name
    reading: Key | None
    binding: RegionBinding


class RegionRow(Contract):
    cells: Annotated[list[RegionRef], Field(min_length=1, max_length=100)]
    key_cell: RegionRef | None
    key_value: Name | None


class RegionTable(Contract):
    key: Key
    name: Name
    parent: RegionRef | None
    columns: Annotated[list[RegionColumn], Field(min_length=1, max_length=100)]
    rows: Annotated[list[RegionRow], Field(min_length=1, max_length=300)]
    row_key_strategy: Literal["printed", "position"]
    include_empty_rows: bool = Field(strict=True)


class RegionHeading(Contract):
    cell: RegionRef
    parent: RegionRef | None
    name: Name | None


class RegionReading(Contract):
    key: Key
    kind: Literal["symbol", "checklist"]
    options: Annotated[list[Name], Field(min_length=1, max_length=50)]
    evidence_refs: Annotated[list[RegionRef], Field(max_length=30)]
    evidence_text: Annotated[str, Field(min_length=1, max_length=300, strict=True)]


class RegionAnnotation(Contract):
    cell: RegionRef
    role: Literal["label", "ignored", "record_key_cell", "column_key_cell"]


class RegionIssue(Contract):
    cell: RegionRef
    reason: IssueReason


class StructureSemantics(Contract):
    readings: Annotated[list[RegionReading], Field(max_length=320)]
    groups: Annotated[list[RegionGroup], Field(max_length=320)]
    headings: Annotated[list[RegionHeading], Field(max_length=1000)]
    annotations: Annotated[list[RegionAnnotation], Field(max_length=1000)]
    tables: Annotated[list[RegionTable], Field(max_length=100)]
    unresolved: Annotated[list[RegionIssue], Field(max_length=1000)]


class StructureAnswer(Contract):
    version: Literal["template-analysis.structure.v1"]
    catalog_digest: Sha
    geometry: GeometryOperations
    semantics: StructureSemantics
