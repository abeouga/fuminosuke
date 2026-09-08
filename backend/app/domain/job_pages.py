"""Explicit page bindings. Upload order is transport metadata, never page identity."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from app.domain.template_config import TemplateConfigModel


class PageBinding(TemplateConfigModel):
    template_page_id: str = Field(min_length=1, max_length=36)
    input_id: str = Field(min_length=1, max_length=36, pattern=r"^[a-zA-Z0-9_-]+$")
    input_revision: int = Field(ge=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PagedSubmission(TemplateConfigModel):
    template_version_id: str
    template_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    bindings: list[PageBinding] = Field(min_length=1, max_length=50)
    upload_ids: list[str] = Field(min_length=1, max_length=50)


def template_page_id(version_id: str, page: dict[str, Any]) -> str:
    # The immutable reference generation changes this identity after every save.
    return str(uuid5(NAMESPACE_URL, f"{version_id}/{page['referenceImage']['artifactId']}"))
