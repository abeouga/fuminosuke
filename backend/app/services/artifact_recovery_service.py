from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import RLock

import cv2
import numpy as np
from numpy.typing import NDArray

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import JobArtifact
from app.db.session import Database
from app.domain.enums import JobType
from app.domain.template_config import FieldMappingConfig, TemplateConfig
from app.image_processing.artifacts import ArtifactWriter
from app.image_processing.document_boundary import DocumentNormalizer
from app.image_processing.image_loader import ImageLoader
from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.job_repository import JobRepository
from app.services.extraction_service import ImageExtractionService
from app.storage.local_storage import LocalStorageService


@dataclass(frozen=True, slots=True)
class ArtifactRecoveryResult:
    artifact_count: int
    restored_source_from_database: bool


class ArtifactRecoveryService:
    """Rebuild derived OCR images from portable source blobs when files are missing."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        storage: LocalStorageService,
    ) -> None:
        self._database = database
        self._storage = storage
        self._loader = ImageLoader(settings.storage, settings.image_processing)
        self._writer = ArtifactWriter(database, storage)
        self._extraction = ImageExtractionService(
            settings=settings,
            database=database,
            storage=storage,
        )
        self._lock = RLock()

    def ensure_artifact(self, job_id: str, artifact_name: str) -> None:
        if self._artifact_exists(job_id, artifact_name):
            return
        self.recover(job_id)
        if not self._artifact_exists(job_id, artifact_name):
            raise self._artifact_not_found()

    def recover(
        self,
        job_id: str,
        *,
        source_bytes: bytes | None = None,
    ) -> ArtifactRecoveryResult:
        with self._lock:
            return self._recover_locked(job_id, source_bytes=source_bytes)

    def _recover_locked(
        self,
        job_id: str,
        *,
        source_bytes: bytes | None,
    ) -> ArtifactRecoveryResult:
        with self._database.session() as session:
            repository = JobRepository(session)
            job = repository.get(job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._artifact_not_found()
            inputs = repository.list_inputs(job_id)
            if len(inputs) != 1:
                raise self._source_missing()
            source_input = inputs[0]
            restored_from_database = False
            if source_bytes is None:
                if source_input.content_bytes is not None:
                    source_bytes = bytes(source_input.content_bytes)
                    restored_from_database = True
                else:
                    source_bytes = self._read_existing(source_input.relative_path)
            if source_bytes is None:
                raise self._source_missing()
            if (
                len(source_bytes) != source_input.file_bytes
                or hashlib.sha256(source_bytes).hexdigest() != source_input.sha256
            ):
                raise AppError(
                    status=409,
                    code="OCR_SOURCE_IMAGE_MISMATCH",
                    title="OCR source image does not match",
                    detail="選択した画像は、このOCR結果で使用した元画像と一致しません。",
                )

            snapshot = job.config_snapshot_json.get("templateSnapshot")
            if not isinstance(snapshot, dict):
                raise self._template_missing()
            expected_template_sha = snapshot.get("cleanTemplateSha256")
            source_artifact_id = snapshot.get("sourceArtifactId")
            if not isinstance(expected_template_sha, str):
                raise self._template_missing()
            template_bytes = (
                bytes(job.template_snapshot_bytes)
                if job.template_snapshot_bytes is not None
                else None
            )
            snapshot_path = snapshot.get("cleanTemplatePath")
            if template_bytes is None and isinstance(snapshot_path, str):
                template_bytes = self._read_existing(snapshot_path)
            if template_bytes is None and isinstance(source_artifact_id, str):
                source_artifact = session.get(JobArtifact, source_artifact_id)
                if source_artifact is not None:
                    template_bytes = self._read_existing(source_artifact.relative_path)
            if (
                template_bytes is not None
                and hashlib.sha256(template_bytes).hexdigest() != expected_template_sha
            ):
                template_bytes = None

            canonical_snapshot_path = (
                f"uploads/{job_id}/template_snapshot/clean_template.png"
            )
            next_snapshot = dict(snapshot)
            next_snapshot["cleanTemplatePath"] = canonical_snapshot_path
            next_config = dict(job.config_snapshot_json)
            next_config["templateSnapshot"] = next_snapshot
            source_input.content_bytes = source_bytes
            if template_bytes is not None:
                job.template_snapshot_bytes = template_bytes
            job.config_snapshot_json = next_config
            previous_stage = job.progress_stage
            previous_percent = job.progress_percent
            attempt_number = job.attempt_number
            session.commit()
            source_relative_path = source_input.relative_path

        self._storage.write_bytes(source_relative_path, source_bytes)
        if template_bytes is not None:
            self._storage.write_bytes(canonical_snapshot_path, template_bytes)
            try:
                self._extraction.process(job_id)
            finally:
                self._restore_progress(job_id, previous_stage, previous_percent)
        else:
            artifact_count = self._rebuild_legacy_previews(
                job_id=job_id,
                attempt_number=attempt_number,
                source_bytes=source_bytes,
                media_type=source_input.media_type,
                config_snapshot=next_config,
            )
            self._restore_progress(job_id, previous_stage, previous_percent)
            return ArtifactRecoveryResult(
                artifact_count=artifact_count,
                restored_source_from_database=restored_from_database,
            )

        with self._database.session() as session:
            artifacts = ArtifactRepository(session).list_for_job(
                job_id,
                attempt_number=attempt_number,
            )
            missing = [
                item.artifact_name
                for item in artifacts
                if not self._storage.resolve(item.relative_path).is_file()
            ]
        if missing:
            raise AppError(
                status=500,
                code="OCR_ARTIFACT_RECOVERY_INCOMPLETE",
                title="OCR artifact recovery was incomplete",
                detail="画像プレビューの再生成を完了できませんでした。",
                metadata={"missingCount": len(missing)},
            )
        return ArtifactRecoveryResult(
            artifact_count=len(artifacts),
            restored_source_from_database=restored_from_database,
        )

    def _rebuild_legacy_previews(
        self,
        *,
        job_id: str,
        attempt_number: int,
        source_bytes: bytes,
        media_type: str,
        config_snapshot: dict[str, object],
    ) -> int:
        raw_template_snapshot = config_snapshot.get("templateSnapshot")
        if not isinstance(raw_template_snapshot, dict):
            raise self._template_missing()
        raw_config = raw_template_snapshot.get("config")
        raw_mappings = config_snapshot.get("fieldMappings")
        if not isinstance(raw_config, dict) or not isinstance(raw_mappings, list):
            raise self._template_missing()
        config = TemplateConfig.model_validate(raw_config)
        mappings = [
            FieldMappingConfig.model_validate(item)
            for item in raw_mappings
            if isinstance(item, dict)
        ]
        source = self._loader.load(source_bytes, media_type).rgb
        aligned = DocumentNormalizer(
            canonical_width=config.canonical_width,
            canonical_height=config.canonical_height,
        ).normalize(source).image_rgb

        def image(
            name: str,
            file_name: str,
            value: NDArray[np.uint8],
            *,
            rgb: bool = True,
        ) -> None:
            self._writer.image(
                job_id=job_id,
                attempt_number=attempt_number,
                name=name,
                file_name=file_name,
                image=value,
                rgb=rgb,
                replace=True,
            )

        image("normalized", "normalized.png", aligned)
        image("alignedSource", "aligned_source.png", aligned)
        image("handwritingOnly", "handwriting_only.png", aligned)
        generated = 3
        for mapping in mappings:
            x1 = max(0, mapping.x)
            y1 = max(0, mapping.y)
            x2 = min(aligned.shape[1], mapping.x + mapping.width)
            y2 = min(aligned.shape[0], mapping.y + mapping.height)
            if x2 <= x1 or y2 <= y1:
                continue
            crop = aligned[y1:y2, x1:x2].copy()
            gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).astype(np.uint8)
            prefix = f"field_crops/{mapping.mapping_id}"
            image(
                f"fieldCrop.{mapping.mapping_id}.alignedColor",
                f"{prefix}/aligned_color.png",
                crop,
            )
            image(
                f"fieldCrop.{mapping.mapping_id}.handwritingOnly",
                f"{prefix}/handwriting_only.png",
                crop,
            )
            image(
                f"fieldCrop.{mapping.mapping_id}.enhancedGray",
                f"{prefix}/enhanced_gray.png",
                gray,
                rgb=False,
            )
            generated += 3
        return generated

    def _restore_progress(
        self,
        job_id: str,
        stage: str | None,
        percent: int,
    ) -> None:
        with self._database.session() as session:
            JobRepository(session).update(
                job_id,
                progress_stage=stage,
                progress_percent=percent,
            )

    def _artifact_exists(self, job_id: str, artifact_name: str) -> bool:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            if job is None:
                return False
            artifact = ArtifactRepository(session).get_by_name(
                job_id=job_id,
                attempt_number=job.attempt_number,
                artifact_name=artifact_name,
            )
            return bool(
                artifact is not None
                and self._storage.resolve(artifact.relative_path).is_file()
            )

    def _read_existing(self, relative_path: str) -> bytes | None:
        path = self._storage.resolve(relative_path)
        return path.read_bytes() if path.is_file() else None

    @staticmethod
    def _artifact_not_found() -> AppError:
        return AppError(
            status=404,
            code="ARTIFACT_NOT_FOUND",
            title="Artifact not found",
            detail="指定された生成物が見つかりません。",
        )

    @staticmethod
    def _source_missing() -> AppError:
        return AppError(
            status=409,
            code="OCR_SOURCE_IMAGE_REQUIRED",
            title="OCR source image is required",
            detail=(
                "別PCからDBだけが移動され、元画像がありません。"
                "このOCR結果で使用した元画像を再登録してください。"
            ),
        )

    @staticmethod
    def _template_missing() -> AppError:
        return AppError(
            status=409,
            code="OCR_TEMPLATE_SNAPSHOT_MISSING",
            title="OCR template snapshot is missing",
            detail="OCR時点のテンプレート画像がないため、プレビューを再生成できません。",
        )
