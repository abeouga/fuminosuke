from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from io import BytesIO
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Body, Depends, File, Request, Response, UploadFile, status
from pydantic import Field

from app.api.auth_dependencies import require_authenticated_user
from app.core.errors import ApiModel, AppError
from app.services.google_oauth_service import AuthenticatedUser
from app.template_packages.activation_service import (
    TemplatePackageActivationResult,
    TemplatePackageActivationService,
)
from app.template_packages.drive_sync_service import TemplatePackageDriveSyncService
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
    expected_active_version_id: str | None = Field(
        ...,
        min_length=36,
        max_length=36,
    )


class TemplatePackageDrivePublishReleaseRequest(TemplatePackageTokenRequest):
    pass


class TemplatePackageDrivePublishDraftRequest(TemplatePackageTokenRequest):
    draft_id: str | None = Field(default=None, min_length=36, max_length=36)
    expected_revision: int | None = Field(default=None, ge=1)
    base_release_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=SEMVER_PATTERN,
    )


class TemplatePackageDriveInspectRequest(ApiModel):
    file_id: str = Field(min_length=1, max_length=255)
    kind: Literal["RELEASE", "DRAFT"]


class TemplatePackageDriveItemView(ApiModel):
    kind: Literal["RELEASE", "DRAFT"]
    file_id: str
    file_name: str
    publisher_id: str
    package_id: str
    release_version: str
    content_digest: str
    package_sha256: str
    draft_id: str | None = None
    draft_revision: int | None = None
    base_release_version: str | None = None
    drive_version: str
    head_revision_id: str | None = None
    etag: str | None = None
    modified_time: datetime | None = None
    file_bytes: int
    trust_status: Literal["UNVERIFIED"]
    sync_status: Literal["REMOTE_ONLY", "SYNCED", "CONFLICT"]


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


def _drive_service(request: Request) -> TemplatePackageDriveSyncService:
    return cast(
        TemplatePackageDriveSyncService,
        request.app.state.template_package_drive_sync,
    )


def _google_subject(user: AuthenticatedUser | None) -> str:
    if user is None:
        raise AppError(
            status=401,
            code="GOOGLE_ACCOUNT_REQUIRED",
            title="Google account is required",
            detail="Googleアカウントを接続してください。",
        )
    return user.subject


@router.post(
    "/exports/inspect",
    response_model=TemplatePackageOperationView,
    response_model_by_alias=True,
)
def inspect_template_package_export(
    request: Request,
    payload: TemplatePackageExportInspectionRequest,
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> TemplatePackageOperationView:
    export_request = TemplateExportRequest(
        template_id=payload.template_id,
        template_version_id=payload.template_version_id,
        publisher_id=payload.publisher_id,
        package_id=payload.package_id,
        version=payload.version,
        include_preview=payload.include_preview,
        include_handwriting_candidates=payload.include_handwriting_candidates,
        output_layout_owner_key=(
            user.subject if user is not None else "local"
        )
        if payload.include_output_layout
        else None,
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


@router.get(
    "/drive/items",
    response_model=list[TemplatePackageDriveItemView],
    response_model_by_alias=True,
)
def template_package_drive_items(
    request: Request,
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> list[TemplatePackageDriveItemView]:
    items = _drive_service(request).list_items(google_subject=_google_subject(user))
    return [TemplatePackageDriveItemView(**item) for item in items]


@router.post(
    "/drive/releases/publish",
    response_model=TemplatePackageDriveItemView,
    response_model_by_alias=True,
)
def publish_template_package_drive_release(
    request: Request,
    payload: TemplatePackageDrivePublishReleaseRequest,
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> TemplatePackageDriveItemView:
    exported = _service(request).confirm_export(payload.confirmation_token)
    item = _drive_service(request).publish_release(
        exported.content,
        file_name=exported.file_name,
        google_subject=_google_subject(user),
    )
    return TemplatePackageDriveItemView(**item)


@router.post(
    "/drive/drafts/publish",
    response_model=TemplatePackageDriveItemView,
    response_model_by_alias=True,
)
def publish_template_package_drive_draft(
    request: Request,
    payload: TemplatePackageDrivePublishDraftRequest,
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> TemplatePackageDriveItemView:
    exported = _service(request).confirm_export(payload.confirmation_token)
    item = _drive_service(request).publish_draft(
        exported.content,
        google_subject=_google_subject(user),
        base_release_version=payload.base_release_version,
        draft_id=payload.draft_id,
        expected_revision=payload.expected_revision,
    )
    return TemplatePackageDriveItemView(**item)


@router.post(
    "/drive/inspect",
    response_model=TemplatePackageOperationView,
    response_model_by_alias=True,
)
def inspect_template_package_from_drive(
    request: Request,
    payload: TemplatePackageDriveInspectRequest,
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> TemplatePackageOperationView:
    content, file_name = _drive_service(request).package_for_inspection(
        payload.file_id,
        kind="release" if payload.kind == "RELEASE" else "draft",
        google_subject=_google_subject(user),
    )
    return _operation_view(
        _service(request).inspect_import(
            file_name,
            BytesIO(content),
            source_type="DRIVE",
        )
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
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> TemplatePackageActivationView:
    result = TemplatePackageActivationService(request.app.state.database).activate(
        installation_id,
        expected_active_version_id=payload.expected_active_version_id,
        owner_key=user.subject if user is not None else "local",
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
