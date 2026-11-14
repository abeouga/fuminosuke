from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import ConfigDict, Field

from app.api.dependencies import get_database, get_job_service, get_ocr_result_service
from app.api.editing_cutover import legacy_edit_guard
from app.api.v1.routes.shared import JobAccepted, job_accepted
from app.api.v1.routes.uploads import read_upload
from app.core.errors import ApiModel, AppError
from app.domain.ocr_models import BoundingBox, FieldReading, OcrDocument, ReviewItem
from app.domain.template_config import DocumentIdentityConfig
from app.services.document_series import series_candidates
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
    mark_recognition_mode: Literal["automatic", "manual", "symbol", "checklist"] = "automatic"
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
    field_previews: list[OcrFieldPreview]
    record_structure: dict[str, Any]
    review_structure: dict[str, Any]
    document_name: str
    # Present when the job was read as an update of a confirmed document (document_series).
    series: dict[str, Any] | None = None
    reopened: bool = False
    changed_since_confirmed: bool = False


class OcrCorrectionRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=1)
    fields: dict[str, FieldReading] = Field(max_length=1000)
    review_items: list[ReviewItem]


class OcrReviewContextResponse(ApiModel):
    identity_fields: list[DocumentIdentityConfig]


class OcrReopenRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=1, strict=True)


class ReviewPresenceRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    viewer_id: str = Field(min_length=8, max_length=64)
    take_over: bool = False


class ReviewPresenceResponse(ApiModel):
    held: bool
    other_since: datetime | None = None


class OcrConfirmationRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=1)
    document_name: str | None = Field(default=None, min_length=1, max_length=180)
    # Required only when an update's identity differs from its predecessor's.
    accept_identity_mismatch: bool = Field(default=False, strict=True)


class SeriesIdentity(ApiModel):
    label: str
    value: str | None = None


class SeriesCandidate(ApiModel):
    job_id: str
    document_name: str | None
    template_name: str
    template_version_id: str | None
    confirmed_at: datetime
    confirmed_revision: int
    series_version: int
    identity: list[SeriesIdentity]
    selectable: bool
    reason: str | None


class SeriesCandidateList(ApiModel):
    items: list[SeriesCandidate]


