from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from app.core.errors import AppError
from app.domain.quality import DocumentQuality
from app.image_processing.perspective import order_quad, warp_quad


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    image_rgb: NDArray[np.uint8]
    transform: NDArray[np.float64]
    source_corners: NDArray[np.float32]
    debug_rgb: NDArray[np.uint8]
    quality: DocumentQuality


class DocumentNormalizer:
    def __init__(self, *, canonical_width: int, canonical_height: int) -> None:
        self._width = canonical_width
        self._height = canonical_height

    def normalize(self, image_rgb: NDArray[np.uint8]) -> NormalizedDocument:
        source_height, source_width = image_rgb.shape[:2]
        scale = min(1.0, 1600.0 / max(source_width, source_height))
        small = cv2.resize(
            image_rgb,
            (max(1, round(source_width * scale)), max(1, round(source_height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 40, 130)
        edges = cv2.morphologyEx(
            edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        )
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        image_area = float(small.shape[0] * small.shape[1])
        target_ratio = self._width / self._height
        best: tuple[float, NDArray[np.float32]] | None = None
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < image_area * 0.25:
                continue
            perimeter = cv2.arcLength(contour, True)
            approximate = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            if len(approximate) != 4 or not cv2.isContourConvex(approximate):
                continue
            corners = order_quad(approximate.astype(np.float32))
            top = np.linalg.norm(corners[1] - corners[0])
            bottom = np.linalg.norm(corners[2] - corners[3])
            left = np.linalg.norm(corners[3] - corners[0])
            right = np.linalg.norm(corners[2] - corners[1])
            ratio = max(float(top), float(bottom)) / max(
                1.0, max(float(left), float(right))
            )
            ratio_error = abs(ratio - target_ratio) / target_ratio
            if ratio_error > 0.45:
                continue
            score = area / image_area - ratio_error * 0.15
            if best is None or score > best[0]:
                best = score, corners / scale

        warnings: list[str] = []
        if best is None:
            fallback = True
            source_ratio = source_width / source_height
            ratio_error = abs(source_ratio - target_ratio) / target_ratio
            if ratio_error > 0.22:
                raise AppError(
                    status=422,
                    code="DOCUMENT_BOUNDARY_NOT_FOUND",
                    title="Document boundary not found",
                    detail="帳票の外周を検出できません。帳票全体が写るように撮影してください。",
                )
            corners = np.array(
                [
                    [0, 0],
                    [source_width - 1, 0],
                    [source_width - 1, source_height - 1],
                    [0, source_height - 1],
                ],
                dtype=np.float32,
            )
            confidence = max(0.5, 0.72 - ratio_error)
            warnings.append("FULL_IMAGE_BOUNDARY_FALLBACK")
        else:
            fallback = False
            confidence = min(1.0, max(0.0, best[0]))
            corners = best[1]

        normalized, transform = warp_quad(image_rgb, corners, self._width, self._height)
        debug = image_rgb.copy()
        cv2.polylines(debug, [corners.astype(np.int32)], True, (0, 180, 0), 4)
        return NormalizedDocument(
            image_rgb=normalized,
            transform=transform,
            source_corners=corners,
            debug_rgb=debug,
            quality=DocumentQuality(
                boundary_confidence=confidence,
                fallback_used=fallback,
                source_width=source_width,
                source_height=source_height,
                output_width=self._width,
                output_height=self._height,
                warnings=warnings,
            ),
        )
