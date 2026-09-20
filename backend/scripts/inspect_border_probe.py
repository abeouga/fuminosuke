"""Diagnostic only: test whether measurement borders perturb a prepared workbook.

Never imported by production, never writes a saved version/artifact, never issues
qualification. PDF parsing/rendering stays in the existing fixed WSL helpers.
No text/cell-name inference: only explicit saved field ranges select probe edges.
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

from app.services.reference_border_probe import POLICY, SIDES, compare_raster, probe_copy
from app.services.reference_vector_measurement import measure_fields
from app.services.renderer_vector_bridge import inspect_wsl_vectors
from app.services.renderer_wsl import render_wsl_reference


def run(version_id: str, runtime_id: str, data: Path) -> Path:
    root = data.resolve(strict=True)
    uri = (root / "app.db").as_uri() + "?mode=ro"

    def version() -> tuple[str, str]:
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT status,quality_json FROM template_versions WHERE id=?", (version_id,)
            ).fetchone()
            if row is None or len(row) != 2 or not all(isinstance(v, str) for v in row):
                raise ValueError("Missing version")
            return row[0], row[1]

    before = version()
    plan = json.loads(before[1])["printLayout"]
    references = [plan["printFiles"][key] for key in ("xlsx", "pdf", "report")]
    references += [page["referenceImage"] for page in plan["pages"]]
    sources = {}
    for reference in references:
        path = (root / reference["relativePath"]).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("Reference escaped storage")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != reference["sha256"]:
            raise ValueError("Saved reference SHA changed")
        sources[path] = content
    workbook = sources[(root / plan["printFiles"]["xlsx"]["relativePath"]).resolve()]
    directory = Path(tempfile.mkdtemp(prefix="llmocr-border-probe-"))
    print(json.dumps({"stage": "started", "directory": str(directory)}), flush=True)
    report: dict[str, Any] = {
        "policy": POLICY,
        "templateVersionId": version_id,
        "productionQualified": False,
    }
    try:
        pdf, images, renderer = render_wsl_reference(workbook, runtime_id=runtime_id)
        if [hashlib.sha256(image).hexdigest() for image in images] != [
            page["referenceImage"]["sha256"] for page in plan["pages"]
        ]:
            raise ValueError("Fresh baseline differs from saved reference")
        vectors = inspect_wsl_vectors(pdf, renderer)
        (directory / "original.pdf").write_bytes(pdf)
        baseline = [
            measure_fields(page, vector, image)
            for page, vector, image in zip(
                plan["pages"], vectors["vectors"]["pages"], images, strict=True
            )
        ]
        selected = [
            {
                "cellId": field["cellId"],
                "sourceRange": field["sourceRange"],
                "sides": [
                    side
                    for side, edge in zip(SIDES, field["edges"], strict=True)
                    if edge["status"] == "UNOBSERVED"
                ],
            }
            for page in baseline
            for field in page["fields"]
            if any(edge["status"] == "UNOBSERVED" for edge in field["edges"])
        ]
        probe, copy_report = probe_copy(workbook, plan["sheetName"], selected)
        report["copy"] = copy_report
        (directory / "probe.xlsx").write_bytes(probe)
        print(json.dumps({"stage": "probe_render", "fieldCount": len(selected)}), flush=True)
        probe_pdf, probe_images, probe_renderer = render_wsl_reference(probe, runtime_id=runtime_id)
        for key in ("runtimeImage", "fontDirectoryIdentity", "workerIdentity", "resolvedFonts"):
            if renderer[key] != probe_renderer[key]:
                raise ValueError("Probe changed renderer environment: " + key)
        if len(probe_images) != len(images):
            raise ValueError("Probe changed page count")
        probe_vectors = inspect_wsl_vectors(probe_pdf, probe_renderer)
        if vectors["codeBundleSha256"] != probe_vectors["codeBundleSha256"]:
            raise ValueError("Probe changed vector helper implementation")
        report["pages"] = []
        for index, (page, original, changed, vector) in enumerate(
            zip(
                plan["pages"], images, probe_images, probe_vectors["vectors"]["pages"], strict=True
            ),
            1,
        ):
            projected = deepcopy(page)
            projected["referenceImage"]["sha256"] = hashlib.sha256(changed).hexdigest()
            observation = measure_fields(projected, vector, changed)
            prior_vector = vectors["vectors"]["pages"][index - 1]
            original_trace, probe_trace = prior_vector["textProgram"], vector["textProgram"]
            text_program_matches = (
                original_trace["status"] == probe_trace["status"] == "RECORDED"
                and original_trace == probe_trace
                and all(
                    prior_vector[key] == vector[key]
                    for key in ("mediaBox", "cropBox", "effectiveBox", "rotation", "userUnit")
                )
            )
            report["pages"].append(
                {
                    "page": page["page"],
                    "range": page["range"],
                    "fieldCount": observation["fieldCount"],
                    "maxErrorPx": observation["maxObservedErrorPx"],
                    "statusCounts": dict(Counter(f["status"] for f in observation["fields"])),
                    "raster": compare_raster(original, changed),
                    "textProgramMatches": text_program_matches,
                    "originalTextProgram": original_trace,
                    "probeTextProgram": probe_trace,
                }
            )
            (directory / f"original-{index}.png").write_bytes(original)
            (directory / f"probe-{index}.png").write_bytes(changed)
        (directory / "probe.pdf").write_bytes(probe_pdf)
        for name, payload in (
            ("original-vectors.json", vectors),
            ("probe-vectors.json", probe_vectors),
            ("renderer.json", renderer),
            ("probe-renderer.json", probe_renderer),
        ):
            (directory / name).write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        report["status"] = "DIAGNOSTIC_COMPLETE"
    except Exception as exc:
        report["status"] = "DIAGNOSTIC_FAILED"
        report["error"] = str(exc)
        raise
    finally:
        unchanged = version() == before and all(
            path.read_bytes() == payload for path, payload in sources.items()
        )
        report["savedVersionAndArtifactsUnchanged"] = unchanged
        (directory / "report.json").write_text(
            json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        if not unchanged:
            raise ValueError("Saved version or artifacts changed during diagnostic")
    return directory


def recheck_text(directory: Path) -> Path:
    """Inspect exact saved diagnostic PDFs again; no Office conversion or DB access."""
    root = directory.resolve(strict=True)
    names = (
        "report.json",
        "original.pdf",
        "probe.pdf",
        "probe.xlsx",
        "renderer.json",
        "probe-renderer.json",
    )
    contents = {}
    for name in names:
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or not 0 < path.stat().st_size <= 64 * 1024 * 1024
        ):
            raise ValueError("Invalid saved probe diagnostic")
        contents[name] = path.read_bytes()
    prior = json.loads(contents["report.json"])
    if (
        prior.get("policy") != POLICY
        or prior.get("status") != "DIAGNOSTIC_COMPLETE"
        or prior.get("savedVersionAndArtifactsUnchanged") is not True
        or hashlib.sha256(contents["probe.xlsx"]).hexdigest() != prior["copy"]["probeSha256"]
    ):
        raise ValueError("Incomplete or changed diagnostic input")
    original = inspect_wsl_vectors(contents["original.pdf"], json.loads(contents["renderer.json"]))
    probe = inspect_wsl_vectors(contents["probe.pdf"], json.loads(contents["probe-renderer.json"]))
    if (
        original["codeBundleSha256"] != probe["codeBundleSha256"]
        or original["vectors"]["pageCount"] != probe["vectors"]["pageCount"]
    ):
        raise ValueError("Text diagnostic implementation or page count changed")
    pages = []
    for a, b in zip(original["vectors"]["pages"], probe["vectors"]["pages"], strict=True):
        same = (
            a["textProgram"]["status"] == b["textProgram"]["status"] == "RECORDED"
            and a["textProgram"] == b["textProgram"]
            and all(
                a[key] == b[key]
                for key in (
                    "renderedPage",
                    "mediaBox",
                    "cropBox",
                    "effectiveBox",
                    "rotation",
                    "userUnit",
                )
            )
        )
        pages.append(
            {
                "page": a["renderedPage"],
                "textProgramMatches": same,
                "originalTextProgram": a["textProgram"],
                "probeTextProgram": b["textProgram"],
            }
        )
    if any((root / name).read_bytes() != value for name, value in contents.items()):
        raise ValueError("Diagnostic inputs changed during text comparison")
    destination = Path(tempfile.mkdtemp(prefix="llmocr-border-text-"))
    report = {
        "policy": POLICY,
        "inputDirectory": str(root),
        "inputs": {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()},
        "codeBundleSha256": original["codeBundleSha256"],
        "pages": pages,
        "productionQualified": False,
        "visibilityVerified": False,
    }
    for name, value in (
        ("report.json", report),
        ("original-vectors.json", original),
        ("probe-vectors.json", probe),
    ):
        (destination / name).write_text(
            json.dumps(value, ensure_ascii=True, indent=2), encoding="utf-8"
        )
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version_id")
    parser.add_argument("runtime_id")
    parser.add_argument("--data", type=Path, default=Path("data"))
    arguments = parser.parse_args()
    print(run(arguments.version_id, arguments.runtime_id, arguments.data))
