from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import to_camel
from app.template_packages.package_paths import path_collision_key, validate_package_path
from app.template_packages.semantic_version import SEMVER_PATTERN, validate_semantic_version

FORMAT_NAME = "llm-ocr-template"
FORMAT_VERSION = 2
SCHEMA_VERSION = 2

SHA256_PATTERN = r"^[0-9a-f]{64}$"
CONTENT_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
FEATURE_ID_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*-v[1-9]\d*$"
PUBLISHER_ID_PATTERN = (
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
PACKAGE_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$"

PackageFileRole = Literal[
    "template",
    "quality",
    "clean-template",
    "handwriting-mask",
    "handwriting-mask-edited",
    "preview",
    "handwriting-candidates",
    "output-layout",
    "spreadsheet-source",
]

_ROLE_PATHS: dict[str, str] = {
    "template": "template.json",
    "quality": "quality.json",
    "clean-template": "assets/clean-template.png",
    "handwriting-mask": "assets/handwriting-mask.png",
    "handwriting-mask-edited": "assets/handwriting-mask-edited.png",
    "preview": "assets/preview.png",
    "handwriting-candidates": "optional/handwriting-candidates.json",
    "output-layout": "optional/output-layout.json",
    "spreadsheet-source": "optional/spreadsheet-source.xlsx",
}
_ROLE_MEDIA_TYPES: dict[str, str] = {
    "template": "application/json",
    "quality": "application/json",
    "clean-template": "image/png",
    "handwriting-mask": "image/png",
    "handwriting-mask-edited": "image/png",
    "preview": "image/png",
    "handwriting-candidates": "application/json",
    "output-layout": "application/json",
    "spreadsheet-source": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}


class ManifestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        strict=True,
    )


class PackageFile(ManifestModel):
    path: str = Field(min_length=1, max_length=240)
    role: PackageFileRole
    media_type: str = Field(min_length=1, max_length=160)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_role_contract(self) -> Self:
        self.path = validate_package_path(self.path)
        if self.path != _ROLE_PATHS[self.role]:
            raise ValueError(f"File role {self.role} must use path {_ROLE_PATHS[self.role]}")
        if self.media_type != _ROLE_MEDIA_TYPES[self.role]:
            raise ValueError(
                f"File role {self.role} must use media type {_ROLE_MEDIA_TYPES[self.role]}"
            )
        return self


class TemplatePackageManifest(ManifestModel):
    format: Literal["llm-ocr-template"] = "llm-ocr-template"
    format_version: Literal[2] = 2
    schema_version: Literal[2] = 2
    publisher_id: str = Field(min_length=3, max_length=253, pattern=PUBLISHER_ID_PATTERN)
    package_id: str = Field(min_length=1, max_length=100, pattern=PACKAGE_ID_PATTERN)
    name: str = Field(min_length=1, max_length=300)
    version: str = Field(pattern=SEMVER_PATTERN)
    minimum_app_version: str = Field(pattern=SEMVER_PATTERN)
    required_features: list[str] = Field(min_length=1, max_length=50)
    optional_features: list[str] = Field(default_factory=list, max_length=50)
    exported_at: datetime
    content_digest: str = Field(pattern=CONTENT_DIGEST_PATTERN)
    files: list[PackageFile] = Field(min_length=3, max_length=20)

    @field_validator("version", "minimum_app_version")
    @classmethod
    def validate_versions(cls, value: str) -> str:
        return validate_semantic_version(value)

    @field_validator("required_features", "optional_features")
    @classmethod
    def validate_features(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Feature IDs must be unique")
        if any(re.fullmatch(FEATURE_ID_PATTERN, feature) is None for feature in value):
            raise ValueError("Feature IDs must use the versioned feature ID format")
        return value

    @field_validator("exported_at")
    @classmethod
    def validate_exported_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("exportedAt must contain a timezone offset")
        return value

    @model_validator(mode="after")
    def validate_manifest_contract(self) -> Self:
        overlap = set(self.required_features) & set(self.optional_features)
        if overlap:
            raise ValueError("A feature cannot be both required and optional")
        if self.required_features != sorted(self.required_features):
            raise ValueError("requiredFeatures must be sorted")
        if self.optional_features != sorted(self.optional_features):
            raise ValueError("optionalFeatures must be sorted")
        path_keys = [path_collision_key(item.path) for item in self.files]
        if len(path_keys) != len(set(path_keys)):
            raise ValueError("Package file paths must be unique after normalization")
        roles = [item.role for item in self.files]
        if len(roles) != len(set(roles)):
            raise ValueError("Package file roles must be unique")
        required_roles = {"template", "quality", "clean-template"}
        if not required_roles.issubset(roles):
            raise ValueError("template, quality, and clean-template files are required")
        if [item.path for item in self.files] != sorted(item.path for item in self.files):
            raise ValueError("Package files must be sorted by path")
        return self
