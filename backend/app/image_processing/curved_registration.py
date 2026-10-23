"""Register a photo of a curved (bent, binder-held) page onto the template.

Used only after every planar candidate of `register_document` has been rejected. A single
homography cannot follow a page that bends near the binding: the top matches and the lower
table drifts by about a row. Here the page is mapped by

1. a homography plus a smooth cubic correction, fitted to mutual ORB correspondences
   (template -> page, robustly trimmed), and
2. a few passes of local corrections: small template tiles are aligned by translation-only
   ECC within a bounded radius, the tile shifts are smoothed and composed into the map.

The local radius is well below the printed row pitch, so a tile cannot jump to a
neighbouring row; the cubic model brings every tile within that radius first.

Acceptance re-uses the existing thresholds (feature matches, inliers, inlier ratio,
reprojection error, spatial distribution, global edge overlap and the per-grid / per-field
printed-edge support). The global ECC correlation is recorded but not required: on phone
photos it measures lighting and handwriting (0.6-0.7 even when every printed line matches)
rather than geometry. Printed edges that fall outside the photographed area have no evidence
and are left out of the support ratio; at least 85% of the template page and 70% of every
read field must be inside the photo (fields cut by the photo edge are listed for review).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NoReturn

import cv2
import numpy as np
from numpy.typing import NDArray

from app.core.config import RegistrationSettings
from app.domain.quality import RegistrationQuality
from app.image_processing.registration import (
    RegistrationFailure,
    RegistrationResult,
    _edge_overlap,
    _local_template_edge_support,
    _ratio_matches,
    _registration_debug,
    _scaled_gray,
    _spatial_distribution,
    _validate_homography,
)

POLYNOMIAL_DEGREE = 3
# Generous homography inlier radius (fraction of template height) so bent areas are kept
# for the smooth correction; the trimmed correction must still meet the error threshold.
CURVED_RANSAC_FRACTION = 0.03
# Largest smooth correction away from the homography (fraction of the template diagonal).
MAX_CORRECTION_FRACTION = 0.04
TILE = 120
STEP = 60
# Local shift radius per pass in template pixels (form rows are about 50 px).
LOCAL_RADIUS = 20.0
PASSES = 3
MIN_TILE_CORRELATION = 0.5
MIN_IN_FRAME_RATIO = 0.85
MIN_FIELD_IN_FRAME = 0.7


def align_curved(
    source_rgb: NDArray[np.uint8],
    template_rgb: NDArray[np.uint8],
    settings: RegistrationSettings,
    validation_regions: Sequence[tuple[int, int, int, int]],
) -> RegistrationResult:
    height, width = template_rgb.shape[:2]
    quality = RegistrationQuality(registration_method="CURVED_PAGE_ORB_LOCAL_ECC")
    diagnostics: dict[str, Any] = {}
    aligned = cv2.resize(source_rgb, (width, height), interpolation=cv2.INTER_CUBIC)

    def reject(reason: str) -> NoReturn:
        diagnostics.update(rejectionReason=reason, quality=quality.to_dict())
        raise RegistrationFailure(diagnostics, _registration_debug(aligned, template_rgb))

    fitted = _fit_smooth_model(source_rgb, template_rgb, quality)
    if fitted is None:
        reject("CURVED_NO_CORRESPONDENCE")
    homography, coefficients, error_scale = fitted
    to_template = np.linalg.inv(homography)
    quality.coverage_ratio, quality.homography_valid = _validate_homography(
        to_template, source_rgb.shape[1], source_rgb.shape[0], width, height,
    )
    if (
        not quality.homography_valid
        or quality.match_count < settings.min_feature_matches
        or quality.inlier_count < settings.min_inliers
        or quality.inlier_ratio < settings.min_inlier_ratio
        or quality.mean_reprojection_error_px * error_scale > settings.max_reprojection_error_px
        or quality.spatial_distribution_score < 0.08
    ):
        reject("CURVED_GEOMETRY_REJECTED")
    map_x, map_y, correction = _model_map(homography, coefficients, width, height)
    diagnostics["maxSmoothCorrectionPx"] = round(correction, 1)
    if correction > MAX_CORRECTION_FRACTION * float(np.hypot(width, height)):
        reject("CURVED_CORRECTION_TOO_LARGE")

    grid_x, grid_y = np.meshgrid(
        np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32),
    )
    local_shifts: list[float] = []
    tiles: list[int] = []
    for _ in range(PASSES):
        aligned = _remap(source_rgb, map_x, map_y)
        shift_x, shift_y, found = _local_shifts(aligned, template_rgb)
        tiles.append(found)
        local_shifts.append(round(float(np.hypot(shift_x, shift_y).max()), 1))
        map_x = _compose(map_x, grid_x + shift_x, grid_y + shift_y)
        map_y = _compose(map_y, grid_x + shift_x, grid_y + shift_y)
    aligned = _remap(source_rgb, map_x, map_y)
    diagnostics.update(localTiles=tiles, maxLocalShiftPx=local_shifts)

    if not _map_preserves_order(map_x, map_y):
        reject("CURVED_MAP_FOLDED")
    in_frame = (
        (map_x >= 0) & (map_x <= source_rgb.shape[1] - 1)
        & (map_y >= 0) & (map_y <= source_rgb.shape[0] - 1)
    )
    form = _form_box(template_rgb)
    in_frame_ratio = float(in_frame[form].mean())
    diagnostics["inFrameRatio"] = round(in_frame_ratio, 3)
    field_in_frame = [
        float(in_frame[y:y + h, x:x + w].mean()) if w > 0 and h > 0 else 1.0
        for x, y, w, h in validation_regions
    ]
    diagnostics["minFieldInFrameRatio"] = round(min(field_in_frame, default=1.0), 3)
    # Fields reaching past the photographed area (usually blank sheet margin) are kept
    # for the reviewer: their crops show the missing part as white.
    diagnostics["fieldsPartlyOutOfFrame"] = [
        list(region) for region, ratio in zip(validation_regions, field_in_frame, strict=True)
        if ratio < 0.999
    ]
    # A field cut off by the photo edge would be read as (partly) blank.
    if in_frame_ratio < MIN_IN_FRAME_RATIO or min(field_in_frame, default=1.0) < MIN_FIELD_IN_FRAME:
        reject("CURVED_PAGE_OUT_OF_FRAME")

    quality.edge_overlap = _edge_overlap(aligned, template_rgb)
    quality.ecc_score = _correlation(aligned, template_rgb)
    if quality.edge_overlap < settings.min_edge_overlap:
        reject("CURVED_EDGE_OVERLAP_REJECTED")
    local_scores = _local_template_edge_support(
        aligned, template_rgb, validation_regions, in_frame,
    )
    diagnostics["localTemplateEdgeSupport"] = local_scores
    if len(local_scores["grid"]) < 4 or any(
        score < settings.min_edge_overlap
        for scores in local_scores.values() for score in scores
    ):
        reject("CURVED_LOCAL_SUPPORT_REJECTED")
    quality.warnings.append("CURVED_PAGE_REGISTRATION")
    if diagnostics["fieldsPartlyOutOfFrame"]:
        quality.warnings.append("FIELDS_PARTLY_OUT_OF_FRAME")
    diagnostics["recovery"] = quality.to_dict()
    return RegistrationResult(
        aligned_rgb=aligned,
        # Planar part only; the aligned pixels also include the smooth and local corrections.
        homography=to_template,
        debug_rgb=_registration_debug(aligned, template_rgb),
        quality=quality,
        diagnostics=diagnostics,
    )


def _terms(x: NDArray[np.float64], y: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.stack([
        x ** i * y ** j
        for i in range(POLYNOMIAL_DEGREE + 1) for j in range(POLYNOMIAL_DEGREE + 1 - i)
    ], axis=1)


def _fit_smooth_model(
    source_rgb: NDArray[np.uint8],
    template_rgb: NDArray[np.uint8],
    quality: RegistrationQuality,
) -> tuple[NDArray[np.float64], NDArray[np.float64], float] | None:
    """Homography + cubic correction, template pixel -> source pixel."""
    height, width = template_rgb.shape[:2]
    source_gray, source_scale = _scaled_gray(source_rgb)
    template_gray, template_scale = _scaled_gray(template_rgb)
    orb = cv2.ORB_create(nfeatures=5000, scaleFactor=1.2, nlevels=8)
    source_points, source_descriptors = orb.detectAndCompute(source_gray, None)
    template_points, template_descriptors = orb.detectAndCompute(template_gray, None)
    quality.detected_keypoints_source = len(source_points)
    quality.detected_keypoints_template = len(template_points)
    if source_descriptors is None or template_descriptors is None:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    forward = _ratio_matches(matcher.knnMatch(source_descriptors, template_descriptors, k=2))
    backward = _ratio_matches(matcher.knnMatch(template_descriptors, source_descriptors, k=2))
    reverse = {match.queryIdx: match.trainIdx for match in backward}
    matches = [m for m in forward if reverse.get(m.trainIdx) == m.queryIdx]
    quality.match_count = len(matches)
    if len(matches) < 12:
        return None
    source = np.array([source_points[m.queryIdx].pt for m in matches], np.float64) / source_scale
    template = np.array(
        [template_points[m.trainIdx].pt for m in matches], np.float64,
    ) / template_scale
    homography, mask = cv2.findHomography(
        template, source, cv2.RANSAC, CURVED_RANSAC_FRACTION * height,
    )
    if homography is None or mask is None:
        return None
    keep = mask.ravel().astype(bool)
    planar = cv2.perspectiveTransform(template.reshape(-1, 1, 2), homography).reshape(-1, 2)
    basis = _terms(template[:, 0] / width, template[:, 1] / height)
    coefficients = np.zeros((basis.shape[1], 2))
    errors = np.linalg.norm(planar - source, axis=1)
    for _ in range(10):
        if keep.sum() < basis.shape[1] * 3:
            return None
        coefficients, *_ = np.linalg.lstsq(basis[keep], (source - planar)[keep], rcond=None)
        errors = np.linalg.norm(planar + basis @ coefficients - source, axis=1)
        trimmed = errors < max(3.0, 3.0 * float(np.median(errors[keep])))
        if (trimmed == keep).all():
            break
        keep = trimmed
    quality.inlier_count = int(keep.sum())
    quality.inlier_ratio = float(keep.mean())
    quality.mean_reprojection_error_px = float(errors[keep].mean())
    quality.median_reprojection_error_px = float(np.median(errors[keep]))
    quality.spatial_distribution_score = min(
        _spatial_distribution(source[keep].astype(np.float32),
                              source_rgb.shape[1], source_rgb.shape[0]),
        _spatial_distribution(template[keep].astype(np.float32), width, height),
    )
    # Errors are measured in source pixels; express the threshold check in template pixels.
    error_scale = float(np.hypot(width, height) / np.hypot(*source_rgb.shape[:2][::-1]))
    return homography, coefficients, error_scale


def _model_map(
    homography: NDArray[np.float64], coefficients: NDArray[np.float64], width: int, height: int,
) -> tuple[NDArray[np.float32], NDArray[np.float32], float]:
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float64),
                                 np.arange(height, dtype=np.float64))
    points = np.stack([grid_x.ravel(), grid_y.ravel()], axis=1)
    planar = cv2.perspectiveTransform(points.reshape(-1, 1, 2), homography).reshape(-1, 2)
    correction = _terms(points[:, 0] / width, points[:, 1] / height) @ coefficients
    mapped = (planar + correction).astype(np.float32)
    return (
        mapped[:, 0].reshape(height, width), mapped[:, 1].reshape(height, width),
        float(np.abs(correction).max()),
    )


def _remap(
    source_rgb: NDArray[np.uint8], map_x: NDArray[np.float32], map_y: NDArray[np.float32],
) -> NDArray[np.uint8]:
    return cv2.remap(source_rgb, map_x, map_y, cv2.INTER_CUBIC,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def _compose(
    mapping: NDArray[np.float32], at_x: NDArray[np.float32], at_y: NDArray[np.float32],
) -> NDArray[np.float32]:
    """Sample a map at shifted positions, extending it linearly past the template edge."""
    pad = int(LOCAL_RADIUS) + 2
    rows = np.arange(-pad, mapping.shape[0] + pad)
    cols = np.arange(-pad, mapping.shape[1] + pad)
    extended = _extend(_extend(mapping, cols, axis=1), rows, axis=0)
    return cv2.remap(extended, at_x + pad, at_y + pad, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def _extend(
    mapping: NDArray[np.float32], index: NDArray[np.int64], axis: int,
) -> NDArray[np.float32]:
    size = mapping.shape[axis]
    clipped = np.clip(index, 0, size - 1)
    base = np.take(mapping, clipped, axis=axis)
    slope_low = np.take(mapping, [1], axis=axis) - np.take(mapping, [0], axis=axis)
    slope_high = np.take(mapping, [size - 1], axis=axis) - np.take(mapping, [size - 2], axis=axis)
    shape = [1, 1]
    shape[axis] = len(index)
    below = np.minimum(index, 0).reshape(shape)
    above = np.maximum(index - (size - 1), 0).reshape(shape)
    return (base + below * slope_low + above * slope_high).astype(np.float32)


def _smoothed_gray(image_rgb: NDArray[np.uint8]) -> NDArray[np.float32]:
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    return cv2.GaussianBlur(gray, (0, 0), 2).astype(np.float32) / 255.0


def _local_shifts(
    aligned_rgb: NDArray[np.uint8], template_rgb: NDArray[np.uint8],
) -> tuple[NDArray[np.float32], NDArray[np.float32], int]:
    """Dense template-pixel shifts that bring the aligned page onto the template."""
    height, width = template_rgb.shape[:2]
    aligned, template = _smoothed_gray(aligned_rgb), _smoothed_gray(template_rgb)
    margin = int(LOCAL_RADIUS) + 4
    # Only photo pixels near printed template structure take part (not page edges or tabs).
    ink = (cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY) < 160).astype(np.uint8)
    support = cv2.dilate(ink, np.ones((2 * margin + 1,) * 2, np.uint8))
    half = TILE // 2
    ys = sorted({*range(half, height - half + 1, STEP), height - half})
    xs = sorted({*range(half, width - half + 1, STEP), width - half})
    shift_x = np.full((len(ys), len(xs)), np.nan, dtype=np.float32)
    shift_y = shift_x.copy()
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-4)
    for i, center_y in enumerate(ys):
        for j, center_x in enumerate(xs):
            y0, x0 = center_y - half, center_x - half
            patch = template[y0:y0 + TILE, x0:x0 + TILE]
            if float(patch.std()) < 0.03:
                continue
            ya, xa = max(0, y0 - margin), max(0, x0 - margin)
            yb, xb = min(height, y0 + TILE + margin), min(width, x0 + TILE + margin)
            warp = np.array([[1, 0, x0 - xa], [0, 1, y0 - ya]], dtype=np.float32)
            try:
                score, warp = cv2.findTransformECC(
                    patch, aligned[ya:yb, xa:xb], warp, cv2.MOTION_TRANSLATION,
                    criteria, support[ya:yb, xa:xb], 1,
                )
            except cv2.error:
                continue
            dx, dy = warp[0, 2] - (x0 - xa), warp[1, 2] - (y0 - ya)
            if score < MIN_TILE_CORRELATION or max(abs(dx), abs(dy)) > LOCAL_RADIUS:
                continue
            shift_x[i, j], shift_y[i, j] = dx, dy
    found = int((~np.isnan(shift_x)).sum())
    return (
        _dense(_smooth(shift_x), xs, ys, width, height),
        _dense(_smooth(shift_y), xs, ys, width, height),
        found,
    )


def _smooth(field: NDArray[np.float32]) -> NDArray[np.float32]:
    """Fill tiles without evidence from their neighbours, then median + Gaussian smooth."""
    values = field.copy()
    known = ~np.isnan(values)
    offsets = [(di, dj) for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj]
    rows, cols = values.shape
    while not known.all():
        padded = np.pad(np.where(known, values, 0), 1)
        counts = np.pad(known.astype(np.float32), 1)
        total = np.zeros_like(values)
        count = np.zeros_like(values)
        for di, dj in offsets:
            total += padded[1 + di:rows + 1 + di, 1 + dj:cols + 1 + dj]
            count += counts[1 + di:rows + 1 + di, 1 + dj:cols + 1 + dj]
        fill = ~known & (count > 0)
        if not fill.any():
            values[~known] = 0
            break
        values[fill] = total[fill] / count[fill]
        known |= fill
    values = cv2.medianBlur(np.pad(values, 1, mode="edge"), 3)[1:-1, 1:-1]
    return cv2.GaussianBlur(values, (0, 0), 1.0)


def _dense(
    field: NDArray[np.float32], xs: list[int], ys: list[int], width: int, height: int,
) -> NDArray[np.float32]:
    grid_x = np.interp(np.arange(width), xs, np.arange(len(xs))).astype(np.float32)
    grid_y = np.interp(np.arange(height), ys, np.arange(len(ys))).astype(np.float32)
    map_x, map_y = np.meshgrid(grid_x, grid_y)
    return cv2.remap(field, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _map_preserves_order(map_x: NDArray[np.float32], map_y: NDArray[np.float32]) -> bool:
    """The page must not fold: the map keeps the orientation and a sane local scale."""
    dxdx, dxdy = np.gradient(map_x, axis=1), np.gradient(map_x, axis=0)
    dydx, dydy = np.gradient(map_y, axis=1), np.gradient(map_y, axis=0)
    det = dxdx * dydy - dxdy * dydx
    median = float(np.median(det))
    return bool(median > 0 and (det > 0.25 * median).all() and (det < 4 * median).all())


def _form_box(template_rgb: NDArray[np.uint8]) -> tuple[slice, slice]:
    ink = np.argwhere(cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY) < 128)
    if not len(ink):
        return slice(None), slice(None)
    (y0, x0), (y1, x1) = ink.min(axis=0), ink.max(axis=0)
    return slice(int(y0), int(y1) + 1), slice(int(x0), int(x1) + 1)


def _correlation(aligned_rgb: NDArray[np.uint8], template_rgb: NDArray[np.uint8]) -> float:
    a = _smoothed_gray(aligned_rgb).ravel() - float(_smoothed_gray(aligned_rgb).mean())
    b = _smoothed_gray(template_rgb).ravel() - float(_smoothed_gray(template_rgb).mean())
    return float((a * b).sum() / max(1e-9, np.sqrt((a * a).sum() * (b * b).sum())))
