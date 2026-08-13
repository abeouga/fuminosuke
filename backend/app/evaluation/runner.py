from __future__ import annotations

import json
import platform
import sys
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from app.core.config import Settings
from app.extraction.handwriting_isolator import HandwritingIsolator
from app.image_processing.registration import TemplateAligner
from app.storage.atomic_writer import atomic_write_text


def run_evaluation(
    *,
    settings: Settings,
    dataset: Path,
    report_path: Path,
    baseline_path: Path | None = None,
) -> dict[str, Any]:
    tracemalloc.start()
    started = time.perf_counter()
    template, source, expected_mask = _synthetic_case()
    registration_started = time.perf_counter()
    registered = TemplateAligner(settings.registration).align(source, template)
    registration_ms = (time.perf_counter() - registration_started) * 1000
    mask_started = time.perf_counter()
    isolated = HandwritingIsolator(threshold=settings.segmentation.threshold).isolate(
        registered.aligned_rgb, template
    )
    mask_ms = (time.perf_counter() - mask_started) * 1000
    mask_metrics = _binary_metrics(isolated.mask > 0, expected_mask > 0)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    annotations = _annotation_summary(dataset)
    report: dict[str, Any] = {
        "reportVersion": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "opencv": cv2.__version__,
        },
        "configuration": {
            "registration": settings.registration.model_dump(mode="json"),
            "segmentation": settings.segmentation.model_dump(mode="json"),
        },
        "dataset": annotations,
        "synthetic": {
            "caseCount": 1,
            "registration": {
                "edgeOverlap": registered.quality.edge_overlap,
                "eccScore": registered.quality.ecc_score,
                "matchCount": registered.quality.match_count,
                "inlierCount": registered.quality.inlier_count,
                "inlierRatio": registered.quality.inlier_ratio,
                "medianReprojectionErrorPx": (
                    registered.quality.median_reprojection_error_px
                ),
                "accepted": registered.quality.homography_valid,
            },
            "handwritingMask": mask_metrics,
        },
        "ocrComparison": {
            "mock": {"externalCalls": 0, "schemaFixtureValid": True},
            "disabled": {"externalCalls": 0, "producesBlankCandidate": True},
            "openai": {
                "executed": False,
                "reason": "External evaluation is opt-in and needs annotated real images",
            },
        },
        "performance": {
            "registrationMs": round(registration_ms, 2),
            "maskIsolationMs": round(mask_ms, 2),
            "totalMs": round((time.perf_counter() - started) * 1000, 2),
            "pythonTrackedPeakMiB": round(peak_bytes / 1024 / 1024, 2),
            "imageSize": {"width": template.shape[1], "height": template.shape[0]},
        },
        "comparison": _compare_baseline(
            baseline_path, mask_metrics, registered.quality.edge_overlap
        ),
        "notes": [
            "Synthetic figures validate regressions; they are not real-document accuracy.",
            "Python tracked memory excludes some native OpenCV allocations.",
            (
                "Thresholds are configuration snapshots and were not tuned "
                "against final evaluation data."
            ),
        ],
    }
    atomic_write_text(
        report_path, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _synthetic_case() -> tuple[NDArray[np.uint8], NDArray[np.uint8], NDArray[np.uint8]]:
    width, height = 620, 877
    template = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.rectangle(template, (10, 10), (width - 11, height - 11), (25, 25, 25), 3)
    for y in (80, 170, 330, 530, 700):
        cv2.line(template, (20, y), (width - 20, y), (35, 35, 35), 2)
    for x in (90, 210, 370, 500):
        cv2.line(template, (x, 20), (x, height - 20), (45, 45, 45), 1)
    for index, y in enumerate(range(45, height - 30, 70)):
        cv2.putText(
            template,
            f"FIELD {index:02d}",
            (28, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (30, 30, 30),
            1,
            cv2.LINE_AA,
        )
    filled = template.copy()
    expected = np.zeros((height, width), dtype=np.uint8)
    strokes = [((125, 125), (205, 142)), ((245, 255), (345, 265)), ((405, 590), (520, 570))]
    for start, end in strokes:
        cv2.line(filled, start, end, (25, 65, 210), 4, cv2.LINE_AA)
        cv2.line(expected, start, end, (255,), 7, cv2.LINE_AA)
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), 0.45, 1.0)
    matrix[0, 2] += 1.5
    matrix[1, 2] -= 1.0
    source = cv2.warpAffine(
        filled,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderValue=(255, 255, 255),
    ).astype(np.uint8)
    inverse = cv2.invertAffineTransform(matrix)
    expected_aligned = cv2.warpAffine(expected, inverse, (width, height)).astype(
        np.uint8
    )
    return template, source, expected_aligned


def _binary_metrics(predicted: NDArray[np.bool_], expected: NDArray[np.bool_]) -> dict[str, float]:
    true_positive = int((predicted & expected).sum())
    false_positive = int((predicted & ~expected).sum())
    false_negative = int((~predicted & expected).sum())
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 2 * precision * recall / max(1e-12, precision + recall)
    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "falsePositiveRate": round(false_positive / max(1, predicted.size), 6),
    }


def _annotation_summary(dataset: Path) -> dict[str, object]:
    manifests = sorted(dataset.glob("*.json")) if dataset.exists() else []
    samples = 0
    for path in manifests:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("samples"), list):
                samples += len(value["samples"])
        except (OSError, json.JSONDecodeError):
            continue
    return {
        "path": dataset.as_posix(),
        "manifestCount": len(manifests),
        "annotatedSampleCount": samples,
        "realImageEvaluationExecuted": samples > 0,
    }


def _compare_baseline(
    baseline_path: Path | None, metrics: dict[str, float], edge_overlap: float
) -> dict[str, object]:
    if baseline_path is None or not baseline_path.is_file():
        return {"baselineLoaded": False}
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        previous_mask = baseline["synthetic"]["handwritingMask"]
        previous_edge = baseline["synthetic"]["registration"]["edgeOverlap"]
        return {
            "baselineLoaded": True,
            "maskF1Delta": round(metrics["f1"] - float(previous_mask["f1"]), 6),
            "edgeOverlapDelta": round(edge_overlap - float(previous_edge), 6),
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"baselineLoaded": False, "warning": "BASELINE_INVALID"}
