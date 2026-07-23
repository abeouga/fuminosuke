from __future__ import annotations

import hashlib

import cv2
import numpy as np
import yaml

from app.core.errors import AppError
from app.db.session import Database
from app.domain.field_targets import FIELD_TARGET_BY_PATH
from app.domain.template_config import FieldMappingConfig, parse_template_config
from app.image_processing.artifacts import ArtifactWriter
from app.repositories.job_repository import JobRepository
from app.repositories.template_repository import TemplateRepository
from app.storage.local_storage import LocalStorageService


class TemplateManagementService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage
        self._artifact_writer = ArtifactWriter(database, storage)

    def get_field_mappings(self, version_id: str) -> tuple[int, int, list[FieldMappingConfig]]:
        with self._database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._template_not_found()
            config_path = version.config_relative_path
        config = parse_template_config(self._storage.read_bytes(config_path).decode("utf-8"))
        return config.canonical_width, config.canonical_height, config.field_mappings

    def save_field_mappings(
        self, version_id: str, mappings: list[FieldMappingConfig]
    ) -> list[FieldMappingConfig]:
        unknown = [
            mapping.target_path
            for mapping in mappings
            if mapping.target_path not in FIELD_TARGET_BY_PATH
        ]
        if unknown:
            raise AppError(
                status=422,
                code="INVALID_FIELD_MAPPING",
                title="Invalid field mapping",
                detail=f"未対応の保存先です: {unknown[0]}",
            )
        with self._database.session() as session:
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
        config = parse_template_config(self._storage.read_bytes(config_path).decode("utf-8"))
        updated = config.model_copy(update={"field_mappings": mappings}, deep=True)
        updated = updated.__class__.model_validate(updated.model_dump(mode="json", by_alias=True))
        payload = yaml.safe_dump(
            updated.model_dump(mode="json", by_alias=True),
            allow_unicode=True,
            sort_keys=False,
        )
        self._storage.write_text(config_path, payload)
        with self._database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise RuntimeError("Template version disappeared")
            version.quality_json = {
                **version.quality_json,
                "fieldMappingCount": len(mappings),
                "mappingMode": "DETERMINISTIC",
            }
            session.commit()
        return mappings

    def save_edited_mask(self, version_id: str, content: bytes) -> str:
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise self._invalid_mask("PNGマスクだけを使用できます。")
        decoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if decoded is None:
            raise self._invalid_mask("マスク画像を読み込めません。")
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
            if decoded.shape != (version.canonical_height, version.canonical_width):
                raise self._invalid_mask("マスク寸法がcanonical幅・高さと一致しません。")
            source_job = JobRepository(session).get(version.source_job_id or "")
            if source_job is None:
                raise self._invalid_mask("元ジョブが見つかりません。")
            attempt = source_job.attempt_number
            template_id = version.template_id
            version_number = version.version_number
        normalized = (decoded >= 128).astype(np.uint8) * 255
        artifact = self._artifact_writer.image(
            job_id=source_job.id,
            attempt_number=attempt,
            name="handwritingMaskEdited",
            file_name="handwriting_mask_edited.png",
            image=normalized,
            rgb=False,
        )
        self._storage.write_bytes(
            f"templates/{template_id}/versions/{version_number}/handwriting_mask_edited.png",
            content,
        )
        with self._database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise RuntimeError("Template version disappeared")
            version.mask_artifact_id = artifact.id
            version.quality_json = {
                **version.quality_json,
                "editedMaskSha256": hashlib.sha256(content).hexdigest(),
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
