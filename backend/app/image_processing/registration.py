from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from app.core.config import RegistrationSettings
from app.core.errors import AppError
from app.domain.quality import RegistrationQuality


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    aligned_rgb: NDArray[np.uint8]
    homography: NDArray[np.float64]
    debug_rgb: NDArray[np.uint8]
    quality: RegistrationQuality


class TemplateAligner:
    def __init__(self, settings: RegistrationSettings) -> None:
        self._settings = settings

    def align(
        self,
        source_rgb: NDArray[np.uint8],
        template_rgb: NDArray[np.uint8],
    ) -> RegistrationResult:
        target_height, target_width = template_rgb.shape[:2]
        source_small, source_scale = _scaled_gray(source_rgb)
        template_small, template_scale = _scaled_gray(template_rgb)
        orb = cv2.ORB_create(nfeatures=5000, scaleFactor=1.2, nlevels=8)
        source_points, source_descriptors = orb.detectAndCompute(source_small, None)
        template_points, template_descriptors = orb.detectAndCompute(template_small, None)
        quality = RegistrationQuality(
            detected_keypoints_source=len(source_points),
            detected_keypoints_template=len(template_points),
        )

        homography: NDArray[np.float64] | None = None
        if source_descriptors is not None and template_descriptors is not None:
            matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
            pairs = matcher.knnMatch(source_descriptors, template_descriptors, k=2)
            matches = [
                pair[0]
                for pair in pairs
                if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
            ]
            quality.match_count = len(matches)
            if len(matches) >= self._settings.min_feature_matches:
                source_match_small = np.float32(
                    [source_points[match.queryIdx].pt for match in matches]
                ).reshape(-1, 1, 2)
                template_match_small = np.float32(
                    [template_points[match.trainIdx].pt for match in matches]
                ).reshape(-1, 1, 2)
                small_homography, inlier_mask = cv2.findHomography(
                    source_match_small,
                    template_match_small,
                    cv2.RANSAC,
                    self._settings.max_reprojection_error_px * template_scale,
                )
                if small_homography is not None and inlier_mask is not None:
                    homography = _to_full_homography(
                        small_homography, source_scale, template_scale
                    )
                    inliers = inlier_mask.ravel().astype(bool)
                    source_full = source_match_small.reshape(-1, 2) / source_scale
                    template_full = template_match_small.reshape(-1, 2) / template_scale
                    projected = cv2.perspectiveTransform(
                        source_full.reshape(-1, 1, 2), homography
                    ).reshape(-1, 2)
                    errors = np.linalg.norm(projected - template_full, axis=1)
                    inlier_errors = errors[inliers]
                    quality.inlier_count = int(inliers.sum())
                    quality.inlier_ratio = float(inliers.mean())
                    if inlier_errors.size:
                        quality.mean_reprojection_error_px = float(inlier_errors.mean())
                        quality.median_reprojection_error_px = float(np.median(inlier_errors))
                    quality.spatial_distribution_score = _spatial_distribution(
                        source_full[inliers], source_rgb.shape[1], source_rgb.shape[0]
                    )
                    quality.coverage_ratio, quality.homography_valid = _validate_homography(
                        homography,
                        source_rgb.shape[1],
                        source_rgb.shape[0],
                        target_width,
                        target_height,
                    )

        if homography is not None and self._orb_quality_sufficient(quality):
            aligned = cv2.warpPerspective(
                source_rgb,
                homography,
                (target_width, target_height),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(255, 255, 255),
            )
        else:
            homography = np.eye(3, dtype=np.float64)
            aligned = cv2.resize(
                source_rgb, (target_width, target_height), interpolation=cv2.INTER_CUBIC
            )
            quality.registration_method = "IDENTITY_ECC_FALLBACK"
            quality.warnings.append("ORB_REGISTRATION_FALLBACK")

        aligned, ecc_score = _ecc_refine(aligned, template_rgb)
        quality.ecc_score = ecc_score
        quality.edge_overlap = _edge_overlap(aligned, template_rgb)
        if quality.registration_method == "IDENTITY_ECC_FALLBACK":
            quality.coverage_ratio = 1.0
            quality.homography_valid = (
                ecc_score is not None
                and ecc_score >= self._settings.min_ecc_score
                and quality.edge_overlap >= self._settings.min_edge_overlap
            )

        if not quality.homography_valid or quality.edge_overlap < self._settings.min_edge_overlap:
            raise AppError(
                status=422,
                code="LOW_REGISTRATION_CONFIDENCE",
                title="Low registration confidence",
                detail="帳票とテンプレートを十分な精度で位置合わせできません。",
                metadata={
                    "matchCount": quality.match_count,
                    "inlierCount": quality.inlier_count,
                },
            )

        debug = _registration_debug(aligned, template_rgb)
        return RegistrationResult(
            aligned_rgb=aligned,
            homography=homography,
            debug_rgb=debug,
            quality=quality,
        )

    def _orb_quality_sufficient(self, quality: RegistrationQuality) -> bool:
        return (
            quality.homography_valid
            and quality.match_count >= self._settings.min_feature_matches
            and quality.inlier_count >= self._settings.min_inliers
            and quality.inlier_ratio >= self._settings.min_inlier_ratio
            and quality.mean_reprojection_error_px
            <= self._settings.max_reprojection_error_px
            and quality.spatial_distribution_score >= 0.08
        )


