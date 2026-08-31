from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import to_camel
from app.domain.text_encoding import repair_utf8_mojibake


class TemplateDefinitionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class TemplateBox(TemplateDefinitionModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class TemplateMarkOption(TemplateDefinitionModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=100)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @field_validator("label", "value", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value


class TemplateMarkSet(TemplateDefinitionModel):
    id: str = Field(min_length=1, max_length=100)
    target_path: str = Field(min_length=1, max_length=200)
    recognition_mode: Literal["automatic", "manual"] = "automatic"
    selection_mode: Literal["single", "multiple"] = "multiple"
    options: list[TemplateMarkOption] = Field(min_length=1, max_length=50)


class NormalizedBox(TemplateDefinitionModel):
    left: int = Field(ge=0, le=1000)
    top: int = Field(ge=0, le=1000)
    right: int = Field(ge=0, le=1000)
    bottom: int = Field(ge=0, le=1000)


class TemplateCell(TemplateDefinitionModel):
    id: str = Field(min_length=1, max_length=100)
    type: Literal["LABEL", "VALUE", "PARENT_LABEL", "IGNORE"]
    text: str = Field(default="", max_length=300)
    bbox: TemplateBox
    detection_confidence: float = Field(default=1, ge=0, le=1)
    text_confidence: float | None = Field(default=None, ge=0, le=1)
    source_role: str | None = Field(default=None, max_length=40)
    mark_sets: list[TemplateMarkSet] = Field(default_factory=list, max_length=10)

    @field_validator("text", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value


class CellGeometry(TemplateDefinitionModel):
    cell_id: str
    left: list[str] = Field(default_factory=list)
    right: list[str] = Field(default_factory=list)
    above: list[str] = Field(default_factory=list)
    below: list[str] = Field(default_factory=list)
    nearest_left: str | None = None
    nearest_right: str | None = None
    nearest_above: str | None = None
    nearest_below: str | None = None
    same_row_cells: list[str] = Field(default_factory=list)
    same_column_cells: list[str] = Field(default_factory=list)
    horizontal_distance: float | None = Field(default=None, ge=0)
    vertical_distance: float | None = Field(default=None, ge=0)
    row_cluster: int = Field(ge=0)
    column_cluster: int = Field(ge=0)
    contained_cells: list[str] = Field(default_factory=list)
    containing_cell: str | None = None
    normalized_bbox: NormalizedBox
    record_pattern_candidate: bool = False


class TemplateField(TemplateDefinitionModel):
    id: str = Field(min_length=1, max_length=120)
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    name: str = Field(min_length=1, max_length=300)
    label_cell_id: str | None = Field(default=None, max_length=100)
    value_cell_id: str = Field(min_length=1, max_length=100)
    value_type: Literal["text", "integer", "decimal", "date", "boolean", "enum"] = "text"
    required: bool = False
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    legacy_target_path: str | None = Field(default=None, max_length=200)
    mark_set_id: str | None = Field(default=None, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def repair_name_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value


class TemplateRecord(TemplateDefinitionModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    order: int = Field(ge=1)
    bbox: TemplateBox
    fields: list[TemplateField] = Field(default_factory=list, max_length=200)
    record_key_value: str | None = Field(default=None, max_length=100)
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)


class TemplateSection(TemplateDefinitionModel):
    id: str = Field(min_length=1, max_length=120)
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    name: str = Field(min_length=1, max_length=300)
    parent_label_cell_id: str | None = Field(default=None, max_length=100)
    bbox: TemplateBox
    records: list[TemplateRecord] = Field(default_factory=list, max_length=300)
    include_empty_records: bool = False
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)


class InferencePhase(TemplateDefinitionModel):
    phase: Literal["FIELD_BINDING", "RECORD_GROUPING", "SECTION_ASSIGNMENT"]
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list, max_length=30)


class TemplateDefinition(TemplateDefinitionModel):
    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    template_version_id: str = Field(min_length=1, max_length=36)
    template_name: str = Field(min_length=1, max_length=300)
    state: Literal["AI_GENERATED", "USER_REVIEWING", "APPROVED"] = "AI_GENERATED"
    canonical_width: int = Field(gt=0)
    canonical_height: int = Field(gt=0)
    cells: list[TemplateCell] = Field(default_factory=list, max_length=1000)
    geometry: list[CellGeometry] = Field(default_factory=list, max_length=1000)
    standalone_fields: list[TemplateField] = Field(default_factory=list, max_length=1000)
    sections: list[TemplateSection] = Field(default_factory=list, max_length=200)
    unassigned_cell_ids: list[str] = Field(default_factory=list, max_length=1000)
    confidence: float = Field(ge=0, le=1)
    phases: list[InferencePhase] = Field(default_factory=list, max_length=3)
    warnings: list[str] = Field(default_factory=list, max_length=100)


class ValidationIssue(TemplateDefinitionModel):
    severity: Literal["ERROR", "WARNING"]
    code: str = Field(min_length=1, max_length=100)
    path: str = Field(default="", max_length=300)
    message: str = Field(min_length=1, max_length=500)
    cell_ids: list[str] = Field(default_factory=list, max_length=30)


class TemplateDefinitionValidation(TemplateDefinitionModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list, max_length=1000)


class TemplateDefinitionRevisionView(TemplateDefinitionModel):
    id: str
    revision: int = Field(ge=1)
    source: Literal["AI_GENERATED", "HUMAN_REVISION", "APPROVED"]
    definition: TemplateDefinition
    validation: TemplateDefinitionValidation
    created_at: datetime
