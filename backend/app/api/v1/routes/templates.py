from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import ConfigDict, Field, model_validator

from app.api.dependencies import get_job_service
from app.api.editing_cutover import legacy_edit_guard
from app.api.template_revision import template_revision_guard
from app.api.v1.routes.shared import JobAccepted, JobDetail, job_accepted, job_detail
from app.api.v1.routes.uploads import read_upload
from app.core.errors import ApiModel, AppError, FieldError
from app.db.models import TemplateVersion
from app.domain.enums import JobStatus, JobType, TemplateMode
from app.domain.field_targets import (
    FieldTarget,
    field_key,
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
from app.services.job_service import UploadPayload
from app.services.layout_service import LayoutService
from app.services.legacy_template_print_service import LegacyTemplatePrintService
from app.services.spreadsheet_template_import_service import SpreadsheetTemplateImportService
from app.services.template_authoring_service import TemplateAuthoringService
from app.services.template_definition_service import TemplateDefinitionService
from app.services.template_field_hint_service import TemplateFieldHintService
from app.services.template_management_service import TemplateManagementService
from app.template_packages.activation_service import TemplatePackageActivationService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["templates"])


class TemplateVersionView(ApiModel):
    id: str
    version_number: int
    revision: int
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


class TemplateDuplicateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    activate: bool = False


class TemplateDuplicateResponse(ApiModel):
    template_id: str
    template_version_id: str


class TemplateNameUpdateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200)


class TemplateNameUpdateView(ApiModel):
    id: str
    name: str


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


class TemplateFieldHintView(ApiModel):
    target_patterns: list[str]
    label: str
    keywords: list[str]
    keywords_enabled: bool
    keywords_forced: bool


class TemplateFieldHintSettingsView(ApiModel):
    resource_revision: int
    template_id: str
    items: list[TemplateFieldHintView]


class TemplateFieldHintUpdate(ApiModel):
    target_patterns: list[str] = Field(min_length=1, max_length=300)
    keywords: list[str] = Field(default_factory=list, max_length=500)
    keywords_enabled: bool = True
    keywords_forced: bool = False


class TemplateFieldHintSettingsUpdate(ApiModel):
    expected_revision: int = Field(ge=1, strict=True)
    template_version_id: str = Field(min_length=1, max_length=36)
    items: list[TemplateFieldHintUpdate] = Field(default_factory=list, max_length=300)


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
    reading_mode: Literal["number"] | None = None
    sequence_number: int | None = Field(default=None, ge=1, strict=True)
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
    mark_recognition_mode: Literal["automatic", "manual", "symbol", "checklist"] = "automatic"
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
    document_identity_fields: list[DocumentIdentityView] = Field(default_factory=list)
    document_name: DocumentNameView | None = None
    targets: list[FieldMappingTargetView]
    valid_for_ocr: bool
    field_errors: list[FieldError] = Field(default_factory=list)
    warnings: list[str]


