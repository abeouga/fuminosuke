from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import to_camel


class OcrModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class ReadingStatus(StrEnum):
    READABLE = "readable"
    UNCERTAIN = "uncertain"
    BLANK = "blank"


class BoundingBox(OcrModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=0)
    height: int = Field(ge=0)


class FieldReading(OcrModel):
    raw: str | None = Field(default=None, max_length=500)
    normalized: str | None = Field(default=None, max_length=500)
    status: ReadingStatus
    alternatives: list[str] = Field(default_factory=list, max_length=10)
    review_reason: str | None = Field(default=None, max_length=100)
    source_region_id: str = Field(max_length=100)
    bounding_box: BoundingBox | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_empty_and_status_values(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for key in ("raw", "normalized", "reviewReason"):
            item = normalized.get(key)
            if isinstance(item, str) and not item.strip():
                normalized[key] = None
        status = normalized.get("status")
        if status == ReadingStatus.BLANK.value:
            normalized["raw"] = None
            normalized["normalized"] = None
        elif status == ReadingStatus.UNCERTAIN.value:
            normalized["normalized"] = None
        elif status == ReadingStatus.READABLE.value and normalized.get("raw") is None:
            normalized["status"] = ReadingStatus.UNCERTAIN.value
            normalized["normalized"] = None
        return normalized

    @model_validator(mode="after")
    def validate_status_values(self) -> Self:
        if self.status is ReadingStatus.BLANK and (
            self.raw is not None or self.normalized is not None
        ):
            raise ValueError("blank readings cannot contain values")
        if self.status is ReadingStatus.READABLE and self.raw is None:
            raise ValueError("readable readings require raw text")
        if self.status is ReadingStatus.UNCERTAIN and self.normalized is not None:
            raise ValueError("uncertain readings cannot have a normalized value")
        return self


class ReviewItem(OcrModel):
    code: str = Field(max_length=100)
    path: str = Field(max_length=300)
    message: str = Field(max_length=500)
    severity: str = Field(pattern="^(warning|error)$")
    source_region_id: str | None = Field(default=None, max_length=100)


class ProcessingMetadata(OcrModel):
    model_id: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=100)
    source_regions: list[str] = Field(default_factory=list, max_length=300)
    notes: list[str] = Field(default_factory=list, max_length=50)


class GenericFieldValue(OcrModel):
    field_id: str = Field(max_length=120)
    key: str = Field(max_length=100)
    name: str = Field(max_length=300)
    value_type: str = Field(max_length=20)
    reading: FieldReading


class GenericRecordValue(OcrModel):
    record_id: str = Field(max_length=120)
    name: str = Field(max_length=200)
    order: int = Field(ge=1)
    record_key_value: str | None = Field(default=None, max_length=100)
    fields: list[GenericFieldValue] = Field(default_factory=list, max_length=200)


class GenericSectionValue(OcrModel):
    section_id: str = Field(max_length=120)
    key: str = Field(max_length=100)
    name: str = Field(max_length=300)
    records: list[GenericRecordValue] = Field(default_factory=list, max_length=300)


class GenericDataDocument(OcrModel):
    template_name: str = Field(max_length=300)
    standalone_fields: list[GenericFieldValue] = Field(default_factory=list, max_length=1000)
    sections: list[GenericSectionValue] = Field(default_factory=list, max_length=200)


class OcrDocument(OcrModel):
    schema_version: str = Field(pattern=r"^2\.0$")
    template_id: str = Field(max_length=100)
    template_version: str = Field(max_length=50)
    fields: dict[str, FieldReading] = Field(default_factory=dict, max_length=1000)
    field_labels: dict[str, str] = Field(default_factory=dict, max_length=1000)
    generic_data: GenericDataDocument | None = None
    generic_values: dict[str, object] | None = None
    review_items: list[ReviewItem] = Field(default_factory=list, max_length=1000)
    processing_metadata: ProcessingMetadata

    @model_validator(mode="after")
    def validate_field_keys(self) -> Self:
        from app.domain.field_targets import is_supported_field_path

        if any(not is_supported_field_path(f"fields.{key}") for key in self.fields):
            raise ValueError("Field keys must be valid generic target keys")
        if not set(self.field_labels).issubset(self.fields):
            raise ValueError("Field labels must refer to existing fields")
        return self
