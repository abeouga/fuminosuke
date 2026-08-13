import cv2
import numpy as np

from app.layout_analysis.detector import TextRegion
from app.ocr.text_presence import PaddleTextPresenceGate


class StubPaddleDetector:
    available = True

    def __init__(self) -> None:
        self.call_count = 0

    def detect(self, image_rgb: np.ndarray) -> list[TextRegion]:
        self.call_count += 1
        return [
            TextRegion(
                points=((10, 10), (80, 10), (80, 45), (10, 45)),
                text="27083",
                confidence=0.94,
            )
        ]


def test_paddle_presence_gate_rejects_blank_without_calling_detector() -> None:
    detector = StubPaddleDetector()
    result = PaddleTextPresenceGate(detector).inspect(
        np.full((80, 240, 3), 255, dtype=np.uint8)
    )

    assert result.detected is False
    assert result.text_count == 0
    assert detector.call_count == 0


def test_paddle_presence_gate_accepts_only_a_detected_handwriting_crop() -> None:
    detector = StubPaddleDetector()
    crop = np.full((80, 240, 3), 255, dtype=np.uint8)
    cv2.putText(
        crop,
        "27083",
        (20, 55),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.1,
        (10, 10, 10),
        2,
        cv2.LINE_AA,
    )

    result = PaddleTextPresenceGate(detector).inspect(crop)

    assert result.detected is True
    assert result.texts == ("27083",)
    assert result.confidences == (0.94,)
    assert detector.call_count == 1
