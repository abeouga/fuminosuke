"""Diagnostic measurement copy for edges without an interior raster sample.

Use exact saved reference bytes and explicit version fields. Never writes a DB,
print artifact, or qualification. Native work is opt-in and uses the fixed WSL
renderer under its machine mutex; the default only reads existing observations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.services.prepared_reference_evidence import PreparedReference, _replay_identity
from app.services.prepared_reference_geometry import (
    PAGE_GEOMETRY,
    _observations,
    _reference,
    _vectors,
)
from app.services.print_layout_service import digest
from app.services.reference_border_probe import compare_raster, probe_copy
from app.services.reference_measurement_selection import select_measurement_edges
from app.services.reference_stroke_differential import raster_ink_observed
from app.services.renderer_glyph_bridge import canonical_bytes
from app.services.renderer_process import renderer_mutex
from app.services.renderer_vector_bridge import inspect_wsl_vectors
from app.services.renderer_wsl import render_wsl_reference


def summary(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for page in observations:
        samples = page["rasterObservation"]["segments"]
        errors = [abs(e["errorPx"]) for f in page["fields"] for e in f["edges"]
                  if e["errorPx"] is not None]
        result.append({"page": page["page"], "fieldCount": len(page["fields"]),
                       "fieldStatuses": dict(Counter(f["status"] for f in page["fields"])),
                       "edgeStatuses": dict(Counter(e["status"] for f in page["fields"]
                                                    for e in f["edges"])),
                       "maxErrorPx": max(errors, default=None),
                       "unverifiedRasterSegments": [s["segmentIndex"] for s in samples
                           if not raster_ink_observed(s, page["strokeDifferential"], samples)]})
    return result


def run(data: Path, version_id: str, reference: Path, vectors_path: Path,
        *, render: bool = False) -> None:
    root = data.resolve(strict=True)
    uri = (root / "app.db").as_uri() + "?mode=ro"

    def version() -> tuple[str, str]:
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute("SELECT status,quality_json FROM template_versions WHERE id=?",
                             (version_id,)).fetchone()
            if row is None or not all(isinstance(v, str) for v in row):
                raise ValueError("Missing version")
            return row[0], row[1]

    before = version()
    plan = json.loads(before[1])["printLayout"]
    paths = [reference / "original.pdf", reference / "renderer.json", vectors_path]
    paths.extend(reference / f"original-{n}.png" for n in range(1, len(plan["pages"]) + 1))
    book_path = (root / plan["printFiles"]["xlsx"]["relativePath"]).resolve(strict=True)
    if not book_path.is_relative_to(root):
        raise ValueError("Workbook escaped storage")
    paths.append(book_path)
    paths = [p.resolve(strict=True) for p in paths]
    if len(set(paths)) != len(paths) or sum(p.stat().st_size for p in paths) > 64 * 1024 * 1024:
        raise ValueError("Duplicate or excessive diagnostic input")
    contents = [p.read_bytes() for p in paths]
    pdf, raw_renderer, raw_vectors, *rest = contents
    images, workbook = rest[:-1], rest[-1]
    if hashlib.sha256(workbook).hexdigest() != plan["printFiles"]["xlsx"]["sha256"]:
        raise ValueError("Workbook artifact changed")
    prepared = PreparedReference(workbook, pdf, images, json.loads(raw_renderer),
                                 {"planSha256": digest(plan)})
    vectors = json.loads(raw_vectors)
    _reference(prepared, plan)
    _vectors(pdf, prepared.report, vectors)
    observations = _observations(prepared, plan, vectors)
    selected = select_measurement_edges(observations)
    report: dict[str, Any] = {"policy": "diagnostic-unsampleable-edge-copy-v1",
        "templateVersionId": version_id, "planSha256": digest(plan),
        "selected": selected, "original": summary(observations),
        "productionQualified": False, "visibilityVerified": False}
    destination = None
    try:
        if render:
            copied, mutation = probe_copy(workbook, plan["sheetName"], selected)
            report["copy"] = mutation
            destination = Path(tempfile.mkdtemp(prefix="llmocr-measurement-selection-"))
            (destination / "probe.xlsx").write_bytes(copied)
            print(json.dumps({"stage": "render", "directory": str(destination),
                              "selectedFields": len(selected)}), flush=True)
            with renderer_mutex():
                probe_pdf, probe_images, renderer = render_wsl_reference(
                    copied, runtime_id=prepared.report["runtimeImage"]["sha256"])
                probe_vectors = inspect_wsl_vectors(probe_pdf, renderer)
            for name, payload in (("probe.pdf", probe_pdf),
                                  ("probe-renderer.json", canonical_bytes(renderer)),
                                  ("probe-vectors.json", canonical_bytes(probe_vectors))):
                (destination / name).write_bytes(payload)
            for number, image in enumerate(probe_images, 1):
                (destination / f"probe-{number}.png").write_bytes(image)
            a, b = _replay_identity(prepared.report), _replay_identity(renderer)
            a["sourceSha256"] = hashlib.sha256(copied).hexdigest()
            if a != b or prepared.report["imageSizes"] != renderer["imageSizes"]:
                raise ValueError("Measurement changed the fixed environment or page dimensions")
            _vectors(probe_pdf, renderer, probe_vectors)
            projected = deepcopy(plan)
            comparisons = []
            for page, original, changed, prior, current in zip(
                    projected["pages"], images, probe_images, vectors["vectors"]["pages"],
                    probe_vectors["vectors"]["pages"], strict=True):
                if (prior["textProgram"]["status"] != "RECORDED"
                        or prior["textProgram"] != current["textProgram"]
                        or any(prior[k] != current[k] for k in PAGE_GEOMETRY)):
                    raise ValueError("Measurement changed text program or page geometry")
                comparisons.append(compare_raster(original, changed))
                page["referenceImage"]["sha256"] = hashlib.sha256(changed).hexdigest()
            probe = PreparedReference(copied, probe_pdf, probe_images, renderer, {})
            report.update(copy=mutation, comparison=comparisons,
                          measurement=summary(_observations(probe, projected, probe_vectors)))
            # Record failures as evidence, never turn a diagnostic into approval.
            report["changesContained"] = all(not c["changedOutsideRedNeighborhood"]
                                             for c in comparisons)
    except Exception as exc:
        report["failureType"] = type(exc).__name__
        raise
    finally:
        unchanged = version() == before and all(p.read_bytes() == content
            for p, content in zip(paths, contents, strict=True))
        report["inputsUnchanged"] = unchanged
        if destination:
            (destination / "report.json").write_bytes(canonical_bytes(report))
        if not unchanged:
            raise ValueError("Diagnostic inputs changed")
    print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    run(args.data, args.version_id, args.reference, args.vectors, render=args.render)
