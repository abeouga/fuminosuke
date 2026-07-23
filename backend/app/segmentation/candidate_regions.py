from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray


def handwriting_candidate_regions(
    mask: NDArray[np.uint8],
    probability: NDArray[np.float32] | None = None,
) -> list[dict[str, float | int | str]]:
    """Group nearby mask components into selectable text-sized rectangles."""
    binary = (mask > 0).astype(np.uint8) * 255
    height, width = binary.shape[:2]
    horizontal_gap = max(7, width // 100)
    vertical_gap = max(3, height // 600)
    grouped = cv2.dilate(
        binary,
        cv2.getStructuringElement(cv2.MORPH_RECT, (horizontal_gap, vertical_gap)),
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(grouped, 8)
    regions: list[dict[str, float | int | str]] = []
    for label in range(1, count):
        x, y, region_width, region_height, _ = map(int, stats[label])
        padding = max(2, round(min(width, height) * 0.002))
        x1 = max(0, x - padding)
        y1 = max(0, y - padding)
        x2 = min(width, x + region_width + padding)
        y2 = min(height, y + region_height + padding)
        selected = binary[y1:y2, x1:x2] > 0
        pixel_count = int(selected.sum())
        if pixel_count < 5:
            continue
        confidence = 0.5
        if probability is not None and probability.shape == binary.shape:
            confidence = float(probability[y1:y2, x1:x2][selected].mean())
        regions.append(
            {
                "candidateId": "",
                "x": x1 / width,
                "y": y1 / height,
                "width": (x2 - x1) / width,
                "height": (y2 - y1) / height,
                "pixelCount": pixel_count,
                "confidence": round(confidence, 3),
            }
        )

    regions.sort(key=lambda item: (float(item["y"]), float(item["x"])))
    for index, region in enumerate(regions, start=1):
        region["candidateId"] = f"handwriting-{index:03d}"
    return regions


def handwriting_candidates_payload(
    mask: NDArray[np.uint8],
    candidates: list[dict[str, float | int | str]],
) -> dict[str, object]:
    height, width = mask.shape[:2]
    return {
        "coordinateSystem": {
            "origin": "top-left",
            "unit": "normalized",
            "referenceWidth": width,
            "referenceHeight": height,
        },
        "candidates": candidates,
    }
