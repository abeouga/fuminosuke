import cv2
import numpy as np

from app.layout_analysis.detector import LayoutDetector, TextRegion, _parse_paddle_result


class StubTextDetector:
    available = True

    def detect(self, image_rgb: np.ndarray) -> list[TextRegion]:
        return [
            TextRegion(
                points=((31, 31), (170, 31), (170, 105), (31, 105)),
                text="個体識別番号",
                confidence=0.96,
            )
        ]


class UnavailableTextDetector:
    available = False

    def detect(self, image_rgb: np.ndarray) -> list[TextRegion]:
        return []


def test_detects_cells_classifies_text_and_excludes_table_container() -> None:
    image = np.full((360, 620, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (20, 20), (600, 330), (0, 0, 0), 3)
    cv2.line(image, (20, 120), (600, 120), (0, 0, 0), 3)
    cv2.line(image, (20, 220), (600, 220), (0, 0, 0), 3)
    cv2.line(image, (200, 20), (200, 330), (0, 0, 0), 3)
    cv2.line(image, (400, 20), (400, 330), (0, 0, 0), 3)

    result = LayoutDetector(StubTextDetector()).detect(image)

    assert result.ocr_available is True
    assert result.detector == "opencv+paddleocr"
    assert len(result.cells) >= 6
    assert any(cell.role == "label" and cell.text == "個体識別番号" for cell in result.cells)
    assert not any(cell.width > 550 and cell.height > 280 for cell in result.cells)


def test_reports_only_opencv_when_paddle_is_unavailable() -> None:
    image = np.full((120, 180, 3), 255, dtype=np.uint8)

    result = LayoutDetector(UnavailableTextDetector()).detect(image)

    assert result.ocr_available is False
    assert result.detector == "opencv"
    assert any(
        warning.startswith("PADDLEOCR_NOT_AVAILABLE")
        for warning in result.warnings
    )


def test_paddle_3_result_with_json_and_to_dict_is_not_duplicated() -> None:
    payload = {
        "rec_polys": [np.array([[1, 2], [20, 2], [20, 15], [1, 15]])],
        "rec_texts": ["27083"],
        "rec_scores": [0.98],
    }

    class DualResult:
        json = payload

        def to_dict(self) -> dict[str, object]:
            return payload

    result = _parse_paddle_result([DualResult()])

    assert len(result) == 1
    assert result[0].text == "27083"
