from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NoReturn

import cv2
import numpy as np
from numpy.typing import NDArray

from app.core.config import RegistrationSettings
from app.core.errors import AppError
from app.domain.quality import RegistrationQuality

# Shortest printed run treated as a ruled line in the local edge check.
RULED_LINE_PX = 25


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    aligned_rgb: NDArray[np.uint8]
    homography: NDArray[np.float64]
    debug_rgb: NDArray[np.uint8]
    quality: RegistrationQuality
    diagnostics: dict[str, Any] | None = None


class RegistrationFailure(AppError):
    """Safe numerical diagnostics and a separate local comparison artifact."""

    def __init__(self, diagnostics: dict[str, Any], debug_rgb: NDArray[np.uint8]) -> None:
        super().__init__(
            status=422,
            code="LOW_REGISTRATION_CONFIDENCE",
            title="Low registration confidence",
            detail="帳票とテンプレートを十分な精度で位置合わせできません。",
            metadata=diagnostics,
        )
        self.debug_rgb = debug_rgb
        # The page image the failed candidate was aligned from (set by register_document).
        self.normalized_rgb: NDArray[np.uint8] | None = None


class TemplateAligner:
    def __init__(self, settings: RegistrationSettings) -> None:
        self._settings = settings

    @property
    def settings(self) -> RegistrationSettings:
        return self._settings

    def align(
        self,
        source_rgb: NDArray[np.uint8],
        template_rgb: NDArray[np.uint8],
        *,
        validation_regions: Sequence[tuple[int, int, int, int]] = (),
    ) -> RegistrationResult:
        # Preserve the pixels and acceptance of every previously successful path.
        # Recovery uses reciprocal matches, not a lower inlier-ratio threshold.
        try:
            legacy = self._align_legacy(source_rgb, template_rgb)
        except AppError as exc:
            if exc.code != "LOW_REGISTRATION_CONFIDENCE":
                raise
            legacy_diagnostics = exc.metadata
        else:
            # A bent page can pass the global edge overlap while one side of the form is
            # a column off. Every grid cell and read field must keep its own printed edges.
            local_scores = _local_template_edge_support(
                legacy.aligned_rgb, template_rgb, validation_regions,
            )
            if not any(
                score < self._settings.min_edge_overlap
                for scores in local_scores.values() for score in scores
            ):
                return legacy
            legacy_diagnostics = {
                "rejectionReason": "LEGACY_LOCAL_SUPPORT_REJECTED",
                "quality": legacy.quality.to_dict(),
                "localTemplateEdgeSupport": local_scores,
            }
        return self._recover_mutual(
            source_rgb, template_rgb, validation_regions, legacy_diagnostics,
        )

    def _recover_mutual(
        self,
        source_rgb: NDArray[np.uint8],
        template_rgb: NDArray[np.uint8],
        validation_regions: Sequence[tuple[int, int, int, int]],
        legacy_diagnostics: dict[str, Any],
    ) -> RegistrationResult:
        height, width = template_rgb.shape[:2]
        source_gray, source_scale = _scaled_gray(source_rgb)
        template_gray, template_scale = _scaled_gray(template_rgb)
        orb = cv2.ORB_create(nfeatures=5000, scaleFactor=1.2, nlevels=8)
        source_points, source_descriptors = orb.detectAndCompute(source_gray, None)
        template_points, template_descriptors = orb.detectAndCompute(template_gray, None)
        quality = RegistrationQuality(
            registration_method="MUTUAL_ORB_RANSAC_ECC",
            detected_keypoints_source=len(source_points),
            detected_keypoints_template=len(template_points),
        )
        diagnostics: dict[str, Any] = {"legacy": legacy_diagnostics}
        aligned = cv2.resize(source_rgb, (width, height), interpolation=cv2.INTER_CUBIC)

        def reject(reason: str) -> NoReturn:
            diagnostics.update(rejectionReason=reason, recovery=quality.to_dict())
            raise RegistrationFailure(diagnostics, _registration_debug(aligned, template_rgb))

        if source_descriptors is None or template_descriptors is None:
            reject("NO_DESCRIPTORS")
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        forward = _ratio_matches(matcher.knnMatch(source_descriptors, template_descriptors, k=2))
        backward = _ratio_matches(matcher.knnMatch(template_descriptors, source_descriptors, k=2))
        reverse = {match.queryIdx: match.trainIdx for match in backward}
        matches = [m for m in forward if reverse.get(m.trainIdx) == m.queryIdx]
        quality.match_count = len(matches)
        if len(matches) < self._settings.min_feature_matches:
            reject("INSUFFICIENT_MUTUAL_MATCHES")
        source_small = np.float32([source_points[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
        target_small = np.float32(
            [template_points[m.trainIdx].pt for m in matches],
        ).reshape(-1, 1, 2)
        small_h, mask = cv2.findHomography(
            source_small, target_small, cv2.RANSAC,
            self._settings.max_reprojection_error_px * template_scale,
        )
        if small_h is None or mask is None:
            reject("NO_MUTUAL_HOMOGRAPHY")
        homography = _to_full_homography(small_h, source_scale, template_scale)
        source_full = source_small / source_scale
        target_full = target_small / template_scale
        inliers = mask.ravel().astype(bool)
        errors = np.linalg.norm(
            cv2.perspectiveTransform(source_full, homography) - target_full, axis=2,
        ).ravel()[inliers]
        quality.inlier_count = int(inliers.sum())
        quality.inlier_ratio = float(inliers.mean())
        if errors.size:
            quality.mean_reprojection_error_px = float(errors.mean())
            quality.median_reprojection_error_px = float(np.median(errors))
        quality.spatial_distribution_score = min(
            _spatial_distribution(
                source_full[inliers, 0], source_rgb.shape[1], source_rgb.shape[0],
            ),
            _spatial_distribution(target_full[inliers, 0], width, height),
        )
        quality.coverage_ratio, quality.homography_valid = _validate_homography(
            homography, source_rgb.shape[1], source_rgb.shape[0], width, height,
        )
        if not self._orb_quality_sufficient(quality):
            reject("MUTUAL_GEOMETRY_REJECTED")
        aligned = cv2.warpPerspective(
            source_rgb, homography, (width, height), flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255),
        )
        aligned, quality.ecc_score, ecc_warp = _ecc_refine_transform(aligned, template_rgb)
        if not np.isfinite(ecc_warp).all() or abs(float(np.linalg.det(ecc_warp))) < 1e-8:
            reject("DEGENERATE_ECC_TRANSFORM")
        final_homography = np.linalg.inv(ecc_warp) @ homography
        diagnostics["appliedHomography"] = final_homography.tolist()
        quality.coverage_ratio, quality.homography_valid = _validate_homography(
            final_homography, source_rgb.shape[1], source_rgb.shape[0], width, height,
        )
        final_errors = np.linalg.norm(
            cv2.perspectiveTransform(source_full, final_homography) - target_full, axis=2,
        ).ravel()[inliers]
        diagnostics["finalMeanReprojectionErrorPx"] = float(final_errors.mean())
        quality.edge_overlap = _edge_overlap(aligned, template_rgb)
        # ECC may drift to a repeated row; validate its applied transform too.
        if (
            not quality.homography_valid
            or not _orientation_preserved(
                final_homography, source_rgb.shape[1], source_rgb.shape[0],
            )
            or float(final_errors.mean()) > self._settings.max_reprojection_error_px
            or quality.ecc_score is None
            or quality.ecc_score < self._settings.min_ecc_score
            or quality.edge_overlap < self._settings.min_edge_overlap
        ):
            reject("REFINED_GEOMETRY_REJECTED")
        local_scores = _local_template_edge_support(aligned, template_rgb, validation_regions)
        diagnostics["localTemplateEdgeSupport"] = local_scores
        # One strong portion of a form cannot qualify missing or displaced portions.
        if len(local_scores["grid"]) < 4 or any(
            score < self._settings.min_edge_overlap
            for scores in local_scores.values() for score in scores
        ):
            reject("LOCAL_TEMPLATE_SUPPORT_REJECTED")
        diagnostics["recovery"] = quality.to_dict()
        return RegistrationResult(
            aligned_rgb=aligned, homography=final_homography,
            debug_rgb=_registration_debug(aligned, template_rgb),
            quality=quality, diagnostics=diagnostics,
        )

    def _align_legacy(
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
            # cv2.resize maps pixel centers, not corner coordinates. Preserve
            # the same resized pixels while recording their actual transform.
            sx = target_width / source_rgb.shape[1]
            sy = target_height / source_rgb.shape[0]
            homography = np.array(
                [[sx, 0.0, (sx - 1.0) / 2],
                 [0.0, sy, (sy - 1.0) / 2], [0.0, 0.0, 1.0]], dtype=np.float64,
            )
            aligned = cv2.resize(
                source_rgb, (target_width, target_height), interpolation=cv2.INTER_CUBIC
            )
            quality.registration_method = "IDENTITY_ECC_FALLBACK"
            quality.warnings.append("ORB_REGISTRATION_FALLBACK")

        aligned, ecc_score, ecc_warp = _ecc_refine_transform(aligned, template_rgb)
        if not np.isfinite(ecc_warp).all() or abs(float(np.linalg.det(ecc_warp))) < 1e-8:
            raise AppError(
                status=422, code="LOW_REGISTRATION_CONFIDENCE",
                title="Low registration confidence",
                detail="帳票とテンプレートを十分な精度で位置合わせできません。",
                metadata={"rejectionReason": "DEGENERATE_ECC_TRANSFORM"},
            )
        # ECC uses WARP_INVERSE_MAP. Its inverse must be composed with the
        # preceding source -> aligned mapping; do not resample the image again.
        homography = np.linalg.inv(ecc_warp) @ homography
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
                    "quality": quality.to_dict(),
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


def _ratio_matches(pairs: Sequence[Sequence[cv2.DMatch]]) -> list[cv2.DMatch]:
    return [
        pair[0] for pair in pairs
        if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
    ]


def _orientation_preserved(homography: NDArray[np.float64], width: int, height: int) -> bool:
    corners = np.float32([[[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]]])
    transformed = cv2.perspectiveTransform(corners, homography)
    return bool(np.isfinite(transformed).all() and cv2.contourArea(transformed, oriented=True) > 0)


def _local_template_edge_support(
    aligned_rgb: NDArray[np.uint8],
    template_rgb: NDArray[np.uint8],
    regions: Sequence[tuple[int, int, int, int]],
    in_frame: NDArray[np.bool_] | None = None,
) -> dict[str, list[float]]:
    """Check fixed print evidence independently of the ORB correspondences.

    Use the existing one-pixel edge tolerance. Directional template recall does
    not penalize additional handwriting; global symmetric overlap still applies.
    Sparse/blank areas are not evidence and cannot qualify the recovery alone.
    For read fields, vertical and horizontal ruled-line edges are also scored separately
    (each field takes the lowest): a shift along a ruled line keeps that line matched, so
    a field a column off would otherwise still pass on its horizontal lines. `in_frame`
    limits the evidence to printed edges that were photographed (curved-page registration).
    """
    source = cv2.Canny(cv2.cvtColor(aligned_rgb, cv2.COLOR_RGB2GRAY), 60, 160)
    template_gray = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY)
    template = cv2.Canny(template_gray, 60, 160) > 0
    if in_frame is not None:
        # Keep a small border off the photo edge (the remap fills it with white).
        template &= cv2.erode(in_frame.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    supported = template & (cv2.dilate(source, np.ones((3, 3), np.uint8)) > 0)
    # Only the edges of long ruled lines: printed characters (and any writing left in
    # the template image) are not shifted-line evidence.
    ink = (template_gray < 160).astype(np.uint8)
    near = np.ones((5, 5), np.uint8)
    orientations = tuple(
        template & (cv2.dilate(cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel), near) > 0)
        for kernel in (
            np.ones((RULED_LINE_PX, 1), np.uint8), np.ones((1, RULED_LINE_PX), np.uint8),
        )
    )
    height, width = template.shape
    grid = [
        (x * width // 3, y * height // 3, (x + 1) * width // 3, (y + 1) * height // 3)
        for y in range(3) for x in range(3)
    ]
    # Include each field's immediate printed border without changing crop geometry.
    fields = [(max(0, x - 3), max(0, y - 3), min(width, x + w + 3), min(height, y + h + 3))
              for x, y, w, h in regions]
    result: dict[str, list[float]] = {"grid": [], "fields": []}
    for key, boxes in (("grid", grid), ("fields", fields)):
        for x0, y0, x1, y1 in boxes:
            count = int(template[y0:y1, x0:x1].sum())
            if count < 32:
                continue
            scores = [float(supported[y0:y1, x0:x1].sum()) / count]
            # Ruled lines decide whether a read field sits on its own cell; the coarse
            # grid also holds template-only print (frames, stamps) and keeps the plain ratio.
            for edges in orientations if key == "fields" else ():
                oriented = int(edges[y0:y1, x0:x1].sum())
                if oriented >= 32:
                    scores.append(
                        float((edges & supported)[y0:y1, x0:x1].sum()) / oriented,
                    )
            result[key].append(min(scores))
    return result


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
    refined, score, _ = _ecc_refine_transform(aligned_rgb, template_rgb)
    return refined, score


def _ecc_refine_transform(
    aligned_rgb: NDArray[np.uint8], template_rgb: NDArray[np.uint8]
) -> tuple[NDArray[np.uint8], float | None, NDArray[np.float64]]:
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
        return refined, float(score), np.vstack((warp, [0.0, 0.0, 1.0]))
    except cv2.error:
        return aligned_rgb, None, np.eye(3, dtype=np.float64)


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
