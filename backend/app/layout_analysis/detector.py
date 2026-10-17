from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from app.domain.text_encoding import normalize_paddle_japanese_text
from app.layout_analysis.models import LayoutCell, LayoutDefinition
from app.layout_analysis.text_placement import infer_text_placement, infer_vertical_alignment


@dataclass(frozen=True, slots=True)
class TextRegion:
    points: tuple[tuple[float, float], ...]
    text: str
    confidence: float

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs = [point[0] for point in self.points]
        ys = [point[1] for point in self.points]
        return min(xs), min(ys), max(xs), max(ys)


class TextDetector(Protocol):
    @property
    def available(self) -> bool: ...

    def detect(self, image_rgb: NDArray[np.uint8]) -> list[TextRegion]: ...


class PaddleTextDetector:
    """Lazy PaddleOCR adapter.

    PaddleOCR is deliberately imported on first detection. The application can
    still start and the OpenCV rectangle editor remains usable on machines where
    the optional Paddle runtime has not been installed yet.
    """

    def __init__(self) -> None:
        self._engine: Any | None = None
        self._import_failed = False

    @property
    def available(self) -> bool:
        self._load()
        return self._engine is not None

    def _load(self) -> None:
        if self._engine is not None or self._import_failed:
            return
        if os.getenv("PADDLEOCR_DISABLED") == "1":
            self._import_failed = True
            return
        try:
            from paddleocr import PaddleOCR

            try:
                self._engine = PaddleOCR(
                    lang="japan",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    # PaddlePaddle 3.3 on Windows can fail in the oneDNN executor
                    # for PP-OCRv6 array attributes. The plain CPU executor is
                    # stable and fast enough for individual form cells.
                    enable_mkldnn=False,
                )
            except TypeError:
                # PaddleOCR 2.x compatibility.
                try:
                    self._engine = PaddleOCR(
                        lang="japan",
                        use_angle_cls=True,
                        show_log=False,
                        use_mkldnn=False,
                    )
                except TypeError:
                    self._engine = PaddleOCR(
                        lang="japan",
                        use_angle_cls=True,
                        show_log=False,
                    )
        except (ImportError, OSError):
            self._import_failed = True

    def detect(self, image_rgb: NDArray[np.uint8]) -> list[TextRegion]:
        self._load()
        if self._engine is None:
            return []
        bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
        try:
            if hasattr(self._engine, "predict"):
                raw = self._engine.predict(bgr)
            else:
                raw = self._engine.ocr(bgr, cls=True)
        except (RuntimeError, ValueError, TypeError):
            return []
        return _parse_paddle_result(raw)


def _parse_paddle_result(raw: Any) -> list[TextRegion]:
    regions: list[TextRegion] = []

    def add(points: Any, text: Any, confidence: Any) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        text = normalize_paddle_japanese_text(text)
        try:
            normalized = tuple((float(point[0]), float(point[1])) for point in points)
            score = float(confidence)
        except (TypeError, ValueError, IndexError):
            return
        if len(normalized) >= 4:
            regions.append(TextRegion(normalized, text.strip(), max(0.0, min(1.0, score))))

    def visit(value: Any) -> None:
        if value is None:
            return
        if hasattr(value, "to_dict"):
            try:
                visit(value.to_dict())
                return
            except (TypeError, ValueError):
                pass
        if hasattr(value, "json"):
            try:
                json_value = value.json
                visit(json_value() if callable(json_value) else json_value)
                return
            except (TypeError, ValueError):
                pass
        if isinstance(value, dict):
            polygons = value.get("rec_polys")
            if polygons is None:
                polygons = value.get("dt_polys")
            texts = value.get("rec_texts")
            scores = value.get("rec_scores")
            if polygons is not None and texts is not None and scores is not None:
                for points, text, score in zip(polygons, texts, scores, strict=False):
                    add(points, text, score)
                return
            for child in value.values():
                visit(child)
            return
        if isinstance(value, list | tuple):
            # PaddleOCR 2.x: [box, (text, score)]
            if (
                len(value) == 2
                and isinstance(value[0], list | tuple | np.ndarray)
                and isinstance(value[1], list | tuple)
                and len(value[1]) >= 2
                and isinstance(value[1][0], str)
            ):
                add(value[0], value[1][0], value[1][1])
                return
            for child in value:
                visit(child)

    visit(raw)
    return regions


# A lone horizontal rule at least this long (pixels, or share of the page width)
# outside every ruled box is a writing space drawn as an underline; the field above
# it is this share of the page height tall (2026-10-17).
UNDERLINE_MIN_PIXELS = 90
UNDERLINE_MIN_RATIO = 0.06
UNDERLINE_FIELD_RATIO = 0.03


@dataclass(frozen=True, slots=True)
class _Rect:
    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def area(self) -> int:
        return self.width * self.height


