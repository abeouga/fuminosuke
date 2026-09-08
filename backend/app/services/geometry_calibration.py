"""Raw-grid measurement for controlled fixtures, independent of predicted positions."""

from __future__ import annotations

import io
from typing import Any

import cv2
import numpy as np
from PIL import Image


def grid_boundaries(content: bytes, axis: str, kernel: int = 9) -> list[float]:
    with Image.open(io.BytesIO(content)) as image:
        ink = 255 - np.asarray(image.convert("L"))
    shape = (kernel, 1) if axis == "x" else (1, kernel)
    ruled = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones(shape, np.uint8))
    profile = ruled.astype(float).sum(axis=0 if axis == "x" else 1)
    # Intersection background is not a border. No +/-8px prediction window:
    # independent line counts must match the declared synthetic grid topology.
    threshold = float(profile.max()) * 0.25
    groups: list[list[int]] = []
    for p in np.flatnonzero(profile > threshold).tolist():
        if not groups or p != groups[-1][-1] + 1:
            groups.append([])
        groups[-1].append(p)
    return [float(np.average(g, weights=profile[g])) for g in groups]


def statistics(errors: list[float]) -> dict[str, float | None]:
    if not errors:
        return dict(mean=None, median=None, p95=None, max=None)
    absolute = np.abs(np.asarray(errors))
    return {"mean": float(absolute.mean()), "median": float(np.median(absolute)),
            "p95": float(np.percentile(absolute, 95)), "max": float(absolute.max())}


def compare_boundaries(predicted: list[float], actual: list[float]) -> dict[str, Any]:
    if len(predicted) != len(actual):
        return {"status": "UNMEASURABLE", "expectedCount": len(predicted),
                "actualCount": len(actual)}
    errors = [a - p for a, p in zip(actual, predicted, strict=True)]
    return {"status": "MEASURED", "statistics": statistics(errors), "offsetError": errors[0],
            "endCumulativeDrift": errors[-1] - errors[0],
            "boundaries": [{"predicted": p, "actual": a, "signedError": e,
                             "absoluteError": abs(e), "cumulativeError": e - errors[0]}
                            for p, a, e in zip(predicted, actual, errors, strict=True)],
            "intervals": [{"predictedDelta": predicted[i + 1] - predicted[i],
                           "actualDelta": actual[i + 1] - actual[i],
                           "deltaError": errors[i + 1] - errors[i]}
                          for i in range(len(errors) - 1)]}
