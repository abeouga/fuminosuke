from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray


def ledger_image(
    *,
    width: int = 620,
    height: int = 877,
    handwriting_seed: int | None = None,
) -> NDArray[np.uint8]:
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (10, 10), (width - 11, height - 11), (25, 25, 25), 3)
    for y in (80, 170, 330, 530, 700):
        cv2.line(image, (20, y), (width - 20, y), (35, 35, 35), 2)
    for x in (90, 210, 370, 500):
        cv2.line(image, (x, 20), (x, height - 20), (45, 45, 45), 1)
    for index, y in enumerate(range(45, height - 30, 70)):
        cv2.putText(
            image,
            f"FIELD {index:02d}",
            (28, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (30, 30, 30),
            1,
            cv2.LINE_AA,
        )
    if handwriting_seed is not None:
        rng = np.random.default_rng(handwriting_seed)
        color = (20, 60 + handwriting_seed * 20 % 160, 210)
        for _ in range(5):
            start = (
                int(rng.integers(110, width - 120)),
                int(rng.integers(100, height - 100)),
            )
            end = (start[0] + int(rng.integers(25, 90)), start[1] + int(rng.integers(-15, 20)))
            cv2.line(image, start, end, color, 3, cv2.LINE_AA)
    return image


def encode_png(image_rgb: NDArray[np.uint8]) -> bytes:
    success, content = cv2.imencode(".png", cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR))
    assert success
    return content.tobytes()

