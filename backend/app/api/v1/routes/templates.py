from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import Field, model_validator

from app.api.auth_dependencies import require_authenticated_user
from app.api.dependencies import get_job_service
from app.api.v1.routes.shared import JobAccepted, JobDetail, job_accepted, job_detail
from app.api.v1.routes.uploads import read_upload
from app.core.errors import ApiModel, AppError, FieldError
from app.db.models import TemplateVersion
from app.domain.enums import JobStatus, JobType, TemplateMode
from app.domain.field_targets import (
    FIELD_TARGET_BY_PATH,
    FieldTarget,
    custom_field_key,
    is_supported_field_path,
)
from app.domain.polygon import PolygonPoint
from app.domain.template_config import (
    DocumentIdentityConfig,
    DocumentNameConfig,
    FieldMappingConfig,
)
from app.domain.template_definition import (
    TemplateDefinition,
    TemplateDefinitionRevisionView,
)
from app.layout_analysis.models import LayoutDefinition
from app.repositories.job_repository import JobRepository
from app.repositories.template_repository import TemplateRepository
from app.services.google_oauth_service import AuthenticatedUser
from app.services.job_service import UploadPayload
from app.services.layout_service import LayoutService
from app.services.spreadsheet_template_import_service import SpreadsheetTemplateImportService
from app.services.template_candidate_service import TemplateCandidateService
from app.services.template_definition_service import TemplateDefinitionService
from app.services.template_management_service import TemplateManagementService
from app.template_packages.activation_service import TemplatePackageActivationService

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


class TemplateDeleteResponse(ApiModel):
    outcome: Literal["deleted", "archived"]


class TemplateOutputFolderView(ApiModel):
    template_folder_id: str | None
    template_folder_name: str | None
    effective_folder_id: str | None
    effective_folder_name: str | None
    uses_default: bool


class TemplateOutputFolderSelection(ApiModel):
    folder_id: str = Field(min_length=1, max_length=255)


