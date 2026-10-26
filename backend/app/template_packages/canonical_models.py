from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from app.core.errors import to_camel
from app.domain.enums import TemplateMode
from app.domain.template_candidates import (
    MAX_TEMPLATE_CANDIDATE_BINDING_COUNT,
    MAX_TEMPLATE_CANDIDATE_NAME_LENGTH,
    MAX_TEMPLATE_CANDIDATE_SET_COUNT,
    MAX_TEMPLATE_CANDIDATE_VALUE_COUNT,
    MAX_TEMPLATE_CANDIDATE_VALUE_LENGTH,
    clean_candidate_text,
    normalize_candidate_target,
    normalize_candidate_text,
)
from app.domain.template_config import (
    DocumentIdentityConfig,
    DocumentNameConfig,
    FieldMappingConfig,
    MarkConfig,
    RegionConfig,
    RegistrationConfig,
    TemplateConfig,
)
from app.domain.template_definition import (
    CellGeometry,
    InferencePhase,
    TemplateCell,
    TemplateDefinition,
    TemplateField,
    TemplateSection,
)
from app.layout_analysis.models import LayoutDefinition
from app.template_packages.feature_registry import TemplatePackageFeature
from app.template_packages.package_paths import validate_package_path
from app.validation.template_definition_validator import validate_template_definition

CANONICAL_TEMPLATE_SCHEMA_VERSION = 2
CANONICAL_QUALITY_SCHEMA_VERSION = 1
CANONICAL_OUTPUT_LAYOUT_SCHEMA_VERSION = 1
PACKAGE_INTERNAL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


class CanonicalModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class CanonicalRuntimeConfig(CanonicalModel):
    registration: RegistrationConfig
    regions: list[RegionConfig] = Field(default_factory=list, max_length=1000)
    field_mappings: list[FieldMappingConfig] = Field(default_factory=list, max_length=300)
    document_identity_fields: list[DocumentIdentityConfig] = Field(
        default_factory=list,
        max_length=20,
    )
    document_name: DocumentNameConfig | None = None
    marks: list[MarkConfig] = Field(default_factory=list, max_length=300)


class CanonicalSpreadsheetSource(CanonicalModel):
    sheet_name: str = Field(min_length=1, max_length=200)
    file_name: str = Field(min_length=1, max_length=255)

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        if value != value.strip(" .") or not value.lower().endswith(".xlsx"):
            raise ValueError("Spreadsheet source fileName must be a portable .xlsx name")
        if re.search(r'[\\/:*?"<>|\x00-\x1f]', value):
            raise ValueError("Spreadsheet source fileName contains a forbidden character")
        validate_package_path(f"metadata/{value}")
        return value


class CanonicalApprovedDefinition(CanonicalModel):
    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
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

    def as_runtime_definition(self, *, template_name: str) -> TemplateDefinition:
        return TemplateDefinition(
            schema_version=self.schema_version,
            template_version_id="package-version",
            template_name=template_name,
            state="APPROVED",
            canonical_width=self.canonical_width,
            canonical_height=self.canonical_height,
            cells=self.cells,
            geometry=self.geometry,
            standalone_fields=self.standalone_fields,
            sections=self.sections,
            unassigned_cell_ids=self.unassigned_cell_ids,
            confidence=self.confidence,
            phases=self.phases,
            warnings=self.warnings,
        )


