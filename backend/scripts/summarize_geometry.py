"""Recompute calibration diagnostics from saved rasters; no Office or remote API."""
from __future__ import annotations

import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.geometry_calibration import grid_boundaries, statistics


def main():
    root = Path(__file__).resolve().parents[2] / "docs/reference-renderer-evidence"
    folder = root / "calibration"
    dataset = json.loads((folder / "dataset.json").read_text())
    truth = []
    for axis in ["x", "y"]:
        for thickness in [1, 2, 3]:
            for position in [100.0, 100.25, 100.5]:
                for kernel in [1, 9, 15]:
                    centers = np.arange(240, dtype=float)
                    coverage = np.maximum(0, np.minimum(centers + .5, position + thickness / 2)
                                          - np.maximum(centers - .5, position - thickness / 2))
                    ink = np.zeros((240, 240), dtype=np.uint8)
                    ink[20:220, :] = np.rint(coverage * 255).astype(np.uint8)
                    if axis == "y":
                        ink = ink.T
                    output = BytesIO()
                    Image.fromarray(255 - ink).save(output, format="PNG")
                    measured = grid_boundaries(output.getvalue(), axis, kernel)
                    assert len(measured) == 1
                    truth.append(dict(axis=axis, thickness=thickness, position=position,
                                      kernel=kernel, measured=measured[0],
                                      signedError=measured[0] - position))
    result = {"productionQualified": False, "dpi": 150,
              "selectedModel": "D-office-import", "geometryModelVersion": "lo-import-twip-v1",
              "detector": {"cases": truth,
                           "statistics": statistics([r["signedError"] for r in truth]),
                           "limitation": "Ideal integrated-coverage lines; not all PDF ink."},
              "fixtureDiagnostics": [], "endDrift": {}, "originalHashes": []}
    for row in dataset["fixtures"]:
        definition = row["definition"]
        model = row["models"]["D-office-import"]
        metrics = {"name": definition["name"], "scalePercent": definition["scale"],
                   "axes": {}}
        for axis in ["x", "y"]:
            boundaries = model[axis]["boundaries"]
            actual = boundaries[-1]["actual"] - boundaries[0]["actual"]
            predicted = boundaries[-1]["predicted"] - boundaries[0]["predicted"]
            metrics["axes"][axis] = {
                "offsetError": model[axis]["offsetError"],
                "endCumulativeDrift": model[axis]["endCumulativeDrift"],
                "actualScaleRatio": actual / predicted * definition["scale"] / 100,
                "maxError": model[axis]["statistics"]["max"]}
        result["fixtureDiagnostics"].append(metrics)
    for split in ["calibration", "validation"]:
        result["endDrift"][split] = {}
        for model in dataset["summary"][split]:
            result["endDrift"][split][model] = {
                axis: statistics([r["models"][model][axis]["endCumulativeDrift"]
                                  for r in dataset["fixtures"]
                                  if r["definition"]["split"] == split and model in r["models"]])
                for axis in ["x", "y"]}
    audit = json.loads((root / "audit.json").read_text(encoding="utf-8"))
    for rows in audit.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if "filename" not in row:
                continue
            path = Path("C:/Users/hanam/Desktop/xlsx") / row["filename"]
            current = hashlib.sha256(path.read_bytes()).hexdigest()
            result["originalHashes"].append({"filename": row["filename"],
                                            "sha256": current,
                                            "unchanged": current == row["sourceSha256"]})
    assert len(result["originalHashes"]) == 5
    assert all(r["unchanged"] for r in result["originalHashes"])
    (folder / "analysis.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"detector": result["detector"]["statistics"],
                      "originalsUnchanged": len(result["originalHashes"]),
                      "fixtures": len(result["fixtureDiagnostics"])}))


if __name__ == "__main__":
    main()
