"""Paper-background flattening for phone photos (dark, shadowed, tinted).

A scanned or exported form already has a uniform white background and is returned
unchanged (same pixels), so previously successful inputs keep their exact pixels.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

# The page is flattened when its paper is darker or less even than the clean template's
# by these luminance (0-255) margins. Printed shading that the template also has is kept.
LEVEL_MARGIN = 20.0
SPREAD_MARGIN = 20.0
# Ink level restoration: the darkest INK_PERCENTILE of a flattened photo is mapped to
# black when it is lighter than INK_TARGET, with a mild gamma to firm up strokes.
INK_PERCENTILE = 0.5
INK_TARGET = 40.0
INK_GAMMA = 1.5
# Robust noise sigma (luminance) above which the brightened page is denoised.
NOISE_LIMIT = 3.0


@dataclass(frozen=True, slots=True)
class FlattenResult:
    image_rgb: NDArray[np.uint8]
    applied: bool
    paper_level: float
    paper_spread: float
    white_level: float = 255.0

    def to_dict(self) -> dict[str, object]:
        return {
            "photometricFlattening": self.applied,
            "paperLevel": round(self.paper_level, 1),
            "paperSpread": round(self.paper_spread, 1),
        }


def paper_background(image_rgb: NDArray[np.uint8]) -> NDArray[np.float32]:
    """Estimate the paper colour at every pixel by removing ink, lines and text."""
    height, width = image_rgb.shape[:2]
    scale = min(1.0, 400.0 / max(height, width))
    small = cv2.resize(
        image_rgb, (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    # Closing with a kernel wider than strokes and table lines keeps only paper.
    size = max(5, round(max(small.shape[:2]) * 0.035)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    background = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)
    background = cv2.GaussianBlur(background, (0, 0), size / 2)
    resized = cv2.resize(background, (width, height), interpolation=cv2.INTER_LINEAR)
    return resized.astype(np.float32)


def _paper_stats(background: NDArray[np.float32]) -> tuple[float, float]:
    luminance = background @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    low, level, high = np.percentile(luminance, [5, 50, 95])
    return float(level), float(high - low)


def flatten_paper(
    image_rgb: NDArray[np.uint8], reference_rgb: NDArray[np.uint8],
) -> FlattenResult:
    background = paper_background(image_rgb)
    level, spread = _paper_stats(background)
    reference_level, reference_spread = _paper_stats(paper_background(reference_rgb))
    if level >= reference_level - LEVEL_MARGIN and spread <= reference_spread + SPREAD_MARGIN:
        return FlattenResult(image_rgb, False, level, spread, reference_level)
    flattened = image_rgb.astype(np.float32) * reference_level / np.maximum(background, 8.0)
    return FlattenResult(
        np.clip(flattened, 0, 255).astype(np.uint8), True, level, spread, reference_level,
    )


def restore_ink(image_rgb: NDArray[np.uint8], white: float) -> NDArray[np.uint8]:
    """Darken ink that a dim, blurred photo left grey, keeping paper white and ink colour.

    Applied to the registered page for cropping only; registration itself uses the
    flattened page, whose features this contrast change would distort. Only the
    luminance is stretched and each pixel's RGB is scaled by the same factor, so a
    blue or red pen stays distinguishable from black print.
    """
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    residual = gray.astype(np.float32) - cv2.medianBlur(gray, 5).astype(np.float32)
    noise = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
    if noise > NOISE_LIMIT:
        # Brightening a dim photo amplifies sensor noise; remove it before the ink stretch.
        image_rgb = cv2.fastNlMeansDenoisingColored(
            # A low colour strength keeps a blue or red pen distinguishable from print.
            image_rgb, None, h=min(15.0, noise * 1.2), hColor=min(10.0, noise * 0.6),
            templateWindowSize=7, searchWindowSize=21,
        )
    else:
        image_rgb = cv2.medianBlur(image_rgb, 3)
    luminance = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    black = float(np.percentile(luminance, INK_PERCENTILE))
    if black <= INK_TARGET or black >= white - 60:
        return image_rgb
    stretched = np.clip((luminance - black) / (white - black), 0, 1) ** INK_GAMMA * white
    gain = stretched / np.maximum(luminance, 1.0)
    return np.clip(image_rgb.astype(np.float32) * gain[..., None], 0, 255).astype(np.uint8)
