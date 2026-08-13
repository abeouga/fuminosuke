from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Query, Request

from app.core.errors import ApiModel
from app.domain.ocr_models import OcrDocument
from app.services.individual_record_service import IndividualRecordService

router = APIRouter(tags=["individual-records"])


class IndividualRecord(ApiModel):
    record_id: str
    individual_id: str
    source_job_id: str
    ocr_result_revision: int
    confirmed_at: datetime
    data: OcrDocument
    needs_review: bool
    review_warnings: list[str]


class IndividualRecordSearchResponse(ApiModel):
    query: str
    individual_id: str
    count: int
    records: list[IndividualRecord]


class IndividualRecordSummary(ApiModel):
    individual_id: str
    cattle_name: str | None
    record_count: int
    last_confirmed_at: datetime
    needs_review: bool


class IndividualRecordCatalogResponse(ApiModel):
    count: int
    items: list[IndividualRecordSummary]


class SaleCalfRecordMatch(ApiModel):
    record_field_id: str
    confirmed_result_id: str
    individual_id: str | None
    source_job_id: str
    confirmed_at: datetime
    section_key: str
    section_title: str
    record_order: int
    record_key: str | None
    matched_value: str | None
    data: dict[str, Any]
    review_warnings: list[str]


class SaleCalfSireSearchResponse(ApiModel):
    query: str
    normalized_query: str
    count: int
    matches: list[SaleCalfRecordMatch]


@router.get(
    "/individual-records/catalog",
    response_model=IndividualRecordCatalogResponse,
    response_model_by_alias=True,
)
def list_individual_records(request: Request) -> dict[str, Any]:
    service = cast(IndividualRecordService, request.app.state.individual_record_service)
    return service.list_individuals()


@router.get(
    "/individual-records",
    response_model=IndividualRecordSearchResponse,
    response_model_by_alias=True,
)
def search_individual_records(
    request: Request,
    individual_id: Annotated[
        str, Query(alias="individualId", min_length=1, max_length=50)
    ],
) -> dict[str, Any]:
    service = cast(IndividualRecordService, request.app.state.individual_record_service)
    return service.search(individual_id)


@router.get(
    "/sale-calf-records",
    response_model=SaleCalfSireSearchResponse,
    response_model_by_alias=True,
)
def search_sale_calf_records(
    request: Request,
    sire_name: Annotated[
        str, Query(alias="sireName", min_length=1, max_length=200)
    ],
) -> dict[str, Any]:
    service = cast(IndividualRecordService, request.app.state.individual_record_service)
    return service.search_sale_calves_by_sire(sire_name)
