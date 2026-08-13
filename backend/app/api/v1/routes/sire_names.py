from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request, Response, status
from pydantic import Field

from app.api.dependencies import get_database
from app.core.errors import ApiModel
from app.services.sire_name_service import SireNameService

router = APIRouter(tags=["sire-names"])


class SireNameCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=100)


class SireNameView(ApiModel):
    id: str
    name: str
    created_at: datetime


class SireNameListResponse(ApiModel):
    items: list[SireNameView]
    count: int
    allowed_suffixes: list[str]


@router.get(
    "/sire-names",
    response_model=SireNameListResponse,
    response_model_by_alias=True,
)
def list_sire_names(request: Request) -> dict[str, object]:
    return SireNameService(get_database(request)).get_pool()


@router.post(
    "/sire-names",
    response_model=SireNameView,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
)
def create_sire_name(
    request: Request, body: SireNameCreateRequest
) -> dict[str, object]:
    return SireNameService(get_database(request)).create(body.name)


@router.delete(
    "/sire-names/{sire_name_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_sire_name(request: Request, sire_name_id: str) -> Response:
    SireNameService(get_database(request)).delete(sire_name_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
