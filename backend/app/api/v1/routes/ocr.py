from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import Field

from app.api.auth_dependencies import require_authenticated_user
from app.api.dependencies import (
    get_google_sheets,
    get_job_service,
    get_ocr_result_service,
    get_output_layout_templates,
)
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
from app.services.google_oauth_service import AuthenticatedUser
from app.services.ocr_result_service import CorrectionValues

router = APIRouter(tags=["ocr"])
AuthenticatedRequestUser = Annotated[
    AuthenticatedUser | None,
    Depends(require_authenticated_user),
]


class OcrFieldPreview(ApiModel):
    target_path: str
    mapping_id: str
    artifact_url: str
    bounding_box: BoundingBox | None
    record_group_id: str | None = None
    field_key: str | None = None
    mapping_type: Literal["text", "mark"] = "text"
    mark_recognition_mode: Literal["automatic", "manual"] = "automatic"
    mark_selection_mode: Literal["single", "multiple"] = "multiple"
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
    review_structure: dict[str, Any]
    spreadsheet_sync: dict[str, Any]
    document_resolution: dict[str, Any]
    document_name: str


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
    document_name: str | None = Field(default=None, min_length=1, max_length=180)


class SpreadsheetNameRequest(ApiModel):
    file_name: str = Field(min_length=1, max_length=185)


class DocumentResolutionRequest(ApiModel):
    action: Literal["ADD_PAGE", "RESCAN_PAGE", "NEW_DOCUMENT"]
    target_document_id: str | None = None
    target_job_id: str | None = None
    target_page_id: str | None = None


class OutputLayoutTemplateResponse(ApiModel):
    active: bool
    template_version_id: str
    revision: int | None
    sheet_name: str
    binding_count: int
    source_job_id: str | None
    created_at: str | None


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
    send_full_document_to_openai: Annotated[bool, Form(alias="sendFullDocumentToOpenAi")] = False,
    document_action: Annotated[
        Literal["ADD_PAGE", "RESCAN_PAGE", "NEW_DOCUMENT"] | None,
        Form(alias="documentAction"),
    ] = None,
    target_document_id: Annotated[str | None, Form(alias="targetDocumentId")] = None,
    target_page_id: Annotated[str | None, Form(alias="targetPageId")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    upload = await read_upload(file, request.app.state.settings.storage.max_upload_bytes)
    job = await get_job_service(request).create_ocr_job(
        template_version_id=template_version_id,
        mother_individual_id=mother_individual_id,
        upload=upload,
        ocr_enabled=ocr_enabled,
        send_full_document_to_openai=send_full_document_to_openai,
        document_action=document_action,
        target_document_id=target_document_id,
        target_page_id=target_page_id,
        idempotency_key=idempotency_key,
    )
    return job_accepted(job)


@router.get(
    "/ocr-jobs/{job_id}/result",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def get_ocr_result(
    request: Request,
    job_id: str,
    user: AuthenticatedRequestUser,
) -> dict[str, Any]:
    return get_ocr_result_service(request).get(
        job_id,
        expected_subject=user.subject if user is not None else None,
    )


@router.put(
    "/ocr-jobs/{job_id}/corrections",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def save_ocr_corrections(
    request: Request,
    job_id: str,
    body: OcrCorrectionRequest,
    user: AuthenticatedRequestUser,
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
    return get_ocr_result_service(request).correct(
        job_id,
        values,
        expected_subject=user.subject if user is not None else None,
    )


@router.post(
    "/ocr-jobs/{job_id}/reopen",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def reopen_ocr_result(
    request: Request,
    job_id: str,
    user: AuthenticatedRequestUser,
) -> dict[str, Any]:
    return get_ocr_result_service(request).reopen(
        job_id,
        expected_subject=user.subject if user is not None else None,
    )


@router.post(
    "/ocr-jobs/{job_id}/document-resolution",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def resolve_ocr_document(
    request: Request,
    job_id: str,
    body: DocumentResolutionRequest,
    user: AuthenticatedRequestUser,
) -> dict[str, Any]:
    return get_ocr_result_service(request).resolve_document(
        job_id,
        action=body.action,
        target_document_id=body.target_document_id,
        target_job_id=body.target_job_id,
        target_page_id=body.target_page_id,
        expected_subject=user.subject if user is not None else None,
    )


@router.post(
    "/ocr-jobs/{job_id}/confirm",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def confirm_ocr_result(
    request: Request,
    job_id: str,
    body: OcrConfirmationRequest,
    user: AuthenticatedRequestUser,
) -> dict[str, Any]:
    return get_ocr_result_service(request).confirm(
        job_id,
        base_revision=body.base_revision,
        document_name=body.document_name,
        expected_subject=user.subject if user else None,
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
def retry_spreadsheet_sync(
    request: Request,
    job_id: str,
    user: AuthenticatedRequestUser,
) -> dict[str, Any]:
    return get_ocr_result_service(request).sync_spreadsheet(
        job_id,
        user.subject if user else None,
    )


@router.put("/ocr-jobs/{job_id}/spreadsheet-name")
def rename_spreadsheet(
    request: Request,
    job_id: str,
    body: SpreadsheetNameRequest,
    user: AuthenticatedRequestUser,
) -> dict[str, object | None]:
    return get_ocr_result_service(request).rename_spreadsheet(
        job_id,
        body.file_name,
        user.subject if user else None,
    )


@router.get(
    "/ocr-jobs/{job_id}/output-layout",
    response_model=OutputLayoutTemplateResponse,
    response_model_by_alias=True,
)
def get_output_layout_template(request: Request, job_id: str) -> dict[str, object | None]:
    return get_output_layout_templates(request).status_for_job(job_id)


@router.post(
    "/ocr-jobs/{job_id}/output-layout/import",
    response_model=OutputLayoutTemplateResponse,
    response_model_by_alias=True,
)
def import_output_layout_template(
    request: Request,
    job_id: str,
    user: AuthenticatedRequestUser,
) -> dict[str, object | None]:
    document_id = get_ocr_result_service(request).document_id(job_id)
    layout = get_google_sheets(request).capture_output_layout(
        document_id=document_id,
        expected_subject=user.subject if user is not None else None,
    )
    return get_output_layout_templates(request).save_for_job(job_id, layout)


@router.get("/google-sheets/status")
def get_google_sheets_status(
    request: Request,
    user: AuthenticatedRequestUser,
    job_id: str | None = Query(default=None, alias="jobId"),
) -> dict[str, Any]:
    sheets = get_google_sheets(request)
    if job_id is None:
        return sheets.target_status().payload()
    return get_ocr_result_service(request).spreadsheet_status(
        job_id,
        expected_subject=user.subject if user is not None else None,
    )


@router.get("/ocr-jobs/{job_id}/excel-table")
def get_ocr_excel_table(request: Request, job_id: str) -> dict[str, object]:
    return get_ocr_result_service(request).excel_table(job_id)
