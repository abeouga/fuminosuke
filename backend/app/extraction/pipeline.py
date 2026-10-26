"""The image part of reading a document: no database, no storage, no provider.

The OCR job runs it in the API process, or (cloud) in the extraction Lambda with the same code
and image; both store exactly the images artifact_images() lists, in that order.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import (
    ImageProcessingSettings,
    RegistrationSettings,
    SegmentationSettings,
    StorageSettings,
)
from app.domain.template_config import RegionConfig, TemplateConfig
from app.extraction.handwriting_isolator import HandwritingIsolator
from app.extraction.image_variants import create_variants
from app.extraction.mark_detector import MarkDetector
from app.extraction.region_cropper import RegionCropper, RegionCrops
from app.image_processing.image_loader import ImageLoader
from app.image_processing.photo_registration import register_document
from app.image_processing.registration import RegistrationFailure, TemplateAligner

# (name, file_name, pixels, rgb): the arguments of ArtifactWriter.image(..., replace=True).
ImageItem = tuple[str, str, NDArray[np.uint8], bool]
CROP_VARIANTS = (
    ("alignedColor", "aligned_color", True),
    ("cleanReference", "clean_reference", True),
    ("handwritingOnly", "handwriting_only", True),
    ("enhancedGray", "enhanced_gray", False),
)


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    storage: StorageSettings
    image_processing: ImageProcessingSettings
    registration: RegistrationSettings
    segmentation: SegmentationSettings

    def dump(self) -> dict[str, Any]:
        return {
            "storage": self.storage.model_dump(mode="json"),
            "imageProcessing": self.image_processing.model_dump(mode="json"),
            "registration": self.registration.model_dump(mode="json"),
            "segmentation": self.segmentation.model_dump(mode="json"),
        }

    @classmethod
    def load(cls, value: dict[str, Any]) -> PipelineSettings:
        return cls(
            storage=StorageSettings.model_validate(value["storage"]),
            image_processing=ImageProcessingSettings.model_validate(value["imageProcessing"]),
            registration=RegistrationSettings.model_validate(value["registration"]),
            segmentation=SegmentationSettings.model_validate(value["segmentation"]),
        )


@dataclass(slots=True)
class PipelineResult:
    normalized: NDArray[np.uint8]
    aligned: NDArray[np.uint8]
    handwriting_probability: NDArray[np.uint8]
    handwriting_mask: NDArray[np.uint8]
    handwriting_only: NDArray[np.uint8]
    debug_overlay: NDArray[np.uint8]
    registration_debug: NDArray[np.uint8]
    crops: dict[str, RegionCrops]
    marks: list[dict[str, object]]
    # quality = {**quality_head, <job keys>, registrationDiagnostics?, photoRegistration}
    quality_head: dict[str, Any]
    registration_diagnostics: Any
    photo_registration: Any
    unknown: list[dict[str, object]]


def run_pipeline(
    settings: PipelineSettings,
    *,
    source_bytes: bytes,
    source_media_type: str | None,
    template: NDArray[np.uint8],
    config: TemplateConfig,
    crop_regions: list[RegionConfig],
    progress: Callable[[str, int], None] = lambda _stage, _percent: None,
) -> PipelineResult:
    """Raises RegistrationFailure (normalized_rgb, debug_rgb) when the page cannot be aligned."""
    loader = ImageLoader(settings.storage, settings.image_processing)
    source = loader.load(source_bytes, source_media_type).rgb
    progress("NORMALIZING_DOCUMENT", 12)
    progress("REGISTERING_TEMPLATE", 28)
    document = register_document(
        source, template, aligner=TemplateAligner(settings.registration),
        validation_regions=[(r.x, r.y, r.width, r.height) for r in crop_regions],
    )
    normalized, registered = document.normalized, document.registered
    progress("ISOLATING_HANDWRITING", 48)
    handwriting = HandwritingIsolator(threshold=settings.segmentation.threshold).isolate(
        registered.aligned_rgb, template,
    )
    variants = create_variants(
        registered.aligned_rgb, template, handwriting.handwriting_only_rgb
    )
    progress("DETECTING_MARKS", 58)
    marks = [
        result.to_dict()
        for result in MarkDetector(config).detect_all(registered.aligned_rgb, template)
    ]
    progress("CROPPING_FIELDS", 68)
    crops = RegionCropper(config, crop_regions).crop(variants)
    return PipelineResult(
        normalized=document.registration_input,
        aligned=registered.aligned_rgb,
        handwriting_probability=np.clip(handwriting.probability * 255, 0, 255).astype(np.uint8),
        handwriting_mask=handwriting.mask,
        handwriting_only=handwriting.handwriting_only_rgb,
        debug_overlay=handwriting.debug_overlay_rgb,
        registration_debug=registered.debug_rgb,
        crops=crops,
        marks=marks,
        quality_head={
            **normalized.quality.to_dict(),
            **registered.quality.to_dict(),
            "handwritingCoverageRatio": handwriting.coverage_ratio,
            "unknownOverlapRegionCount": len(handwriting.unknown_overlap_regions),
            "warnings": [*normalized.quality.warnings, *registered.quality.warnings],
        },
        registration_diagnostics=registered.diagnostics,
        photo_registration=document.info,
        unknown=[dict(item.to_dict()) for item in handwriting.unknown_overlap_regions],
    )


def artifact_images(result: PipelineResult, template: NDArray[np.uint8]) -> list[ImageItem]:
    """Every image the reading stores, in order: the page images, then 4 crops per field."""
    items: list[ImageItem] = [
        ("normalized", "normalized.png", result.normalized, True),
        ("alignedSource", "aligned_source.png", result.aligned, True),
        ("cleanTemplate", "clean_template.png", template, True),
        ("handwritingProbability", "handwriting_probability.png",
         result.handwriting_probability, False),
        ("handwritingMask", "handwriting_mask.png", result.handwriting_mask, False),
        ("handwritingOnly", "handwriting_only.png", result.handwriting_only, True),
        ("debugOverlay", "debug_overlay.png", result.debug_overlay, True),
        ("registrationDebug", "registration_debug.png", result.registration_debug, True),
    ]
    for region_id, region in result.crops.items():
        prefix = f"field_crops/{region_id}"
        for name, file, rgb in CROP_VARIANTS:
            items.append((f"fieldCrop.{region_id}.{name}", f"{prefix}/{file}.png",
                          getattr(region, file), rgb))
    return items


def failure_images(
    failure: RegistrationFailure, template: NDArray[np.uint8],
) -> list[ImageItem]:
    normalized = failure.normalized_rgb if failure.normalized_rgb is not None else failure.debug_rgb
    return [
        (name, f"{name}.png", pixels, True)
        for name, pixels in (
            ("registrationNormalized", normalized),
            ("registrationReference", template),
            ("registrationRejectedDebug", failure.debug_rgb),
        )
    ]
