from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from app.domain.template_config import (
    FieldMappingConfig,
    MarkConfig,
    MarkOptionConfig,
    TemplateConfig,
)


@dataclass(frozen=True, slots=True)
class MarkResult:
    mark_id: str
    field: str
    selected_options: list[str]
    status: str
    review_required: bool
    difference_ratio: float
    component_count: int
    mapping_id: str | None = None
    target_path: str | None = None
    option_value: str | None = None
    option_label: str | None = None
    selection_mode: str = "multiple"

    def to_dict(self) -> dict[str, object]:
        return {
            "markId": self.mark_id,
            "field": self.field,
            "selectedOptions": self.selected_options,
            "mappingId": self.mapping_id,
            "targetPath": self.target_path,
            "optionValue": self.option_value,
            "optionLabel": self.option_label,
            "selectionMode": self.selection_mode,
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
        configured = [
            self._detect_mapping_option(mapping, option, aligned_rgb, template_rgb)
            for mapping in self._config.field_mappings
            if mapping.enabled and mapping.mapping_type == "mark"
            for option in mapping.mark_options
        ]
        legacy = [
            self._detect(mark, aligned_rgb, template_rgb)
            for mark in self._config.marks
        ]
        return [*configured, *legacy]

    def _detect_mapping_option(
        self,
        mapping: FieldMappingConfig,
        option: MarkOptionConfig,
        aligned_rgb: NDArray[np.uint8],
        template_rgb: NDArray[np.uint8],
    ) -> MarkResult:
        return self._measure(
            mark_id=f"{mapping.mapping_id}_{option.id}",
            field=mapping.target_path,
            option_value=option.value,
            option_label=option.label,
            mapping_id=mapping.mapping_id,
            target_path=mapping.target_path,
            selection_mode=mapping.mark_selection_mode,
            x1=mapping.x + option.x,
            y1=mapping.y + option.y,
            width=option.width,
            height=option.height,
            aligned_rgb=aligned_rgb,
            template_rgb=template_rgb,
        )

    def _detect(
        self,
        mark: MarkConfig,
        aligned_rgb: NDArray[np.uint8],
        template_rgb: NDArray[np.uint8],
    ) -> MarkResult:
        region = self._regions[mark.region_id]
        x1 = region.x + mark.x
        y1 = region.y + mark.y
        return self._measure(
            mark_id=mark.mark_id,
            field=mark.option_group,
            option_value=mark.option_value,
            option_label=mark.option_value,
            mapping_id=None,
            target_path=None,
            selection_mode="multiple",
            x1=x1,
            y1=y1,
            width=mark.width,
            height=mark.height,
            aligned_rgb=aligned_rgb,
            template_rgb=template_rgb,
        )

    @staticmethod
    def _measure(
        *,
        mark_id: str,
        field: str,
        option_value: str,
        option_label: str,
        mapping_id: str | None,
        target_path: str | None,
        selection_mode: str,
        x1: int,
        y1: int,
        width: int,
        height: int,
        aligned_rgb: NDArray[np.uint8],
        template_rgb: NDArray[np.uint8],
    ) -> MarkResult:
        x2 = x1 + width
        y2 = y1 + height
        source = cv2.cvtColor(aligned_rgb[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
        template = cv2.cvtColor(template_rgb[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
        if source.size == 0 or template.size == 0:
            return MarkResult(
                mark_id=mark_id,
                field=field,
                selected_options=[],
                status="uncertain",
                review_required=True,
                difference_ratio=0.0,
                component_count=0,
                mapping_id=mapping_id,
                target_path=target_path,
                option_value=option_value,
                option_label=option_label,
                selection_mode=selection_mode,
            )
        darkening = np.clip(template.astype(np.int16) - source.astype(np.int16), 0, 255)
        changed = (darkening > 24).astype(np.uint8) * 255
        changed = cv2.morphologyEx(
            changed,
            cv2.MORPH_OPEN,
            np.ones((2, 2), dtype=np.uint8),
        )
        ratio = float((changed > 0).mean())
        components = max(0, cv2.connectedComponents(changed, 8)[0] - 1)
        if ratio >= 0.03:
            status = "detected"
            selected = [option_value]
            review = False
        elif ratio >= 0.012:
            status = "uncertain"
            selected = []
            review = True
        else:
            status = "blank"
            selected = []
            review = False
        return MarkResult(
            mark_id=mark_id,
            field=field,
            selected_options=selected,
            status=status,
            review_required=review,
            difference_ratio=ratio,
            component_count=components,
            mapping_id=mapping_id,
            target_path=target_path,
            option_value=option_value,
            option_label=option_label,
            selection_mode=selection_mode,
        )

