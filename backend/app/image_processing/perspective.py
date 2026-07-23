from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray


def order_quad(points: NDArray[np.float32]) -> NDArray[np.float32]:
    reshaped = points.reshape(4, 2).astype(np.float32)
    sums = reshaped.sum(axis=1)
    differences = np.diff(reshaped, axis=1).ravel()
    return np.array(
        [
            reshaped[np.argmin(sums)],
            reshaped[np.argmin(differences)],
            reshaped[np.argmax(sums)],
            reshaped[np.argmax(differences)],
        ],
        dtype=np.float32,
    )


def warp_quad(
    image_rgb: NDArray[np.uint8],
    corners: NDArray[np.float32],
    width: int,
    height: int,
) -> tuple[NDArray[np.uint8], NDArray[np.float64]]:
    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(order_quad(corners), destination)
    warped = cv2.warpPerspective(
        image_rgb,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    return warped, matrix