def _scaled_gray(image_rgb: NDArray[np.uint8]) -> tuple[NDArray[np.uint8], float]:
    height, width = image_rgb.shape[:2]
    scale = min(1.0, 1400.0 / max(width, height))
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    if scale < 1:
        gray = cv2.resize(
            gray,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return gray, scale


def _to_full_homography(
    small_homography: NDArray[np.float64], source_scale: float, template_scale: float
) -> NDArray[np.float64]:
    source_matrix = np.diag([source_scale, source_scale, 1.0])
    template_inverse = np.diag([1.0 / template_scale, 1.0 / template_scale, 1.0])
    return template_inverse @ small_homography @ source_matrix


def _validate_homography(
    homography: NDArray[np.float64],
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> tuple[float, bool]:
    if not np.isfinite(homography).all() or abs(float(np.linalg.det(homography))) < 1e-8:
        return 0.0, False
    corners = np.array(
        [
            [
                [0, 0],
                [source_width - 1, 0],
                [source_width - 1, source_height - 1],
                [0, source_height - 1],
            ]
        ],
        dtype=np.float32,
    )
    transformed = cv2.perspectiveTransform(corners, homography).reshape(4, 2)
    polygon = transformed.astype(np.float32)
    if not cv2.isContourConvex(polygon.astype(np.int32)):
        return 0.0, False
    area = abs(float(cv2.contourArea(polygon)))
    target_area = float(target_width * target_height)
    coverage = area / max(1.0, target_area)
    margin_x = target_width * 0.35
    margin_y = target_height * 0.35
    inside_margin = (
        transformed[:, 0].min() >= -margin_x
        and transformed[:, 0].max() <= target_width + margin_x
        and transformed[:, 1].min() >= -margin_y
        and transformed[:, 1].max() <= target_height + margin_y
    )
    return coverage, bool(0.45 <= coverage <= 1.65 and inside_margin)


def _spatial_distribution(points: NDArray[np.float32], width: int, height: int) -> float:
    if len(points) < 4:
        return 0.0
    span_x = float(np.ptp(points[:, 0])) / max(1, width)
    span_y = float(np.ptp(points[:, 1])) / max(1, height)
    return min(1.0, span_x * span_y)


def _ecc_refine(
    aligned_rgb: NDArray[np.uint8], template_rgb: NDArray[np.uint8]
) -> tuple[NDArray[np.uint8], float | None]:
    template_gray = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY)
    aligned_gray = cv2.cvtColor(aligned_rgb, cv2.COLOR_RGB2GRAY)
    template_float = cv2.GaussianBlur(template_gray, (5, 5), 0).astype(np.float32) / 255.0
    aligned_float = cv2.GaussianBlur(aligned_gray, (5, 5), 0).astype(np.float32) / 255.0
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5)
    try:
        score, warp = cv2.findTransformECC(
            template_float,
            aligned_float,
            warp,
            cv2.MOTION_AFFINE,
            criteria,
            None,
            3,
        )
        refined = cv2.warpAffine(
            aligned_rgb,
            warp,
            (template_rgb.shape[1], template_rgb.shape[0]),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),
        )
        return refined, float(score)
    except cv2.error:
        return aligned_rgb, None


def _edge_overlap(source_rgb: NDArray[np.uint8], template_rgb: NDArray[np.uint8]) -> float:
    source = cv2.Canny(cv2.cvtColor(source_rgb, cv2.COLOR_RGB2GRAY), 60, 160) > 0
    template = cv2.Canny(cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY), 60, 160) > 0
    kernel = np.ones((3, 3), np.uint8)
    source_dilated = cv2.dilate(source.astype(np.uint8), kernel) > 0
    template_dilated = cv2.dilate(template.astype(np.uint8), kernel) > 0
    source_count = max(1, int(source.sum()))
    template_count = max(1, int(template.sum()))
    source_hit = float((source & template_dilated).sum()) / source_count
    template_hit = float((template & source_dilated).sum()) / template_count
    return (source_hit + template_hit) / 2.0


def _registration_debug(
    aligned_rgb: NDArray[np.uint8], template_rgb: NDArray[np.uint8]
) -> NDArray[np.uint8]:
    source_edges = cv2.Canny(cv2.cvtColor(aligned_rgb, cv2.COLOR_RGB2GRAY), 60, 160)
    template_edges = cv2.Canny(cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY), 60, 160)
    debug = np.full_like(aligned_rgb, 255)
    debug[template_edges > 0] = (0, 170, 0)
    debug[source_edges > 0] = (200, 0, 180)
    debug[(template_edges > 0) & (source_edges > 0)] = (0, 0, 0)
    return debug
