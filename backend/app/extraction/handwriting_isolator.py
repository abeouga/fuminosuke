from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from app.image_processing.line_detection import LineDetector


@dataclass(frozen=True, slots=True)
class UnknownOverlapRegion:
    x: int
    y: int
    width: int
    height: int
    reason: str = "HANDWRITING_OVER_PRINTED_STRUCTURE"

    def to_dict(self) -> dict[str, int | str]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class HandwritingResult:
    probability: NDArray[np.float32]
    mask: NDArray[np.uint8]
    handwriting_only_rgb: NDArray[np.uint8]
    debug_overlay_rgb: NDArray[np.uint8]
    unknown_overlap_regions: list[UnknownOverlapRegion]
    coverage_ratio: float


class HandwritingIsolator:
    def __init__(self, *, threshold: float) -> None:
        self._threshold = threshold
        self._line_detector = LineDetector()

    def isolate(
        self,
        aligned_rgb: NDArray[np.uint8],
        template_rgb: NDArray[np.uint8],
        model_probability: NDArray[np.float32] | None = None,
    ) -> HandwritingResult:
        source_gray = cv2.cvtColor(aligned_rgb, cv2.COLOR_RGB2GRAY)
        template_gray = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY)
        darkening = np.clip(
            (template_gray.astype(np.float32) - source_gray.astype(np.float32)) / 90.0,
            0,
            1,
        )
        source_lab = cv2.cvtColor(aligned_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        template_lab = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        color_difference = np.clip(
            np.linalg.norm(source_lab - template_lab, axis=2) / 80.0, 0, 1
        )
        source_edges = cv2.Canny(source_gray, 45, 130)
        template_edges = cv2.Canny(template_gray, 45, 130)
        edge_residual = cv2.bitwise_and(
            source_edges,
            cv2.bitwise_not(cv2.dilate(template_edges, np.ones((3, 3), np.uint8))),
        ).astype(np.float32) / 255.0
        probability = darkening * 0.55 + color_difference * 0.30 + edge_residual * 0.15
        if model_probability is not None:
            probability = np.clip(probability * 0.7 + model_probability * 0.3, 0, 1)

        template_ink = cv2.threshold(template_gray, 205, 255, cv2.THRESH_BINARY_INV)[1]
        protection = cv2.dilate(template_ink, np.ones((3, 3), np.uint8))
        candidate_before_protection = probability >= self._threshold
        overlap = candidate_before_protection & (protection > 0)
        probability = np.where(protection > 0, probability * 0.55, probability)
        lines = self._line_detector.detect(template_gray)
        probability = np.where(lines > 0, probability * 0.35, probability)

        raw_mask = (probability >= self._threshold).astype(np.uint8) * 255
        mask = self._cleanup(raw_mask)
        unknown_regions = _component_regions(overlap.astype(np.uint8) * 255)
        handwriting_only = np.full_like(aligned_rgb, 255)
        handwriting_only[mask > 0] = aligned_rgb[mask > 0]
        debug = aligned_rgb.copy()
        red = np.zeros_like(debug)
        red[:, :] = (230, 30, 40)
        selection = mask > 0
        debug[selection] = cv2.addWeighted(debug, 0.35, red, 0.65, 0)[selection]
        return HandwritingResult(
            probability=probability.astype(np.float32),
            mask=mask,
            handwriting_only_rgb=handwriting_only,
            debug_overlay_rgb=debug,
            unknown_overlap_regions=unknown_regions,
            coverage_ratio=float((mask > 0).mean()),
        )

    def _cleanup(self, mask: NDArray[np.uint8]) -> NDArray[np.uint8]:
        cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, 8)
        result = np.zeros_like(cleaned)
        image_area = cleaned.shape[0] * cleaned.shape[1]
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if 5 <= area <= image_area * 0.2:
                result[labels == label] = 255
        return result


def _component_regions(mask: NDArray[np.uint8]) -> list[UnknownOverlapRegion]:
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    regions: list[UnknownOverlapRegion] = []
    for label in range(1, count):
        x, y, width, height, area = stats[label]
        if area >= 6:
            regions.append(
                UnknownOverlapRegion(
                    x=int(x), y=int(y), width=int(width), height=int(height)
                )
            )
    return regions[:100]

