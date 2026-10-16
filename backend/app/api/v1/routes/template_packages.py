from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Body, File, Request, Response, UploadFile, status
from pydantic import Field

from app.core.errors import ApiModel
from app.template_packages.activation_service import (
    TemplatePackageActivationResult,
    TemplatePackageActivationService,
)
from app.template_packages.export_models import TemplateExportInspection, TemplateExportRequest
from app.template_packages.import_models import (
    TemplateImportInspection,
    TemplatePackageImportResult,
)
from app.template_packages.manifest_models import PACKAGE_ID_PATTERN, PUBLISHER_ID_PATTERN
from app.template_packages.operation_service import (
    ErrorCategory,
    TemplatePackageOperation,
    TemplatePackageOperationService,
)
from app.template_packages.semantic_version import SEMVER_PATTERN

router = APIRouter(prefix="/template-packages", tags=["template-packages"])


class TemplatePackageTokenRequest(ApiModel):
    confirmation_token: str = Field(min_length=20, max_length=200)


class TemplatePackageImportConfirmRequest(TemplatePackageTokenRequest):
    allow_older_version: bool = False


class TemplatePackageExportInspectionRequest(ApiModel):
    template_id: str = Field(min_length=36, max_length=36)
    template_version_id: str = Field(min_length=36, max_length=36)
    publisher_id: str = Field(
        min_length=3,
        max_length=253,
        pattern=PUBLISHER_ID_PATTERN,
    )
    package_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=PACKAGE_ID_PATTERN,
    )
    version: str = Field(min_length=1, max_length=100, pattern=SEMVER_PATTERN)
    include_preview: bool = True
    include_handwriting_candidates: bool = False
    include_output_layout: bool = False


class TemplatePackageActivationRequest(ApiModel):
    expected_revision: int = Field(ge=1, strict=True)
    expected_active_version_id: str | None = Field(
        ...,
        min_length=36,
        max_length=36,
    )


class TemplatePackageImportResultView(ApiModel):
    installation_id: str
    local_template_id: str
    local_template_version_id: str
    local_job_id: str
    content_digest: str
    release_version: str
    created: bool
    active_version_changed: bool


class TemplatePackageOperationView(ApiModel):
    operation_id: str
    kind: Literal["IMPORT", "EXPORT"]
    status: Literal["READY", "RUNNING", "COMPLETED", "FAILED"]
    stage: str
    progress_percent: int = Field(ge=0, le=100)
    message: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    file_name: str | None = None
    package_sha256: str | None = None
    confirmation_token: str | None = None
    import_inspection: TemplateImportInspection | None = None
    export_inspection: TemplateExportInspection | None = None
    result: TemplatePackageImportResultView | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_category: ErrorCategory | None = None


class TemplatePackageActivationView(ApiModel):
    installation_id: str
    local_template_id: str
    local_template_version_id: str
    previous_active_version_id: str | None
    active_version_id: str
    candidate_set_count: int
    output_layout_materialized: bool
    already_active: bool


def _service(request: Request) -> TemplatePackageOperationService:
    return cast(
        TemplatePackageOperationService,
        request.app.state.template_package_operations,
    )


@router.post(
    "/exports/inspect",
    response_model=TemplatePackageOperationView,
    response_model_by_alias=True,
)
def inspect_template_package_export(
    request: Request,
    payload: TemplatePackageExportInspectionRequest,
) -> TemplatePackageOperationView:
    export_request = TemplateExportRequest(
        template_id=payload.template_id,
        template_version_id=payload.template_version_id,
        publisher_id=payload.publisher_id,
        package_id=payload.package_id,
        version=payload.version,
        include_preview=payload.include_preview,
        include_handwriting_candidates=payload.include_handwriting_candidates,
        output_layout_owner_key=("local") if payload.include_output_layout else None,
    )
    return _operation_view(_service(request).inspect_export(export_request))


