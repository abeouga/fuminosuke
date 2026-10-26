from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import Settings
from app.core.errors import AppError
from app.db.session import Database
from app.domain.template_config import (
    FieldMappingConfig,
    TemplateConfig,
    load_template_config,
)
from app.extraction.pipeline import (
    PipelineSettings,
    artifact_images,
    failure_images,
    run_pipeline,
)
from app.extraction.region_cropper import RegionCrops
from app.extraction.remote import EncodedImage, RemoteExtraction, encode_images
from app.image_processing.artifacts import ArtifactWriter
from app.image_processing.image_loader import ImageLoader
from app.image_processing.registration import RegistrationFailure
from app.repositories.job_repository import JobRepository
from app.storage.local_storage import LocalStorageService, StoragePathError


@dataclass(slots=True)
class ExtractionOutput:
    job_id: str
    attempt_number: int
    template_version_id: str
    aligned_rgb: NDArray[np.uint8]
    template_rgb: NDArray[np.uint8]
    crops: dict[str, RegionCrops]
    marks: list[dict[str, object]]
    quality: dict[str, Any]
    unknown_overlap_regions: list[dict[str, object]]
    artifacts: dict[str, str]
    config: TemplateConfig
    page_snapshot: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TemplateProcessingSnapshot:
    template_version_id: str
    template_id: str
    template_version_number: int
    source_artifact_id: str
    clean_template_sha256: str
    config_sha256: str
    template_rgb: NDArray[np.uint8]
    config: TemplateConfig