class CanonicalCandidateSet(CanonicalModel):
    id: str = Field(min_length=1, max_length=128, pattern=PACKAGE_INTERNAL_ID_PATTERN)
    name: str = Field(min_length=1, max_length=MAX_TEMPLATE_CANDIDATE_NAME_LENGTH)
    values: list[str] = Field(
        min_length=1,
        max_length=MAX_TEMPLATE_CANDIDATE_VALUE_COUNT,
    )
    allowed_suffixes: list[str] = Field(default_factory=list, max_length=30)
    target_patterns: list[str] = Field(default_factory=list, max_length=300)

    @model_validator(mode="after")
    def validate_candidate_set(self) -> Self:
        self.name = clean_candidate_text(
            self.name,
            maximum=MAX_TEMPLATE_CANDIDATE_NAME_LENGTH,
        )
        self.values = self._clean_unique(self.values, required=True)
        self.allowed_suffixes = self._clean_unique(self.allowed_suffixes, required=False)
        normalized_targets = [normalize_candidate_target(item) for item in self.target_patterns]
        if any(not item for item in normalized_targets):
            raise ValueError("Candidate target patterns cannot be blank")
        if len(normalized_targets) != len(set(normalized_targets)):
            raise ValueError("Candidate target patterns must be unique")
        self.target_patterns = normalized_targets
        return self

    @staticmethod
    def _clean_unique(values: list[str], *, required: bool) -> list[str]:
        cleaned: list[str] = []
        normalized: set[str] = set()
        for value in values:
            candidate = clean_candidate_text(
                value,
                maximum=MAX_TEMPLATE_CANDIDATE_VALUE_LENGTH,
            )
            key = normalize_candidate_text(candidate)
            if key in normalized:
                raise ValueError("Candidate values must be unique after normalization")
            normalized.add(key)
            cleaned.append(candidate)
        if required and not cleaned:
            raise ValueError("At least one candidate value is required")
        return cleaned


class CanonicalAssetReferences(CanonicalModel):
    clean_template: str = "assets/clean-template.png"
    handwriting_mask: str | None = None
    handwriting_mask_edited: str | None = None
    preview: str | None = None
    handwriting_candidates: str | None = None
    output_layout: str | None = None
    spreadsheet_source: str | None = None
    # The blank form as printed from the source workbook (optional, spreadsheet templates only).
    printable_pdf: str | None = None

    @model_validator(mode="after")
    def validate_asset_paths(self) -> Self:
        expected = {
            "clean_template": "assets/clean-template.png",
            "handwriting_mask": "assets/handwriting-mask.png",
            "handwriting_mask_edited": "assets/handwriting-mask-edited.png",
            "preview": "assets/preview.png",
            "handwriting_candidates": "optional/handwriting-candidates.json",
            "output_layout": "optional/output-layout.json",
            "spreadsheet_source": "optional/spreadsheet-source.xlsx",
            "printable_pdf": "optional/printable.pdf",
        }
        for field_name, required_path in expected.items():
            value = getattr(self, field_name)
            if value is None:
                continue
            validated = validate_package_path(value)
            if validated != required_path:
                raise ValueError(f"{field_name} must use package path {required_path}")
        return self


