"""Compare one owned PDF/raster set in the fixed vector sandbox, without DB writes."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tempfile
from collections import Counter
from pathlib import Path

from PIL import Image

from app.services.reference_stroke_differential import observe_page
from app.services.renderer_glyph_bridge import canonical_bytes
from app.services.renderer_process import renderer_mutex
from app.services.renderer_stroke_probe_pixels import unpack_delta
from app.services.renderer_vector_bridge import inspect_wsl_vectors


def inspect(pdf_path: Path, renderer_path: Path, image_paths: list[Path]) -> Path:
    paths = [p.resolve(strict=True) for p in [pdf_path, renderer_path, *image_paths]]
    if len(set(paths)) != len(paths) or len(paths) > 52:
        raise ValueError("Duplicate or excessive input paths")
    if sum(path.stat().st_size for path in paths) > 64 * 1024 * 1024:
        raise ValueError("Diagnostic input budget exceeded")
    contents = [p.read_bytes() for p in paths]
    pdf, raw_renderer, *images = contents
    renderer = json.loads(raw_renderer)
    if (renderer["pdfSha256"] != hashlib.sha256(pdf).hexdigest()
            or renderer["pageCount"] != len(images)
            or renderer["imageSha256"] != [hashlib.sha256(i).hexdigest() for i in images]):
        raise ValueError("Diagnostic source identity mismatch")
    directory = Path(tempfile.mkdtemp(prefix="llmocr-stroke-differential-"))
    print(json.dumps({"stage": "started", "directory": str(directory)}), flush=True)
    try:
        with renderer_mutex():
            report = inspect_wsl_vectors(pdf, renderer)
        (directory / "vectors.json").write_bytes(canonical_bytes(report))
        observations = []
        probes = report["strokeProbes"]["probes"]
        for number, (vector, image) in enumerate(zip(report["vectors"]["pages"], images,
                                                     strict=True), 1):
            width, height = renderer["imageSizes"][number - 1]
            page = {"width": width, "height": height,
                    "referenceImage": {"sha256": renderer["imageSha256"][number - 1]}}
            measured = observe_page(page, vector, image, probes)
            observations.append({"renderedPage": number, "segments": measured,
                                 "counts": dict(Counter(item["status"] for item in measured))})
            with Image.open(io.BytesIO(image)) as original:
                rgb = original.convert("RGB").tobytes()
            for probe in probes:
                if probe["renderedPage"] != number or probe["status"] != "MEASURED":
                    continue
                changed = unpack_delta(rgb, probe["delta"])
                with Image.frombytes("RGB", (width, height), changed) as raster:
                    raster.save(directory / f"page-{number}-operation-{probe['operation']}.png")
        summary = {"pdfSha256": renderer["pdfSha256"],
                   "codeBundleSha256": report["codeBundleSha256"], "pages": observations,
                   "sourcePaths": [str(path) for path in paths],
                   "visibilityVerified": False, "productionQualified": False}
        (directory / "report.json").write_bytes(canonical_bytes(summary))
        print(json.dumps({"stage": "completed", "directory": str(directory),
                          "pages": [{"page": item["renderedPage"], "counts": item["counts"]}
                                    for item in observations]}), flush=True)
        return directory
    finally:
        if any(path.read_bytes() != content for path, content in zip(paths, contents, strict=True)):
            raise ValueError("Diagnostic inputs changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--renderer", type=Path, required=True)
    parser.add_argument("--images", type=Path, nargs="+", required=True)
    args = parser.parse_args()
    inspect(args.pdf, args.renderer, args.images)
