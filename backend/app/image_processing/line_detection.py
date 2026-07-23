import cv2
import numpy as np
from numpy.typing import NDArray


def adaptive_ink_mask(gray: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Extract local ink while ignoring uneven paper illumination."""
    shortest_edge = min(gray.shape[:2])
    block_size = min(41, shortest_edge if shortest_edge % 2 else shortest_edge - 1)
    block_size = max(3, block_size)
    background = cv2.GaussianBlur(gray, (0, 0), 15)
    local_contrast = cv2.subtract(background, gray)
    ink = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        block_size,
        10,
    )
    ink[local_contrast < 8] = 0
    return ink


class LineDetector:
    def detect(self, gray: NDArray[np.uint8]) -> NDArray[np.uint8]:
        inverted = adaptive_ink_mask(gray)
        horizontal_size = max(20, gray.shape[1] // 25)
        vertical_size = max(20, gray.shape[0] // 35)
        horizontal = cv2.morphologyEx(
            inverted,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (horizontal_size, 1)),
        )
        vertical = cv2.morphologyEx(
            inverted,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, vertical_size)),
        )
        return cv2.bitwise_or(horizontal, vertical)
