from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import to_camel


class TemplateConfigModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class RegistrationConfig(TemplateConfigModel):
    preferred_method: str
    min_feature_matches: int = Field(ge=4)
    min_inliers: int = Field(ge=4)
    min_inlier_ratio: float = Field(ge=0, le=1)
    max_reprojection_error_px: float = Field(gt=0)
    min_ecc_score: float = Field(ge=0, le=1)


class RegionConfig(TemplateConfigModel):
    region_id: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    padding: int = Field(ge=0)
    field_type: str
    expected_fields: list[str]
    validator_names: list[str]
    model_detail: str
    enabled: bool
    fallback_enabled: bool
    mark_detection_enabled: bool


class FieldMappingConfig(TemplateConfigModel):
    mapping_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    target_path: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=100)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    padding: int = Field(default=4, ge=0, le=50)
    enabled: bool = True
    model_detail: str = "high"

    def as_region(self) -> RegionConfig:
        return RegionConfig(
            region_id=self.mapping_id,
            x=self.x,
            y=self.y,
            width=self.width,
            height=self.height,
            padding=self.padding,
            field_type="MAPPED_FIELD",
            expected_fields=[self.target_path],
            validator_names=[],
            model_detail=self.model_detail,
            enabled=self.enabled,
            fallback_enabled=False,
            mark_detection_enabled=False,
        )


class MarkConfig(TemplateConfigModel):
    mark_id: str
    region_id: str
    option_group: str
    option_value: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    method: str


class ExportConfig(TemplateConfigModel):
    csv_columns: list[str]


class TemplateConfig(TemplateConfigModel):
    template_id: str
    template_version: int
    name: str
    canonical_width: int = Field(gt=0)
    canonical_height: int = Field(gt=0)
    registration: RegistrationConfig
    regions: list[RegionConfig]
    field_mappings: list[FieldMappingConfig] = Field(default_factory=list)
    marks: list[MarkConfig]
    export: ExportConfig

    @model_validator(mode="after")
    def validate_geometry(self) -> TemplateConfig:
        region_ids = {region.region_id for region in self.regions}
        if len(region_ids) != len(self.regions):
            raise ValueError("regionId values must be unique")
        for region in self.regions:
            if region.x + region.width > self.canonical_width:
                raise ValueError(f"Region {region.region_id} exceeds canonical width")
            if region.y + region.height > self.canonical_height:
                raise ValueError(f"Region {region.region_id} exceeds canonical height")
        mapping_ids = {mapping.mapping_id for mapping in self.field_mappings}
        target_paths = {mapping.target_path for mapping in self.field_mappings}
        if len(mapping_ids) != len(self.field_mappings):
            raise ValueError("mappingId values must be unique")
        if len(target_paths) != len(self.field_mappings):
            raise ValueError("targetPath values must be unique")
        for mapping in self.field_mappings:
            if mapping.x + mapping.width > self.canonical_width:
                raise ValueError(f"Field mapping {mapping.mapping_id} exceeds canonical width")
            if mapping.y + mapping.height > self.canonical_height:
                raise ValueError(f"Field mapping {mapping.mapping_id} exceeds canonical height")
        for mark in self.marks:
            if mark.region_id not in region_ids:
                raise ValueError(f"Mark {mark.mark_id} references an unknown region")
        return self

    def ocr_regions(self) -> list[RegionConfig]:
        if self.field_mappings:
            return [mapping.as_region() for mapping in self.field_mappings if mapping.enabled]
        return [region for region in self.regions if region.enabled]


def default_template_config_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "template.yaml"
    )


def load_template_config(path: Path | None = None) -> TemplateConfig:
    config_path = path or default_template_config_path()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return TemplateConfig.model_validate(raw)


def parse_template_config(content: str) -> TemplateConfig:
    return TemplateConfig.model_validate(yaml.safe_load(content))
