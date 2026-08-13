from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Request, UploadFile, status
from pydantic import Field

from app.api.dependencies import get_job_service
from app.api.v1.routes.shared import JobAccepted, job_accepted
from app.api.v1.routes.uploads import read_upload
from app.core.errors import ApiModel, AppError
from app.db.models import TemplateVersion
from app.domain.enums import TemplateMode
from app.domain.field_targets import (
    FieldTarget,
    custom_field_key,
    field_targets,
    is_supported_field_path,
)
from app.domain.template_config import FieldMappingConfig
from app.layout_analysis.models import LayoutDefinition
from app.repositories.job_repository import JobRepository
from app.repositories.template_repository import TemplateRepository
from app.services.job_service import UploadPayload
from app.services.layout_service import LayoutService
from app.services.template_management_service import TemplateManagementService

router = APIRouter(tags=["templates"])


class TemplateVersionView(ApiModel):
    id: str
    version_number: int
    mode: str
    status: str
    canonical_width: int
    canonical_height: int
    source_job_id: str | None
    quality: dict[str, object]
    created_at: datetime


class TemplateView(ApiModel):
    id: str
    name: str
    document_type: str
    active_version_id: str | None
    created_at: datetime
    versions: list[TemplateVersionView] = Field(default_factory=list)


class FieldMappingView(ApiModel):
    mapping_id: str
    target_path: str
    label: str
    x: int
    y: int
    width: int
    height: int
    padding: int = 4
    enabled: bool = True
    model_detail: str = "high"
    printed_label_text: str = ""


class FieldMappingTargetView(ApiModel):
    path: str
    label: str
    section: str


class FieldMappingsResponse(ApiModel):
    canonical_width: int
    canonical_height: int
    mappings: list[FieldMappingView]
    targets: list[FieldMappingTargetView]
    valid_for_ocr: bool
    warnings: list[str]


class FieldMappingsUpdate(ApiModel):
    mappings: list[FieldMappingView] = Field(max_length=300)


def _mapping_targets(mappings: list[FieldMappingConfig]) -> list[FieldTarget]:
    targets = field_targets()
    known_paths = {target.path for target in targets}
    for mapping in mappings:
        key = custom_field_key(mapping.target_path)
        if key is None or mapping.target_path in known_paths:
            continue
        targets.append(
            FieldTarget(
                path=mapping.target_path,
                label=mapping.label,
                section="追加項目",
            )
        )
        known_paths.add(mapping.target_path)
    return targets


def _mapping_validation(
    mappings: list[FieldMappingConfig],
) -> tuple[bool, list[str]]:
    enabled = [mapping for mapping in mappings if mapping.enabled]
    invalid = [
        mapping for mapping in enabled
        if not is_supported_field_path(mapping.target_path)
    ]
    warnings = [
        (
            f"{mapping.label}: {mapping.target_path} は帳票の行数上限を超えています。"
            "意味づけを修正して再検出してください。"
        )
        for mapping in invalid
    ]
    if not enabled:
        warnings.append("読み取りに使用できる意味づけ済みセルがありません。")
    return bool(enabled) and not invalid, warnings


def _version_view(version: TemplateVersion) -> TemplateVersionView:
    return TemplateVersionView(
        id=version.id,
        version_number=version.version_number,
        mode=version.mode,
        status=version.status,
        canonical_width=version.canonical_width,
        canonical_height=version.canonical_height,
        source_job_id=version.source_job_id,
        quality=version.quality_json,
        created_at=version.created_at,
    )


@router.get("/templates", response_model=list[TemplateView], response_model_by_alias=True)
def list_templates(request: Request) -> list[TemplateView]:
    with request.app.state.database.session() as session:
        repository = TemplateRepository(session)
        return [
            TemplateView(
                id=template.id,
                name=template.name,
                document_type=template.document_type,
                active_version_id=template.active_version_id,
                created_at=template.created_at,
                versions=[],
            )
            for template in repository.list_templates()
        ]


