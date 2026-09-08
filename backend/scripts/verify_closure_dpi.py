"""Same physical plan at three DPIs through the new raster helper."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services.geometry_calibration import compare_boundaries, grid_boundaries
from app.services.office_reference_renderer import render_office, sha
from app.services.print_layout_source import read_source
from app.services.reference_crop_safety import clearance
from app.services.reference_geometry import measure_page
from app.services.workbook_geometry import apply_geometry


def main():
    root = Path(__file__).resolve().parents[2] / "docs/reference-renderer-evidence"
    output = root / "closure"
    data = json.loads((root / "calibration/dataset.json").read_text())
    record = next(r for r in data["fixtures"] if r["definition"]["name"] == "explicit843")
    content = (root / "calibration/explicit843/source.xlsx").read_bytes()
    source = apply_geometry(read_source(content, "Calibration", {"cells": []}), record["geometry"])
    left, right, top, bottom = record["definition"]["margins"]
    plan = page_plan(
        source,
        PrintLayoutRequest(
            base_revision="0" * 64,
            break_after_rows=[],
            margins=dict(left=left, right=right, top=top, bottom=bottom),
        ),
    )
    rows = []
    for dpi in [96, 150, 300]:
        execution = []
        identity, images = render_office(content, plan, dpi=dpi, execution_metrics=execution)
        _, repeat = render_office(content, plan, dpi=dpi)
        axes = {
            axis: compare_boundaries(
                [
                    b["predicted"] * dpi / 150
                    for b in record["models"]["D-office-import"][axis]["boundaries"]
                ],
                grid_boundaries(images[0], axis),
            )
            for axis in ["x", "y"]
        }
        row = {
            "dpi": dpi,
            "sourceSha256": sha(content),
            "renderer": identity,
            "referenceSha256": sha(images[0]),
            "repeatIdentical": images == repeat,
            "previousPhaseIdentical": images[0]
            == (root / f"environment/explicit843/{dpi}.png").read_bytes(),
            "axes": axes,
            "execution": execution,
        }
        (output / f"dpi-{dpi}.png").write_bytes(images[0])
        rows.append(row)
        print(dpi, row["repeatIdentical"], row["previousPhaseIdentical"], flush=True)
    # Existing geometry verifier: independently retain its actual 20 px rejection.
    from app.services.print_layout_renderer import render_page
    from tests.unit.test_reference_qualification import case

    _, synthetic = case()
    page = synthetic["pages"][0]
    image = render_page(page)
    shifted = copy.deepcopy(page)
    shifted["fields"][0]["rectangle"]["x"] += 20
    geometry = measure_page(page, image)
    shifted_geometry = measure_page(shifted, image)
    assert geometry["status"] == "PASS" and shifted_geometry["status"] == "FAIL"
    wide = dict(
        widthMm=20,
        heightMm=10,
        contentInsetMm=2,
        neighborGapMm=2,
        borderStrokeMm=0.1,
        inkStrokeMm=0.3,
        paddingMm=0.5,
    )
    crop = {
        "geometry": geometry,
        "displacedGeometry": shifted_geometry,
        "wide": clearance(wide, geometry["maxErrorPx"], 150),
        "narrow": clearance({**wide, "contentInsetMm": 0.25}, geometry["maxErrorPx"], 150),
        "scope": "synthetic ink-envelope assumptions; not production handwriting evidence",
    }
    assert crop["wide"]["status"] == "PASS" and crop["narrow"]["status"] == "FAIL"
    (output / "dpi-verification.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (output / "crop-geometry-verification.json").write_text(
        json.dumps(crop, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