class FieldMappingsUpdate(ApiModel):
    model_config = ConfigDict(extra="forbid")
    mappings: list[FieldMappingView] = Field(max_length=300)
    document_identity_fields: list[DocumentIdentityView] = Field(
        default_factory=list,
        max_length=20,
    )
    document_name: DocumentNameView | None = None

    @model_validator(mode="after")
    def validate_identity_roles(self) -> FieldMappingsUpdate:
        fields = self.document_identity_fields
        if not fields:
            return self
        if len({field.target_path for field in fields}) != len(fields):
            raise ValueError("書類を見分ける項目を重複して指定できません。")
        grouped_fields = [field for field in fields if field.match_group_id]
        if grouped_fields:
            if len(grouped_fields) != len(fields):
                raise ValueError("単独の識別項目と識別グループは混在できません。")
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
    used_paths.extend(mark_set.target_path for cell in layout.cells for mark_set in cell.mark_sets)
    mapping_by_path = {mapping.target_path: mapping for mapping in mappings}
    for target_path in [*used_paths, *mapping_by_path]:
        if target_path in known_paths:
            continue
        mapping = mapping_by_path.get(target_path)
        key = field_key(target_path)
        targets.append(
            FieldTarget(
                path=target_path,
                label=(mapping.label if mapping else "") or key or target_path,
                section="追加項目",
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
    assigned.update(cell.id for cell in layout.cells if cell.role == "mark" and cell.mark_sets)
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
    document_identity_fields: list[DocumentIdentityConfig] | None = None,
    document_name: DocumentNameConfig | None = None,
    *,
    require_identity: bool = False,
) -> tuple[bool, list[str]]:
    enabled = [mapping for mapping in mappings if mapping.enabled]
    invalid = [mapping for mapping in enabled if not is_supported_field_path(mapping.target_path)]
    warnings = [
        (
            f"「{mapping.label}」に対応していない保存先が設定されています。"
            "意味づけを修正して再検出してください。"
        )
        for mapping in invalid
    ]
    if not enabled:
        warnings.append("読み取りに使用できる意味づけ済みセルがありません。")
    identity_fields = document_identity_fields or []
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
                warnings.append("単独の識別項目と識別グループは混在できません。")
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
            (identity_valid and (document_name is not None or not uses_grouped_matching))
            or not require_identity
        ),
        warnings,
    )


def _version_view(version: TemplateVersion) -> TemplateVersionView:
    return TemplateVersionView(
        id=version.id,
        version_number=version.version_number,
        revision=version.revision,
        mode=version.mode,
        status=version.status,
        canonical_width=version.canonical_width,
        canonical_height=version.canonical_height,
        source_job_id=version.source_job_id,
        quality=version.quality_json,
        created_at=version.created_at,
    )


@router.post(
    "/template-versions/{version_id}/edit",
    response_model=TemplateVersionView,
    response_model_by_alias=True,
dependencies=[Depends(template_revision_guard)])
def edit_template_version(
    request: Request,
    version_id: str,
    base_revision: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
) -> TemplateVersionView:
    from app.services.template_version_service import TemplateVersionService

    return _version_view(
        TemplateVersionService(
            request.app.state.database,
            request.app.state.storage,
        ).edit(version_id, expected_revision=base_revision)
    )


@router.post(
    "/template-versions/{version_id}/duplicate",
    response_model=TemplateDuplicateResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
dependencies=[Depends(template_revision_guard)])
def duplicate_template_version(
    request: Request,
    version_id: str,
    payload: TemplateDuplicateRequest,
) -> TemplateDuplicateResponse:
    from app.services.template_duplicate_service import TemplateDuplicateService

    template_id, template_version_id = TemplateDuplicateService(
        request.app.state.database,
        request.app.state.storage,
    ).duplicate(version_id, payload.name, activate=payload.activate)
    return TemplateDuplicateResponse(
        template_id=template_id,
        template_version_id=template_version_id,
    )


@router.get("/template-versions/{version_id}/field-identities")
def get_template_field_identities(request: Request, version_id: str) -> dict[str, str]:
    from app.services.template_field_identity import identities

    mappings = TemplateManagementService(
        request.app.state.database,
        request.app.state.storage,
    ).get_field_mappings(version_id)[2]
    with request.app.state.database.session() as session:
        version = TemplateRepository(session).get_version(version_id)
        if version is None:
            raise AppError(
                status=404,
                code="TEMPLATE_NOT_FOUND",
                title="Template not found",
                detail="テンプレートがありません。",
            )
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


@router.patch(
    "/templates/{template_id}/name",
    response_model=TemplateNameUpdateView,
    response_model_by_alias=True,
)
def rename_template(
    request: Request,
    template_id: str,
    payload: TemplateNameUpdateRequest,
) -> TemplateNameUpdateView:
    renamed_id, name = TemplateManagementService(
        request.app.state.database,
        request.app.state.storage,
    ).rename_template(template_id, payload.name)
    return TemplateNameUpdateView(id=renamed_id, name=name)


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


