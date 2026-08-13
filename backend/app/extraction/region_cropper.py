from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.domain.template_config import RegionConfig, TemplateConfig
from app.extraction.image_variants import ImageVariants


@dataclass(frozen=True, slots=True)
class RegionCrops:
    config: RegionConfig
    bbox: tuple[int, int, int, int]
    aligned_color: NDArray[np.uint8]
    clean_reference: NDArray[np.uint8]
    handwriting_only: NDArray[np.uint8]
    enhanced_gray: NDArray[np.uint8]
    ink_difference: NDArray[np.uint8]


class RegionCropper:
    def __init__(
        self,
        config: TemplateConfig,
        regions: list[RegionConfig] | None = None,
    ) -> None:
        self._config = config
        self._regions = regions if regions is not None else config.ocr_regions()

    def crop(self, variants: ImageVariants) -> dict[str, RegionCrops]:
        result: dict[str, RegionCrops] = {}
        for region in self._regions:
            x1 = region.x - region.padding
            y1 = region.y - region.padding
            x2 = region.x + region.width + region.padding
            y2 = region.y + region.height + region.padding
            if (
                x1 < 0
                or y1 < 0
                or x2 > self._config.canonical_width
                or y2 > self._config.canonical_height
            ):
                raise ValueError(f"Region {region.region_id} padding exceeds canonical bounds")
            slices = np.s_[y1:y2, x1:x2]
            result[region.region_id] = RegionCrops(
                config=region,
                bbox=(x1, y1, x2 - x1, y2 - y1),
                aligned_color=variants.aligned_color[slices].copy(),
                clean_reference=variants.clean_reference[slices].copy(),
                handwriting_only=variants.handwriting_only[slices].copy(),
                enhanced_gray=variants.enhanced_gray[slices].copy(),
                ink_difference=variants.ink_difference[slices].copy(),
            )
        return result
