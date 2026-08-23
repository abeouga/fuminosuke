from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

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
    def __init__(
        self,
        *,
        canonical_width: int,
        canonical_height: int,
        allow_orientation_swap: bool = False,
    ) -> None:
        self._width = canonical_width
        self._height = canonical_height
        self._allow_orientation_swap = allow_orientation_swap

    def normalize(
        self,
        image_rgb: NDArray[np.uint8],
        *,
        source_corners: NDArray[np.float32] | None = None,
    ) -> NormalizedDocument:
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
        paper_mask = self._paper_mask(small)
        paper_contours, _ = cv2.findContours(
            paper_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        image_area = float(small.shape[0] * small.shape[1])
        target_shapes = self._target_shapes()
        best: tuple[float, NDArray[np.float32], int, int] | None = None
        manual_boundary_used = source_corners is not None
        if source_corners is not None:
            manual_corners = self._validated_manual_corners(
                source_corners,
                source_width=source_width,
                source_height=source_height,
            )
            ratio = self._quad_ratio(manual_corners)
            _, output_width, output_height = min(
                target_shapes,
                key=lambda target: abs(ratio - target[0]) / target[0],
            )
            best = 1.0, manual_corners, output_width, output_height
        else:
            for contour in [*contours, *paper_contours]:
                area = cv2.contourArea(contour)
                if area < image_area * 0.2:
                    continue
                perimeter = cv2.arcLength(contour, True)
                for corners in self._quadrilateral_candidates(contour, perimeter):
                    quad_area = abs(float(cv2.contourArea(corners)))
                    if quad_area < image_area * 0.2:
                        continue
                    ratio = self._quad_ratio(corners)
                    target_ratio, output_width, output_height = min(
                        target_shapes,
                        key=lambda target: abs(ratio - target[0]) / target[0],
                    )
                    ratio_error = abs(ratio - target_ratio) / target_ratio
                    if ratio_error > 0.45:
                        continue
                    # The document aspect ratio is known. Weighting it strongly avoids
                    # selecting a strip of desk or binder that happens to touch the page.
                    score = quad_area / image_area - ratio_error * 1.25
                    if best is None or score > best[0]:
                        best = score, corners / scale, output_width, output_height

        warnings: list[str] = []
        if best is None:
            fallback = True
            source_ratio = source_width / source_height
            target_ratio, output_width, output_height = min(
                target_shapes,
                key=lambda target: abs(source_ratio - target[0]) / target[0],
            )
            ratio_error = abs(source_ratio - target_ratio) / target_ratio
            if ratio_error > 0.22:
                raise AppError(
                    status=422,
                    code="DOCUMENT_BOUNDARY_NOT_FOUND",
                    title="Document boundary not found",
                    detail=(
                        "自動で書類の範囲を判断できませんでした。"
                        "書類全体が写っている場合は、画面で書類の四隅を指定してください。"
                    ),
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
            output_width = best[2]
            output_height = best[3]
            if manual_boundary_used:
                warnings.append("MANUAL_DOCUMENT_BOUNDARY")

        normalized, transform = warp_quad(
            image_rgb,
            corners,
            output_width,
            output_height,
        )
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
                manual_boundary_used=manual_boundary_used,
                source_width=source_width,
                source_height=source_height,
                output_width=output_width,
                output_height=output_height,
                warnings=warnings,
            ),
        )

    def _target_shapes(self) -> list[tuple[float, int, int]]:
        shapes = [(self._width / self._height, self._width, self._height)]
        if self._allow_orientation_swap and self._width != self._height:
            shapes.append((self._height / self._width, self._height, self._width))
        return shapes

    @staticmethod
    def _quad_ratio(corners: NDArray[np.float32]) -> float:
        top = np.linalg.norm(corners[1] - corners[0])
        bottom = np.linalg.norm(corners[2] - corners[3])
        left = np.linalg.norm(corners[3] - corners[0])
        right = np.linalg.norm(corners[2] - corners[1])
        return max(float(top), float(bottom)) / max(
            1.0,
            max(float(left), float(right)),
        )

    @staticmethod
    def _validated_manual_corners(
        corners: NDArray[np.float32],
        *,
        source_width: int,
        source_height: int,
    ) -> NDArray[np.float32]:
        values = np.asarray(corners, dtype=np.float32)
        if values.shape != (4, 2) or not np.isfinite(values).all():
            raise AppError(
                status=422,
                code="DOCUMENT_BOUNDARY_INVALID",
                title="Document boundary is invalid",
                detail="書類の四隅を正しく指定してください。",
            )
        ordered = order_quad(values)
        if (
            (ordered[:, 0] < 0).any()
            or (ordered[:, 0] >= source_width).any()
            or (ordered[:, 1] < 0).any()
            or (ordered[:, 1] >= source_height).any()
            or abs(float(cv2.contourArea(ordered))) < source_width * source_height * 0.1
        ):
            raise AppError(
                status=422,
                code="DOCUMENT_BOUNDARY_INVALID",
                title="Document boundary is invalid",
                detail="四隅は画像内で、書類全体を囲むように指定してください。",
            )
        return ordered

    @staticmethod
    def _paper_mask(image_rgb: NDArray[np.uint8]) -> NDArray[np.uint8]:
        hsv = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2HSV)
        mask = ((hsv[:, :, 1] < 72) & (hsv[:, :, 2] > 100)).astype(np.uint8) * 255
        close_size = max(9, round(max(image_rgb.shape[:2]) * 0.019))
        if close_size % 2 == 0:
            close_size += 1
        open_size = max(3, close_size // 3)
        if open_size % 2 == 0:
            open_size += 1
        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (close_size, close_size)),
        )
        return np.asarray(
            cv2.morphologyEx(
                mask,
                cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (open_size, open_size)),
            ),
            dtype=np.uint8,
        )

    @staticmethod
    def _quadrilateral_candidates(
        contour: NDArray[np.int32], perimeter: float
    ) -> list[NDArray[np.float32]]:
        candidates: list[NDArray[np.float32]] = []
        seen: set[tuple[int, ...]] = set()
        for epsilon in (0.005, 0.01, 0.015, 0.02, 0.03, 0.04):
            approximate = cv2.approxPolyDP(contour, epsilon * perimeter, True)
            points = approximate.reshape(-1, 2)
            selections: list[NDArray[np.int32]] = []
            if len(points) == 4:
                selections.append(points)
            elif 5 <= len(points) <= 8:
                selections.extend(
                    np.asarray(items, dtype=np.int32)
                    for items in combinations(points.tolist(), 4)
                )
            for selection in selections:
                hull = cv2.convexHull(selection.reshape(-1, 1, 2))
                if len(hull) != 4:
                    continue
                corners = order_quad(hull.astype(np.float32))
                key = tuple(np.rint(corners).astype(np.int32).ravel().tolist())
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(corners)
        if not candidates:
            box = cv2.boxPoints(cv2.minAreaRect(contour))
            candidates.append(order_quad(box.astype(np.float32)))
        return candidates