class TemplateDisplayNamesView(ApiModel):
    names: dict[str, str]


class TemplateDisplayNameUpdate(ApiModel):
    target_path: str = Field(min_length=1, max_length=200)
    # Empty or null goes back to the template's own name.
    display_name: str | None = Field(default=None, max_length=200)


@router.get("/templates/{template_id}/display-names", response_model=TemplateDisplayNamesView)
def get_template_display_names(request: Request, template_id: str) -> TemplateDisplayNamesView:
    from app.services.template_display_name_service import TemplateDisplayNameService

    return TemplateDisplayNamesView(
        names=TemplateDisplayNameService(request.app.state.database).names(template_id)
    )


@router.put("/templates/{template_id}/display-names", response_model=TemplateDisplayNamesView)
def rename_template_item(
    request: Request, template_id: str, payload: TemplateDisplayNameUpdate
) -> TemplateDisplayNamesView:
    """Display only: saved values, keys, mappings and accepted snapshots are unchanged."""
    from app.services.template_display_name_service import TemplateDisplayNameService

    return TemplateDisplayNamesView(
        names=TemplateDisplayNameService(request.app.state.database).rename(
            template_id, payload.target_path, payload.display_name
        )
    )


class TemplateDocumentNameRule(ApiModel):
    field_paths: list[str] = Field(min_length=1, max_length=20)
    separator: str = Field(default="", max_length=20)
    prefix: str = Field(default="", max_length=100)
    suffix: str = Field(default="", max_length=100)


class TemplateDocumentNameCandidate(ApiModel):
    target_path: str
    label: str
    identity: bool


class TemplateDocumentNameView(ApiModel):
    rule: TemplateDocumentNameRule | None
    template_rule: TemplateDocumentNameRule | None
    custom: bool
    candidates: list[TemplateDocumentNameCandidate]


class TemplateDocumentNameUpdate(ApiModel):
    # null goes back to the name the template was made with.
    rule: TemplateDocumentNameRule | None


@router.get("/templates/{template_id}/document-name", response_model=TemplateDocumentNameView)
def get_template_document_name(request: Request, template_id: str) -> TemplateDocumentNameView:
    from app.services.template_document_name_service import TemplateDocumentNameService

    return TemplateDocumentNameView.model_validate(
        TemplateDocumentNameService(request.app.state.database, request.app.state.storage)
        .settings(template_id)
    )


@router.put("/templates/{template_id}/document-name", response_model=TemplateDocumentNameView)
def save_template_document_name(
    request: Request, template_id: str, payload: TemplateDocumentNameUpdate
) -> TemplateDocumentNameView:
    """Only proposes the name typed on the review screen; versions and snapshots are unchanged."""
    from app.services.template_document_name_service import TemplateDocumentNameService

    return TemplateDocumentNameView.model_validate(
        TemplateDocumentNameService(request.app.state.database, request.app.state.storage)
        .replace(
            template_id,
            payload.rule.model_dump(by_alias=True) if payload.rule else None,
        )
    )


class TemplateItemJoinMember(ApiModel):
    target_path: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=200)
    before: str = Field(default="", max_length=100)
    after: str = Field(default="", max_length=100)


class TemplateItemJoinItem(ApiModel):
    id: str | None = None
    name: str = Field(max_length=200)
    kind: Literal["join", "order", "sections"] = "join"
    separator: str = Field(default="", max_length=10)
    members: list[TemplateItemJoinMember] = Field(max_length=100)


class TemplateItemSectionMember(ApiModel):
    target_path: str
    label: str
    kind: Literal["text", "mark"]


class TemplateItemSection(ApiModel):
    name: str
    members: list[TemplateItemSectionMember]


class TemplateItemBlock(ApiModel):
    """A table, a heading or items under no heading, in form order (「結果の表示順」)."""

    kind: Literal["record", "parent", "independent"]
    title: str
    key: str
    paths: list[str]


