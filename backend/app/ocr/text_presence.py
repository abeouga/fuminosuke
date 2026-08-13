from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from app.layout_analysis.detector import PaddleTextDetector, TextDetector


@dataclass(frozen=True, slots=True)
class TextPresence:
    detected: bool
    text_count: int
    texts: tuple[str, ...]
    confidences: tuple[float, ...]
    ink_ratio: float

    def to_dict(self, *, region_id: str) -> dict[str, object]:
        return {
            "regionId": region_id,
            "detected": self.detected,
            "engine": "paddleocr",
            "textCount": self.text_count,
            "texts": list(self.texts),
            "confidences": list(self.confidences),
            "inkRatio": self.ink_ratio,
        }


class PaddleTextPresenceGate:
    """Collect optional PaddleOCR audit evidence without gating external OCR."""

    def __init__(self, detector: TextDetector | None = None) -> None:
        self._detector = detector or PaddleTextDetector()

    @property
    def available(self) -> bool:
        return self._detector.available

    def inspect(self, handwriting_rgb: NDArray[np.uint8]) -> TextPresence:
        gray = cv2.cvtColor(handwriting_rgb, cv2.COLOR_RGB2GRAY)
        dark = gray < 235
        ink_ratio = float(dark.mean())
        locations = cv2.findNonZero(dark.astype(np.uint8))
        if locations is None or len(locations) < 4:
            return TextPresence(False, 0, (), (), ink_ratio)

        x, y, width, height = cv2.boundingRect(locations)
        padding = max(6, round(max(width, height) * 0.08))
        x1 = max(0, x - padding)
        y1 = max(0, y - padding)
        x2 = min(handwriting_rgb.shape[1], x + width + padding)
        y2 = min(handwriting_rgb.shape[0], y + height + padding)
        content = handwriting_rgb[y1:y2, x1:x2]
        if content.size == 0:
            return TextPresence(False, 0, (), (), ink_ratio)
        scale = max(1.0, min(4.0, 96.0 / max(1, content.shape[0])))
        if scale > 1.0:
            content = np.asarray(
                cv2.resize(
                    content,
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_CUBIC,
                ),
                dtype=np.uint8,
            )
        content = np.asarray(
            cv2.copyMakeBorder(
                content,
                12,
                12,
                12,
                12,
                cv2.BORDER_CONSTANT,
                value=(255, 255, 255),
            ),
            dtype=np.uint8,
        )
        regions = self._detector.detect(np.asarray(content, dtype=np.uint8))
        return TextPresence(
            detected=bool(regions),
            text_count=len(regions),
            texts=tuple(region.text for region in regions),
            confidences=tuple(round(region.confidence, 4) for region in regions),
            ink_ratio=ink_ratio,
        )
