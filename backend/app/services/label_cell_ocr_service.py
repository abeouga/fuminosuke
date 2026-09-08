from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

import numpy as np
from numpy.typing import NDArray

from app.layout_analysis.detector import PaddleTextDetector, TextDetector, TextRegion
from app.layout_analysis.models import LayoutCell, LayoutDefinition
from app.layout_analysis.text_placement import infer_text_placement, infer_vertical_alignment


@dataclass(frozen=True, slots=True)
class LabelCellOcrResult:
    cells: list[LayoutCell]
    recognized_count: int
    ocr_available: bool
    warnings: list[str]


def recognize_label_cells(
    image_rgb: NDArray[np.uint8],
    cells: list[LayoutCell],
    *,
    text_detector: TextDetector | None = None,
    excluded_cell_ids: set[str] | None = None,
) -> LabelCellOcrResult:
    """OCR only cells that the user classified as printed labels.

    Input, mark, and ignored cells are deliberately excluded. This operation
    only transcribes printed label text and does not assign field meanings.
    """

    excluded = excluded_cell_ids or set()
    label_cells = [
        cell
        for cell in cells
        if cell.role in {"label", "parent_label"} and cell.id not in excluded
    ]
    if not label_cells:
        return LabelCellOcrResult(
            cells=cells,
            recognized_count=0,
            ocr_available=True,
            warnings=["LABEL_OCR_SKIPPED_NO_LABEL_CELLS"],
        )

    detector = text_detector or PaddleTextDetector()
    if not detector.available:
        return LabelCellOcrResult(
            cells=cells,
            recognized_count=0,
            ocr_available=False,
            warnings=["LABEL_OCR_NOT_AVAILABLE_EXISTING_TEXT_RETAINED"],
        )

    by_cell_id = {
        cell.id: detector.detect(crop)
        for cell in label_cells
        if (crop := _crop_cell(image_rgb, cell)) is not None
    }
    recognized_count = sum(bool(items) for items in by_cell_id.values())
    updated = [
        _apply_regions(cell, by_cell_id.get(cell.id, []))
        if cell.role in {"label", "parent_label"}
        else cell
        for cell in cells
    ]
    warnings: list[str] = []
    if recognized_count < len(label_cells):
        warnings.append(f"LABEL_OCR_PARTIAL:{recognized_count}/{len(label_cells)}")
    return LabelCellOcrResult(
        cells=updated,
        recognized_count=recognized_count,
        ocr_available=True,
        warnings=warnings,
    )


def protected_label_cell_ids(layout: LayoutDefinition) -> set[str]:
    """Return labels whose confirmed meaning or record role must not be re-OCRed."""

    protected = {
        label_id
        for relation in layout.relations
        for label_id in relation.label_cell_ids
    }
    for section in layout.record_sections:
        protected.update(section.record_label_cell_ids)
        if section.parent_label_cell_id:
            protected.add(section.parent_label_cell_id)
    return protected


def _crop_cell(
    image_rgb: NDArray[np.uint8],
    cell: LayoutCell,
) -> NDArray[np.uint8] | None:
    image_height, image_width = image_rgb.shape[:2]
    left = max(0, min(cell.x, image_width))
    top = max(0, min(cell.y, image_height))
    right = max(left, min(cell.x + cell.width, image_width))
    bottom = max(top, min(cell.y + cell.height, image_height))
    if right <= left or bottom <= top:
        return None
    return np.asarray(image_rgb[top:bottom, left:right], dtype=np.uint8)


def _apply_regions(cell: LayoutCell, regions: list[TextRegion]) -> LayoutCell:
    if not regions:
        return cell
    preliminary_text = " ".join(
        region.text.strip() for region in regions if region.text.strip()
    ).strip()
    text_direction, text_alignment = infer_text_placement(
        cell_width=cell.width,
        cell_height=cell.height,
        text=preliminary_text,
        bounds=(region.bounds for region in regions),
    )
    regions = sorted(
        regions,
        key=(
            (lambda item: (item.bounds[1], item.bounds[0]))
            if text_direction == "vertical"
            else (lambda item: (item.bounds[0], item.bounds[1]))
        ),
    )
    separator = "" if text_direction == "vertical" else " "
    text = separator.join(
        region.text.strip() for region in regions if region.text.strip()
    ).strip()
    if not text:
        return cell
    return cell.model_copy(
        update={
            "text": text,
            "text_confidence": round(mean(region.confidence for region in regions), 3),
            "text_direction": text_direction,
            "text_alignment": text_alignment,
            "text_vertical_alignment": infer_vertical_alignment(
                cell_height=cell.height, bounds=(region.bounds for region in regions),
            ),
        }
    )
