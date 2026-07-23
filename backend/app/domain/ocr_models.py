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


class DocumentIdentity(OcrModel):
    unified_ear_tag: FieldReading
    cattle_name: FieldReading
    birth_date: FieldReading
    registration_number: FieldReading
    coat_color: FieldReading
    breeder: FieldReading
    owner: FieldReading


class SaleCalf(OcrModel):
    parity: FieldReading
    age_days: FieldReading
    weight_kg: FieldReading
    price_yen: FieldReading
    sire_name: FieldReading
    sale_date: FieldReading
    notes: FieldReading


class Pedigree(OcrModel):
    father: FieldReading
    mother: FieldReading
    paternal_grandfather: FieldReading
    paternal_grandmother: FieldReading
    maternal_grandfather: FieldReading
    maternal_grandmother: FieldReading


class Calving(OcrModel):
    parity: FieldReading
    calving_date: FieldReading
    sex: FieldReading
    outcome: FieldReading
    notes: FieldReading


class ReviewItem(OcrModel):
    code: str = Field(max_length=100)
    path: str = Field(max_length=300)
    message: str = Field(max_length=500)
    severity: str = Field(pattern="^(warning|error)$")
    source_region_id: str | None = Field(default=None, max_length=100)


class ProcessingMetadata(OcrModel):
    model_id: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=100)
    source_regions: list[str] = Field(default_factory=list, max_length=20)
    notes: list[str] = Field(default_factory=list, max_length=50)


class OcrDocument(OcrModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    template_id: str = Field(max_length=100)
    template_version: str = Field(max_length=50)
    document: DocumentIdentity
    sale_calves: list[SaleCalf] = Field(default_factory=list, max_length=100)
    pedigree: Pedigree
    calvings: list[Calving] = Field(default_factory=list, max_length=100)
    review_items: list[ReviewItem] = Field(default_factory=list, max_length=200)
    processing_metadata: ProcessingMetadata
