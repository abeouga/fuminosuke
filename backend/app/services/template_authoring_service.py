from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import ProcessingJob, TemplateVersion
from app.db.mutation import require_revision, revision_conflict
from app.db.session import Database
from app.domain.print_layout import PrintLayoutRequest, page_plan, print_error
from app.domain.template_config import (
    DocumentIdentityConfig,
    DocumentNameConfig,
    TemplateConfig,
    parse_template_config,
)
from app.layout_analysis.models import LayoutDefinition
from app.layout_analysis.record_geometry import validate_record_row_keys
from app.services.layout_service import LayoutService
from app.services.page_authoring_validation import validate_page_edit, validate_page_headings
from app.services.prepared_print_contract import render_key
from app.services.print_layout_service import PrintLayoutService, digest
from app.services.template_field_identity import reconcile
from app.services.template_management_service import (
    _UNCHANGED,
    TemplateManagementService,
    _Unchanged,
)
from app.services.template_mutation import invalidated_layout_quality, require_draft
from app.storage.immutable_artifact import publish_config
from app.storage.local_storage import LocalStorageService


@dataclass(frozen=True)
class PreparedAuthoring:
    version_id: str
    revision: int
    previous_path: str
    previous_bytes: bytes
    previous_config: TemplateConfig
    config: TemplateConfig
    config_path: str
    layout: LayoutDefinition
    quality: dict[str, Any]
    reconcile_mappings: bool


