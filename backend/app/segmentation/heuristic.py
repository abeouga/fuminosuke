import cv2
import numpy as np
from numpy.typing import NDArray

from app.image_processing.line_detection import LineDetector, adaptive_ink_mask


class HeuristicHandwritingSegmenter:
    def __init__(self) -> None:
        self._line_detector = LineDetector()

    def predict(self, image_rgb: NDArray[np.uint8]) -> NDArray[np.float32]:
        gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        hsv = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2HSV)
        background = cv2.GaussianBlur(gray, (0, 0), 15)
        local_contrast = cv2.subtract(background, gray)
        ink = adaptive_ink_mask(gray)
        lines = self._line_detector.detect(gray)
        candidate = cv2.bitwise_and(
            ink,
            cv2.bitwise_not(cv2.dilate(lines, np.ones((3, 3), np.uint8))),
        )
        component_probability = np.zeros(gray.shape, dtype=np.float32)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
        image_area = gray.shape[0] * gray.shape[1]
        for label in range(1, count):
            x, y, width, height, area = stats[label]
            if area < 6 or area > image_area * 0.015 or width < 2 or height < 3:
                continue

            component = (labels[y : y + height, x : x + width] == label).astype(np.uint8)
            selected = component > 0
            contrast_values = local_contrast[y : y + height, x : x + width][selected].astype(
                np.float32
            )
            saturation_values = hsv[y : y + height, x : x + width, 1][selected].astype(np.float32)
            padded_component = cv2.copyMakeBorder(
                component, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0
            )
            stroke_distance = cv2.distanceTransform(padded_component, cv2.DIST_L2, 3)[1:-1, 1:-1]
            stroke_width = float(stroke_distance[selected].mean())
            density = float(area / (width * height))
            mean_contrast = float(contrast_values.mean())
            strong_ink_ratio = float((contrast_values > 105).mean())

            medium_ink = float(np.clip(1.0 - abs(mean_contrast - 58.0) / 62.0, 0, 1))
            thin_stroke = float(np.clip((1.85 - stroke_width) / 0.85, 0, 1))
            sparse_shape = float(np.clip((0.75 - density) / 0.45, 0, 1))
            color_ink = float(np.clip((float(saturation_values.mean()) - 15.0) / 70.0, 0, 1))
            handwriting_sized = 5 <= height <= 65 and width <= 180
            size_weight = 1.0 if handwriting_sized else 0.25
            score = (
                0.42 * medium_ink
                + 0.25 * thin_stroke
                + 0.18 * sparse_shape
                + 0.15 * color_ink
                - 0.20 * strong_ink_ratio
            ) * size_weight

            if color_ink > 0.55 and height <= 80 and width <= 220:
                score = max(score, 0.85)
            elif score < 0.50:
                continue
            component_probability[labels == label] = max(score, 0.56)
        return component_probability
