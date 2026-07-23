import cv2
import numpy as np
from numpy.typing import NDArray


class IlluminationNormalizer:
    def normalize_gray(self, image_rgb: NDArray[np.uint8]) -> NDArray[np.uint8]:
        lab = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2LAB)
        lightness = lab[:, :, 0]
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(12, 12))
        corrected = clahe.apply(lightness)
        white_level = max(1.0, float(np.percentile(corrected, 98)))
        return np.clip(corrected.astype(np.float32) * 245.0 / white_level, 0, 255).astype(
            np.uint8
        )