class ImageExtractionService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        storage: LocalStorageService,
    ) -> None:
        self._settings = settings
        self._database = database
        self._storage = storage
        self._config: TemplateConfig = load_template_config()
        self._loader = ImageLoader(settings.storage, settings.image_processing)
        self._writer = ArtifactWriter(database, storage)
        self._remote: RemoteExtraction | None = RemoteExtraction.configured(storage)

    def process(self, job_id: str, *, page_id: str | None = None) -> ExtractionOutput:
        started = time.perf_counter()
        with self._database.session() as session:
            repository = JobRepository(session)
            job = repository.get(job_id)
            if job is None:
                raise ValueError("OCR job is missing")
            inputs = repository.list_inputs(job_id)
            if page_id is None and len(inputs) != 1:
                raise ValueError("EXTRACT_DOCUMENT requires exactly one input")
            attempt = job.attempt_number
            job_snapshot = dict(job.config_snapshot_json)
            from app.domain.print_layout import require_image_ocr

            if page_id is None:
                if "jobPages" in job_snapshot:
                    raise self._invalid_template_snapshot()
                require_image_ocr(job_snapshot)
                source_input = inputs[0]
            else:
                from app.services.job_page_service import JobPageService

                pages = JobPageService(
                    self._database, self._storage, self._settings,
                ).validate(session, job)
                matches = [p for p in pages if p["pageId"] == page_id]
                if len(matches) != 1:
                    raise self._invalid_template_snapshot()
                job_snapshot = matches[0]
                source_input = next(i for i in inputs if i.id == job_snapshot["inputId"])
            linked_template_version_id = job.template_version_id
        template_snapshot = self._load_template_snapshot(
            job_id=job_id,
            job_snapshot=job_snapshot,
            linked_template_version_id=linked_template_version_id,
        )
        config = template_snapshot.config
        raw_mappings = job_snapshot.get("fieldMappings")
        if isinstance(raw_mappings, list):
            snapshot_mappings = [
                FieldMappingConfig.model_validate(item) for item in raw_mappings
            ]
            config = config.model_copy(
                update={"field_mappings": snapshot_mappings},
                deep=True,
            )
        strict_mapped_ocr = (
            bool(job_snapshot.get("ocrEnabled", True))
            and job_snapshot.get("ocrProvider") == "openai"
        )
        if strict_mapped_ocr and not config.mapped_ocr_regions():
            raise AppError(
                status=409,
                code="TEMPLATE_FIELD_MAPPING_REQUIRED",
                title="Template field mappings are required",
                detail=(
                    "OpenAI読み取りには意味づけ済みの手書きセルが必要です。"
                    "テンプレート画面で記入欄とJSON項目を設定してください。"
                ),
            )
        crop_regions = (
            config.mapped_ocr_regions() if strict_mapped_ocr else config.ocr_regions()
        )
        template = template_snapshot.template_rgb
        pipeline_settings = PipelineSettings(
            storage=self._settings.storage,
            image_processing=self._settings.image_processing,
            registration=self._settings.registration,
            segmentation=self._settings.segmentation,
        )
        encoded: list[EncodedImage] | None = None
        remote = None
        if page_id is None and self._remote is not None:
            # Cloud: the image part runs in the extraction Lambda (same code and image);
            # None when it is unavailable, and the reading continues here.
            self._progress(job_id, "NORMALIZING_DOCUMENT", 12)
            self._progress(job_id, "REGISTERING_TEMPLATE", 28)
            remote = self._remote.run(
                job_id=job_id, attempt=attempt, settings=pipeline_settings,
                source_path=source_input.relative_path, source_sha256=source_input.sha256,
                source_media_type=source_input.media_type,
                template_path=str(job_snapshot["templateSnapshot"]["cleanTemplatePath"]),
                template_sha256=template_snapshot.clean_template_sha256,
                template=template, config=config, crop_regions=crop_regions,
                mapped=strict_mapped_ocr,
            )
        if remote is not None and remote.failure is not None:
            self._write_failure(
                job_id, attempt, page_id, remote.images, source_input.sha256,
                template_snapshot, remote.failure,
            )
            raise remote.failure
        if remote is not None and remote.result is not None:
            result, encoded = remote.result, remote.images
        else:
            try:
                result = run_pipeline(
                    pipeline_settings,
                    source_bytes=self._storage.read_bytes(source_input.relative_path),
                    source_media_type=source_input.media_type, template=template,
                    config=config, crop_regions=crop_regions,
                    progress=lambda stage, percent: self._progress(job_id, stage, percent),
                )
            except RegistrationFailure as exc:
                self._write_failure(
                    job_id, attempt, page_id, encode_images(failure_images(exc, template)),
                    source_input.sha256, template_snapshot, exc,
                )
                raise
        quality: dict[str, Any] = {
            **result.quality_head,
            "durationMsBeforeOcr": round((time.perf_counter() - started) * 1000, 2),
            "fieldMappingCount": len(config.mapped_ocr_regions()),
            "fieldMappingHash": job_snapshot.get("fieldMappingHash"),
            "templateSnapshotSha256": template_snapshot.clean_template_sha256,
            "templateConfigSnapshotSha256": template_snapshot.config_sha256,
            "templateSourceArtifactId": template_snapshot.source_artifact_id,
            "ocrCropSource": (
                "TEMPLATE_FIELD_MAPPINGS_SNAPSHOT"
                if strict_mapped_ocr
                else "CONFIGURED_REGIONS"
            ),
        }
        if result.registration_diagnostics is not None:
            quality["registrationDiagnostics"] = result.registration_diagnostics
        quality["photoRegistration"] = result.photo_registration
        crops, marks, unknown = result.crops, result.marks, result.unknown
        artifacts = self._write_artifacts(
            job_id=job_id,
            attempt=attempt,
            images=encoded if encoded is not None else encode_images(
                artifact_images(result, template)
            ),
            crops=crops,
            marks=marks,
            quality=quality,
            unknown=unknown,
            page_id=page_id,
        )
        return ExtractionOutput(
            job_id=job_id,
            attempt_number=attempt,
            template_version_id=template_snapshot.template_version_id,
            aligned_rgb=result.aligned,
            template_rgb=template,
            crops=crops,
            marks=marks,
            quality=quality,
            unknown_overlap_regions=unknown,
            artifacts=artifacts,
            config=config,
            page_snapshot=job_snapshot if page_id is not None else None,
        )

    def _load_template_snapshot(
        self,
        *,
        job_id: str,
        job_snapshot: dict[str, Any],
        linked_template_version_id: str | None,
    ) -> TemplateProcessingSnapshot:
        raw_snapshot = job_snapshot.get("templateSnapshot")
        if not isinstance(raw_snapshot, dict):
            raise self._invalid_template_snapshot()
        template_id = raw_snapshot.get("templateId")
        template_version_id = raw_snapshot.get("templateVersionId")
        template_version_number = raw_snapshot.get("templateVersionNumber")
        source_artifact_id = raw_snapshot.get("sourceArtifactId")
        source_artifact_sha256 = raw_snapshot.get("sourceArtifactSha256")
        clean_template_sha256 = raw_snapshot.get("cleanTemplateSha256")
        clean_template_path = raw_snapshot.get("cleanTemplatePath")
        clean_template_media_type = raw_snapshot.get("cleanTemplateMediaType")
        raw_config = raw_snapshot.get("config")
        config_sha256 = raw_snapshot.get("configSha256")
        if (
            raw_snapshot.get("schemaVersion") != 1
            or not isinstance(template_id, str)
            or not template_id
            or not isinstance(template_version_id, str)
            or not template_version_id
            or (
                linked_template_version_id is not None
                and linked_template_version_id != template_version_id
            )
            or job_snapshot.get("templateVersionId") != template_version_id
            or not isinstance(template_version_number, int)
            or template_version_number < 1
            or not isinstance(source_artifact_id, str)
            or not source_artifact_id
            or not isinstance(source_artifact_sha256, str)
            or not isinstance(clean_template_sha256, str)
            or source_artifact_sha256 != clean_template_sha256
            or not isinstance(clean_template_path, str)
            or clean_template_media_type != "image/png"
            or not isinstance(raw_config, dict)
            or not isinstance(config_sha256, str)
        ):
            raise self._invalid_template_snapshot()
        try:
            snapshot_root = self._storage.resolve(
                f"uploads/{job_id}/template_snapshot"
            )
            template_path = self._storage.resolve(clean_template_path)
            template_path.relative_to(snapshot_root)
            template_bytes = template_path.read_bytes()
        except (OSError, StoragePathError, ValueError) as exc:
            raise self._invalid_template_snapshot() from exc
        if hashlib.sha256(template_bytes).hexdigest() != clean_template_sha256:
            raise self._invalid_template_snapshot()
        try:
            config = TemplateConfig.model_validate(raw_config)
        except ValueError as exc:
            raise self._invalid_template_snapshot() from exc
        canonical_config = json.dumps(
            config.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if hashlib.sha256(canonical_config).hexdigest() != config_sha256:
            raise self._invalid_template_snapshot()
        if (
            config.template_id != template_id
            or config.template_version != template_version_number
        ):
            raise self._invalid_template_snapshot()
        template = self._loader.load(
            template_bytes,
            clean_template_media_type,
        ).rgb
        if template.shape[:2] != (
            config.canonical_height,
            config.canonical_width,
        ):
            raise self._invalid_template_snapshot()
        return TemplateProcessingSnapshot(
            template_version_id=template_version_id,
            template_id=template_id,
            template_version_number=template_version_number,
            source_artifact_id=source_artifact_id,
            clean_template_sha256=clean_template_sha256,
            config_sha256=config_sha256,
            template_rgb=template,
            config=config,
        )

    @staticmethod
    def _invalid_template_snapshot() -> AppError:
        return AppError(
            status=409,
            code="OCR_TEMPLATE_SNAPSHOT_INVALID",
            title="OCR template snapshot is invalid",
            detail=(
                "受付時のテンプレート固定データを確認できません。"
                "この処理は再試行せず、読み取りを新しく開始してください。"
            ),
        )

    def _write_failure(
        self, job_id: str, attempt: int, page_id: str | None, images: list[EncodedImage],
        input_sha256: str, template_snapshot: TemplateProcessingSnapshot,
        failure: RegistrationFailure,
    ) -> None:
        writer = ArtifactWriter(self._database, self._storage, page_id=page_id)
        writer.encoded_images(job_id=job_id, attempt_number=attempt, images=images)
        writer.json(
            job_id=job_id, attempt_number=attempt, name="registrationFailure",
            file_name="registration_failure.json", replace=True,
            value={
                "inputSha256": input_sha256,
                "templateSnapshotSha256": template_snapshot.clean_template_sha256,
                "templateConfigSnapshotSha256": template_snapshot.config_sha256,
                "registrationSettings": self._settings.registration.model_dump(mode="json"),
                "diagnostics": failure.metadata,
            },
        )

    def _write_artifacts(
        self,
        *,
        job_id: str,
        attempt: int,
        images: list[EncodedImage],
        crops: dict[str, RegionCrops],
        marks: list[dict[str, object]],
        quality: dict[str, Any],
        unknown: list[dict[str, object]],
        page_id: str | None = None,
    ) -> dict[str, str]:
        artifacts: dict[str, str] = {}
        writer = ArtifactWriter(self._database, self._storage, page_id=page_id)
        # The page images and the per-field crops (4 per field, about 700 for a large form)
        # are stored as one batch.
        for (name, *_), artifact in zip(
            images,
            writer.encoded_images(job_id=job_id, attempt_number=attempt, images=images),
            strict=True,
        ):
            artifacts[name] = artifact.artifact_name
        field_metadata: dict[str, object] = {}
        for region_id, region in crops.items():
            field_metadata[region_id] = {
                "bbox": region.bbox,
                "fieldType": region.config.field_type,
                "expectedFields": region.config.expected_fields,
            }
        field_results = {
            "regions": field_metadata,
            "marks": marks,
            "quality": quality,
            "unknownOverlapRegions": unknown,
        }
        writer.json(
            job_id=job_id,
            attempt_number=attempt,
            name="fieldResults",
            file_name="field_results.json",
            value=field_results,
            replace=True,
        )
        artifacts["fieldResults"] = "fieldResults"
        return artifacts

    def _progress(self, job_id: str, stage: str, percent: int) -> None:
        with self._database.session() as session:
            JobRepository(session).update(job_id, progress_stage=stage, progress_percent=percent)