class TemplateItemJoinsView(ApiModel):
    items: list[TemplateItemJoinItem]
    suggestions: list[TemplateItemJoinItem] = Field(default_factory=list)
    sections: list[TemplateItemSection] = Field(default_factory=list)
    blocks: list[TemplateItemBlock] = Field(default_factory=list)


class TemplateItemJoinsUpdate(ApiModel):
    items: list[TemplateItemJoinItem] = Field(max_length=100)


@router.get("/templates/{template_id}/item-joins", response_model=TemplateItemJoinsView)
def get_template_item_joins(
    request: Request,
    template_id: str,
    template_version_id: str | None = Query(default=None, alias="templateVersionId"),
) -> TemplateItemJoinsView:
    """Items shown as one value (display only); suggestions from the given version."""
    from app.services.template_item_join_service import TemplateItemJoinService

    return TemplateItemJoinsView.model_validate(
        TemplateItemJoinService(request.app.state.database, request.app.state.storage)
        .settings(template_id, template_version_id)
    )


@router.put("/templates/{template_id}/item-joins", response_model=TemplateItemJoinsView)
def save_template_item_joins(
    request: Request, template_id: str, payload: TemplateItemJoinsUpdate
) -> TemplateItemJoinsView:
    """Display only: readings, mappings and accepted snapshots are unchanged."""
    from app.services.template_item_join_service import TemplateItemJoinService

    items = TemplateItemJoinService(request.app.state.database).replace(
        template_id,
        [item.model_dump(by_alias=True) for item in payload.items],
    )
    return TemplateItemJoinsView.model_validate({"items": items})


@router.post(
    "/templates/{template_id}/item-joins/ai-suggestions", response_model=TemplateItemJoinsView
)
async def suggest_template_item_joins(
    request: Request,
    template_id: str,
    template_version_id: str = Query(alias="templateVersionId"),
) -> TemplateItemJoinsView:
    """One GPT-6 Luna Decisions request (shared API budget); nothing is saved."""
    from app.ai.api_budget import ApiBudget
    from app.api.dependencies import get_app_settings
    from app.services.template_item_join_service import TemplateItemJoinService
    from app.structured.decisions import OpenAIDecisions

    database = request.app.state.database
    chooser = OpenAIDecisions(
        get_app_settings(request), ApiBudget(database), purpose="template.item_joins"
    )
    chooser.check_available()
    suggestions = await TemplateItemJoinService(
        database, request.app.state.storage
    ).ai_suggestions(template_id, template_version_id, chooser)
    return TemplateItemJoinsView.model_validate({"items": [], "suggestions": suggestions})


@router.get(
    "/templates/{template_id}/field-settings",
    response_model=TemplateFieldHintSettingsView,
    response_model_by_alias=True,
)
def get_template_field_settings(
    request: Request,
    template_id: str,
    template_version_id: str = Query(alias="templateVersionId"),
) -> TemplateFieldHintSettingsView:
    result = TemplateFieldHintService(
        request.app.state.database,
        request.app.state.storage,
    ).get_settings(template_id, template_version_id)
    return TemplateFieldHintSettingsView.model_validate(result)


@router.put(
    "/templates/{template_id}/field-settings",
    response_model=TemplateFieldHintSettingsView,
    response_model_by_alias=True,
)
def replace_template_field_settings(
    request: Request,
    template_id: str,
    payload: TemplateFieldHintSettingsUpdate,
) -> TemplateFieldHintSettingsView:
    result = TemplateFieldHintService(
        request.app.state.database,
        request.app.state.storage,
    ).replace_settings(
        template_id,
        payload.template_version_id,
        [item.model_dump(mode="json", by_alias=True) for item in payload.items],
        expected_revision=payload.expected_revision,
    )
    return TemplateFieldHintSettingsView.model_validate(result)


class TemplateMemoryBox(ApiModel):
    x: int
    y: int
    width: int
    height: int