class CanonicalTemplate(CanonicalModel):
    schema_version: Literal[2] = 2
    name: str = Field(min_length=1, max_length=300)
    document_type: Literal["generic-document", "spreadsheet"]
    mode: TemplateMode
    canonical_width: int = Field(gt=0)
    canonical_height: int = Field(gt=0)
    runtime: CanonicalRuntimeConfig
    spreadsheet: CanonicalSpreadsheetSource | None = None
    layout: LayoutDefinition
    approved_definition: CanonicalApprovedDefinition | None = None
    candidate_sets: list[CanonicalCandidateSet] = Field(
        default_factory=list,
        max_length=MAX_TEMPLATE_CANDIDATE_SET_COUNT,
    )
    assets: CanonicalAssetReferences

    @model_validator(mode="after")
    def validate_portable_template(self) -> Self:
        # Packages have no saved printLayout; accepting page-local display nodes
        # would silently lose their page ownership on import.
        if any(cell.page_range is not None for cell in self.layout.cells) or any(
            part.cell.page_range is not None
            for merge in self.layout.cell_merges
            for part in merge.parts
        ):
            raise ValueError("Page virtual headings require printLayout, unsupported in packages")
        runtime_config = TemplateConfig(
            template_id="package-template",
            template_version=1,
            name=self.name,
            canonical_width=self.canonical_width,
            canonical_height=self.canonical_height,
            registration=self.runtime.registration,
            regions=self.runtime.regions,
            field_mappings=self.runtime.field_mappings,
            document_identity_fields=self.runtime.document_identity_fields,
            document_name=self.runtime.document_name,
            marks=self.runtime.marks,
        )
        if (
            self.layout.canonical_width != self.canonical_width
            or self.layout.canonical_height != self.canonical_height
        ):
            raise ValueError("Layout dimensions must match canonical template dimensions")
        if self.approved_definition is not None:
            if (
                self.approved_definition.canonical_width != self.canonical_width
                or self.approved_definition.canonical_height != self.canonical_height
            ):
                raise ValueError(
                    "Approved definition dimensions must match canonical template dimensions"
                )
            definition = self.approved_definition.as_runtime_definition(template_name=self.name)
            validation = validate_template_definition(definition)
            if not validation.valid:
                codes = sorted(
                    issue.code for issue in validation.issues if issue.severity == "ERROR"
                )
                raise ValueError(
                    "Approved definition failed runtime validation: " + ", ".join(codes)
                )
        available_candidate_targets = {
            normalize_candidate_target(mapping.target_path)
            for mapping in runtime_config.field_mappings
            if mapping.enabled and mapping.mapping_type == "text"
        }
        set_ids = [candidate_set.id for candidate_set in self.candidate_sets]
        if len(set_ids) != len(set(set_ids)):
            raise ValueError("Candidate set package IDs must be unique")
        normalized_names = [
            normalize_candidate_text(candidate_set.name) for candidate_set in self.candidate_sets
        ]
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError("Candidate set names must be unique after normalization")
        bound_targets: set[str] = set()
        binding_count = 0
        for candidate_set in self.candidate_sets:
            for target in candidate_set.target_patterns:
                if target not in available_candidate_targets:
                    raise ValueError(
                        f"Candidate set {candidate_set.id} references unknown target {target}"
                    )
                if target in bound_targets:
                    raise ValueError("One target cannot use more than one candidate set")
                bound_targets.add(target)
                binding_count += 1
        if binding_count > MAX_TEMPLATE_CANDIDATE_BINDING_COUNT:
            raise ValueError("Candidate binding limit exceeded")
        if self.mode is TemplateMode.SPREADSHEET_TEMPLATE:
            if self.assets.spreadsheet_source is None:
                raise ValueError("Spreadsheet templates require a sanitized source workbook")
            if self.spreadsheet is None:
                raise ValueError("Spreadsheet templates require portable source workbook metadata")
        elif (
            self.assets.spreadsheet_source is not None
            or self.assets.printable_pdf is not None
            or self.spreadsheet is not None
        ):
            raise ValueError("Only spreadsheet templates can include source workbook data")
        return self

    def required_features(self) -> list[str]:
        features = {
            TemplatePackageFeature.CORE,
            TemplatePackageFeature.FIELD_MAPPINGS,
            TemplatePackageFeature.LAYOUT,
        }
        if self.runtime.document_identity_fields:
            features.add(TemplatePackageFeature.COMPOSITE_DOCUMENT_DETECTION)
        if self.runtime.document_name is not None:
            features.add(TemplatePackageFeature.DOCUMENT_NAME)
        if self.approved_definition is not None:
            features.add(TemplatePackageFeature.TEMPLATE_DEFINITION)
        if self.candidate_sets:
            features.add(TemplatePackageFeature.CANDIDATE_SETS)
        if self.runtime.marks or any(
            mapping.mapping_type == "mark" for mapping in self.runtime.field_mappings
        ):
            features.add(TemplatePackageFeature.MARK_RECOGNITION)
        if self.mode is TemplateMode.SPREADSHEET_TEMPLATE:
            features.update(
                {
                    TemplatePackageFeature.SPREADSHEET_TEMPLATE,
                    TemplatePackageFeature.XLSX_LAYOUT,
                }
            )
        if self.assets.output_layout is not None:
            features.add(TemplatePackageFeature.LOCAL_OUTPUT_LAYOUT)
        return sorted(features)