@router.post("/exports/confirm")
def confirm_template_package_export(
    request: Request,
    payload: Annotated[TemplatePackageTokenRequest, Body()],
) -> Response:
    exported = _service(request).confirm_export(payload.confirmation_token)
    return Response(
        content=exported.content,
        media_type="application/vnd.llmocr.template+zip",
        headers={
            "Content-Disposition": f'attachment; filename="{exported.file_name}"',
            "Cache-Control": "no-store",
            "X-Content-Digest": exported.manifest.content_digest,
        },
    )


@router.post(
    "/imports/inspect",
    response_model=TemplatePackageOperationView,
    response_model_by_alias=True,
)
def inspect_template_package_import(
    request: Request,
    file: Annotated[UploadFile, File()],
) -> TemplatePackageOperationView:
    return _operation_view(
        _service(request).inspect_import(
            file.filename or "template.llmocr-template",
            file.file,
        )
    )


@router.post(
    "/imports/confirm",
    response_model=TemplatePackageOperationView,
    response_model_by_alias=True,
    status_code=status.HTTP_202_ACCEPTED,
)
def confirm_template_package_import(
    request: Request,
    payload: Annotated[TemplatePackageImportConfirmRequest, Body()],
) -> TemplatePackageOperationView:
    return _operation_view(
        _service(request).confirm_import(
            payload.confirmation_token,
            allow_older_version=payload.allow_older_version,
        )
    )


@router.get(
    "/operations/{operation_id}",
    response_model=TemplatePackageOperationView,
    response_model_by_alias=True,
)
def template_package_operation(
    request: Request,
    operation_id: str,
) -> TemplatePackageOperationView:
    return _operation_view(_service(request).operation(operation_id))


@router.get("/operations/{operation_id}/assets/{role}")
def template_package_preview_asset(
    request: Request,
    operation_id: str,
    role: Literal["preview", "clean-template"],
) -> Response:
    content, media_type = _service(request).preview_asset(operation_id, role)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.delete(
    "/operations/{operation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_template_package_operation(request: Request, operation_id: str) -> Response:
    _service(request).delete(operation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/installations/{installation_id}/activate",
    response_model=TemplatePackageActivationView,
    response_model_by_alias=True,
)
def activate_template_package_installation(
    request: Request,
    installation_id: str,
    payload: Annotated[TemplatePackageActivationRequest, Body()],
) -> TemplatePackageActivationView:
    result = TemplatePackageActivationService(request.app.state.database).activate(
        installation_id,
        expected_active_version_id=payload.expected_active_version_id,
        expected_revision=payload.expected_revision,
        owner_key="local",
    )
    return _activation_view(result)


def _operation_view(operation: TemplatePackageOperation) -> TemplatePackageOperationView:
    import_inspection = (
        operation.inspection if isinstance(operation.inspection, TemplateImportInspection) else None
    )
    export_inspection = (
        operation.inspection if isinstance(operation.inspection, TemplateExportInspection) else None
    )
    result = _result_view(operation.result) if operation.result is not None else None
    return TemplatePackageOperationView(
        operation_id=operation.operation_id,
        kind=operation.kind,
        status=operation.status,
        stage=operation.stage,
        progress_percent=operation.progress_percent,
        message=operation.message,
        created_at=operation.created_at,
        updated_at=operation.updated_at,
        expires_at=operation.expires_at,
        file_name=operation.file_name,
        package_sha256=operation.package_sha256,
        confirmation_token=operation.confirmation_token,
        import_inspection=import_inspection,
        export_inspection=export_inspection,
        result=result,
        error_code=operation.error_code,
        error_message=operation.error_message,
        error_category=operation.error_category,
    )


def _result_view(result: TemplatePackageImportResult) -> TemplatePackageImportResultView:
    return TemplatePackageImportResultView(**asdict(result))


def _activation_view(result: TemplatePackageActivationResult) -> TemplatePackageActivationView:
    return TemplatePackageActivationView(**asdict(result))
