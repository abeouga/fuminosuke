from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from app.domain.template_config import MarkConfig, TemplateConfig


@dataclass(frozen=True, slots=True)
class MarkResult:
    mark_id: str
    field: str
    selected_options: list[str]
    status: str
    review_required: bool
    difference_ratio: float
    component_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "markId": self.mark_id,
            "field": self.field,
            "selectedOptions": self.selected_options,
            "status": self.status,
            "reviewRequired": self.review_required,
            "metrics": {
                "differenceRatio": self.difference_ratio,
                "componentCount": self.component_count,
            },
        }


class MarkDetector:
    def __init__(self, config: TemplateConfig) -> None:
        self._config = config
        self._regions = {region.region_id: region for region in config.regions}

    def detect_all(
        self,
        aligned_rgb: NDArray[np.uint8],
        template_rgb: NDArray[np.uint8],
    ) -> list[MarkResult]:
        return [self._detect(mark, aligned_rgb, template_rgb) for mark in self._config.marks]

    def _detect(
        self,
        mark: MarkConfig,
        aligned_rgb: NDArray[np.uint8],
        template_rgb: NDArray[np.uint8],
    ) -> MarkResult:
        region = self._regions[mark.region_id]
        x1 = region.x + mark.x
        y1 = region.y + mark.y
        x2 = x1 + mark.width
        y2 = y1 + mark.height
        source = cv2.cvtColor(aligned_rgb[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
        template = cv2.cvtColor(template_rgb[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
        darkening = np.clip(template.astype(np.int16) - source.astype(np.int16), 0, 255)
        changed = (darkening > 30).astype(np.uint8) * 255
        ratio = float((changed > 0).mean())
        components = max(0, cv2.connectedComponents(changed, 8)[0] - 1)
        if ratio >= 0.035:
            status = "detected"
            selected = [mark.option_value]
            review = False
        elif ratio >= 0.015:
            status = "uncertain"
            selected = []
            review = True
        else:
            status = "blank"
            selected = []
            review = False
        return MarkResult(
            mark_id=mark.mark_id,
            field=mark.option_group,
            selected_options=selected,
            status=status,
            review_required=review,
            difference_ratio=ratio,
            component_count=components,
        )