class LayoutDetector:
    def __init__(self, text_detector: TextDetector | None = None) -> None:
        self._text_detector = text_detector or PaddleTextDetector()

    def detect(self, image_rgb: NDArray[np.uint8]) -> LayoutDefinition:
        layout, _ = self.detect_with_text_regions(image_rgb)
        return layout

    def detect_with_text_regions(
        self,
        image_rgb: NDArray[np.uint8],
    ) -> tuple[LayoutDefinition, list[TextRegion]]:
        if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
            raise ValueError("LayoutDetector requires an RGB image")
        height, width = image_rgb.shape[:2]
        gray = np.asarray(cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY), dtype=np.uint8)
        line_mask = self._line_mask(gray)
        rectangles = self._rectangles(line_mask, width, height)
        rectangles = sorted(
            rectangles + self._underline_fields(gray, rectangles, width, height),
            key=lambda rect: (rect.y, rect.x),
        )
        text_regions = self._text_detector.detect(image_rgb)
        ocr_available = self._text_detector.available
        cells = [
            self._cell(index, rectangle, gray, line_mask, text_regions)
            for index, rectangle in enumerate(rectangles, start=1)
        ]
        warnings: list[str] = []
        if not ocr_available:
            warnings.append(
                "PADDLEOCR_NOT_AVAILABLE: ラベル候補はOpenCV文字密度で仮判定しています。"
            )
        if not cells:
            warnings.append("NO_RULED_CELLS_DETECTED")
        return (
            LayoutDefinition(
                canonical_width=width,
                canonical_height=height,
                detector="opencv+paddleocr" if ocr_available else "opencv",
                ocr_available=ocr_available,
                cells=cells,
                warnings=warnings,
            ),
            text_regions,
        )

    @staticmethod
    def _line_mask(gray: NDArray[np.uint8]) -> NDArray[np.uint8]:
        height, width = gray.shape
        binary = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            31,
            9,
        )
        horizontal = cv2.morphologyEx(
            binary,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(18, width // 45), 1)),
        )
        vertical = cv2.morphologyEx(
            binary,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(18, height // 65))),
        )
        return np.asarray(cv2.bitwise_or(horizontal, vertical), dtype=np.uint8)

    @classmethod
    def _rectangles(
        cls, line_mask: NDArray[np.uint8], image_width: int, image_height: int
    ) -> list[_Rect]:
        contours, _ = cv2.findContours(line_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        image_area = image_width * image_height
        minimum_area = max(450, int(image_area * 0.00005))
        candidates: list[_Rect] = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            area = width * height
            if area < minimum_area or width < 22 or height < 16:
                continue
            if area > image_area * 0.82:
                continue
            aspect = width / height
            if aspect > 35 or aspect < 0.04:
                continue
            border = line_mask[y : y + height, x : x + width]
            if border.size == 0:
                continue
            edge = max(2, min(5, width // 5, height // 5))
            supports = (
                float(np.mean(border[:edge, :] > 0)),
                float(np.mean(border[-edge:, :] > 0)),
                float(np.mean(border[:, :edge] > 0)),
                float(np.mean(border[:, -edge:] > 0)),
            )
            confidence = float(np.mean(supports))
            if min(supports) < 0.09 or confidence < 0.2:
                continue
            candidates.append(_Rect(x, y, width, height, min(0.99, confidence)))

        deduplicated: list[_Rect] = []
        for candidate in sorted(candidates, key=lambda rect: rect.area):
            if any(cls._iou(candidate, existing) > 0.88 for existing in deduplicated):
                continue
            deduplicated.append(candidate)

        # A large contour enclosing two or more real cells is the table/container,
        # not an input box. This is the explicit V2 false-positive rule.
        cells = [
            candidate
            for candidate in deduplicated
            if sum(
                cls._strictly_contains(candidate, other)
                for other in deduplicated
                if other != candidate
            )
            < 2
        ]
        return sorted(cells, key=lambda rect: (rect.y, rect.x))

    @staticmethod
    def _underline_fields(
        gray: NDArray[np.uint8], rectangles: list[_Rect], image_width: int, image_height: int
    ) -> list[_Rect]:
        """Writing spaces drawn as a lone underline, such as a caption and a blank set
        on a rule. A rule on the border of a ruled box, or inside one, is not one."""
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 9
        )
        horizontal = cv2.morphologyEx(
            binary,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(18, image_width // 45), 1)),
        )
        count, _, stats, _ = cv2.connectedComponentsWithStats(horizontal, connectivity=8)
        minimum = max(UNDERLINE_MIN_PIXELS, int(image_width * UNDERLINE_MIN_RATIO))
        field_height = max(24, round(image_height * UNDERLINE_FIELD_RATIO))
        fields: list[_Rect] = []
        for item in range(1, count):
            x, y, width, height = (int(v) for v in stats[item, :4])
            if width < minimum or height > 6:
                continue

            def overlaps(rect: _Rect, x: int = x, width: int = width) -> int:
                return min(rect.x + rect.width, x + width) - max(rect.x, x)

            # The ruled boxes at this height (on the rule or around it) cover the rule
            # when their spans together reach 30 % of its length.
            spans = sorted(
                (max(rect.x, x), min(rect.x + rect.width, x + width))
                for rect in rectangles
                if overlaps(rect) > 0 and rect.y - 6 <= y <= rect.y + rect.height + 6
            )
            covered = reach = 0
            for start, end in spans:
                start = max(start, reach)
                if end > start:
                    covered += end - start
                    reach = end
            if covered >= 0.3 * width:
                continue
            # A solid rule, not a dotted separator: dark along nearly its whole length.
            row = gray[max(0, y - 1) : y + height + 1, x : x + width] < 128
            if float(row.any(axis=0).mean()) < 0.9:
                continue
            top = max(0, y - field_height)
            for rect in [*rectangles, *fields]:
                bottom = rect.y + rect.height
                if overlaps(rect) > 0 and top < bottom <= y:
                    top = bottom + 1
            if y - top < 16:
                continue
            # A writing space has a printed caption on the rule or just left of it, on
            # paper; a bare rule, or the paper edge against a dark background, does not.
            band = gray[top : max(top, y - 2), max(0, x - 4 * field_height) : x + width]
            if band.size == 0 or float(np.median(band)) < 160:
                continue
            if not 0.004 <= float((band < 128).mean()) <= 0.5:
                continue
            fields.append(_Rect(x, top, width, y + height - top, 0.5))
        return fields

    @staticmethod
    def _cell(
        index: int,
        rect: _Rect,
        gray: NDArray[np.uint8],
        line_mask: NDArray[np.uint8],
        text_regions: Iterable[TextRegion],
    ) -> LayoutCell:
        matched = [
            region for region in text_regions if LayoutDetector._text_is_in_cell(region, rect)
        ]
        preliminary_text = " ".join(region.text for region in matched)
        text_direction, text_alignment = infer_text_placement(
            cell_width=rect.width,
            cell_height=rect.height,
            text=preliminary_text,
            bounds=(region.bounds for region in matched),
            origin_x=rect.x,
        )
        ordered = sorted(
            matched,
            key=(
                (lambda item: (item.bounds[1], item.bounds[0]))
                if text_direction == "vertical"
                else (lambda item: (item.bounds[0], item.bounds[1]))
            ),
        )
        separator = "" if text_direction == "vertical" else " "
        text = separator.join(region.text for region in ordered)
        text_confidence = (
            float(np.mean([region.confidence for region in matched])) if matched else None
        )
        has_ink = LayoutDetector._has_printed_ink(gray, line_mask, rect)
        role = "label" if matched or has_ink else "input"
        return LayoutCell(
            id=f"cell_{index:04d}",
            role=role,
            x=rect.x,
            y=rect.y,
            width=rect.width,
            height=rect.height,
            confidence=round(rect.confidence, 3),
            text=text,
            text_confidence=round(text_confidence, 3) if text_confidence is not None else None,
            text_direction=text_direction,
            text_alignment=text_alignment,
            text_vertical_alignment=infer_vertical_alignment(
                cell_height=rect.height,
                bounds=(region.bounds for region in matched),
                origin_y=rect.y,
            ),
            source="detected",
        )

    @staticmethod
    def _has_printed_ink(
        gray: NDArray[np.uint8], line_mask: NDArray[np.uint8], rect: _Rect
    ) -> bool:
        inset_x = max(2, min(6, rect.width // 10))
        inset_y = max(2, min(6, rect.height // 10))
        left, right = rect.x + inset_x, rect.x + rect.width - inset_x
        top, bottom = rect.y + inset_y, rect.y + rect.height - inset_y
        if right <= left or bottom <= top:
            return False
        ink = gray[top:bottom, left:right] < 190
        lines = (
            cv2.dilate(
                line_mask[top:bottom, left:right],
                np.ones((3, 3), np.uint8),
            )
            > 0
        )
        printable = ink & ~lines
        component_count, _, stats, _ = cv2.connectedComponentsWithStats(
            printable.astype(np.uint8), connectivity=8
        )
        meaningful = sum(
            2 <= stats[item, cv2.CC_STAT_WIDTH] <= max(40, rect.width * 0.5)
            and 3 <= stats[item, cv2.CC_STAT_HEIGHT] <= max(50, rect.height * 0.8)
            and stats[item, cv2.CC_STAT_AREA] >= 5
            for item in range(1, component_count)
        )
        return meaningful >= 1 and float(printable.mean()) >= 0.0025

    @staticmethod
    def _text_is_in_cell(region: TextRegion, cell: _Rect) -> bool:
        left, top, right, bottom = region.bounds
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        return (
            cell.x <= center_x <= cell.x + cell.width and cell.y <= center_y <= cell.y + cell.height
        )

    @staticmethod
    def _iou(a: _Rect, b: _Rect) -> float:
        left, top = max(a.x, b.x), max(a.y, b.y)
        right = min(a.x + a.width, b.x + b.width)
        bottom = min(a.y + a.height, b.y + b.height)
        overlap = max(0, right - left) * max(0, bottom - top)
        if overlap == 0:
            return 0.0
        return overlap / (a.area + b.area - overlap)

    @staticmethod
    def _strictly_contains(outer: _Rect, inner: _Rect) -> bool:
        return (
            outer.x <= inner.x
            and outer.y <= inner.y
            and outer.x + outer.width >= inner.x + inner.width
            and outer.y + outer.height >= inner.y + inner.height
            and outer.area > inner.area * 1.2
        )
