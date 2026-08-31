from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import to_camel
from app.template_packages.canonical_models import (
    CanonicalOutputLayout,
    CanonicalTemplate,
    CanonicalTemplateQuality,
)
from app.template_packages.manifest_models import TemplatePackageManifest


class TemplatePackageImportError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class TemplateImportConflictStatus(StrEnum):
    NEW_IDENTITY = "NEW_IDENTITY"
    SAME_RELEASE = "SAME_RELEASE"
    NEWER_RELEASE = "NEWER_RELEASE"
    OLDER_RELEASE = "OLDER_RELEASE"
    VERSION_CONFLICT = "VERSION_CONFLICT"


class ImportModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class TemplateImportAssetInspection(ImportModel):
    path: str
    role: str
    media_type: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)


class TemplateImportInspection(ImportModel):
    format_version: int = Field(ge=1)
    schema_version: int = Field(ge=1)
    publisher_id: str
    package_id: str
    version: str
    name: str
    content_digest: str
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    minimum_app_version: str
    required_features: list[str]
    optional_features: list[str]
    mode: str
    conflict_status: TemplateImportConflictStatus
    existing_local_template_id: str | None = None
    existing_local_template_version_id: str | None = None
    document_identity_field_count: int = Field(ge=0)
    document_name_field_paths: list[str]
    candidate_set_count: int = Field(ge=0)
    assets: list[TemplateImportAssetInspection]
    spreadsheet_source_included: bool
    output_layout_included: bool
    trust_status: str = "UNVERIFIED"


@dataclass(frozen=True)
class ValidatedTemplatePackage:
    manifest: TemplatePackageManifest
    template: CanonicalTemplate
    quality: CanonicalTemplateQuality
    output_layout: CanonicalOutputLayout | None
    handwriting_candidates: Any | None
    payloads: dict[str, bytes]
    image_dimensions: dict[str, tuple[int, int]]
    package_sha256: str


@dataclass(frozen=True)
class TemplatePackageImportResult:
    installation_id: str
    local_template_id: str
    local_template_version_id: str
    local_job_id: str
    content_digest: str
    release_version: str
    created: bool
    active_version_changed: bool = False