class TemplateAuthoringService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self.database, self.storage = database, storage

    @staticmethod
    def version(session: Session, version_id: str) -> TemplateVersion:
        version = session.get(TemplateVersion, version_id)
        if version is None:
            raise AppError(
                status=404,
                code="TEMPLATE_NOT_FOUND",
                title="Template not found",
                detail="指定されたテンプレートがありません。",
            )
        return version

    def snapshot(self, session: Session, version: TemplateVersion) -> dict[str, Any]:
        config = parse_template_config(
            self.storage.read_bytes(version.config_relative_path).decode("utf-8")
        )
        layout = (
            LayoutDefinition.model_validate(version.layout_json)
            if version.layout_json
            else LayoutDefinition(
                canonical_width=version.canonical_width, canonical_height=version.canonical_height
            )
        )
        if version.mode == "SPREADSHEET_TEMPLATE":
            layout = LayoutService._with_saved_text_placement(
                layout, version.quality_json.get("spreadsheetPrintCells")
            )
        return {
            "versionId": version.id,
            "revision": version.revision,
            "status": version.status,
            "layout": layout,
            "config": config,
            "quality": copy.deepcopy(version.quality_json),
        }

    def get(self, version_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            return self.snapshot(session, self.version(session, version_id))

    def prepare(
        self,
        version_id: str,
        expected_revision: int,
        layout: LayoutDefinition,
        *,
        document_identity_fields: list[DocumentIdentityConfig] | _Unchanged = _UNCHANGED,
        document_name: DocumentNameConfig | None | _Unchanged = _UNCHANGED,
    ) -> PreparedAuthoring:
        with self.database.session() as session:
            version = self.version(session, version_id)
            require_revision(version, expected_revision)
            require_draft(version)
            try:
                validate_record_row_keys(layout.model_dump(mode="json", by_alias=True))
            except ValueError as error:
                raise AppError(
                    status=422, code=str(error), title="Invalid physical row key",
                    detail="行番号の枠は、各行に1つずつ設定してください。",
                ) from error
            previous_path = version.config_relative_path
            previous_bytes = self.storage.read_bytes(previous_path)
            config = parse_template_config(previous_bytes.decode("utf-8"))
            saved_print = copy.deepcopy(version.quality_json.get("printLayout"))
            print_service = PrintLayoutService(self.database, self.storage)
            print_request = None
            if saved_print is not None:
                if saved_print.get("layoutSha256") != digest(version.layout_json):
                    raise print_error(
                        "印刷ページを保存し直してください。", "PRINT_LAYOUT_STALE", 409
                    )
                proposed = layout.model_dump(mode="json", by_alias=True)
                validate_page_edit(version.layout_json, proposed)
                validate_page_headings(proposed, saved_print)
                print_request = PrintLayoutRequest.model_validate(
                    {
                        **{
                            key: saved_print[key]
                            for key in (
                                "paperSize",
                                "orientation",
                                "margins",
                                "horizontalCentering",
                                "verticalCentering",
                                "targetPagesWide",
                                "targetPagesTall",
                            )
                        },
                        "baseRevision": print_service.revision(version),
                        "breakAfterRows": [page["endRow"] for page in saved_print["pages"][:-1]],
                        "autoArrange": False,
                    }
                )
                preflight = page_plan(
                    print_service._source(session, version, proposed), print_request, saved_print
                )
                for page, old in zip(preflight["pages"], saved_print["pages"], strict=True):
                    if any(
                        page[key] != old[key] for key in ("range", "transform", "width", "height")
                    ):
                        raise print_error(
                            "保存したページ境界・寸法を変更できません。",
                            "PRINT_AUTHORING_GEOMETRY_CHANGED",
                            409,
                        )
            layout, mappings, finalized = LayoutService(self.database, self.storage).prepare_layout(
                session, version, layout
            )
            updated = config
            if mappings is not None:
                # Identity is explicit physical mapping + target path. Only the displayed
                # label follows the accepted mapping label; never rebind by label/order.
                identities = document_identity_fields
                if not isinstance(identities, _Unchanged):
                    selected = {
                        (m.mapping_id, m.target_path): m
                        for m in mappings
                        if m.enabled and m.mapping_type == "text"
                    }
                    identities = [
                        field.model_copy(
                            update={"label": selected[(field.mapping_id, field.target_path)].label}
                        )
                        if (field.mapping_id, field.target_path) in selected
                        else field
                        for field in identities
                    ]
                updated = TemplateManagementService.updated_config(
                    version,
                    config,
                    mappings,
                    document_identity_fields=identities,
                    document_name=document_name,
                )
            elif (
                not isinstance(document_identity_fields, _Unchanged)
                and document_identity_fields != config.document_identity_fields
            ) or (
                not isinstance(document_name, _Unchanged) and document_name != config.document_name
            ):
                raise AppError(
                    status=409,
                    code="AUTHORING_MAPPING_INCOMPLETE",
                    title="Mapping incomplete",
                    detail="欄の設定を完成させてから識別項目・書類名を変更してください。",
                )
            # Unchanged bytes keep existing evidence hashes and references valid.
            config_path = (
                previous_path
                if updated == config
                else publish_config(
                    self.storage,
                    yaml.safe_dump(
                        updated.model_dump(mode="json", by_alias=True),
                        allow_unicode=True,
                        sort_keys=False,
                    ),
                )
            )
            quality = {
                **invalidated_layout_quality(version.quality_json, bump_input_revision=True),
                "fieldMappingCount": len(updated.field_mappings),
                "mappingMode": "DETERMINISTIC",
                "documentIdentityFieldCount": len(updated.document_identity_fields),
                "documentNameConfigured": updated.document_name is not None,
                "layoutCellCount": len(layout.cells),
                "layoutLabelCount": sum(cell.role == "label" for cell in layout.cells),
                "layoutParentLabelCount": sum(cell.role == "parent_label" for cell in layout.cells),
                "layoutInputCount": sum(cell.role == "input" for cell in layout.cells),
                "layoutMarkCount": sum(cell.role == "mark" for cell in layout.cells),
                "layoutRelationCount": len(layout.relations),
                "layoutDetector": layout.detector,
                "layoutFinalized": finalized,
                "layoutDraftSaved": True,
                "processingOrder": [
                    "PERSPECTIVE_CORRECTION",
                    "LAYOUT_CLASSIFICATION",
                    "LAYOUT_VERIFICATION",
                ],
            }
            if saved_print is not None and print_request is not None:
                # This read session is never committed. Compute the complete successor
                # using its already-published config before opening the write transaction.
                version.layout_json = layout.model_dump(mode="json", by_alias=True)
                version.config_relative_path = config_path
                plan = print_service._plan(session, version, print_request)
                for page, old in zip(plan["pages"], saved_print["pages"], strict=True):
                    page["referenceImage"] = old["referenceImage"]
                if (
                    saved_print.get("printFiles")
                    and render_key(plan) == saved_print["printFiles"]["renderKey"]
                ):
                    plan["printFiles"] = copy.deepcopy(saved_print["printFiles"])
                    plan["rendererVersion"] = saved_print["rendererVersion"]
                plan["source"] = "user"
                quality["printLayout"] = plan
            return PreparedAuthoring(
                version_id,
                expected_revision,
                previous_path,
                previous_bytes,
                config,
                updated,
                config_path,
                layout,
                quality,
                mappings is not None,
            )

    def persist(self, session: Session, prepared: PreparedAuthoring) -> TemplateVersion:
        """Apply all references and derived state in the caller's transaction."""
        version = self.version(session, prepared.version_id)
        require_revision(version, prepared.revision)
        require_draft(version)
        if (
            version.config_relative_path != prepared.previous_path
            or self.storage.read_bytes(version.config_relative_path) != prepared.previous_bytes
        ):
            raise revision_conflict()
        if (
            parse_template_config(self.storage.read_bytes(prepared.config_path).decode("utf-8"))
            != prepared.config
        ):
            raise revision_conflict()
        # Reconciliation may flush newly allocated sidecar IDs. Do it before dirtying
        # the aggregate, so the accepted authoring save advances its revision once.
        if prepared.reconcile_mappings:
            reconcile(
                session,
                version,
                prepared.previous_config.field_mappings,
                prepared.config.field_mappings,
            )
        version.layout_json = prepared.layout.model_dump(mode="json", by_alias=True)
        version.config_relative_path = prepared.config_path
        version.quality_json = copy.deepcopy(prepared.quality)
        if prepared.quality.get("printLayout") is not None:
            job = session.get(ProcessingJob, version.source_job_id)
            if job is not None:
                job.config_snapshot_json = {
                    **job.config_snapshot_json,
                    "printLayout": prepared.quality["printLayout"],
                }
        session.flush()
        return version

    def save(
        self, version_id: str, expected_revision: int, layout: LayoutDefinition, **settings: Any
    ) -> dict[str, Any]:
        prepared = self.prepare(version_id, expected_revision, layout, **settings)
        with self.database.transaction() as session:
            version = self.persist(session, prepared)
            return self.snapshot(session, version)
