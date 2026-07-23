from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import JobArtifact, TemplateVersion
from app.db.session import Database
from app.domain.enums import TemplateVersionStatus
from app.domain.template_config import TemplateConfig, load_template_config, parse_template_config
from app.extraction.handwriting_isolator import HandwritingIsolator
from app.extraction.image_variants import create_variants
from app.extraction.mark_detector import MarkDetector
from app.extraction.region_cropper import RegionCropper, RegionCrops
from app.image_processing.artifacts import ArtifactWriter
from app.image_processing.document_boundary import DocumentNormalizer
from app.image_processing.image_loader import ImageLoader
from app.image_processing.registration import TemplateAligner
from app.repositories.job_repository import JobRepository
from app.storage.local_storage import LocalStorageService


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

    def process(self, job_id: str) -> ExtractionOutput:
        started = time.perf_counter()
        with self._database.session() as session:
            repository = JobRepository(session)
            job = repository.get(job_id)
            if job is None or not job.template_version_id:
                raise ValueError("OCR job or template version is missing")
            version = session.get(TemplateVersion, job.template_version_id)
            if version is None or version.status != TemplateVersionStatus.ACTIVE.value:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="Template version is not active",
                    detail="ACTIVEテンプレート版が必要です。",
                )
            clean_artifact = session.get(JobArtifact, version.clean_template_artifact_id)
            if clean_artifact is None:
                raise AppError(
                    status=500,
                    code="ARTIFACT_NOT_FOUND",
                    title="Template artifact not found",
                    detail="テンプレート画像が見つかりません。",
                )
            inputs = repository.list_inputs(job_id)
            if len(inputs) != 1:
                raise ValueError("EXTRACT_DOCUMENT requires exactly one input")
            attempt = job.attempt_number
            template_version_id = version.id
            source_input = inputs[0]
            template_path = clean_artifact.relative_path
            config_path = version.config_relative_path
        config = parse_template_config(self._storage.read_bytes(config_path).decode("utf-8"))
        normalizer = DocumentNormalizer(
            canonical_width=config.canonical_width,
            canonical_height=config.canonical_height,
        )
        cropper = RegionCropper(config)
        mark_detector = MarkDetector(config)
        source = self._loader.load(
            self._storage.read_bytes(source_input.relative_path), source_input.media_type
        ).rgb
        template = self._loader.load(
            self._storage.read_bytes(template_path), clean_artifact.media_type
        ).rgb
        self._progress(job_id, "NORMALIZING_DOCUMENT", 12)
        normalized = normalizer.normalize(source)
        self._progress(job_id, "REGISTERING_TEMPLATE", 28)
        registered = self._aligner.align(normalized.image_rgb, template)
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
        }
        unknown: list[dict[str, object]] = [
            dict(item.to_dict()) for item in handwriting.unknown_overlap_regions
        ]
        artifacts = self._write_artifacts(
            job_id=job_id,
            attempt=attempt,
            normalized=normalized.image_rgb,
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
        )
        return ExtractionOutput(
            job_id=job_id,
            attempt_number=attempt,
            template_version_id=template_version_id,
            aligned_rgb=registered.aligned_rgb,
            template_rgb=template,
            crops=crops,
            marks=marks,
            quality=quality,
            unknown_overlap_regions=unknown,
            artifacts=artifacts,
            config=config,
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
    ) -> dict[str, str]:
        artifacts: dict[str, str] = {}

        def image(name: str, file_name: str, value: NDArray[np.uint8], *, rgb: bool = True) -> None:
            artifact = self._writer.image(
                job_id=job_id,
                attempt_number=attempt,
                name=name,
                file_name=file_name,
                image=value,
                rgb=rgb,
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
        self._writer.json(
            job_id=job_id,
            attempt_number=attempt,
            name="fieldResults",
            file_name="field_results.json",
            value=field_results,
        )
        artifacts["fieldResults"] = "fieldResults"
        return artifacts

    def _progress(self, job_id: str, stage: str, percent: int) -> None:
        with self._database.session() as session:
            JobRepository(session).update(job_id, progress_stage=stage, progress_percent=percent)
