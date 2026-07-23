from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class FieldError(ApiModel):
    field: str
    message: str


class ProblemDetails(ApiModel):
    type: str = "about:blank"
    title: str
    status: int
    code: str
    detail: str
    job_id: str | None = None
    field_errors: list[FieldError] = Field(default_factory=list)


class AppError(Exception):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str,
        job_id: str | None = None,
        field_errors: list[FieldError] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.job_id = job_id
        self.field_errors = field_errors or []
        self.metadata = metadata or {}

