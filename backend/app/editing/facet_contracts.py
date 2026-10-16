"""Finite authoring facets, shared by hand editing and analysis provenance."""

from typing import Literal, Self

from pydantic import Field, model_validator

from app.editing.operation_contracts import Contract, Handle

Facet = Literal["role", "meaning", "binding", "parent", "records", "reading", "mark", "geometry"]


class FacetTarget(Contract):
    handle: Handle | None = None
    draft_entity_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def exclusive(self) -> Self:
        if (self.handle is None) == (self.draft_entity_id is None):
            raise ValueError("Specify exactly one handle or draftEntityId")
        return self


class FacetTouch(Contract):
    target: FacetTarget
    facet: Facet