class TemplateMemoryItem(ApiModel):
    id: str
    label: str
    rule: str
    example_before: str | None
    example_after: str | None
    hit_count: int
    updated_at: str
    boxes: list[TemplateMemoryBox]


class TemplateMemoryView(ApiModel):
    template_id: str
    items: list[TemplateMemoryItem]


@router.get(
    "/templates/{template_id}/memory",
    response_model=TemplateMemoryView,
    response_model_by_alias=True,
)
def get_template_memory(
    request: Request,
    template_id: str,
    template_version_id: str = Query(alias="templateVersionId"),
) -> TemplateMemoryView:
    """Writing rules learned at confirmation, with the cells of each item."""
    from app.services.template_field_memory_service import TemplateFieldMemoryService

    return TemplateMemoryView.model_validate(
        TemplateFieldMemoryService(request.app.state.database, request.app.state.storage)
        .view(template_id, template_version_id)
    )


@router.delete(
    "/templates/{template_id}/memory/{memory_id}", status_code=204, response_class=Response
)
def delete_template_memory(request: Request, template_id: str, memory_id: str) -> Response:
    from app.services.template_field_memory_service import TemplateFieldMemoryService

    TemplateFieldMemoryService(request.app.state.database, request.app.state.storage).delete(
        template_id, memory_id
    )
    return Response(status_code=204)


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
    # The import loads the workbook and renders it; keep that off the event loop.
    job = await asyncio.to_thread(
        SpreadsheetTemplateImportService(
            request.app.state.database,
            request.app.state.storage,
        ).import_xlsx,
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
                detail="指定されたテンプレートが見つかりません。",
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


@router.get("/template-versions/{version_id}/blank-files/{format_name}")
def download_legacy_template_print_file(
    request: Request,
    version_id: str,
    format_name: Literal["xlsx", "pdf"],
) -> Response:
    result = LegacyTemplatePrintService(
        request.app.state.database,
        request.app.state.storage,
    ).export(version_id, format_name)
    return Response(
        result.content,
        media_type=(
            "application/pdf"
            if format_name == "pdf"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="print-{version_id}.{format_name}"',
            "ETag": f'"{result.sha256}"',
            "X-Renderer-Qualified": "false",
        },
    )


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
                detail="指定されたテンプレートが見つかりません。",
            )
        if version.quality_json.get("printLayout") is not None:
            raise AppError(
                status=409,
                code="PRINT_LAYOUT_PDF_UNAVAILABLE",
                title="Paged PDF is not available",
                detail="印刷ページ設定はページ画像で確認してください。旧方式のPDFは使用できません。",
            )
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
dependencies=[Depends(template_revision_guard)])
def approve_template_version(
    request: Request,
    version_id: str,
    base_revision: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
) -> TemplateVersionView:
    from app.services.template_mutation import template_mutation_lock

    with template_mutation_lock():
        return _approve_template_version(request, version_id, base_revision)


