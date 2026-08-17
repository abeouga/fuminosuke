from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Header, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import Field

from app.api.dependencies import get_job_service, get_ocr_result_service
from app.api.v1.routes.shared import JobAccepted, job_accepted
from app.api.v1.routes.uploads import read_upload
from app.core.errors import ApiModel
from app.domain.field_targets import CALVING_ROW_COUNT, SALE_CALF_ROW_COUNT
from app.domain.ocr_models import (
    BoundingBox,
    Calving,
    DocumentIdentity,
    FieldReading,
    OcrDocument,
    Pedigree,
    ReviewItem,
    SaleCalf,
)
from app.services.ocr_result_service import CorrectionValues

router = APIRouter(tags=["ocr"])


class OcrFieldPreview(ApiModel):
    target_path: str
    mapping_id: str
    artifact_url: str
    bounding_box: BoundingBox | None
    record_group_id: str | None = None
    field_key: str | None = None
    mapping_type: Literal["text", "mark"] = "text"
    mark_options: list[dict[str, str]] = Field(default_factory=list)


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
    field_previews: list[OcrFieldPreview]
    record_structure: dict[str, Any]
    spreadsheet_sync: dict[str, Any]


class OcrCorrectionRequest(ApiModel):
    base_revision: int = Field(ge=1)
    document: DocumentIdentity
    sale_calves: list[SaleCalf] = Field(max_length=SALE_CALF_ROW_COUNT)
    pedigree: Pedigree
    calvings: list[Calving] = Field(max_length=CALVING_ROW_COUNT)
    custom_fields: dict[str, FieldReading] = Field(default_factory=dict, max_length=300)
    review_items: list[ReviewItem]


class OcrConfirmationRequest(ApiModel):
    base_revision: int = Field(ge=1)


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
    mother_individual_id: Annotated[str | None, Form(alias="motherIndividualId")] = None,
    ocr_enabled: Annotated[bool, Form(alias="ocrEnabled")] = True,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    upload = await read_upload(file, request.app.state.settings.storage.max_upload_bytes)
    job = await get_job_service(request).create_ocr_job(
        template_version_id=template_version_id,
        mother_individual_id=mother_individual_id,
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
        custom_fields=body.custom_fields,
        review_items=body.review_items,
    )
    return get_ocr_result_service(request).correct(job_id, values)


@router.post(
    "/ocr-jobs/{job_id}/reopen",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def reopen_ocr_result(request: Request, job_id: str) -> dict[str, Any]:
    return get_ocr_result_service(request).reopen(job_id)


@router.post(
    "/ocr-jobs/{job_id}/confirm",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def confirm_ocr_result(
    request: Request,
    job_id: str,
    body: OcrConfirmationRequest,
) -> dict[str, Any]:
    return get_ocr_result_service(request).confirm(
        job_id,
        base_revision=body.base_revision,
    )


@router.get("/ocr-jobs/{job_id}/export", response_class=FileResponse)
def export_ocr_result(request: Request, job_id: str, format: str = "json") -> FileResponse:
    path, media_type, file_name = get_ocr_result_service(request).export_path(job_id, format)
    return FileResponse(path, media_type=media_type, filename=file_name)


@router.post(
    "/ocr-jobs/{job_id}/spreadsheet-sync",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def retry_spreadsheet_sync(request: Request, job_id: str) -> dict[str, Any]:
    return get_ocr_result_service(request).sync_spreadsheet(job_id)


@router.get("/ocr-jobs/{job_id}/excel-table")
def get_ocr_excel_table(request: Request, job_id: str) -> dict[str, object]:
    return get_ocr_result_service(request).excel_table(job_id)
