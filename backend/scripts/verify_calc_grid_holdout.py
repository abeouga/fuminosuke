"""Read an independent grid fixture; emit isolated observations, never certificates.

Run serially, never alongside the full suite or another native renderer.
The input must be a synthetic, fully bordered worksheet, not a user template.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from app.domain import calc_print_grid as grid
from app.domain.print_layout import PAPER_MM, PrintLayoutRequest, page_plan
from app.services.preserving_print_workbook import workbook_copy
from app.services.print_layout_source import read_source
from app.services.reference_vector_measurement import measure_fields
from app.services.renderer_vector_bridge import inspect_wsl_vectors
from app.services.renderer_wsl import import_wsl_geometry, render_wsl_reference
from app.services.wsl_workbook_geometry import apply_dimensions, dimensions

CASES: tuple[dict[str, Any], ...] = (
    {"name": "horizontal-69", "zoom": 69, "orientation": "portrait",
     "margins": {"left": 18, "right": 22, "top": 19, "bottom": 13},
     "horizontalCentering": True, "verticalCentering": False, "breakAfterRows": [12]},
    {"name": "asymmetric-83", "zoom": 83, "orientation": "landscape",
     "margins": {"left": 7.1, "right": 13.3, "top": 11.7, "bottom": 16.2},
     "horizontalCentering": False, "verticalCentering": False, "breakAfterRows": [7, 19]},
    {"name": "both-centered-51", "zoom": 51, "orientation": "portrait",
     "margins": {"left": 9.2, "right": 14.1, "top": 12.3, "bottom": 18.7},
     "horizontalCentering": True, "verticalCentering": True, "breakAfterRows": [12]},
)


def project(plan: dict[str, Any]) -> dict[str, Any]:
    """Diagnostic candidate only. No saved plan or mapping is changed."""
    candidate = copy.deepcopy(plan)
    evidence = plan["geometryEvidence"]
    columns = grid.dimension_grid(evidence["columns"], evidence["hiddenColumns"])
    rows = grid.dimension_grid(evidence["rows"], evidence["hiddenRows"])
    zoom = round(plan["commonScale"] * 100)
    paper = PAPER_MM[plan["paperSize"]]
    if plan["orientation"] == "landscape":
        paper = paper[::-1]
    for page in candidate["pages"]:
        origins = []
        for axis, dims, prefix, before, after, centered in (
            (0, columns, "Col", "left", "right", "horizontalCentering"),
            (1, rows, "Row", "top", "bottom", "verticalCentering"),
        ):
            start, end = page["start" + prefix], page["end" + prefix]
            nominal = sum(dims["twips"][start - 1:end])
            origin = grid.frame_origin(
                round(paper[axis] * 1440 / 25.4),
                round(plan["margins"][before] * 1440 / 25.4),
                round(plan["margins"][after] * 1440 / 25.4),
                nominal, zoom, centered=plan[centered],
            )
            origins.append(origin)
        fields = []
        for cell in page["cells"]:
            edges = []
            for axis, dims, prefix in ((0, columns, "Col"), (1, rows, "Row")):
                boundaries = dims["boundariesMm100"]
                base = boundaries[page["start" + prefix] - 1]
                for boundary in (cell["start" + prefix] - 1, cell["end" + prefix]):
                    edges.append((origins[axis] + boundaries[boundary] - base)
                                 * zoom / 100 * plan["dpi"] / 2540)
            left, right, top, bottom = edges
            identity = "holdout:" + cell["sourceRange"]
            fields.append({**cell, "cellId": identity, "predictedEdges": edges,
                           "rectangle": {"x": round(left), "y": round(top),
                                         "width": round(right) - round(left),
                                         "height": round(bottom) - round(top)},
                           "bindings": [{"mappingId": identity, "sourceRegionId": identity,
                                         "targetPath": identity}]})
        page["fields"] = fields
    return candidate


def run(source_path: Path, output: Path, runtime: str, sheet_name: str) -> None:
    original = source_path.read_bytes()
    source = read_source(original, sheet_name, {"cells": []}, draft_support=True)
    if source["unsupported"] or source["startRow"] != 1 or source["endRow"] != 24:
        raise ValueError("Expected an independent 24-row synthetic grid")
    output.mkdir(parents=True, exist_ok=False)
    fods, imported = import_wsl_geometry(original, font_source="windows", runtime_id=runtime)
    (output / "source.fods").write_bytes(fods)
    (output / "import.json").write_text(json.dumps(imported), encoding="utf-8")
    evidence = dimensions(fods, sheet_name, source["endRow"], source["endCol"])
    evidence.update(sourceSha256=source["sha256"], importReport=imported)
    measured = apply_dimensions(source, evidence)
    summaries = []
    for case in CASES:
        request = PrintLayoutRequest.model_validate({"baseRevision": "0" * 64,
                    **{k: v for k, v in case.items() if k not in {"name", "zoom"}}})
        plan = page_plan(measured, request)
        if case["zoom"] / 100 > plan["commonScale"]:
            raise ValueError("Synthetic case exceeds its page bounds")
        plan["commonScale"] = case["zoom"] / 100
        directory = output / case["name"]
        directory.mkdir()
        printable = workbook_copy(original, plan)
        (directory / "print.xlsx").write_bytes(printable)
        pdf, images, renderer = render_wsl_reference(printable, runtime_id=runtime)
        for key in ("runtimeImage", "fontDirectoryIdentity", "workerIdentity", "resolvedFonts"):
            if not imported.get(key) or renderer.get(key) != imported[key]:
                raise ValueError(f"Measurement/render conditions changed: {key}")
        (directory / "print.pdf").write_bytes(pdf)
        (directory / "renderer.json").write_text(json.dumps(renderer), encoding="utf-8")
        vectors = inspect_wsl_vectors(pdf, renderer)
        (directory / "vectors.json").write_text(json.dumps(vectors), encoding="utf-8")
        candidate = project(plan)
        if len(images) != len(candidate["pages"]):
            raise ValueError("Native page count changed")
        observations = []
        for page, image, vector in zip(candidate["pages"], images,
                                       vectors["vectors"]["pages"], strict=True):
            (directory / f"page-{page['page']}.png").write_bytes(image)
            page["referenceImage"] = {"sha256": hashlib.sha256(image).hexdigest()}
            observation = measure_fields(page, vector, image)
            predicted = {f["cellId"]: f for f in page["fields"]}
            exact_errors = []
            for field in observation["fields"]:
                for expected, edge in zip(predicted[field["cellId"]]["predictedEdges"],
                                          field["edges"], strict=True):
                    if edge.get("positionPx") is not None:
                        exact_errors.append(abs(edge["positionPx"] - expected))
            observation["maximumContinuousErrorPx"] = max(exact_errors, default=None)
            observations.append(observation)
        report = {"case": case, "sourceSha256": source["sha256"],
                  "gridImplementationSha256": hashlib.sha256(
                      Path(grid.__file__).read_bytes()).hexdigest(),
                  "policy": grid.POLICY, "productionQualified": False,
                  "candidate": candidate, "observations": observations}
        (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
        summary = {"case": case["name"], "pages": len(observations),
                   "fields": sum(p["fieldCount"] for p in observations),
                   "maximumErrorPx": max(p["maxObservedErrorPx"] for p in observations),
                   "maximumContinuousErrorPx": max(p["maximumContinuousErrorPx"]
                                                    for p in observations),
                   "unobservedFields": sum(f["status"] != "OBSERVED_WITHIN_DIAGNOSTIC_BUDGET"
                                           for p in observations for f in p["fields"])}
        summaries.append(summary)
        print(json.dumps(summary), flush=True)
    if source_path.read_bytes() != original:
        raise ValueError("Synthetic source changed during verification")
    (output / "summary.json").write_text(json.dumps(summaries), encoding="utf-8")
    # This tests a mathematical hypothesis, not a production/crop tolerance.
    # Keep failed observations on disk and do not retune the limit to fit them.
    if any(s["unobservedFields"] or s["maximumContinuousErrorPx"] > .01
           for s in summaries):
        raise ValueError("Calc grid hypothesis failed on the independent holdout")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--sheet", default="Grid")
    args = parser.parse_args()
    run(args.source, args.output, args.runtime, args.sheet)
