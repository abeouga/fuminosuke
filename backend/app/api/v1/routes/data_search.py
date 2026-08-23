from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Body, Request
from pydantic import Field, model_validator

from app.core.errors import ApiModel
from app.services.data_search_service import DataSearchService

router = APIRouter(tags=["data-search"])


class DataSearchFilter(ApiModel):
    field_key: str = Field(min_length=1, max_length=200)
    operator: Literal[
        "exact",
        "contains",
        "startsWith",
        "gte",
        "lte",
        "between",
        "on",
        "before",
        "after",
    ]
    value: str = Field(max_length=300)
    value_to: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_values(self) -> DataSearchFilter:
        if not self.value.strip():
            raise ValueError("検索値を入力してください")
        if self.operator == "between" and not (self.value_to or "").strip():
            raise ValueError("範囲の終了値を入力してください")
        return self


class DataSearchRequest(ApiModel):
    query: str = Field(default="", max_length=300)
    template_id: str | None = Field(default=None, max_length=36)
    filters: list[DataSearchFilter] = Field(default_factory=list, max_length=12)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


@router.get("/data-search/catalog")
def data_search_catalog(request: Request) -> dict[str, Any]:
    service = cast(DataSearchService, request.app.state.data_search_service)
    return service.catalog()


@router.post("/data-search")
def data_search(
    request: Request,
    body: Annotated[DataSearchRequest, Body()],
) -> dict[str, Any]:
    service = cast(DataSearchService, request.app.state.data_search_service)
    return service.search(
        query=body.query,
        template_id=body.template_id,
        filters=[item.model_dump(mode="json", by_alias=True) for item in body.filters],
        page=body.page,
        page_size=body.page_size,
    )
