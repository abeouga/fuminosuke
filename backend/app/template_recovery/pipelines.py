from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.enums import TemplateMode
from app.domain.template_config import TemplateConfig
from app.image_processing.document_boundary import DocumentNormalizer
from app.image_processing.line_detection import LineDetector
from app.image_processing.registration import TemplateAligner
from app.segmentation.candidate_regions import handwriting_candidate_regions


@dataclass(slots=True)
class PreparedTemplate:
    clean_template_rgb: NDArray[np.uint8]
    preview_rgb: NDArray[np.uint8]
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
        )
        self._aligner = TemplateAligner(settings.registration)
        self._line_detector = LineDetector()

    def reference(self, images: list[NDArray[np.uint8]]) -> PreparedTemplate:
        if len(images) != 1:
            raise ValueError("REFERENCE_TEMPLATE requires one image")
        started = time.perf_counter()
        normalized = self._normalizer.normalize(images[0])
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

    def consensus(self, images: list[NDArray[np.uint8]]) -> PreparedTemplate:
        if len(images) < self._settings.registration.min_consensus_images:
            raise AppError(
                status=422,
                code="INSUFFICIENT_CONSENSUS_IMAGES",
                title="Insufficient consensus images",
                detail="MULTI_SCAN_CONSENSUSには3枚以上の画像が必要です。",
            )
        started = time.perf_counter()
        normalized_items = [self._normalizer.normalize(image) for image in images]
        sharpness = [
            float(cv2.Laplacian(cv2.cvtColor(item.image_rgb, cv2.COLOR_RGB2GRAY), cv2.CV_64F).var())
            for item in normalized_items
        ]
        base_index = int(np.argmax(sharpness))
        base = normalized_items[base_index].image_rgb
        accepted: list[tuple[int, NDArray[np.uint8], dict[str, Any]]] = [
            (
                base_index,
                base,
                {
                    "registrationMethod": "BASE_IMAGE",
                    "edgeOverlap": 1.0,
                    "homographyValid": True,
                },
            )
        ]
        excluded: list[dict[str, object]] = []
        for index, item in enumerate(normalized_items):
            if index == base_index:
                continue
            try:
                registered = self._aligner.align(item.image_rgb, base)
                accepted.append((index, registered.aligned_rgb, registered.quality.to_dict()))
            except AppError as exc:
                excluded.append({"inputIndex": index, "reason": exc.code})
        if len(accepted) < self._settings.registration.min_consensus_images:
            raise AppError(
                status=422,
                code="INSUFFICIENT_CONSENSUS_IMAGES",
                title="Insufficient accepted consensus images",
                detail="位置合わせ品質を満たす画像が3枚未満です。",
            )

        aligned_images = [item[1] for item in accepted]
        gray_stack = np.stack(
            [cv2.cvtColor(item, cv2.COLOR_RGB2GRAY) for item in aligned_images], axis=0
        )
        dark_frequency = (gray_stack < 205).mean(axis=0).astype(np.float32)
        high_background = np.percentile(gray_stack, 90, axis=0)
        dark_median = np.median(gray_stack, axis=0)
        static_mask = dark_frequency >= self._settings.registration.static_ink_frequency_threshold
        clean_gray = high_background.copy()
        clean_gray[static_mask] = dark_median[static_mask]
        line_frequency = np.stack(
            [self._line_detector.detect(gray) > 0 for gray in gray_stack], axis=0
        ).mean(axis=0)
        stable_lines = line_frequency >= 0.6
        clean_gray[stable_lines] = np.minimum(clean_gray[stable_lines], dark_median[stable_lines])
        clean_gray = np.clip(clean_gray, 0, 255).astype(np.uint8)
        clean_rgb = cv2.cvtColor(clean_gray, cv2.COLOR_GRAY2RGB)
        frequency_image = np.clip(dark_frequency * 255, 0, 255).astype(np.uint8)
        suspected = (
            (dark_frequency >= 0.35)
            & (dark_frequency < self._settings.registration.static_ink_frequency_threshold)
        ).astype(np.uint8) * 255
        suspected_ratio = float((suspected > 0).mean())
        warnings = ["PERSISTENT_HANDWRITING_POSSIBLE"] if suspected_ratio > 0.003 else []
        quality = {
            "mode": TemplateMode.MULTI_SCAN_CONSENSUS.value,
            "inputImageCount": len(images),
            "acceptedImageCount": len(accepted),
            "rejectedInputCount": len(excluded),
            "baseInputIndex": base_index,
            "inputSharpness": sharpness,
            "registration": [
                {"inputIndex": index, **registration} for index, _, registration in accepted
            ],
            "excludedInputs": excluded,
            "staticInkMeanFrequency": float(dark_frequency.mean()),
            "suspectedPersistentHandwritingRatio": suspected_ratio,
            "templateConfidence": max(
                0.0, min(1.0, len(accepted) / len(images) * (1.0 - suspected_ratio))
            ),
            "warnings": warnings,
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
        }
        output_images: dict[str, tuple[str, NDArray[np.uint8], bool]] = {
            "normalized": ("normalized.png", base, True),
            "staticInkFrequency": (
                "static_ink_frequency.png",
                frequency_image,
                False,
            ),
            "suspectedHandwritingMask": (
                "suspected_handwriting_mask.png",
                suspected,
                False,
            ),
        }
        for output_index, (_, image, _) in enumerate(accepted, start=1):
            output_images[f"alignedSource{output_index:02d}"] = (
                f"aligned_source_{output_index:02d}.png",
                image,
                True,
            )
        return PreparedTemplate(
            clean_template_rgb=clean_rgb,
            preview_rgb=clean_rgb.copy(),
            images=output_images,
            quality=quality,
            handwriting_mask=suspected,
            handwriting_candidates=handwriting_candidate_regions(suspected),
        )

    def single_experimental(self, images: list[NDArray[np.uint8]]) -> PreparedTemplate:
        if len(images) != 1:
            raise ValueError("SINGLE_IMAGE_EXPERIMENTAL requires one image")
        started = time.perf_counter()
        normalized = self._normalizer.normalize(images[0])
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