class CanonicalTemplateQuality(CanonicalModel):
    schema_version: Literal[1] = 1
    document_boundary_verified: bool
    layout_verified: bool
    layout_finalized: bool
    record_structure_verified: bool
    definition_approved: bool
    definition_valid: bool
    field_mapping_count: int = Field(ge=0, le=300)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_definition_flags(self) -> Self:
        if self.definition_approved and not self.definition_valid:
            raise ValueError("An approved definition must also be valid")
        return self


class CanonicalOutputLayoutCell(CanonicalModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    value: JsonValue | None = None
    format: dict[str, JsonValue] = Field(default_factory=dict)
    binding: str | None = Field(default=None, min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=2000)
    data_validation: dict[str, JsonValue] | None = None
    text_format_runs: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=200)

    @field_validator("note")
    @classmethod
    def reject_internal_template_note(cls, value: str | None) -> str | None:
        if value is not None and value.startswith("llm-ocr:"):
            raise ValueError("Output layout notes cannot contain internal LLM OCR references")
        return value


class CanonicalOutputLayoutMerge(CanonicalModel):
    start_row_index: int = Field(ge=0)
    end_row_index: int = Field(gt=0)
    start_column_index: int = Field(ge=0)
    end_column_index: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end_row_index <= self.start_row_index:
            raise ValueError("Merge end row must be after its start row")
        if self.end_column_index <= self.start_column_index:
            raise ValueError("Merge end column must be after its start column")
        return self


class CanonicalOutputLayout(CanonicalModel):
    schema_version: Literal[1] = 1
    sheet_name: str = Field(min_length=1, max_length=200)
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    frozen_row_count: int = Field(default=0, ge=0)
    frozen_column_count: int = Field(default=0, ge=0)
    hide_gridlines: bool = False
    cells: list[CanonicalOutputLayoutCell] = Field(default_factory=list, max_length=10000)
    merges: list[CanonicalOutputLayoutMerge] = Field(default_factory=list, max_length=1000)
    row_heights: dict[str, int] = Field(default_factory=dict)
    column_widths: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_layout(self) -> Self:
        coordinates = [(cell.row, cell.column) for cell in self.cells]
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("Output layout cell coordinates must be unique")
        if any(row >= self.row_count or column >= self.column_count for row, column in coordinates):
            raise ValueError("Output layout cells must fit within declared dimensions")
        for merge in self.merges:
            if merge.end_row_index > self.row_count or merge.end_column_index > self.column_count:
                raise ValueError("Output layout merges must fit within declared dimensions")
        self._validate_dimension_overrides(
            self.row_heights,
            limit=self.row_count,
            field_name="rowHeights",
        )
        self._validate_dimension_overrides(
            self.column_widths,
            limit=self.column_count,
            field_name="columnWidths",
        )
        if self.frozen_row_count > self.row_count:
            raise ValueError("frozenRowCount exceeds rowCount")
        if self.frozen_column_count > self.column_count:
            raise ValueError("frozenColumnCount exceeds columnCount")
        return self

    @staticmethod
    def _validate_dimension_overrides(
        values: dict[str, int], *, limit: int, field_name: str
    ) -> None:
        for raw_index, size in values.items():
            if re.fullmatch(r"0|[1-9]\d*", raw_index) is None:
                raise ValueError(f"{field_name} keys must be non-negative integer strings")
            if int(raw_index) >= limit:
                raise ValueError(f"{field_name} index exceeds declared dimensions")
            if size <= 0:
                raise ValueError(f"{field_name} sizes must be positive")
