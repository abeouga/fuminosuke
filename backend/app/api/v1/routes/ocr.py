from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Header, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import Field

from app.api.dependencies import get_job_service, get_ocr_result_service
from app.api.v1.routes.shared import JobAccepted, job_accepted
from app.api.v1.routes.uploads import read_upload
from app.core.errors import ApiModel
from app.domain.ocr_models import (
    Calving,
    DocumentIdentity,
    OcrDocument,
    Pedigree,
    ReviewItem,
    SaleCalf,
)
from app.services.ocr_result_service import CorrectionValues

router = APIRouter(tags=["ocr"])


class OcrResultResponse(ApiModel):
    job_id: str
    revision: int
    result: OcrDocument
    validation: dict[str, Any]
    provider: str
    model_id: str | None
    prompt_version: str | None
    api_call_count: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None
    confirmed: bool
    individual_id: str | None


class OcrCorrectionRequest(ApiModel):
    base_revision: int = Field(ge=1)
    document: DocumentIdentity
    sale_calves: list[SaleCalf]
    pedigree: Pedigree
    calvings: list[Calving]
    review_items: list[ReviewItem]


@router.post(
    "/ocr-jobs",
    response_model=JobAccepted,
    response_model_by_alias=True,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_ocr_job(
    request: Request,
    template_version_id: Annotated[str, Form(alias="templateVersionId")],
    file: Annotated[UploadFile, File()],
    ocr_enabled: Annotated[bool, Form(alias="ocrEnabled")] = True,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    upload = await read_upload(file, request.app.state.settings.storage.max_upload_bytes)
    job = await get_job_service(request).create_ocr_job(
        template_version_id=template_version_id,
        upload=upload,
        ocr_enabled=ocr_enabled,
        idempotency_key=idempotency_key,
    )
    return job_accepted(job)


@router.get(
    "/ocr-jobs/{job_id}/result",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def get_ocr_result(request: Request, job_id: str) -> dict[str, Any]:
    return get_ocr_result_service(request).get(job_id)


@router.put(
    "/ocr-jobs/{job_id}/corrections",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def save_ocr_corrections(
    request: Request, job_id: str, body: OcrCorrectionRequest
) -> dict[str, Any]:
    values = CorrectionValues(
        base_revision=body.base_revision,
        document=body.document,
        sale_calves=body.sale_calves,
        pedigree=body.pedigree,
        calvings=body.calvings,
        review_items=body.review_items,
    )
    return get_ocr_result_service(request).correct(job_id, values)


@router.post(
    "/ocr-jobs/{job_id}/confirm",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def confirm_ocr_result(request: Request, job_id: str) -> dict[str, Any]:
    return get_ocr_result_service(request).confirm(job_id)


@router.get("/ocr-jobs/{job_id}/export", response_class=FileResponse)
def export_ocr_result(
    request: Request, job_id: str, format: str = "json"
) -> FileResponse:
    path, media_type, file_name = get_ocr_result_service(request).export_path(
        job_id, format
    )
    return FileResponse(path, media_type=media_type, filename=file_name)
