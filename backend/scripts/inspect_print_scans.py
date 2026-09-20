"""Local scan baseline, not production admission or a qualification certificate.

Explicit --scan PAGE=PATH bindings must be visually checked by the operator.
Reads SQLite in query-only mode. Never loads app settings, secrets, or providers.
Diagnostic images contain user data; output stays in a new private temp folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

import cv2
from PIL import Image

from app.core.config import ImageProcessingSettings, RegistrationSettings, StorageSettings
from app.core.errors import AppError
from app.domain.job_pages import template_page_id
from app.domain.template_config import parse_template_config
from app.extraction.handwriting_isolator import HandwritingIsolator
from app.extraction.image_variants import create_variants
from app.extraction.region_cropper import RegionCropper
from app.image_processing.document_boundary import DocumentNormalizer
from app.image_processing.image_loader import ImageLoader
from app.image_processing.registration import RegistrationFailure, TemplateAligner
from app.services.job_page_service import page_config
from app.services.reference_geometry import measure_page
from app.storage.local_storage import LocalStorageService


def inspect(version_id: str, data: Path, scans: list[tuple[int, Path]]) -> dict[str, Any]:
    storage = LocalStorageService(data.resolve(strict=True))
    uri = (storage.root / "app.db").as_uri() + "?mode=ro"

    def snapshot() -> tuple[Any, ...]:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                "SELECT status,quality_json,layout_json,config_relative_path "
                "FROM template_versions WHERE id=?", (version_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown template version")
            return tuple(row)

    before = snapshot()
    plan = json.loads(before[1])["printLayout"]
    pages = {page["page"]: page for page in plan["pages"]}
    if (not scans or len(pages) != len(plan["pages"])
            or any(number not in pages for number, _ in scans)):
        raise ValueError("Invalid explicit page binding")
    originals: dict[Path, str] = {}

    def read(path: Path, expected: str | None = None) -> bytes:
        path = path.resolve(strict=True)
        content = path.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        if expected is not None and sha != expected:
            raise ValueError("Saved artifact SHA mismatch")
        if path in originals and originals[path] != sha:
            raise ValueError("Input changed between diagnostic reads")
        originals[path] = sha
        return content

    config = parse_template_config(read(storage.resolve(before[3])).decode("utf-8"))
    settings = RegistrationSettings(
        min_feature_matches=30, min_inliers=20, min_inlier_ratio=0.4,
        max_reprojection_error_px=5.0, min_ecc_score=0.75,
        min_edge_overlap=0.7, min_template_confidence=0.75,
    )
    loader = ImageLoader(
        StorageSettings(data_dir=storage.root, max_upload_bytes=20_971_520),
        ImageProcessingSettings(max_image_pixels=30_000_000, max_image_edge=8000,
                                opencv_num_threads=1),
    )
    cv2.setNumThreads(1)
    destination = Path(tempfile.mkdtemp(prefix="llmocr-scan-baseline-"))
    results: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "policy": "print-scan-diagnostic-v1", "templateVersionId": version_id,
        "templateState": before[0], "outputDirectory": str(destination),
        "registrationSettings": settings.model_dump(mode="json"),
        "segmentationThreshold": 0.55, "externalApiCalls": 0,
        "productionQualified": False, "results": results,
    }
    try:
        for number, path in scans:
            page = pages[number]
            content = read(path)
            loaded = loader.load(content, "image/png")
            sha = loaded.metadata.sha256
            reference = page["referenceImage"]
            template = loader.load(read(storage.resolve(reference["relativePath"]),
                                        reference["sha256"]), "image/png").rgb
            cfg = page_config(config, page)
            regions = cfg.mapped_ocr_regions()
            folder = destination / f"page-{number}-{sha[:12]}"
            folder.mkdir()
            item: dict[str, Any] = {
                "page": number, "templatePageId": template_page_id(version_id, page),
                "inputPath": str(path.resolve()), "inputSha256": sha,
                "referenceSha256": reference["sha256"], "range": page["range"],
                "sourceSize": [loaded.metadata.width, loaded.metadata.height],
                "outputDirectory": str(folder), "fieldCount": len(regions),
            }
            results.append(item)
            try:
                normal = DocumentNormalizer(canonical_width=cfg.canonical_width,
                                            canonical_height=cfg.canonical_height).normalize(loaded.rgb)
                item["normalization"] = normal.quality.to_dict()
                Image.fromarray(normal.image_rgb).save(folder / "normalized.png")
                aligned = TemplateAligner(settings).align(
                    normal.image_rgb, template,
                    validation_regions=[(r.x, r.y, r.width, r.height) for r in regions],
                )
                item["registration"] = aligned.quality.to_dict()
                item["diagnostics"] = aligned.diagnostics
                item["homography"] = aligned.homography.tolist()
                item["sourceToCanonical"] = (aligned.homography @ normal.transform).tolist()
                Image.fromarray(aligned.aligned_rgb).save(folder / "aligned.png")
                item["rasterGeometryDiagnostic"] = measure_page(
                    page, (folder / "aligned.png").read_bytes())
                Image.fromarray(aligned.debug_rgb).save(folder / "registration.png")
                handwriting = HandwritingIsolator(threshold=0.55).isolate(
                    aligned.aligned_rgb, template)
                Image.fromarray(handwriting.mask).save(folder / "heuristic-mask.png")
                variants = create_variants(aligned.aligned_rgb, template,
                                           handwriting.handwriting_only_rgb)
                crops = RegionCropper(cfg, regions).crop(variants)
                overlay = aligned.aligned_rgb.copy()
                fields = []
                for index, (region_id, crop) in enumerate(crops.items()):
                    x, y, width, height = crop.bbox
                    fields.append({"regionId": region_id, "bbox": crop.bbox,
                                   "cropFile": f"crop-{index}.png"})
                    Image.fromarray(crop.aligned_color).save(folder / f"crop-{index}.png")
                    cv2.rectangle(overlay, (x, y), (x + width - 1, y + height - 1),
                                  (255, 0, 0), 1)
                    cv2.putText(overlay, str(index), (x, max(10, y)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)
                Image.fromarray(overlay).save(folder / "crop-overlay.png")
                item.update(status="ALIGNED_DIAGNOSTIC_ONLY", fields=fields,
                            heuristicInkCoverage=handwriting.coverage_ratio,
                            inkClearanceVerified=False)
            except AppError as exc:
                item.update(status="REJECTED", code=exc.code, diagnostics=exc.metadata)
                if isinstance(exc, RegistrationFailure):
                    Image.fromarray(exc.debug_rgb).save(folder / "rejected.png")
            item["imageSha256"] = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(folder.glob("*.png"))
            }
            print(json.dumps({k: item[k] for k in ("page", "inputSha256", "status")}),
                  flush=True)
    finally:
        unchanged = snapshot() == before and all(
            hashlib.sha256(path.read_bytes()).hexdigest() == sha
            for path, sha in originals.items())
        report["originalUnchanged"] = unchanged
        (destination / "report.json").write_text(
            json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
        if not unchanged:
            raise ValueError("Original template or input changed during diagnostic")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version_id")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--scan", action="append", required=True, help="PAGE=PATH")
    args = parser.parse_args()
    bindings = [(int(raw.split("=", 1)[0]), Path(raw.split("=", 1)[1])) for raw in args.scan]
    result = inspect(args.version_id, args.data, bindings)
    print(json.dumps({"report": str(Path(result["outputDirectory"]) / "report.json")}))
