"""Read-only developer diagnostic; never prepares/qualifies/mutates a saved version.

Run from backend: python -m scripts.inspect_prepared_vectors VERSION_ID RUNTIME_SHA
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

from app.services.reference_vector_raster import observe_raster
from app.services.renderer_vector_bridge import inspect_wsl_vectors
from app.services.renderer_wsl import render_wsl_reference


def inspect(version_id: str, runtime_id: str, data: Path) -> dict[str, Any]:
    root = data.resolve(strict=True)
    database_uri = (root / "app.db").as_uri() + "?mode=ro"

    def version() -> tuple[str, str]:
        with closing(sqlite3.connect(database_uri, uri=True)) as database:
            database.execute("PRAGMA query_only=ON")
            row = database.execute(
                "SELECT status,quality_json FROM template_versions WHERE id=?", (version_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Template version was not found")
            status, quality = row
            if not isinstance(status, str) or not isinstance(quality, str):
                raise ValueError("Invalid saved template metadata")
            return status, quality

    before = version()
    plan = json.loads(before[1])["printLayout"]
    refs = [plan["printFiles"][key] for key in ("xlsx", "pdf", "report")]
    refs += [page["referenceImage"] for page in plan["pages"]]
    contents = {}
    for ref in refs:
        path = (root / ref["relativePath"]).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("Artifact path escaped the data directory")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != ref["sha256"]:
            raise ValueError("Saved artifact SHA mismatch")
        contents[path] = payload
    workbook = contents[(root / plan["printFiles"]["xlsx"]["relativePath"]).resolve()]
    try:
        pdf, images, renderer = render_wsl_reference(workbook, runtime_id=runtime_id)
        if (len(images) != len(plan["pages"])
                or any(hashlib.sha256(image).hexdigest() != page["referenceImage"]["sha256"]
                       for image, page in zip(images, plan["pages"], strict=True))):
            raise ValueError("Fresh rendering differs from saved page images")
        report = inspect_wsl_vectors(pdf, renderer)
        observations = [observe_raster(page, vector, image)
                        for page, vector, image in zip(plan["pages"], report["vectors"]["pages"],
                                                       images, strict=True)]
        return {
            "templateVersionId": version_id, "status": before[0],
            "savedPrintXlsxSha256": plan["printFiles"]["xlsx"]["sha256"],
            "observedPdfSha256": hashlib.sha256(pdf).hexdigest(),
            "codeBundleSha256": report["codeBundleSha256"],
            "runtimeImage": report["runtimeImage"], "allSavedImagesMatch": True,
            "glyphs": report["glyphs"]["textInstructionGlyphs"],
            "segmentCount": report["vectors"]["segmentCount"],
            "fieldObservations": [{
                "page": observation["page"], "policy": observation["policy"],
                "fieldCount": observation["fieldCount"],
                "statusCounts": dict(Counter(field["status"] for field in observation["fields"])),
                "maxObservedErrorPx": observation["maxObservedErrorPx"],
                "rasterPolicy": observation["rasterObservation"]["policy"],
                "rasterSampleCount": observation["rasterObservation"]["sampleCount"],
                "rasterSegmentCounts": dict(Counter(
                    segment["status"] for segment in observation["rasterObservation"]["segments"])),
                "rasterUnverified": [
                    {"segmentIndex": segment["segmentIndex"], "reason": segment["reason"]}
                    for segment in observation["rasterObservation"]["segments"]
                    if segment["status"] != "SAMPLED"],
                "rasterImperfectContrast": [
                    {"segmentIndex": segment["segmentIndex"],
                     "paintSamples": segment["paintSamples"],
                     "inkCoverage": segment["inkCoverage"],
                     "contrastCoverage": segment["contrastCoverage"],
                     "gapInkSamples": segment["gapInkSamples"]}
                    for segment in observation["rasterObservation"]["segments"]
                    if segment["status"] == "SAMPLED" and segment["contrastCoverage"] < 1],
                "incompleteFields": [
                    {"sourceRange": field["sourceRange"], "status": field["status"],
                     "edgeStatus": [edge["status"] for edge in field["edges"]]}
                    for field in observation["fields"]
                    if field["status"] != "OBSERVED_WITHIN_DIAGNOSTIC_BUDGET"],
                "visibilityVerified": False, "productionQualified": False,
            } for observation in observations],
            "pages": [{"page": page["renderedPage"], "mediaBox": page["mediaBox"],
                       "cropBox": page["cropBox"], "rotation": page["rotation"],
                       "userUnit": page["userUnit"], "segments": len(page["strokes"]["segments"]),
                       "clippingUnverified": sum(line["clippingUnverified"]
                                                 for line in page["strokes"]["segments"]),
                       "curvedPaths": page["strokes"]["curvedStrokePaths"],
                       "dashedSegments": sum(bool(line["dash"])
                                             for line in page["strokes"]["segments"]),
                       "dashStyles": [{"dash": list(style[0]), "phase": style[1],
                                       "width": style[2], "cap": style[3], "count": count}
                                      for style, count in Counter(
                                          (tuple(line["dash"]), line["dashPhase"],
                                           line["width"], line["lineCap"])
                                          for line in page["strokes"]["segments"]
                                          if line["dash"]).items()]}
                      for page in report["vectors"]["pages"]],
            "productionQualified": False, "visibilityVerified": False,
        }
    finally:
        if version() != before or any(path.read_bytes() != content
                                      for path, content in contents.items()):
            raise ValueError("Saved template or artifacts changed during diagnostic")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version_id")
    parser.add_argument("runtime_id")
    parser.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args()
    print(json.dumps(inspect(args.version_id, args.runtime_id, args.data), ensure_ascii=True))
