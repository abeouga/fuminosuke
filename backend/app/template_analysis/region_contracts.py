"""Explicit input ownership, never an opaque input/label pair selection."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.api.v2.editing_contracts import Sha
from app.editing.geometry_contracts import GeometryOperations, Rectangle, RegionRef
from app.editing.operation_contracts import Contract
from app.template_analysis.contracts import IssueReason, Key, Name
from app.template_analysis.structure_contracts import (
    RegionAnnotation,
    RegionHeading,
    RegionReading,
    RegionRow,
    RegionUnlabelled,
)

POLICY = "template-analysis.region-answer.v1"


class LabelledInput(Contract):
    kind: Literal["labelled"]
    input: RegionRef
    labels: Annotated[list[RegionRef], Field(min_length=1, max_length=30)]
    axis: Literal["horizontal", "vertical"]


class InputField(Contract):
    name: Name
    binding: LabelledInput | RegionUnlabelled


class InputColumn(Contract):
    key: Key
    name: Name
    reading: Key | None
    binding: LabelledInput | RegionUnlabelled


class InputGroup(Contract):
    parent: RegionRef | None
    reading: Key | None
    fields: Annotated[list[InputField], Field(min_length=1, max_length=320)]


class InputTable(Contract):
    key: Key
    name: Name
    parent: RegionRef | None
    columns: Annotated[list[InputColumn], Field(min_length=1, max_length=100)]
    rows: Annotated[list[RegionRow], Field(min_length=1, max_length=300)]
    row_key_strategy: Literal["printed", "position"]
    include_empty_rows: bool = Field(strict=True)


class RegionProblem(Contract):
    cell: RegionRef | None
    bounds: Rectangle | None
    reason: IssueReason | Literal["manual_required", "cross_region"]

    @model_validator(mode="after")
    def one_location(self) -> "RegionProblem":
        if (self.cell is None) == (self.bounds is None):
            raise ValueError("REGION_ISSUE_LOCATION")
        return self


class InputSemantics(Contract):
    readings: Annotated[list[RegionReading], Field(max_length=320)]
    headings: Annotated[list[RegionHeading], Field(max_length=1000)]
    annotations: Annotated[list[RegionAnnotation], Field(max_length=1000)]
    groups: Annotated[list[InputGroup], Field(max_length=320)]
    tables: Annotated[list[InputTable], Field(max_length=100)]
    unresolved: Annotated[list[RegionProblem], Field(max_length=1000)]


class RegionAnswer(Contract):
    version: Literal["template-analysis.region.v1"]
    catalog_digest: Sha
    geometry: GeometryOperations
    semantics: InputSemantics
