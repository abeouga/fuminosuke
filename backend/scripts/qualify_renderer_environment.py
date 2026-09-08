"""Actual Office DPI matrix, immutable input evidence, no OCR/Internet calls."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services.geometry_calibration import compare_boundaries, grid_boundaries, statistics
from app.services.office_reference_renderer import render_office, sha
from app.services.print_layout_source import read_source
from app.services.renderer_environment import environment_identity, environment_report
from app.services.workbook_geometry import apply_geometry


def main():
    evidence = Path(__file__).resolve().parents[2] / "docs/reference-renderer-evidence"
    previous = evidence / "calibration"
    output = evidence / "environment"
    output.mkdir(exist_ok=True)
    dataset = json.loads((previous / "dataset.json").read_text())
    selected = {"default", "explicit843", "width-matrix", "height-matrix",
                "margins", "centering", "origin", "holdout-many"}
    records = []
    for record in dataset["fixtures"]:
        case = record["definition"]
        if case["name"] not in selected:
            continue
        folder = output / case["name"]
        folder.mkdir(exist_ok=True)
        content = (previous / case["name"] / "source.xlsx").read_bytes()
        assert sha(content) == record["sourceSha256"]
        source = apply_geometry(read_source(content, "Calibration", {"cells": []}),
                                record["geometry"])
        left, right, top, bottom = case["margins"]
        plan = page_plan(source, PrintLayoutRequest(
            base_revision="0" * 64, break_after_rows=[],
            margins=dict(left=left, right=right, top=top, bottom=bottom),
            horizontal_centering=case["center"], vertical_centering=case["center"]))
        plan["commonScale"] = case["scale"] / 100
        for dpi in [96, 150, 300]:
            execution = []
            renderer, images = render_office(content, plan, dpi=dpi, execution_metrics=execution)
            axes = {}
            for axis in ["x", "y"]:
                predicted = [b["predicted"] * dpi / 150
                             for b in record["models"]["D-office-import"][axis]["boundaries"]]
                measured = grid_boundaries(images[0], axis)
                axes[axis] = compare_boundaries(predicted, measured)
                assert axes[axis]["status"] == "MEASURED", (case["name"], dpi, axis)
            repeat = None
            if case["name"] == "explicit843":
                again_identity, again = render_office(content, plan, dpi=dpi)
                repeat = images == again and renderer == again_identity
            (folder / f"{dpi}.png").write_bytes(images[0])
            row = {"fixture": case["name"], "sourceSha256": sha(content), "dpi": dpi,
                   "renderer": renderer, "normalFont": record["normalFont"],
                   "referenceSha256": sha(images[0]), "repeatIdentical": repeat,
                   "axes": axes, "execution": execution}
            records.append(row)
            print(case["name"], dpi, {a: v["statistics"]["max"] for a, v in axes.items()},
                  execution[0]["peakJobMemoryBytes"], flush=True)
    summary = {}
    for dpi in [96, 150, 300]:
        summary[dpi] = {}
        for axis in ["x", "y"]:
            rows = [r["axes"][axis] for r in records if r["dpi"] == dpi]
            errors = [b["signedError"] for r in rows for b in r["boundaries"]]
            summary[dpi][axis] = {"px": statistics(errors),
                                  "pt": statistics([e * 72 / dpi for e in errors]),
                                  "mm": statistics([e * 25.4 / dpi for e in errors]),
                                  "endDriftPx": statistics([r["endCumulativeDrift"] for r in rows])}
    result = {"schemaVersion": 1, "environmentQualified": False,
              "geometryModelVersion": "lo-import-twip-v1",
              "calibrationDatasetSha256": sha((previous / "dataset.json").read_bytes()),
              "calibrationAnalysisSha256": sha((previous / "analysis.json").read_bytes()),
              "records": records, "summary": summary}
    (output / "dpi-dataset.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    content = (previous / "explicit843/source.xlsx").read_bytes()
    report = environment_report(environment_identity(content))
    report["dpiCalibration"] = {"datasetSha256": sha((output / "dpi-dataset.json").read_bytes()),
                                "summary": summary}
    (output / "environment.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