class BoundaryPoint(ApiModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class TemplateBoundaryUpdate(ApiModel):
    corners: list[BoundaryPoint] = Field(min_length=4, max_length=4)


class TemplateCandidateBindingView(ApiModel):
    target_pattern: str
    label: str


class TemplateCandidateSetView(ApiModel):
    id: str
    name: str
    values: list[str]
    allowed_suffixes: list[str]
    bindings: list[TemplateCandidateBindingView]


class TemplateCandidateSettingsView(ApiModel):
    template_id: str
    sets: list[TemplateCandidateSetView]


class TemplateCandidateSetUpdate(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    values: list[str] = Field(min_length=1, max_length=500)
    allowed_suffixes: list[str] = Field(default_factory=list, max_length=30)
    target_patterns: list[str] = Field(default_factory=list, max_length=300)


class TemplateCandidateSettingsUpdate(ApiModel):
    template_version_id: str = Field(min_length=1, max_length=36)
    sets: list[TemplateCandidateSetUpdate] = Field(default_factory=list, max_length=30)


class MarkOptionView(ApiModel):
    id: str
    label: str
    value: str
    x: int
    y: int
    width: int
    height: int


class CompositeSubfieldView(ApiModel):
    id: str
    semantic_type: Literal["address_prefecture", "address_municipality"]
    target_path: str
    label: str
    x_ratio: float
    y_ratio: float
    width_ratio: float
    height_ratio: float


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
    vertices: list[PolygonPoint] = Field(default_factory=list, max_length=100)
    printed_label_text: str = ""
    mapping_type: str = "text"
    mark_recognition_mode: Literal["automatic", "manual"] = "automatic"
    mark_selection_mode: str = "multiple"
    mark_options: list[MarkOptionView] = Field(default_factory=list)
    composite_subfields: list[CompositeSubfieldView] = Field(default_factory=list)


class FieldMappingTargetView(ApiModel):
    path: str
    label: str
    section: str


class DocumentIdentityView(ApiModel):
    mapping_id: str
    target_path: str
    label: str
    value_type: str = "text"
    exact_length: int | None = None
    match_role: Literal["primary", "confirmation"] = "primary"
    display_role: Literal["title", "subtitle", "none"] = "title"
    match_group_id: str | None = None
    match_group_label: str | None = None
    component_order: int = 0


class DocumentNameView(ApiModel):
    field_paths: list[str] = Field(min_length=1, max_length=20)
    separator: str = Field(default="", max_length=20)
    prefix: str = Field(default="", max_length=100)
    suffix: str = Field(default="", max_length=100)


class FieldMappingsResponse(ApiModel):
    canonical_width: int
    canonical_height: int
    mappings: list[FieldMappingView]
    document_identity: DocumentIdentityView | None
    document_identity_fields: list[DocumentIdentityView] = Field(default_factory=list)
    document_name: DocumentNameView | None = None
    targets: list[FieldMappingTargetView]
    valid_for_ocr: bool
    field_errors: list[FieldError] = Field(default_factory=list)
    warnings: list[str]


class FieldMappingsUpdate(ApiModel):
    mappings: list[FieldMappingView] = Field(max_length=300)
    document_identity: DocumentIdentityView | None = None
    document_identity_fields: list[DocumentIdentityView] = Field(
        default_factory=list,
        max_length=20,
    )
    document_name: DocumentNameView | None = None

    @model_validator(mode="after")
    def validate_identity_roles(self) -> FieldMappingsUpdate:
        fields = self.document_identity_fields or (
            [self.document_identity] if self.document_identity is not None else []
        )
        if not fields:
            return self
        if len({field.target_path for field in fields}) != len(fields):
            raise ValueError("書類を見分ける項目を重複して指定できません。")
        grouped_fields = [field for field in fields if field.match_group_id]
        if grouped_fields:
            if len(grouped_fields) != len(fields):
                raise ValueError("従来の照合条件と照合グループは混在できません。")
            if any(not field.match_group_label for field in grouped_fields):
                raise ValueError("照合グループ名を入力してください。")
            return self
        primary_count = sum(field.match_role == "primary" for field in fields)
        if primary_count != 1 and not (primary_count == 0 and len(fields) >= 2):
            raise ValueError(
                "書類を見分ける基準を1つ指定するか、"
                "2項目以上をすべて「違っていたら確認」にしてください。"
            )
        if sum(field.display_role == "title" for field in fields) != 1:
            raise ValueError("一覧で大きく表示する項目を1つ指定してください。")
        if sum(field.display_role == "subtitle" for field in fields) > 1:
            raise ValueError("見出しの下に表示する項目は1つまでです。")
        return self


def _mapping_targets(
    mappings: list[FieldMappingConfig],
    layout: LayoutDefinition,
) -> list[FieldTarget]:
    targets = [
        FieldTarget(path=entry.target_path, label=entry.label, section=entry.section)
        for entry in layout.label_pool
    ]
    known_paths = {target.path for target in targets}
    used_paths = [relation.target_path for relation in layout.relations]
    used_paths.extend(
        mark_set.target_path
        for cell in layout.cells
        for mark_set in cell.mark_sets
    )
    mapping_by_path = {mapping.target_path: mapping for mapping in mappings}
    for target_path in [*used_paths, *mapping_by_path]:
        if target_path in known_paths:
            continue
        mapping = mapping_by_path.get(target_path)
        known = FIELD_TARGET_BY_PATH.get(target_path)
        key = custom_field_key(target_path)
        targets.append(
            FieldTarget(
                path=target_path,
                label=(mapping.label if mapping else "")
                or (known.label if known else key or target_path),
                section=known.section if known else "追加項目",
            )
        )
        known_paths.add(target_path)
    return targets


def _unassigned_layout_warnings(layout: LayoutDefinition) -> list[str]:
    has_layout_assignments = bool(layout.relations) or any(
        cell.role == "mark" and cell.mark_sets for cell in layout.cells
    )
    if not has_layout_assignments:
        return []
    assigned = {relation.input_cell_id for relation in layout.relations}
    assigned.update(
        cell.id for cell in layout.cells if cell.role == "mark" and cell.mark_sets
    )
    missing = [
        cell.id
        for cell in layout.cells
        if cell.role in {"input", "mark"} and cell.id not in assigned
    ]
    if not missing:
        return []
    return [
        f"読み取る内容が未設定の記入欄が{len(missing)}個あります。"
        "すべて確認してからOCRを有効にしてください。"
    ]


def _mapping_validation(
    mappings: list[FieldMappingConfig],
    document_identity: DocumentIdentityConfig | None = None,
    document_identity_fields: list[DocumentIdentityConfig] | None = None,
    document_name: DocumentNameConfig | None = None,
    *,
    require_identity: bool = False,
) -> tuple[bool, list[str]]:
    enabled = [mapping for mapping in mappings if mapping.enabled]
    invalid = [mapping for mapping in enabled if not is_supported_field_path(mapping.target_path)]
    warnings = [
        (
            f"{mapping.label}: {mapping.target_path} は帳票の行数上限を超えています。"
            "意味づけを修正して再検出してください。"
        )
        for mapping in invalid
    ]
    if not enabled:
        warnings.append("読み取りに使用できる意味づけ済みセルがありません。")
    identity_fields = document_identity_fields or (
        [document_identity] if document_identity is not None else []
    )
    if not identity_fields:
        warnings.append("書類を見分ける項目が未設定です。")
    else:
        selected_count = sum(
            any(
                mapping.mapping_id == field.mapping_id
                and mapping.target_path == field.target_path
                and mapping.mapping_type == "text"
                for mapping in enabled
            )
            for field in identity_fields
        )
        if selected_count != len(identity_fields):
            warnings.append("書類を見分ける項目を、使用中の文字欄から選び直してください。")
        grouped_fields = [field for field in identity_fields if field.match_group_id]
        if grouped_fields:
            if len(grouped_fields) != len(identity_fields):
                warnings.append("従来の照合条件と照合グループは混在できません。")
            if any(not field.match_group_label for field in grouped_fields):
                warnings.append("照合グループ名を入力してください。")
        else:
            primary_count = sum(field.match_role == "primary" for field in identity_fields)
            if primary_count != 1 and not (primary_count == 0 and len(identity_fields) >= 2):
                warnings.append(
                    "書類を見分ける基準を1つ指定するか、"
                    "2項目以上をすべて「違っていたら確認」にしてください。"
                )
            if sum(field.display_role == "title" for field in identity_fields) != 1:
                warnings.append("一覧で大きく表示する項目を1つ指定してください。")
    uses_grouped_matching = bool(identity_fields) and all(
        field.match_group_id for field in identity_fields
    )
    if uses_grouped_matching and document_name is None:
        warnings.append("ライブラリと出力で使う書類名の作り方が未設定です。")
    identity_valid = bool(identity_fields) and not any(
        warning.startswith(
            (
                "書類を見分ける項目",
                "書類を見分ける基準",
                "一覧で大きく",
                "従来の照合条件",
                "照合グループ名",
            )
        )
        for warning in warnings
    )
    return (
        bool(enabled)
        and not invalid
        and (
            (
                identity_valid
                and (document_name is not None or not uses_grouped_matching)
            )
            or not require_identity
        ),
        warnings,
    )


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


@router.post("/template-versions/{version_id}/edit", response_model=TemplateVersionView,
             response_model_by_alias=True)
def edit_template_version(request: Request, version_id: str) -> TemplateVersionView:
    from app.services.template_version_service import TemplateVersionService

    return _version_view(TemplateVersionService(
        request.app.state.database, request.app.state.storage,
    ).edit(version_id))


@router.get("/template-versions/{version_id}/field-identities")
def get_template_field_identities(request: Request, version_id: str) -> dict[str, str]:
    from app.services.template_field_identity import identities

    mappings = TemplateManagementService(
        request.app.state.database, request.app.state.storage,
    ).get_field_mappings(version_id)[2]
    with request.app.state.database.session() as session:
        version = TemplateRepository(session).get_version(version_id)
        if version is None:
            raise AppError(status=404, code="TEMPLATE_NOT_FOUND", title="Template not found",
                           detail="テンプレート版がありません。")
        return identities(session, version, mappings)


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


@router.get(
    "/templates/{template_id}/output-folder",
    response_model=TemplateOutputFolderView,
    response_model_by_alias=True,
)
def get_template_output_folder(
    request: Request,
    template_id: str,
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> TemplateOutputFolderView:
    return TemplateOutputFolderView.model_validate(
        request.app.state.google_drive.template_output_folder(
            template_id,
            user.subject if user else None,
        )
    )


@router.put(
    "/templates/{template_id}/output-folder",
    response_model=TemplateOutputFolderView,
    response_model_by_alias=True,
)
def select_template_output_folder(
    request: Request,
    template_id: str,
    payload: TemplateOutputFolderSelection,
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> TemplateOutputFolderView:
    return TemplateOutputFolderView.model_validate(
        request.app.state.google_drive.select_template_output_folder(
            template_id,
            payload.folder_id,
            user.subject if user else None,
        )
    )


@router.get(
    "/templates/{template_id}/candidate-settings",
    response_model=TemplateCandidateSettingsView,
    response_model_by_alias=True,
)
def get_template_candidate_settings(
    request: Request,
    template_id: str,
) -> TemplateCandidateSettingsView:
    result = TemplateCandidateService(
        request.app.state.database,
        request.app.state.storage,
    ).get_settings(template_id)
    return TemplateCandidateSettingsView.model_validate(result)


@router.put(
    "/templates/{template_id}/candidate-settings",
    response_model=TemplateCandidateSettingsView,
    response_model_by_alias=True,
)
def replace_template_candidate_settings(
    request: Request,
    template_id: str,
    payload: TemplateCandidateSettingsUpdate,
) -> TemplateCandidateSettingsView:
    result = TemplateCandidateService(
        request.app.state.database,
        request.app.state.storage,
    ).replace_settings(
        template_id,
        payload.template_version_id,
        [item.model_dump(mode="json", by_alias=True) for item in payload.sets],
    )
    return TemplateCandidateSettingsView.model_validate(result)


@router.delete(
    "/templates/{template_id}",
    response_model=TemplateDeleteResponse,
    response_model_by_alias=True,
)
def delete_template(request: Request, template_id: str) -> TemplateDeleteResponse:
    outcome = TemplateManagementService(
        request.app.state.database,
        request.app.state.storage,
    ).delete_template(template_id)
    return TemplateDeleteResponse(outcome=outcome)


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
    field_names_json: Annotated[str, Form(alias="fieldNames")] = "[]",
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
    for index, item in enumerate(files):
        try:
            upload = await read_upload(item, max_bytes)
        except Exception:
            for remaining in files[index + 1 :]:
                await remaining.close()
            raise
        uploads.append(upload)

    try:
        field_names = json.loads(field_names_json)
    except json.JSONDecodeError as exc:
        raise AppError(
            status=422,
            code="INVALID_TEMPLATE_FIELD_CATALOG",
            title="Invalid template field catalog",
            detail="保存する項目の形式が正しくありません。",
        ) from exc
    if not isinstance(field_names, list) or any(not isinstance(item, str) for item in field_names):
        raise AppError(
            status=422,
            code="INVALID_TEMPLATE_FIELD_CATALOG",
            title="Invalid template field catalog",
            detail="保存する項目は文字列の一覧で指定してください。",
        )

    job = await job_service.create_template_job(
        name=name,
        mode=mode,
        uploads=uploads,
        idempotency_key=idempotency_key,
        field_names=field_names,
    )
    return job_accepted(job)


@router.post(
    "/spreadsheet-template-jobs",
    response_model=JobAccepted,
    response_model_by_alias=True,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_spreadsheet_template_job(
    request: Request,
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    max_bytes = request.app.state.settings.storage.max_upload_bytes
    try:
        content = await file.read(max_bytes + 1)
    finally:
        await file.close()
    if len(content) > max_bytes:
        raise AppError(
            status=413,
            code="UPLOAD_TOO_LARGE",
            title="Upload is too large",
            detail="XLSXファイルが許容サイズを超えています。",
        )
    job = SpreadsheetTemplateImportService(
        request.app.state.database,
        request.app.state.storage,
    ).import_xlsx(
        name=name,
        source_name=file.filename or "source.xlsx",
        content=content,
        idempotency_key=idempotency_key,
    )
    return job_accepted(job)


@router.get(
    "/template-versions/{version_id}/print",
    response_class=HTMLResponse,
)
def print_template_version(
    request: Request,
    version_id: str,
    auto_print: bool = False,
) -> HTMLResponse:
    with request.app.state.database.session() as session:
        version = TemplateRepository(session).get_version(version_id)
        if version is None:
            raise AppError(
                status=404,
                code="TEMPLATE_NOT_FOUND",
                title="Template not found",
                detail="指定されたテンプレート版が見つかりません。",
            )
        template = TemplateRepository(session).get_template(version.template_id)
        if template is None:
            raise AppError(
                status=404,
                code="TEMPLATE_NOT_FOUND",
                title="Template not found",
                detail="指定されたテンプレートが見つかりません。",
            )
        content = SpreadsheetTemplateImportService.printable_html(
            version,
            template_name=template.name,
            auto_print=auto_print,
        )
    return HTMLResponse(content)


@router.get(
    "/template-versions/{version_id}/print-pdf",
    response_class=FileResponse,
)
def get_template_version_print_pdf(request: Request, version_id: str) -> FileResponse:
    with request.app.state.database.session() as session:
        version = TemplateRepository(session).get_version(version_id)
        if version is None:
            raise AppError(
                status=404,
                code="TEMPLATE_NOT_FOUND",
                title="Template not found",
                detail="指定されたテンプレート版が見つかりません。",
            )
        if version.quality_json.get("printLayout") is not None:
            raise AppError(status=409, code="PRINT_LAYOUT_PDF_UNAVAILABLE",
                           title="Paged PDF is not available",
                           detail="印刷ページ設定はページ画像で確認してください。旧方式のPDFは使用できません。")
        relative_path = version.quality_json.get("spreadsheetPrintablePdfPath")
    if not isinstance(relative_path, str) or not relative_path:
        raise AppError(
            status=404,
            code="TEMPLATE_PRINT_PDF_NOT_FOUND",
            title="Template print PDF not found",
            detail="このテンプレートにはExcel由来の印刷PDFがありません。",
        )
    path = request.app.state.storage.resolve(relative_path)
    if not path.is_file():
        raise AppError(
            status=404,
            code="TEMPLATE_PRINT_PDF_NOT_FOUND",
            title="Template print PDF not found",
            detail="印刷PDFが保存先に見つかりません。テンプレートを再作成してください。",
        )
    return FileResponse(
        path=path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@router.get("/template-jobs/{job_id}/boundary-image", response_class=FileResponse)
def get_template_boundary_image(request: Request, job_id: str) -> FileResponse:
    job = get_job_service(request).get(job_id)
    if (
        job.job_type != JobType.PREPARE_TEMPLATE.value
        or job.status != JobStatus.FAILED.value
        or job.error_code != "DOCUMENT_BOUNDARY_NOT_FOUND"
    ):
        raise AppError(
            status=409,
            code="DOCUMENT_BOUNDARY_REVIEW_NOT_AVAILABLE",
            title="Document boundary review is not available",
            detail="この処理は書類の四隅を指定できる状態ではありません。",
        )
    with request.app.state.database.session() as session:
        inputs = JobRepository(session).list_inputs(job_id)
    if len(inputs) != 1:
        raise AppError(
            status=409,
            code="DOCUMENT_BOUNDARY_SOURCE_NOT_AVAILABLE",
            title="Document boundary source is not available",
            detail="四隅の指定に使用する元画像を取得できません。",
        )
    source = inputs[0]
    path = request.app.state.storage.resolve(source.relative_path)
    if not path.is_file():
        raise AppError(
            status=404,
            code="DOCUMENT_BOUNDARY_SOURCE_NOT_AVAILABLE",
            title="Document boundary source is not available",
            detail="四隅の指定に使用する元画像が見つかりません。",
        )
    return FileResponse(
        path=path,
        media_type=source.media_type,
    )


@router.put(
    "/template-jobs/{job_id}/boundary",
    response_model=JobDetail,
    response_model_by_alias=True,
)
async def update_template_boundary(
    request: Request,
    job_id: str,
    body: TemplateBoundaryUpdate,
) -> JobDetail:
    job = await get_job_service(request).set_template_boundary_and_retry(
        job_id,
        [(point.x, point.y) for point in body.corners],
    )
    return job_detail(job)


@router.post(
    "/template-versions/{version_id}/approve",
    response_model=TemplateVersionView,
    response_model_by_alias=True,
)
def approve_template_version(request: Request, version_id: str) -> TemplateVersionView:
    from app.services.template_mutation import template_mutation_lock

    with template_mutation_lock():
        return _approve_template_version(request, version_id)


def _approve_template_version(request: Request, version_id: str) -> TemplateVersionView:
    with request.app.state.database.session() as session:
        from app.domain.print_layout import require_legacy_ocr

        candidate = TemplateRepository(session).get_version(version_id)
        if candidate is not None:
            require_legacy_ocr(candidate.quality_json)
        if TemplatePackageActivationService.imported_installation_for_version(
            session,
            version_id,
        ) is not None:
            raise AppError(
                status=409,
                code="TEMPLATE_PACKAGE_ACTIVATION_REQUIRED",
                title="Imported template package requires package activation",
                detail=(
                    "取り込み版は読み取り候補と出力レイアウトを同時に反映するため、"
                    "取り込み結果の「有効版にする」から有効化してください。"
                ),
            )
    (
        _,
        _,
        mappings,
        document_identity,
        document_identity_fields,
        document_name,
    ) = TemplateManagementService(
        request.app.state.database,
        request.app.state.storage,
    ).get_mapping_configuration(version_id)
    valid_for_ocr, warnings = _mapping_validation(
        mappings,
        document_identity,
        document_identity_fields,
        document_name,
        require_identity=True,
    )
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
    width, height, mappings, document_identity, document_identity_fields, document_name = (
        TemplateManagementService(
            request.app.state.database,
            request.app.state.storage,
        ).get_mapping_configuration(version_id)
    )
    layout = LayoutService(
        request.app.state.database,
        request.app.state.storage,
    ).get(version_id)
    valid_for_ocr, warnings = _mapping_validation(
        mappings,
        document_identity,
        document_identity_fields,
        document_name,
    )
    layout_warnings = _unassigned_layout_warnings(layout)
    layout_error = LayoutService.mapping_validation_error(layout)
    if layout_error:
        layout_warnings.insert(0, f"[{layout_error.code}] {layout_error.detail}")
    if layout_warnings:
        valid_for_ocr = False
        warnings = [*warnings, *layout_warnings]
    return FieldMappingsResponse(
        canonical_width=width,
        canonical_height=height,
        mappings=[FieldMappingView.model_validate(item.model_dump()) for item in mappings],
        document_identity=(
            DocumentIdentityView.model_validate(document_identity.model_dump())
            if document_identity is not None
            else None
        ),
        document_identity_fields=[
            DocumentIdentityView.model_validate(item.model_dump())
            for item in document_identity_fields
        ],
        document_name=(
            DocumentNameView.model_validate(document_name.model_dump())
            if document_name is not None
            else None
        ),
        targets=[
            FieldMappingTargetView(path=item.path, label=item.label, section=item.section)
            for item in _mapping_targets(mappings, layout)
        ],
        valid_for_ocr=valid_for_ocr,
        field_errors=(
            layout_error.field_errors or [FieldError(field="layout", message=layout_error.detail)]
        ) if layout_error else [],
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
    incoming_identity_fields = body.document_identity_fields or (
        [body.document_identity] if body.document_identity is not None else []
    )
    mappings = service.save_field_mappings(
        version_id,
        [FieldMappingConfig.model_validate(item.model_dump()) for item in body.mappings],
        document_identity=(
            DocumentIdentityConfig.model_validate(body.document_identity.model_dump())
            if body.document_identity is not None
            else None
        ),
        document_identity_fields=[
            DocumentIdentityConfig.model_validate(item.model_dump())
            for item in incoming_identity_fields
        ],
        document_name=(
            DocumentNameConfig.model_validate(body.document_name.model_dump())
            if body.document_name is not None
            else None
        ),
    )
    width, height, _, document_identity, document_identity_fields, document_name = (
        service.get_mapping_configuration(version_id)
    )
    layout = LayoutService(
        request.app.state.database,
        request.app.state.storage,
    ).get(version_id)
    valid_for_ocr, warnings = _mapping_validation(
        mappings,
        document_identity,
        document_identity_fields,
        document_name,
    )
    layout_warnings = _unassigned_layout_warnings(layout)
    layout_error = LayoutService.mapping_validation_error(layout)
    if layout_error:
        layout_warnings.insert(0, f"[{layout_error.code}] {layout_error.detail}")
    if layout_warnings:
        valid_for_ocr = False
        warnings = [*warnings, *layout_warnings]
    return FieldMappingsResponse(
        canonical_width=width,
        canonical_height=height,
        mappings=[FieldMappingView.model_validate(item.model_dump()) for item in mappings],
        document_identity=(
            DocumentIdentityView.model_validate(document_identity.model_dump())
            if document_identity is not None
            else None
        ),
        document_identity_fields=[
            DocumentIdentityView.model_validate(item.model_dump())
            for item in document_identity_fields
        ],
        document_name=(
            DocumentNameView.model_validate(document_name.model_dump())
            if document_name is not None
            else None
        ),
        targets=[
            FieldMappingTargetView(path=item.path, label=item.label, section=item.section)
            for item in _mapping_targets(mappings, layout)
        ],
        valid_for_ocr=valid_for_ocr,
        field_errors=(
            layout_error.field_errors or [FieldError(field="layout", message=layout_error.detail)]
        ) if layout_error else [],
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


@router.post(
    "/template-versions/{version_id}/layout/recognize-labels",
    response_model=LayoutDefinition,
    response_model_by_alias=True,
)
def recognize_template_labels(
    request: Request,
    version_id: str,
    body: LayoutDefinition,
) -> LayoutDefinition:
    return LayoutService(
        request.app.state.database,
        request.app.state.storage,
        handwriting_threshold=request.app.state.settings.segmentation.threshold,
    ).recognize_labels(version_id, body)


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


@router.get(
    "/template-versions/{version_id}/template-definition",
    response_model=TemplateDefinitionRevisionView | None,
    response_model_by_alias=True,
)
def get_template_definition(
    request: Request, version_id: str
) -> TemplateDefinitionRevisionView | None:
    return TemplateDefinitionService(request.app.state.database).latest(version_id)


@router.get(
    "/template-versions/{version_id}/template-definition/history",
    response_model=list[TemplateDefinitionRevisionView],
    response_model_by_alias=True,
)
def get_template_definition_history(
    request: Request, version_id: str
) -> list[TemplateDefinitionRevisionView]:
    return TemplateDefinitionService(request.app.state.database).history(version_id)


@router.post(
    "/template-versions/{version_id}/template-definition/generate",
    response_model=TemplateDefinitionRevisionView,
    response_model_by_alias=True,
)
async def generate_template_definition(
    request: Request, version_id: str
) -> TemplateDefinitionRevisionView:
    return await TemplateDefinitionService(
        request.app.state.database,
        request.app.state.settings.ocr,
    ).generate(version_id)


@router.put(
    "/template-versions/{version_id}/template-definition",
    response_model=TemplateDefinitionRevisionView,
    response_model_by_alias=True,
)
def save_template_definition(
    request: Request, version_id: str, body: TemplateDefinition
) -> TemplateDefinitionRevisionView:
    return TemplateDefinitionService(request.app.state.database).save_human_revision(
        version_id, body
    )


@router.post(
    "/template-versions/{version_id}/template-definition/approve",
    response_model=TemplateDefinitionRevisionView,
    response_model_by_alias=True,
)
def approve_template_definition(
    request: Request,
    version_id: str,
    body: TemplateDefinition | None = None,
) -> TemplateDefinitionRevisionView:
    return TemplateDefinitionService(
        request.app.state.database,
        storage=request.app.state.storage,
    ).approve(version_id, body)


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
        if version.quality_json.get("forkParentVersionId"):
            raise AppError(status=409, code="TEMPLATE_DRAFT_REPROCESS_UNSUPPORTED",
                           title="Verify the editing draft instead",
                           detail="編集用下書きは記入欄の再検出で確定してください。")
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
