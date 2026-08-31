from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import to_camel
from app.template_packages.manifest_models import (
    PACKAGE_ID_PATTERN,
    PUBLISHER_ID_PATTERN,
    TemplatePackageManifest,
)
from app.template_packages.semantic_version import SEMVER_PATTERN, validate_semantic_version


class ExportModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class TemplateExportRequest(ExportModel):
    template_id: str = Field(min_length=1, max_length=36)
    template_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    publisher_id: str = Field(min_length=3, max_length=253, pattern=PUBLISHER_ID_PATTERN)
    package_id: str = Field(min_length=1, max_length=100, pattern=PACKAGE_ID_PATTERN)
    version: str = Field(pattern=SEMVER_PATTERN)
    include_preview: bool = True
    include_handwriting_candidates: bool = False
    output_layout_owner_key: str | None = Field(default=None, min_length=1, max_length=255)

    def validated_version(self) -> str:
        return validate_semantic_version(self.version)


class ExportCandidateSetInspection(ExportModel):
    name: str
    value_count: int = Field(ge=1)
    values: list[str]
    target_patterns: list[str]


class ExportAssetInspection(ExportModel):
    role: str
    media_type: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    sanitized: bool = False


class TemplateExportInspection(ExportModel):
    template_name: str
    template_version_number: int = Field(ge=1)
    mode: str
    candidate_sets: list[ExportCandidateSetInspection]
    assets: list[ExportAssetInspection]
    output_layout_included: bool
    spreadsheet_source_included: bool
    warnings: list[str]
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    confirmation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class TemplatePackageExport:
    content: bytes
    file_name: str
    manifest: TemplatePackageManifest
    inspection: TemplateExportInspection
    storage_relative_path: str | None = None


class TemplatePackageExportError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
