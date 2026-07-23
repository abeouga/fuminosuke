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


class IndividualRecordSearchResponse(ApiModel):
    query: str
    individual_id: str
    count: int
    records: list[IndividualRecord]


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