@router.get("/templates/{template_id}", response_model=TemplateView, response_model_by_alias=True)
def get_template(request: Request, template_id: str) -> TemplateView:
    with request.app.state.database.session() as session:
        repository = TemplateRepository(session)
        template = repository.get_template(template_id)
        if template is None:
            raise AppError(
                status=404,
                code="TEMPLATE_NOT_FOUND",
                title="Template not found",
                detail="指定されたテンプレートが見つかりません。",
            )
        return TemplateView(
            id=template.id,
            name=template.name,
            document_type=template.document_type,
            active_version_id=template.active_version_id,
            created_at=template.created_at,
            versions=[_version_view(version) for version in repository.list_versions(template.id)],
        )


@router.post(
    "/template-jobs",
    response_model=JobAccepted,
    response_model_by_alias=True,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_template_job(
    request: Request,
    name: Annotated[str, Form()],
    mode: Annotated[TemplateMode, Form()],
    files: Annotated[list[UploadFile], File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    job_service = get_job_service(request)
    try:
        job_service.validate_template_input_count(mode, len(files))
    except AppError:
        for item in files:
            await item.close()
        raise

    max_bytes = request.app.state.settings.storage.max_upload_bytes
    uploads: list[UploadPayload] = []
    total_bytes = 0
    for index, item in enumerate(files):
        try:
            upload = await read_upload(item, max_bytes)
            total_bytes += len(upload.content)
            job_service.validate_template_batch_bytes(total_bytes)
        except Exception:
            for remaining in files[index + 1 :]:
                await remaining.close()
            raise
        uploads.append(upload)

    job = await job_service.create_template_job(
        name=name,
        mode=mode,
        uploads=uploads,
        idempotency_key=idempotency_key,
    )
    return job_accepted(job)


@router.post(
    "/template-versions/{version_id}/approve",
    response_model=TemplateVersionView,
    response_model_by_alias=True,
)
def approve_template_version(request: Request, version_id: str) -> TemplateVersionView:
    _, _, mappings = TemplateManagementService(
        request.app.state.database,
        request.app.state.storage,
    ).get_field_mappings(version_id)
    valid_for_ocr, warnings = _mapping_validation(mappings)
    if not valid_for_ocr:
        raise AppError(
            status=409,
            code="TEMPLATE_FIELD_MAPPING_OUT_OF_RANGE",
            title="Template field mappings must be repaired",
            detail=warnings[0],
        )
    with request.app.state.database.session() as session:
        return _version_view(TemplateRepository(session).approve(version_id))


@router.post(
    "/template-versions/{version_id}/reject",
    response_model=TemplateVersionView,
    response_model_by_alias=True,
)
def reject_template_version(request: Request, version_id: str) -> TemplateVersionView:
    with request.app.state.database.session() as session:
        return _version_view(TemplateRepository(session).reject(version_id))


@router.put("/template-versions/{version_id}/handwriting-mask")
async def update_handwriting_mask(
    request: Request,
    version_id: str,
    file: Annotated[UploadFile, File()],
) -> dict[str, str]:
    upload = await read_upload(
        file,
        request.app.state.settings.storage.max_upload_bytes,
    )
    name = TemplateManagementService(
        request.app.state.database, request.app.state.storage
    ).save_edited_mask(version_id, upload.content)
    return {"artifactName": name}


@router.get(
    "/template-versions/{version_id}/field-mappings",
    response_model=FieldMappingsResponse,
    response_model_by_alias=True,
)
def get_field_mappings(request: Request, version_id: str) -> FieldMappingsResponse:
    width, height, mappings = TemplateManagementService(
        request.app.state.database, request.app.state.storage
    ).get_field_mappings(version_id)
    valid_for_ocr, warnings = _mapping_validation(mappings)
    return FieldMappingsResponse(
        canonical_width=width,
        canonical_height=height,
        mappings=[FieldMappingView.model_validate(item.model_dump()) for item in mappings],
        targets=[
            FieldMappingTargetView(path=item.path, label=item.label, section=item.section)
            for item in _mapping_targets(mappings)
        ],
        valid_for_ocr=valid_for_ocr,
        warnings=warnings,
    )


@router.put(
    "/template-versions/{version_id}/field-mappings",
    response_model=FieldMappingsResponse,
    response_model_by_alias=True,
)
def update_field_mappings(
    request: Request, version_id: str, body: FieldMappingsUpdate
) -> FieldMappingsResponse:
    service = TemplateManagementService(request.app.state.database, request.app.state.storage)
    mappings = service.save_field_mappings(
        version_id,
        [FieldMappingConfig.model_validate(item.model_dump()) for item in body.mappings],
    )
    width, height, _ = service.get_field_mappings(version_id)
    valid_for_ocr, warnings = _mapping_validation(mappings)
    return FieldMappingsResponse(
        canonical_width=width,
        canonical_height=height,
        mappings=[FieldMappingView.model_validate(item.model_dump()) for item in mappings],
        targets=[
            FieldMappingTargetView(path=item.path, label=item.label, section=item.section)
            for item in _mapping_targets(mappings)
        ],
        valid_for_ocr=valid_for_ocr,
        warnings=warnings,
    )


@router.get(
    "/template-versions/{version_id}/layout",
    response_model=LayoutDefinition,
    response_model_by_alias=True,
)
def get_template_layout(request: Request, version_id: str) -> LayoutDefinition:
    return LayoutService(request.app.state.database, request.app.state.storage).get(version_id)


@router.post(
    "/template-versions/{version_id}/layout/detect",
    response_model=LayoutDefinition,
    response_model_by_alias=True,
)
def detect_template_layout(request: Request, version_id: str) -> LayoutDefinition:
    return LayoutService(
        request.app.state.database,
        request.app.state.storage,
        handwriting_threshold=request.app.state.settings.segmentation.threshold,
    ).detect(version_id)


@router.put(
    "/template-versions/{version_id}/layout",
    response_model=LayoutDefinition,
    response_model_by_alias=True,
)
def save_template_layout(
    request: Request, version_id: str, body: LayoutDefinition
) -> LayoutDefinition:
    return LayoutService(
        request.app.state.database,
        request.app.state.storage,
        handwriting_threshold=request.app.state.settings.segmentation.threshold,
    ).save(version_id, body)


@router.post(
    "/template-versions/{version_id}/layout/verify",
    response_model=LayoutDefinition,
    response_model_by_alias=True,
)
def verify_template_layout(request: Request, version_id: str) -> LayoutDefinition:
    return LayoutService(
        request.app.state.database,
        request.app.state.storage,
        handwriting_threshold=request.app.state.settings.segmentation.threshold,
    ).verify(version_id)


@router.post(
    "/template-versions/{version_id}/reprocess",
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_template_version(request: Request, version_id: str) -> JobAccepted:
    with request.app.state.database.session() as session:
        version = TemplateRepository(session).get_version(version_id)
        if version is None or not version.source_job_id:
            raise AppError(
                status=404,
                code="TEMPLATE_NOT_FOUND",
                title="Template version not found",
                detail="指定されたテンプレート版が見つかりません。",
            )
        if version.status != "DRAFT":
            raise AppError(
                status=409,
                code="INVALID_STATE_TRANSITION",
                title="Template version cannot be reprocessed",
                detail="DRAFTテンプレート版だけを再処理できます。",
            )
        source_job_id = version.source_job_id
        job_repository = JobRepository(session)
        source_job = job_repository.get(source_job_id)
        if source_job is None:
            raise AppError(
                status=404,
                code="JOB_NOT_FOUND",
                title="Source job not found",
                detail="元ジョブが見つかりません。",
            )
        snapshot = dict(source_job.config_snapshot_json)
        snapshot["reprocessTemplateVersionId"] = version_id
        job_repository.update(source_job_id, config_snapshot_json=snapshot)
    return job_accepted(await get_job_service(request).retry(source_job_id))