@router.get(
    "/document-series/candidates",
    response_model=SeriesCandidateList,
    response_model_by_alias=True,
)
async def list_series_candidates(
    request: Request,
    template_version_id: Annotated[
        str, Query(alias="templateVersionId", min_length=1, max_length=36)
    ],
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> SeriesCandidateList:
    """Confirmed documents (latest versions) that an upload can be read as an update of."""
    items = await asyncio.to_thread(
        series_candidates, get_database(request), template_version_id=template_version_id, query=q
    )
    return SeriesCandidateList.model_validate({"items": items})


@router.post("/template-classifications")
async def classify_template(
    request: Request, file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """Which in-use template this image was filled on (nothing is stored)."""
    from app.services.template_classification_service import TemplateClassificationService

    upload = await read_upload(file, request.app.state.settings.storage.max_upload_bytes)
    service = TemplateClassificationService(
        get_database(request), request.app.state.storage, request.app.state.settings,
    )
    return await asyncio.to_thread(
        service.classify, upload.content, upload.declared_media_type,
    )


@router.post("/document-continuations")
async def compare_document_continuations(
    request: Request,
    file: Annotated[UploadFile, File()],
    job_ids: Annotated[str, Form(alias="jobIds", min_length=1, max_length=1000)],
) -> dict[str, Any]:
    """Whether this image is a later photo of the given readings' sheets (nothing is stored)."""
    from app.services.document_continuation_service import DocumentContinuationService

    upload = await read_upload(file, request.app.state.settings.storage.max_upload_bytes)
    service = DocumentContinuationService(
        get_database(request), request.app.state.storage, request.app.state.settings,
    )
    ids = [item.strip() for item in job_ids.split(",") if item.strip()]
    return await asyncio.to_thread(
        service.compare, upload.content, upload.declared_media_type, ids,
    )


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
    send_full_document_to_openai: Annotated[bool, Form(alias="sendFullDocumentToOpenAi")] = False,
    predecessor_job_id: Annotated[
        str | None, Form(alias="predecessorJobId", min_length=1, max_length=36)
    ] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    # Form fields from discontinued contracts must not be silently accepted.
    form = await request.form()
    accepted = {"templateVersionId", "file", "ocrEnabled", "sendFullDocumentToOpenAi"}
    if set(form) - (accepted | {"predecessorJobId"}):
        raise AppError(
            status=422,
            code="OCR_FORM_UNSUPPORTED",
            title="Unsupported form field",
            detail="未対応の入力項目があります。",
        )
    upload = await read_upload(file, request.app.state.settings.storage.max_upload_bytes)
    if (file.filename or "").lower().endswith(".xlsx"):
        from dataclasses import replace
        upload = replace(upload, is_xlsx=True)
    job = await get_job_service(request).create_ocr_job(
        template_version_id=template_version_id,
        upload=upload,
        ocr_enabled=ocr_enabled,
        send_full_document_to_openai=send_full_document_to_openai,
        idempotency_key=idempotency_key,
        predecessor_job_id=predecessor_job_id,
    )
    return job_accepted(job)


@router.get(
    "/ocr-jobs/{job_id}/result", response_model=OcrResultResponse, response_model_by_alias=True
)
def get_ocr_result(request: Request, job_id: str):
    return get_ocr_result_service(request).get(job_id)


@router.get(
    "/ocr-jobs/{job_id}/review-context",
    response_model=OcrReviewContextResponse,
    response_model_by_alias=True,
)
def get_ocr_review_context(request: Request, job_id: str):
    job = get_job_service(request).get(job_id)
    config = job.config_snapshot_json.get("templateSnapshot", {}).get("config", {})
    return OcrReviewContextResponse(
        identity_fields=[
            DocumentIdentityConfig.model_validate(f)
            for f in config.get("documentIdentityFields", [])
        ]
    )


@router.put(
    "/ocr-jobs/{job_id}/corrections",
    response_model=OcrResultResponse, response_model_by_alias=True,
    dependencies=[Depends(legacy_edit_guard)],
)
def save_ocr_corrections(request: Request, job_id: str, body: OcrCorrectionRequest):
    return get_ocr_result_service(request).correct(
        job_id, CorrectionValues(body.base_revision, body.fields, body.review_items)
    )


@router.post(
    "/ocr-jobs/{job_id}/review-presence",
    response_model=ReviewPresenceResponse,
    response_model_by_alias=True,
)
def claim_review_presence(request: Request, job_id: str, body: ReviewPresenceRequest):
    """Tell a review screen whether another screen has this reading open (notice only)."""
    from datetime import UTC

    from app.services import review_presence

    get_job_service(request).get(job_id)
    presence = review_presence.claim(job_id, body.viewer_id, body.take_over)
    since = presence.other_since
    return ReviewPresenceResponse(
        held=presence.held,
        other_since=datetime.fromtimestamp(since, UTC) if since is not None else None,
    )


@router.post("/ocr-jobs/{job_id}/review-presence/release", status_code=204)
def release_review_presence(job_id: str, body: ReviewPresenceRequest):
    from app.services import review_presence

    review_presence.release(job_id, body.viewer_id)


@router.post(
    "/ocr-jobs/{job_id}/reopen", response_model=OcrResultResponse, response_model_by_alias=True
)
def reopen_ocr_result(request: Request, job_id: str, body: OcrReopenRequest):
    return get_ocr_result_service(request).reopen(job_id, base_revision=body.base_revision)


@router.post(
    "/ocr-jobs/{job_id}/cancel-reopen",
    response_model=OcrResultResponse,
    response_model_by_alias=True,
)
def cancel_reopen_ocr_result(request: Request, job_id: str, body: OcrReopenRequest):
    return get_ocr_result_service(request).cancel_reopen(job_id, base_revision=body.base_revision)


@router.post(
    "/ocr-jobs/{job_id}/confirm", response_model=OcrResultResponse, response_model_by_alias=True
)
def confirm_ocr_result(
    request: Request, job_id: str, body: OcrConfirmationRequest, background: BackgroundTasks
):
    result = get_ocr_result_service(request).confirm(
        job_id,
        base_revision=body.base_revision,
        document_name=body.document_name,
        accept_identity_mismatch=body.accept_identity_mismatch,
    )
    # Learn writing rules from the corrections (memory) after the response;
    # never shown and never affects the confirmation.
    from app.ai.api_budget import ApiBudget
    from app.api.dependencies import get_app_settings
    from app.services.template_field_memory_service import TemplateFieldMemoryService

    database = request.app.state.database
    background.add_task(
        TemplateFieldMemoryService(database, request.app.state.storage).learn,
        job_id,
        get_app_settings(request),
        ApiBudget(database),
    )
    # Keep the reading's print for the comparison with later photos (照合).
    from app.services.document_continuation_service import DocumentContinuationService

    background.add_task(
        DocumentContinuationService(
            get_database(request), request.app.state.storage, request.app.state.settings,
        ).prepare,
        job_id,
    )
    return result


@router.get("/ocr-jobs/{job_id}/export", response_class=FileResponse)
def export_ocr_result(request: Request, job_id: str, format: Literal["xlsx"] = "xlsx"):
    path, media_type, name = get_ocr_result_service(request).export_path(job_id, format)
    return FileResponse(path, media_type=media_type, filename=name)


@router.get("/ocr-jobs/{job_id}/excel-table")
def get_ocr_excel_table(request: Request, job_id: str):
    return get_ocr_result_service(request).excel_table(job_id)