def _approve_template_version(
    request: Request,
    version_id: str,
    base_revision: str | None = None,
) -> TemplateVersionView:
    with request.app.state.database.session() as session:
        candidate = TemplateRepository(session).get_version(version_id)
        paged = bool(
            candidate is not None and candidate.quality_json.get("printLayout") is not None
        )
        if (
            TemplatePackageActivationService.imported_installation_for_version(
                session,
                version_id,
            )
            is not None
        ):
            raise AppError(
                status=409,
                code="TEMPLATE_PACKAGE_ACTIVATION_REQUIRED",
                title="Imported template package requires package activation",
                detail=(
                    "取り込み版は読み取り候補と出力レイアウトを同時に反映するため、"
                    "取り込み結果の「有効版にする」から有効化してください。"
                ),
            )
    if not paged:
        (
            _,
            _,
            mappings,
            document_identity_fields,
            document_name,
        ) = TemplateManagementService(
            request.app.state.database,
            request.app.state.storage,
        ).get_mapping_configuration(version_id)
        valid_for_ocr, warnings = _mapping_validation(
            mappings,
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
    from app.services.reference_qualification_service import ReferenceQualificationService

    with request.app.state.database.session() as session:
        return _version_view(
            TemplateRepository(session).approve(
                version_id,
                expected_revision=base_revision,
                qualification_service=ReferenceQualificationService(
                    request.app.state.database,
                    request.app.state.storage,
                ),
            )
        )


@router.post(
    "/template-versions/{version_id}/reject",
    response_model=TemplateVersionView,
    response_model_by_alias=True,
dependencies=[Depends(template_revision_guard)])
def reject_template_version(request: Request, version_id: str) -> TemplateVersionView:
    with request.app.state.database.session() as session:
        return _version_view(TemplateRepository(session).reject(version_id))


@router.put("/template-versions/{version_id}/handwriting-mask", dependencies=[Depends(template_revision_guard)])
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
    snapshot = TemplateAuthoringService(request.app.state.database, request.app.state.storage).get(version_id)
    return _mapping_response(snapshot["layout"], snapshot["config"])


def _mapping_response(layout: LayoutDefinition, config) -> FieldMappingsResponse:
    width, height = config.canonical_width, config.canonical_height
    mappings = config.field_mappings
    document_identity_fields, document_name = config.document_identity_fields, config.document_name
    valid_for_ocr, warnings = _mapping_validation(
        mappings,
        document_identity_fields,
        document_name,
    )
    layout_warnings = _unassigned_layout_warnings(layout)
    layout_error = LayoutService.mapping_validation_error(layout)
    if layout_error:
        # The person sees the reason only; the code stays in the server log.
        logger.info("layout not ready for OCR: %s %s", layout_error.code, layout_error.detail)
        layout_warnings.insert(0, layout_error.detail)
    if layout_warnings:
        valid_for_ocr = False
        warnings = [*warnings, *layout_warnings]
    return FieldMappingsResponse(
        canonical_width=width,
        canonical_height=height,
        mappings=[FieldMappingView.model_validate(item.model_dump()) for item in mappings],
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
        )
        if layout_error
        else [],
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
dependencies=[Depends(template_revision_guard)])
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
dependencies=[Depends(template_revision_guard)])
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




class MarkOptionSuggestionRequest(ApiModel):
    layout: LayoutDefinition
    cell_ids: list[str] = Field(min_length=1, max_length=50)
    # The person's note on what to look for (how many choices, their names).
    hint: str = Field(default="", max_length=200)


class SuggestedMarkOption(ApiModel):
    label: str
    x: int
    y: int
    width: int
    height: int


class MarkOptionSuggestionCell(ApiModel):
    cell_id: str
    options: list[SuggestedMarkOption]


class MarkOptionSuggestionGroup(ApiModel):
    cell_id: str
    cells: list[MarkOptionSuggestionCell]


class MarkOptionSuggestionView(ApiModel):
    cells: list[MarkOptionSuggestionCell]
    similar: list[MarkOptionSuggestionGroup]


@router.post(
    "/template-versions/{version_id}/layout/mark-options/suggest",
    response_model=MarkOptionSuggestionView,
    response_model_by_alias=True,
)
async def suggest_template_mark_options(
    request: Request, version_id: str, body: MarkOptionSuggestionRequest
) -> MarkOptionSuggestionView:
    """Printed choices of selection boxes, read from the template image (shared API budget).

    Read-only: the draft sent with the request is what is read, so no revision is checked
    (an autosave between typing a name and pressing the button must not refuse it).

    One request per group of boxes that look the same; the screen applies the result
    to its draft and offers it to the look-alike boxes. Nothing is saved here.
    """
    from app.ai.api_budget import ApiBudget
    from app.api.dependencies import get_app_settings
    from app.services.mark_option_suggestion_service import MarkOptionSuggestionService

    service = MarkOptionSuggestionService(
        get_app_settings(request), ApiBudget(request.app.state.database)
    )
    service.check_available()
    layouts = LayoutService(
        request.app.state.database,
        request.app.state.storage,
        handwriting_threshold=request.app.state.settings.segmentation.threshold,
    )
    image = await asyncio.to_thread(layouts.template_image_rgb, version_id, body.layout)
    result = await service.suggest(image, body.layout, body.cell_ids, body.hint.strip())
    return MarkOptionSuggestionView.model_validate(result)


