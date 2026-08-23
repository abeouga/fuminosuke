from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from app.core.config import Settings
from app.domain.enums import TemplateMode
from app.domain.template_config import TemplateConfig
from app.image_processing.document_boundary import DocumentNormalizer
from app.image_processing.line_detection import LineDetector


@dataclass(slots=True)
class PreparedTemplate:
    clean_template_rgb: NDArray[np.uint8]
    preview_rgb: NDArray[np.uint8]
    canonical_width: int
    canonical_height: int
    images: dict[str, tuple[str, NDArray[np.uint8], bool]] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    handwriting_mask: NDArray[np.uint8] | None = None
    handwriting_candidates: list[dict[str, float | int | str]] = field(default_factory=list)
    unknown_overlap_regions: list[dict[str, object]] = field(default_factory=list)


class TemplateRecoveryPipelines:
    def __init__(self, settings: Settings, config: TemplateConfig) -> None:
        self._settings = settings
        self._config = config
        self._normalizer = DocumentNormalizer(
            canonical_width=config.canonical_width,
            canonical_height=config.canonical_height,
            allow_orientation_swap=True,
        )
        self._line_detector = LineDetector()

    def reference(
        self,
        images: list[NDArray[np.uint8]],
        *,
        source_corners: NDArray[np.float32] | None = None,
    ) -> PreparedTemplate:
        if len(images) != 1:
            raise ValueError("REFERENCE_TEMPLATE requires one image")
        started = time.perf_counter()
        normalized = self._normalizer.normalize(images[0], source_corners=source_corners)
        gray = cv2.cvtColor(normalized.image_rgb, cv2.COLOR_RGB2GRAY)
        line_mask = self._line_detector.detect(gray)
        quality = {
            "mode": TemplateMode.REFERENCE_TEMPLATE.value,
            **normalized.quality.to_dict(),
            "templateConfidence": max(0.0, min(1.0, normalized.quality.boundary_confidence)),
            "lineCoverageRatio": float((line_mask > 0).mean()),
            "warnings": normalized.quality.warnings,
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
        }
        return PreparedTemplate(
            clean_template_rgb=normalized.image_rgb,
            preview_rgb=normalized.image_rgb.copy(),
            canonical_width=normalized.quality.output_width,
            canonical_height=normalized.quality.output_height,
            images={
                "normalized": ("normalized.png", normalized.image_rgb, True),
                "registrationDebug": (
                    "registration_debug.png",
                    normalized.debug_rgb,
                    True,
                ),
            },
            quality=quality,
        )

    def single_experimental(
        self,
        images: list[NDArray[np.uint8]],
        *,
        source_corners: NDArray[np.float32] | None = None,
    ) -> PreparedTemplate:
        if len(images) != 1:
            raise ValueError("SINGLE_IMAGE_EXPERIMENTAL requires one image")
        started = time.perf_counter()
        normalized = self._normalizer.normalize(images[0], source_corners=source_corners)
        quality = {
            "mode": TemplateMode.SINGLE_IMAGE_EXPERIMENTAL.value,
            **normalized.quality.to_dict(),
            "processingOrder": [
                "PERSPECTIVE_CORRECTION",
                "LAYOUT_CLASSIFICATION",
                "HANDWRITING_EXTRACTION",
            ],
            "templateConfidence": 0.35,
            "warnings": [
                *normalized.quality.warnings,
                "SINGLE_IMAGE_REQUIRES_HUMAN_REVIEW",
                "LAYOUT_CLASSIFICATION_REQUIRED_BEFORE_HANDWRITING_EXTRACTION",
            ],
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
        }
        return PreparedTemplate(
            clean_template_rgb=normalized.image_rgb,
            preview_rgb=normalized.image_rgb.copy(),
            canonical_width=normalized.quality.output_width,
            canonical_height=normalized.quality.output_height,
            images={
                "normalized": ("normalized.png", normalized.image_rgb, True),
                "registrationDebug": (
                    "registration_debug.png",
                    normalized.debug_rgb,
                    True,
                ),
            },
            quality=quality,
        )
