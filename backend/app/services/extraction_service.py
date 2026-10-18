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
from app.extraction.handwriting_isolator import HandwritingIsolator
from app.extraction.image_variants import create_variants
from app.extraction.mark_detector import MarkDetector
from app.extraction.region_cropper import RegionCropper, RegionCrops
from app.image_processing.artifacts import ArtifactWriter
from app.image_processing.image_loader import ImageLoader
from app.image_processing.photo_registration import register_document
from app.image_processing.registration import RegistrationFailure, TemplateAligner
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
        self._aligner = TemplateAligner(settings.registration)
        self._isolator = HandwritingIsolator(threshold=settings.segmentation.threshold)
        self._writer = ArtifactWriter(database, storage)

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
        cropper = RegionCropper(config, crop_regions)
        mark_detector = MarkDetector(config)
        source = self._loader.load(
            self._storage.read_bytes(source_input.relative_path), source_input.media_type
        ).rgb
        template = template_snapshot.template_rgb
        self._progress(job_id, "NORMALIZING_DOCUMENT", 12)
        self._progress(job_id, "REGISTERING_TEMPLATE", 28)
        try:
            document = register_document(
                source, template, aligner=self._aligner,
                validation_regions=[(r.x, r.y, r.width, r.height) for r in crop_regions],
            )
        except RegistrationFailure as exc:
            writer = ArtifactWriter(self._database, self._storage, page_id=page_id)
            normalized_failure = (
                exc.normalized_rgb if exc.normalized_rgb is not None else exc.debug_rgb
            )
            for name, pixels in (
                ("registrationNormalized", normalized_failure),
                ("registrationReference", template),
                ("registrationRejectedDebug", exc.debug_rgb),
            ):
                writer.image(
                    job_id=job_id, attempt_number=attempt, name=name,
                    file_name=f"{name}.png", image=pixels, replace=True,
                )
            writer.json(
                job_id=job_id, attempt_number=attempt, name="registrationFailure",
                file_name="registration_failure.json", replace=True,
                value={
                    "inputSha256": source_input.sha256,
                    "templateSnapshotSha256": template_snapshot.clean_template_sha256,
                    "templateConfigSnapshotSha256": template_snapshot.config_sha256,
                    "registrationSettings": self._settings.registration.model_dump(mode="json"),
                    "diagnostics": exc.metadata,
                },
            )
            raise
        normalized, registered = document.normalized, document.registered
        self._progress(job_id, "ISOLATING_HANDWRITING", 48)
        handwriting = self._isolator.isolate(registered.aligned_rgb, template)
        variants = create_variants(
            registered.aligned_rgb, template, handwriting.handwriting_only_rgb
        )
        self._progress(job_id, "DETECTING_MARKS", 58)
        marks = [
            result.to_dict()
            for result in mark_detector.detect_all(registered.aligned_rgb, template)
        ]
        self._progress(job_id, "CROPPING_FIELDS", 68)
        crops = cropper.crop(variants)
        quality: dict[str, Any] = {
            **normalized.quality.to_dict(),
            **registered.quality.to_dict(),
            "handwritingCoverageRatio": handwriting.coverage_ratio,
            "unknownOverlapRegionCount": len(handwriting.unknown_overlap_regions),
            "warnings": [
                *normalized.quality.warnings,
                *registered.quality.warnings,
            ],
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
        if registered.diagnostics is not None:
            quality["registrationDiagnostics"] = registered.diagnostics
        quality["photoRegistration"] = document.info
        unknown: list[dict[str, object]] = [
            dict(item.to_dict()) for item in handwriting.unknown_overlap_regions
        ]
        artifacts = self._write_artifacts(
            job_id=job_id,
            attempt=attempt,
            normalized=document.registration_input,
            registered=registered.aligned_rgb,
            template=template,
            handwriting_probability=handwriting.probability,
            handwriting_mask=handwriting.mask,
            handwriting_only=handwriting.handwriting_only_rgb,
            debug_overlay=handwriting.debug_overlay_rgb,
            registration_debug=registered.debug_rgb,
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
            aligned_rgb=registered.aligned_rgb,
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

    def _write_artifacts(
        self,
        *,
        job_id: str,
        attempt: int,
        normalized: NDArray[np.uint8],
        registered: NDArray[np.uint8],
        template: NDArray[np.uint8],
        handwriting_probability: NDArray[np.float32],
        handwriting_mask: NDArray[np.uint8],
        handwriting_only: NDArray[np.uint8],
        debug_overlay: NDArray[np.uint8],
        registration_debug: NDArray[np.uint8],
        crops: dict[str, RegionCrops],
        marks: list[dict[str, object]],
        quality: dict[str, Any],
        unknown: list[dict[str, object]],
        page_id: str | None = None,
    ) -> dict[str, str]:
        artifacts: dict[str, str] = {}
        writer = ArtifactWriter(self._database, self._storage, page_id=page_id)

        def image(name: str, file_name: str, value: NDArray[np.uint8], *, rgb: bool = True) -> None:
            artifact = writer.image(
                job_id=job_id,
                attempt_number=attempt,
                name=name,
                file_name=file_name,
                image=value,
                rgb=rgb,
                replace=True,
            )
            artifacts[name] = artifact.artifact_name

        image("normalized", "normalized.png", normalized)
        image("alignedSource", "aligned_source.png", registered)
        image("cleanTemplate", "clean_template.png", template)
        image(
            "handwritingProbability",
            "handwriting_probability.png",
            np.clip(handwriting_probability * 255, 0, 255).astype(np.uint8),
            rgb=False,
        )
        image("handwritingMask", "handwriting_mask.png", handwriting_mask, rgb=False)
        image("handwritingOnly", "handwriting_only.png", handwriting_only)
        image("debugOverlay", "debug_overlay.png", debug_overlay)
        image("registrationDebug", "registration_debug.png", registration_debug)
        field_metadata: dict[str, object] = {}
        for region_id, region in crops.items():
            prefix = f"field_crops/{region_id}"
            image(
                f"fieldCrop.{region_id}.alignedColor",
                f"{prefix}/aligned_color.png",
                region.aligned_color,
            )
            image(
                f"fieldCrop.{region_id}.cleanReference",
                f"{prefix}/clean_reference.png",
                region.clean_reference,
            )
            image(
                f"fieldCrop.{region_id}.handwritingOnly",
                f"{prefix}/handwriting_only.png",
                region.handwriting_only,
            )
            image(
                f"fieldCrop.{region_id}.enhancedGray",
                f"{prefix}/enhanced_gray.png",
                region.enhanced_gray,
                rgb=False,
            )
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
