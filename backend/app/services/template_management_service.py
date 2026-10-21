from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import cv2
import numpy as np
import yaml
from sqlalchemy import func, select

from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.db.mutation import require_revision
from app.db.session import Database
from app.domain.field_targets import is_supported_field_path
from app.domain.template_config import (
    DocumentIdentityConfig,
    DocumentNameConfig,
    FieldMappingConfig,
    TemplateConfig,
    build_blank_template_config,
    parse_template_config,
)
from app.image_processing.artifacts import ArtifactWriter, encode_png
from app.image_processing.image_loader import verified_image_size
from app.repositories.job_repository import JobRepository
from app.repositories.template_repository import TemplateRepository
from app.services.template_mutation import (
    begin_immediate,
    invalidated_layout_quality,
    require_draft,
    require_isolated_ids,
    require_isolated_structure,
    template_mutation_lock,
)
from app.storage.immutable_artifact import publish_bytes
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
        list[DocumentIdentityConfig],
        DocumentNameConfig | None,
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
            config.effective_document_identity_fields(),
            config.document_name,
        )

    @staticmethod
    def updated_config(
        version, config: TemplateConfig, mappings: list[FieldMappingConfig], *,
        document_identity_fields: list[DocumentIdentityConfig] | _Unchanged = _UNCHANGED,
        document_name: DocumentNameConfig | None | _Unchanged = _UNCHANGED,
    ) -> TemplateConfig:
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
                detail="対応していない保存先が含まれています。",
                metadata={"targetPath": unknown[0]},
            )
        require_draft(version)
        require_isolated_ids(version, [mapping.mapping_id for mapping in mappings])
        require_isolated_structure(
            version, [mapping.model_dump(mode="json", by_alias=True) for mapping in mappings]
        )
        selected_identity_fields = (
            list(config.document_identity_fields)
            if isinstance(document_identity_fields, _Unchanged)
            else list(document_identity_fields)
        )
        selected_document_name = (
            config.document_name if isinstance(document_name, _Unchanged) else document_name
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
                "document_identity_fields": selected_identity_fields,
                "document_name": selected_document_name,
            },
            deep=True,
        )
        updated = updated.__class__.model_validate(updated.model_dump(mode="json", by_alias=True))
        return updated

    def _load_version_config(
        self,
        config_path: str,
        template_id: str,
        template_version: int,
        canonical_width: int,
        canonical_height: int,
    ) -> TemplateConfig:
        content = self._storage.read_bytes(config_path).decode("utf-8")
        return parse_template_config(content)

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


            if self._database.before_delete is not None:
                self._database.before_delete("template", template_id)
            versions = repository.list_versions(template_id)
            referenced = session.scalar(select(ProcessingJob.id).where(
                ProcessingJob.template_version_id.in_(select(TemplateVersion.id).where(
                    TemplateVersion.template_id == template_id,
                )),
            ).limit(1))
            if referenced is not None:
                # Accepted jobs retain their version identity and immutable output binding.
                template.active_version_id, template.archived_at = None, datetime.now(UTC)
                for version in versions:
                    version.status = "ARCHIVED"
                session.commit()
                return "archived"
            template.active_version_id = None
            session.flush()
            for version in versions:
                session.delete(version)
            session.flush()
            session.delete(template)
            session.commit()
        relative_path = f"templates/{template_id}"
        if self._storage.exists(relative_path):
            self._storage.delete_tree(relative_path)
        return "deleted"

    def rename_template(self, template_id: str, name: str) -> tuple[str, str]:
        cleaned_name = name.strip()
        if not cleaned_name or len(cleaned_name) > 200:
            raise AppError(
                status=422,
                code="VALIDATION_FAILED",
                title="Invalid template name",
                detail="テンプレート名は1〜200文字で指定してください。",
            )
        with template_mutation_lock(), self._database.session() as session:
            begin_immediate(session)
            repository = TemplateRepository(session)
            template = repository.get_template(template_id)
            if template is None or template.archived_at is not None:
                raise self._template_not_found()
            if repository.name_exists(cleaned_name, exclude_template_id=template_id):
                raise AppError(
                    status=409,
                    code="TEMPLATE_NAME_CONFLICT",
                    title="Template name already exists",
                    detail="同じ名前のテンプレートが既にあります。区別できる名前を指定してください。",
                )
            template.name = cleaned_name
            session.commit()
        return template_id, cleaned_name

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
                    detail="指定されたテンプレートが見つかりません。",
                )
            require_draft(version)
            if (width, height) != (
                version.canonical_width,
                version.canonical_height,
            ):
                raise self._invalid_mask("マスク寸法がcanonical幅・高さと一致しません。")
            source_job = JobRepository(session).get(version.source_job_id or "")
            if source_job is None:
                raise self._invalid_mask("元ジョブが見つかりません。")
            expected_revision = version.revision
            source_job_id = source_job.id
            attempt = source_job.attempt_number
        decoded = cv2.imdecode(
            np.frombuffer(content, dtype=np.uint8),
            cv2.IMREAD_GRAYSCALE,
        )
        if decoded is None:
            raise self._invalid_mask("マスク画像を読み込めません。")
        normalized = (decoded >= 128).astype(np.uint8) * 255
        normalized_content = encode_png(normalized, rgb=False)
        path = publish_bytes(self._storage, "templates/masks", normalized_content, ".png")
        with self._database.transaction() as session:
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._template_not_found()
            require_draft(version)
            require_revision(version, expected_revision)
            artifact = JobArtifact(
                id=str(uuid4()), job_id=source_job_id, attempt_number=attempt,
                artifact_name=f"handwritingMaskEdited~{uuid4().hex}", media_type="image/png",
                relative_path=path, sha256=hashlib.sha256(normalized_content).hexdigest(),
                width=width, height=height,
            )
            session.add(artifact)
            # The artifact row and owning version become visible together.
            session.flush()
            version.mask_artifact_id = artifact.id
            version.quality_json = {
                **invalidated_layout_quality(version.quality_json, bump_input_revision=True),
                "editedMaskArtifactId": artifact.id, "editedMaskSha256": artifact.sha256,
                "editedMaskApplied": False, "maskSource": "USER_EDITED",
            }
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
            detail="指定されたテンプレートが見つかりません。",
        )
