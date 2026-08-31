from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import cv2
import numpy as np
import yaml
from sqlalchemy import func, select

from app.core.errors import AppError
from app.db.models import LedgerDocument
from app.db.session import Database
from app.domain.field_targets import is_supported_field_path
from app.domain.template_config import (
    DocumentIdentityConfig,
    FieldMappingConfig,
    TemplateConfig,
    build_blank_template_config,
    parse_template_config,
)
from app.domain.template_fields import parse_legacy_spreadsheet_metadata
from app.image_processing.artifacts import ArtifactWriter, encode_png
from app.image_processing.image_loader import verified_image_size
from app.repositories.job_repository import JobRepository
from app.repositories.template_repository import TemplateRepository
from app.services.template_mutation import (
    begin_immediate,
    invalidated_layout_quality,
    template_mutation_lock,
)
from app.storage.local_storage import LocalStorageService


class _Unchanged:
    pass


_UNCHANGED = _Unchanged()


class TemplateManagementService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage
        self._artifact_writer = ArtifactWriter(database, storage)

    def get_field_mappings(self, version_id: str) -> tuple[int, int, list[FieldMappingConfig]]:
        width, height, mappings, _, _ = self.get_mapping_configuration(version_id)
        return width, height, mappings

    def get_mapping_configuration(
        self, version_id: str
    ) -> tuple[
        int,
        int,
        list[FieldMappingConfig],
        DocumentIdentityConfig | None,
        list[DocumentIdentityConfig],
    ]:
        with self._database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._template_not_found()
            config_path = version.config_relative_path
            config_context = (
                version.template_id,
                version.version_number,
                version.canonical_width,
                version.canonical_height,
            )
        config = self._load_version_config(config_path, *config_context)
        return (
            config.canonical_width,
            config.canonical_height,
            config.field_mappings,
            config.document_identity,
            config.effective_document_identity_fields(),
        )

    def save_field_mappings(
        self,
        version_id: str,
        mappings: list[FieldMappingConfig],
        *,
        document_identity: DocumentIdentityConfig | None | _Unchanged = _UNCHANGED,
        document_identity_fields: list[DocumentIdentityConfig] | _Unchanged = _UNCHANGED,
    ) -> list[FieldMappingConfig]:
        with template_mutation_lock():
            return self._save_field_mappings_locked(
                version_id,
                mappings,
                document_identity=document_identity,
                document_identity_fields=document_identity_fields,
            )

    def _save_field_mappings_locked(
        self,
        version_id: str,
        mappings: list[FieldMappingConfig],
        *,
        document_identity: DocumentIdentityConfig | None | _Unchanged,
        document_identity_fields: list[DocumentIdentityConfig] | _Unchanged,
    ) -> list[FieldMappingConfig]:
        unknown = [
            mapping.target_path
            for mapping in mappings
            if not is_supported_field_path(mapping.target_path)
        ]
        if unknown:
            raise AppError(
                status=422,
                code="INVALID_FIELD_MAPPING",
                title="Invalid field mapping",
                detail=f"未対応の保存先です: {unknown[0]}",
            )
        with self._database.session() as session:
            begin_immediate(session)
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._template_not_found()
            if version.status not in {"DRAFT", "ACTIVE"}:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="Field mappings cannot be edited",
                    detail="矩形と項目ラベルはDRAFTまたはACTIVEテンプレートで編集してください。",
                )
            config_path = version.config_relative_path
            config_context = (
                version.template_id,
                version.version_number,
                version.canonical_width,
                version.canonical_height,
            )
            version.quality_json = invalidated_layout_quality(
                version.quality_json,
                bump_input_revision=True,
            )
            session.commit()
        config = self._load_version_config(config_path, *config_context)
        selected_identity: DocumentIdentityConfig | None = (
            config.document_identity
            if isinstance(document_identity, _Unchanged)
            else document_identity
        )
        selected_identity_fields = (
            list(config.document_identity_fields)
            if isinstance(document_identity_fields, _Unchanged)
            else list(document_identity_fields)
        )
        if (
            not isinstance(document_identity, _Unchanged)
            and isinstance(document_identity_fields, _Unchanged)
        ):
            selected_identity_fields = [document_identity] if document_identity is not None else []
        if not isinstance(document_identity_fields, _Unchanged):
            selected_identity = next(
                (
                    field
                    for field in selected_identity_fields
                    if field.match_role == "primary"
                ),
                None,
            )
        invalid_identity_field = next(
            (
                field
                for field in selected_identity_fields
                if not any(
                    mapping.mapping_id == field.mapping_id
                    and mapping.enabled
                    and mapping.mapping_type == "text"
                    and mapping.target_path == field.target_path
                    for mapping in mappings
                )
            ),
            None,
        )
        if invalid_identity_field is not None:
            if isinstance(document_identity_fields, _Unchanged):
                selected_identity_fields = []
                selected_identity = None
            else:
                raise AppError(
                    status=422,
                    code="INVALID_DOCUMENT_IDENTITY",
                    title="Invalid document identity field",
                    detail="書類を見分ける項目は、保存対象の文字欄から選択してください。",
                )
        if selected_identity is not None:
            selected_mapping = next(
                (
                    mapping
                    for mapping in mappings
                    if mapping.mapping_id == selected_identity.mapping_id
                    and mapping.enabled
                    and mapping.mapping_type == "text"
                ),
                None,
            )
            if (
                selected_mapping is None
                or selected_mapping.target_path != selected_identity.target_path
            ):
                if isinstance(document_identity, _Unchanged):
                    selected_identity = None
                    selected_identity_fields = []
                else:
                    raise AppError(
                        status=422,
                        code="INVALID_DOCUMENT_IDENTITY",
                        title="Invalid document identity field",
                        detail="書類を見分ける項目は、保存対象の文字欄から選択してください。",
                    )
        updated = config.model_copy(
            update={
                "field_mappings": mappings,
                "document_identity": selected_identity,
                "document_identity_fields": selected_identity_fields,
            },
            deep=True,
        )
        updated = updated.__class__.model_validate(updated.model_dump(mode="json", by_alias=True))
        payload = yaml.safe_dump(
            updated.model_dump(mode="json", by_alias=True),
            allow_unicode=True,
            sort_keys=False,
        )
        self._storage.write_text(config_path, payload)
        with self._database.session() as session:
            begin_immediate(session)
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise RuntimeError("Template version disappeared")
            version.quality_json = {
                **invalidated_layout_quality(
                    version.quality_json,
                    bump_input_revision=False,
                ),
                "fieldMappingCount": len(mappings),
                "mappingMode": "DETERMINISTIC",
                "documentIdentityMappingId": (
                    selected_identity.mapping_id if selected_identity is not None else None
                ),
                "documentIdentityFieldCount": len(selected_identity_fields),
            }
            session.commit()
        return mappings

    def _load_version_config(
        self,
        config_path: str,
        template_id: str,
        template_version: int,
        canonical_width: int,
        canonical_height: int,
    ) -> TemplateConfig:
        content = self._storage.read_bytes(config_path).decode("utf-8")
        try:
            return parse_template_config(content)
        except (ValueError, yaml.YAMLError):
            legacy = parse_legacy_spreadsheet_metadata(content)
            if legacy is None:
                raise
            name, _ = legacy
            return build_blank_template_config(
                template_id=template_id,
                template_version=template_version,
                name=name,
                canonical_width=canonical_width,
                canonical_height=canonical_height,
            )

    def save_edited_mask(self, version_id: str, content: bytes) -> str:
        with template_mutation_lock():
            return self._save_edited_mask_locked(version_id, content)

    def delete_template(self, template_id: str) -> Literal["deleted", "archived"]:
        with template_mutation_lock(), self._database.session() as session:
            begin_immediate(session)
            repository = TemplateRepository(session)
            template = repository.get_template(template_id)
            if template is None:
                raise self._template_not_found()

            template_document_count = session.scalar(
                select(func.count())
                .select_from(LedgerDocument)
                .where(LedgerDocument.template_id == template_id)
            )
            if template_document_count:
                # Historical ledger records still need their template and
                # version metadata. Hide an in-use template from future
                # selection instead of cascading deletion into those records.
                template.archived_at = datetime.now(UTC)
                session.commit()
                return "archived"

            template.active_version_id = None
            for version in repository.list_versions(template_id):
                session.delete(version)
            session.delete(template)
            session.commit()
        relative_path = f"templates/{template_id}"
        if self._storage.exists(relative_path):
            self._storage.delete_tree(relative_path)
        return "deleted"

    def _save_edited_mask_locked(self, version_id: str, content: bytes) -> str:
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise self._invalid_mask("PNGマスクだけを使用できます。")
        try:
            width, height = verified_image_size(content)
        except ValueError as exc:
            raise self._invalid_mask("マスク画像を読み込めません。") from exc
        with self._database.session() as session:
            repository = TemplateRepository(session)
            version = repository.get_version(version_id)
            if version is None:
                raise AppError(
                    status=404,
                    code="TEMPLATE_NOT_FOUND",
                    title="Template version not found",
                    detail="指定されたテンプレート版が見つかりません。",
                )
            if (width, height) != (
                version.canonical_width,
                version.canonical_height,
            ):
                raise self._invalid_mask("マスク寸法がcanonical幅・高さと一致しません。")
            source_job = JobRepository(session).get(version.source_job_id or "")
            if source_job is None:
                raise self._invalid_mask("元ジョブが見つかりません。")
            source_job_id = source_job.id
            attempt = source_job.attempt_number
            template_id = version.template_id
            version_number = version.version_number
        decoded = cv2.imdecode(
            np.frombuffer(content, dtype=np.uint8),
            cv2.IMREAD_GRAYSCALE,
        )
        if decoded is None:
            raise self._invalid_mask("マスク画像を読み込めません。")
        normalized = (decoded >= 128).astype(np.uint8) * 255
        normalized_content = encode_png(normalized, rgb=False)
        with self._database.session() as session:
            begin_immediate(session)
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise RuntimeError("Template version disappeared")
            version.quality_json = invalidated_layout_quality(
                version.quality_json,
                bump_input_revision=True,
            )
            session.commit()
        artifact = self._artifact_writer.image(
            job_id=source_job_id,
            attempt_number=attempt,
            name="handwritingMaskEdited",
            file_name="handwriting_mask_edited.png",
            image=normalized,
            rgb=False,
            replace=True,
        )
        self._storage.write_bytes(
            f"templates/{template_id}/versions/{version_number}/handwriting_mask_edited.png",
            normalized_content,
        )
        with self._database.session() as session:
            begin_immediate(session)
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise RuntimeError("Template version disappeared")
            version.mask_artifact_id = artifact.id
            version.quality_json = {
                **invalidated_layout_quality(
                    version.quality_json,
                    bump_input_revision=False,
                ),
                "editedMaskArtifactId": artifact.id,
                "editedMaskSha256": artifact.sha256,
                "editedMaskApplied": False,
                "maskSource": "USER_EDITED",
            }
            session.commit()
        return artifact.artifact_name

    @staticmethod
    def _invalid_mask(detail: str) -> AppError:
        return AppError(
            status=422,
            code="INVALID_MASK_DIMENSIONS",
            title="Invalid handwriting mask",
            detail=detail,
        )

    @staticmethod
    def _template_not_found() -> AppError:
        return AppError(
            status=404,
            code="TEMPLATE_NOT_FOUND",
            title="Template version not found",
            detail="指定されたテンプレート版が見つかりません。",
        )
