from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import to_camel
from app.domain.text_encoding import repair_utf8_mojibake


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
    printed_label_text: str = Field(default="", max_length=300)

    @field_validator("printed_label_text", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value


class MarkOptionConfig(TemplateConfigModel):
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


class CompositeSubfieldConfig(TemplateConfigModel):
    id: str = Field(min_length=1, max_length=100)
    semantic_type: Literal["address_prefecture", "address_municipality"]
    target_path: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=100)
    x_ratio: float = Field(ge=0, le=1)
    y_ratio: float = Field(ge=0, le=1)
    width_ratio: float = Field(gt=0, le=1)
    height_ratio: float = Field(gt=0, le=1)


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
    printed_label_text: str = Field(default="", max_length=300)
    mapping_type: Literal["text", "mark"] = "text"
    mark_recognition_mode: Literal["automatic", "manual"] = "automatic"
    mark_selection_mode: Literal["single", "multiple"] = "multiple"
    mark_options: list[MarkOptionConfig] = Field(default_factory=list, max_length=50)
    composite_subfields: list[CompositeSubfieldConfig] = Field(
        default_factory=list,
        max_length=10,
    )

    @field_validator("label", "printed_label_text", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value

    def as_region(self) -> RegionConfig:
        return RegionConfig(
            region_id=self.mapping_id,
            x=self.x,
            y=self.y,
            width=self.width,
            height=self.height,
            padding=self.padding,
            field_type="MAPPED_MARK" if self.mapping_type == "mark" else "MAPPED_FIELD",
            expected_fields=[self.target_path],
            validator_names=[],
            model_detail=self.model_detail,
            enabled=self.enabled,
            fallback_enabled=False,
            mark_detection_enabled=(
                self.mapping_type == "mark"
                and self.mark_recognition_mode == "automatic"
            ),
            printed_label_text=self.printed_label_text,
        )


class DocumentIdentityConfig(TemplateConfigModel):
    mapping_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    target_path: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=100)
    value_type: Literal["text", "digits"] = "text"
    exact_length: int | None = Field(default=None, ge=1, le=100)
    match_role: Literal["primary", "confirmation"] = "primary"
    display_role: Literal["title", "subtitle", "none"] = "title"
    match_group_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    match_group_label: str | None = Field(default=None, min_length=1, max_length=100)
    component_order: int = Field(default=0, ge=0, le=100)

    @field_validator("label", mode="before")
    @classmethod
    def repair_text_encoding(cls, value: object) -> object:
        return repair_utf8_mojibake(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_format(self) -> DocumentIdentityConfig:
        if self.value_type == "text" and self.exact_length is not None:
            raise ValueError("exactLength is supported only for digit identifiers")
        return self


class DocumentNameConfig(TemplateConfigModel):
    field_paths: list[str] = Field(min_length=1, max_length=20)
    separator: str = Field(default="", max_length=20)
    prefix: str = Field(default="", max_length=100)
    suffix: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def validate_fields(self) -> DocumentNameConfig:
        cleaned = [path.strip() for path in self.field_paths]
        if any(not path for path in cleaned):
            raise ValueError("documentName fieldPaths cannot contain blank values")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("documentName fieldPaths must be unique")
        self.field_paths = cleaned
        return self


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
    document_identity: DocumentIdentityConfig | None = None
    document_identity_fields: list[DocumentIdentityConfig] = Field(
        default_factory=list,
        max_length=20,
    )
    document_name: DocumentNameConfig | None = None
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
            if mapping.mapping_type == "mark" and not mapping.mark_options:
                raise ValueError(f"Mark mapping {mapping.mapping_id} has no options")
            option_ids = [option.id for option in mapping.mark_options]
            option_labels = [option.label for option in mapping.mark_options]
            option_values = [option.value for option in mapping.mark_options]
            if len(option_ids) != len(set(option_ids)):
                raise ValueError(f"Mark mapping {mapping.mapping_id} has duplicate option IDs")
            if len(option_labels) != len(set(option_labels)):
                raise ValueError(f"Mark mapping {mapping.mapping_id} has duplicate option labels")
            if len(option_values) != len(set(option_values)):
                raise ValueError(f"Mark mapping {mapping.mapping_id} has duplicate option values")
            for option in mapping.mark_options:
                if option.x + option.width > mapping.width:
                    raise ValueError(f"Mark option {option.id} exceeds mapping width")
                if option.y + option.height > mapping.height:
                    raise ValueError(f"Mark option {option.id} exceeds mapping height")
        identity_fields = self.effective_document_identity_fields()
        if identity_fields:
            identity_target_paths = [field.target_path for field in identity_fields]
            if len(set(identity_target_paths)) != len(identity_target_paths):
                raise ValueError("documentIdentityFields targetPath values must be unique")
            grouped_fields = [field for field in identity_fields if field.match_group_id]
            if grouped_fields:
                if len(grouped_fields) != len(identity_fields):
                    raise ValueError(
                        "documentIdentityFields cannot mix grouped and legacy matching"
                    )
                for field in grouped_fields:
                    if not field.match_group_label:
                        raise ValueError("grouped identity fields require matchGroupLabel")
            else:
                primary_count = sum(field.match_role == "primary" for field in identity_fields)
                combined_confirmation = primary_count == 0 and len(identity_fields) >= 2
                if primary_count != 1 and not combined_confirmation:
                    raise ValueError(
                        "documentIdentityFields must contain exactly one primary field or "
                        "at least two confirmation fields"
                    )
                if sum(field.display_role == "title" for field in identity_fields) != 1:
                    raise ValueError(
                        "documentIdentityFields must contain exactly one title field"
                    )
                if sum(field.display_role == "subtitle" for field in identity_fields) > 1:
                    raise ValueError(
                        "documentIdentityFields can contain at most one subtitle field"
                    )
            for identity_field in identity_fields:
                identity_mapping = next(
                    (
                        mapping
                        for mapping in self.field_mappings
                        if mapping.mapping_id == identity_field.mapping_id
                    ),
                    None,
                )
                if identity_mapping is None:
                    raise ValueError("documentIdentityFields references an unknown mappingId")
                if not identity_mapping.enabled:
                    raise ValueError("documentIdentityFields references a disabled mapping")
                if identity_mapping.target_path != identity_field.target_path:
                    raise ValueError("documentIdentityFields targetPath does not match its mapping")
                if identity_mapping.mapping_type != "text":
                    raise ValueError("documentIdentityFields must reference text mappings")
            primary = next(
                (field for field in identity_fields if field.match_role == "primary"),
                None,
            )
            if self.document_identity is not None and (
                not grouped_fields
                and (
                    primary is None
                    or self.document_identity.mapping_id != primary.mapping_id
                    or self.document_identity.target_path != primary.target_path
                )
            ):
                raise ValueError("documentIdentity must match the primary documentIdentityField")
        if self.document_name is not None:
            configured_paths = {
                mapping.target_path for mapping in self.field_mappings if mapping.enabled
            }
            unknown_name_path = next(
                (path for path in self.document_name.field_paths if path not in configured_paths),
                None,
            )
            if unknown_name_path is not None:
                raise ValueError("documentName references an unknown field path")
        for mark in self.marks:
            if mark.region_id not in region_ids:
                raise ValueError(f"Mark {mark.mark_id} references an unknown region")
        return self

    def effective_document_identity_fields(self) -> list[DocumentIdentityConfig]:
        if self.document_identity_fields:
            return list(self.document_identity_fields)
        return [self.document_identity] if self.document_identity is not None else []

    def ocr_regions(self) -> list[RegionConfig]:
        if self.field_mappings:
            return self.mapped_ocr_regions()
        return [region for region in self.regions if region.enabled]

    def mapped_ocr_regions(self) -> list[RegionConfig]:
        return [mapping.as_region() for mapping in self.field_mappings if mapping.enabled]


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


def build_blank_template_config(
    *,
    template_id: str,
    template_version: int,
    name: str,
    canonical_width: int,
    canonical_height: int,
) -> TemplateConfig:
    base = load_template_config()
    config = base.model_copy(
        update={
            "template_id": template_id,
            "template_version": template_version,
            "name": name,
            "canonical_width": canonical_width,
            "canonical_height": canonical_height,
            "regions": [],
            "field_mappings": [],
            "document_identity": None,
            "document_identity_fields": [],
            "marks": [],
            "export": base.export.model_copy(update={"csv_columns": []}, deep=True),
        },
        deep=True,
    )
    return TemplateConfig.model_validate(
        config.model_dump(mode="json", by_alias=True)
    )


def parse_template_config(content: str) -> TemplateConfig:
    return TemplateConfig.model_validate(yaml.safe_load(content))
