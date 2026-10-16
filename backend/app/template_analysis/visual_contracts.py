"""Visual meaning proposals over issued cells; no coordinates or storage paths."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.editing.operation_contracts import Contract
from app.template_analysis.contracts import (
    CellRef,
    FieldProposal,
    Heading,
    Issue,
    Name,
    Roles,
    Table,
)


class VisualHeading(Heading):
    # Meaning metadata only. The physical cell's printed text is immutable.
    name: Name | None


class VisualMark(Contract):
    cells: Annotated[list[CellRef], Field(min_length=1, max_length=320)]
    kind: Literal["symbol", "checklist"]
    options: Annotated[list[Name], Field(min_length=1, max_length=50)]
    evidence_refs: Annotated[list[CellRef], Field(max_length=30)]
    # A legend can sit outside detected cells. Retain its quoted wording in
    # the proposal; existence/meaning still need image-based quality evaluation.
    evidence_text: Annotated[str, Field(min_length=1, max_length=300, strict=True)]

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        if len(set(self.cells)) != len(self.cells):
            raise ValueError("DUPLICATE_MARK_CELL")
        if len(set(self.options)) != len(self.options):
            raise ValueError("DUPLICATE_MARK_OPTION")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("DUPLICATE_EVIDENCE_REF")
        if any(not v.strip() or v != v.strip() for v in self.options):
            raise ValueError("INVALID_MARK_OPTION")
        if not self.evidence_text.strip():
            raise ValueError("MISSING_MARK_EVIDENCE")
        if self.kind == "checklist" and any(
            any(c in v for c in "・、,;\n\r") for v in self.options
        ):
            raise ValueError("INVALID_CHECKLIST_OPTION")
        return self


class VisualProposal(Contract):
    version: Literal["template-analysis.visual.v2"]
    roles: Roles
    headings: Annotated[list[VisualHeading], Field(max_length=1000)]
    fields: Annotated[list[FieldProposal], Field(max_length=500)]
    tables: Annotated[list[Table], Field(max_length=100)]
    marks: Annotated[list[VisualMark], Field(max_length=320)]
    unresolved: Annotated[list[Issue], Field(max_length=1000)]
