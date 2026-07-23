from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class ImageVariants:
    aligned_color: NDArray[np.uint8]
    clean_reference: NDArray[np.uint8]
    handwriting_only: NDArray[np.uint8]
    enhanced_gray: NDArray[np.uint8]
    ink_difference: NDArray[np.uint8]


def create_variants(
    aligned_rgb: NDArray[np.uint8],
    template_rgb: NDArray[np.uint8],
    handwriting_only_rgb: NDArray[np.uint8],
) -> ImageVariants:
    gray = cv2.cvtColor(aligned_rgb, cv2.COLOR_RGB2GRAY)
    enhanced = cv2.createCLAHE(clipLimit=1.7, tileGridSize=(8, 8)).apply(gray)
    template_gray = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY)
    difference = cv2.absdiff(gray, template_gray)
    return ImageVariants(
        aligned_color=aligned_rgb,
        clean_reference=template_rgb,
        handwriting_only=handwriting_only_rgb,
        enhanced_gray=enhanced,
        ink_difference=difference,
    )

