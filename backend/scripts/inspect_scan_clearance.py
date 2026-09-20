"""Paired blank/filled observations only; absence of detected ink is not a certificate."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tempfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image


def observe(
    blank: NDArray[np.uint8], filled: NDArray[np.uint8], fields: list[dict[str, Any]],
) -> tuple[NDArray[np.uint8], NDArray[np.uint8], list[dict[str, Any]]]:
    if (blank.shape != filled.shape or blank.ndim != 2 or blank.dtype != np.uint8
            or filled.dtype != np.uint8):
        raise ValueError("Paired canonical gray images are required")
    # Retain even isolated pixels. The 2px neighborhood excludes uncertain
    # printed edges; excluded pixels are UNKNOWN, not certified blank pixels.
    blank_near = cv2.erode(blank, np.ones((5, 5), np.uint8))
    candidate = (filled < 160) & (blank_near > 200)
    uncertain = (filled < 160) & (blank_near <= 200)
    height, width = blank.shape
    observations = []
    for field in fields:
        x, y, w, h = field["bbox"]
        if (any(type(v) is not int for v in (x, y, w, h))
                or min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height):
            raise ValueError("Invalid canonical crop")
        ys, xs = np.nonzero(candidate[y:y + h, x:x + w])
        # Pixel-center distance, not an estimate of pen-stroke extent or a
        # bound on registration error. Never substitute this for safety proof.
        inset = int(np.min(np.minimum.reduce([xs, ys, w - 1 - xs, h - 1 - ys]))) \
            if len(xs) else None
        observations.append({
            "regionId": field["regionId"], "bbox": field["bbox"],
            "newDarkPixels": len(xs), "observedInsetPx": inset,
            "uncertainDarkPixels": int(uncertain[y:y + h, x:x + w].sum()),
            "inkClearanceVerified": False,
        })
    return candidate.astype(np.uint8) * 255, uncertain.astype(np.uint8) * 255, observations


def inspect(report_path: Path, pairs: list[tuple[str, str]]) -> Path:
    report_path = report_path.resolve(strict=True)
    root = report_path.parent
    baseline_bytes = report_path.read_bytes()
    baseline = json.loads(baseline_bytes)
    if (baseline.get("policy") != "print-scan-diagnostic-v1"
            or baseline.get("originalUnchanged") is not True):
        raise ValueError("A completed scan baseline is required")
    destination = Path(tempfile.mkdtemp(prefix="llmocr-scan-clearance-"))
    results = []
    for blank_sha, filled_sha in pairs:
        def select(sha: str) -> dict[str, Any]:
            found = [r for r in baseline["results"] if r["inputSha256"] == sha]
            if len(found) != 1 or found[0]["status"] != "ALIGNED_DIAGNOSTIC_ONLY":
                raise ValueError("A unique aligned scan identity is required")
            result: dict[str, Any] = found[0]
            if hashlib.sha256(Path(result["inputPath"]).read_bytes()).hexdigest() != sha:
                raise ValueError("Original scan changed")
            return result

        blank, filled = select(blank_sha), select(filled_sha)
        if (blank_sha == filled_sha or any(blank[key] != filled[key] for key in
                                          ("templatePageId", "referenceSha256", "fields"))):
            raise ValueError("Paired scans must describe the same immutable page and crops")

        def pixels(item: dict[str, Any]) -> NDArray[np.uint8]:
            path = (Path(item["outputDirectory"]) / "aligned.png").resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError("Diagnostic image escaped the baseline directory")
            content = path.read_bytes()
            if (hashlib.sha256(content).hexdigest()
                    != item.get("imageSha256", {}).get("aligned.png")):
                raise ValueError("Diagnostic image SHA is missing or changed")
            with Image.open(io.BytesIO(content)) as image:
                return np.asarray(image.convert("L"), dtype=np.uint8)

        ink, uncertain, fields = observe(pixels(blank), pixels(filled), filled["fields"])
        name = filled["templatePageId"]
        Image.fromarray(ink).save(destination / f"{name}-candidate.png")
        Image.fromarray(uncertain).save(destination / f"{name}-uncertain.png")
        results.append({"templatePageId": name, "page": filled["page"],
                        "blankSha256": blank_sha, "filledSha256": filled_sha,
                        "fields": fields, "inkClearanceVerified": False})
    output = destination / "report.json"
    output.write_text(json.dumps({
        "policy": "paired-scan-observation-v1", "baselineReport": str(report_path),
        "baselineSha256": hashlib.sha256(baseline_bytes).hexdigest(),
        "results": results, "productionQualified": False, "externalApiCalls": 0,
        "limits": ["Unmeasured local registration residual", "Ink on print remains unknown",
                   "Candidates include scan noise", "No inference about ink outside crops"],
    }, ensure_ascii=True, indent=2), encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--pair", required=True, action="append", help="BLANK_SHA:FILLED_SHA")
    args = parser.parse_args()
    bindings = [tuple(raw.split(":")) for raw in args.pair]
    print(inspect(args.report, bindings))