@router.post(
    "/template-versions/{version_id}/layout/verify",
    response_model=LayoutDefinition,
    response_model_by_alias=True,
dependencies=[Depends(template_revision_guard)])
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
dependencies=[Depends(legacy_edit_guard), Depends(template_revision_guard)])
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
dependencies=[Depends(legacy_edit_guard), Depends(template_revision_guard)])
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
dependencies=[Depends(legacy_edit_guard), Depends(template_revision_guard)])
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
dependencies=[Depends(template_revision_guard)])
async def reprocess_template_version(request: Request, version_id: str,
                                     expected_revision: int = Query(alias="expectedRevision", ge=1)) -> JobAccepted:
    with request.app.state.database.session() as session:
        version = TemplateRepository(session).get_version(version_id)
        if version is None or not version.source_job_id:
            raise AppError(
                status=404,
                code="TEMPLATE_NOT_FOUND",
                title="Template version not found",
                detail="指定されたテンプレートが見つかりません。",
            )
        if version.status != "DRAFT":
            raise AppError(
                status=409,
                code="INVALID_STATE_TRANSITION",
                title="Template version cannot be reprocessed",
                detail="作業途中のテンプレートだけを再処理できます。",
            )
        if version.quality_json.get("forkParentVersionId"):
            raise AppError(
                status=409,
                code="TEMPLATE_DRAFT_REPROCESS_UNSUPPORTED",
                title="Verify the editing draft instead",
                detail="編集用下書きは記入欄の再検出で確定してください。",
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
    return job_accepted(await get_job_service(request).retry(
        source_job_id, expected_revision=expected_revision, reprocess_version_id=version_id,
    ))


class TemplateAuthoringUpdate(ApiModel):
    model_config = ConfigDict(extra="forbid")
    layout: LayoutDefinition
    document_identity_fields: list[DocumentIdentityConfig] = Field(max_length=20)
    document_name: DocumentNameConfig | None


class TemplateAuthoringView(ApiModel):
    version_id: str
    revision: int
    status: str
    layout: LayoutDefinition
    mapping: FieldMappingsResponse
    quality: dict[str, object]


def _authoring_view(snapshot) -> TemplateAuthoringView:
    return TemplateAuthoringView(
        version_id=snapshot["versionId"], revision=snapshot["revision"], status=snapshot["status"],
        layout=snapshot["layout"], mapping=_mapping_response(snapshot["layout"], snapshot["config"]),
        quality=snapshot["quality"],
    )


@router.get("/template-versions/{version_id}/authoring", response_model=TemplateAuthoringView)
def get_template_authoring(request: Request, response: Response, version_id: str) -> TemplateAuthoringView:
    snapshot = TemplateAuthoringService(request.app.state.database, request.app.state.storage).get(version_id)
    response.headers["X-Template-Revision"] = str(snapshot["revision"])
    return _authoring_view(snapshot)


@router.put("/template-versions/{version_id}/authoring", response_model=TemplateAuthoringView,
            dependencies=[Depends(legacy_edit_guard), Depends(template_revision_guard)])
def save_template_authoring(request: Request, version_id: str, body: TemplateAuthoringUpdate,
                            expected_revision: Annotated[int, Query(alias="expectedRevision", ge=1)]) -> TemplateAuthoringView:
    return _authoring_view(TemplateAuthoringService(request.app.state.database, request.app.state.storage).save(
        version_id, expected_revision, body.layout, document_identity_fields=body.document_identity_fields,
        document_name=body.document_name,
    ))
