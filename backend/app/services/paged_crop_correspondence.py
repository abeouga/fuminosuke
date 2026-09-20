"""Verify crop provenance before provider construction; not an ink certificate.

The accepted snapshot and owned whole-page artifacts are the independent inputs.
Do not regenerate crops with RegionCropper or trust saved PASS/quality flags.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Never

import cv2
import numpy as np
from numpy.typing import NDArray
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import ProcessingJob
from app.domain.template_config import TemplateConfig
from app.extraction.image_variants import create_variants
from app.image_processing.image_loader import ImageLoader
from app.repositories.artifact_repository import ArtifactRepository
from app.services.job_page_service import JobPageService
from app.services.print_layout_service import digest
from app.storage.local_storage import LocalStorageService, StoragePathError

if TYPE_CHECKING:
    from app.services.extraction_service import ExtractionOutput

POLICY = "paged-crop-correspondence-v1"
ERROR = "PAGED_CROP_CORRESPONDENCE_INVALID"
VARIANTS = (
    "aligned_color", "clean_reference", "handwriting_only", "enhanced_gray", "ink_difference",
)


def reject(reason: str, *, page_id: str | None = None, region_id: str | None = None) -> Never:
    raise AppError(
        status=409, code=ERROR, title="Page crop correspondence is invalid",
        detail="受付時のページ・項目と切り出し画像が一致しないため、OCR送信を中止しました。",
        metadata={"policy": POLICY, "reason": reason, "pageId": page_id, "regionId": region_id},
    )


def _same_pixels(actual: NDArray[np.uint8], expected: NDArray[np.uint8]) -> bool:
    return (
        isinstance(actual, np.ndarray) and actual.dtype == np.uint8
        and actual.shape == expected.shape and np.array_equal(actual, expected)
    )


def verify_pixels(
    extraction: ExtractionOutput,
    page: dict[str, Any],
    *,
    aligned: NDArray[np.uint8],
    reference: NDArray[np.uint8],
    handwriting: NDArray[np.uint8],
) -> dict[str, Any]:
    """Pure comparison against accepted geometry and verified page image bytes."""
    page_id = page["pageId"]
    if digest(extraction.page_snapshot) != digest(page):
        reject("PAGE_SNAPSHOT_CHANGED", page_id=page_id)
    config = TemplateConfig.model_validate(page["templateSnapshot"]["config"])
    if (
        extraction.template_version_id != page["templateVersionId"]
        or extraction.config.model_dump(mode="json", by_alias=True)
        != config.model_dump(mode="json", by_alias=True)
        or page["fieldMappings"]
        != [m.model_dump(mode="json", by_alias=True) for m in config.field_mappings]
    ):
        reject("PAGE_CONFIG_CHANGED", page_id=page_id)
    shape = (config.canonical_height, config.canonical_width, 3)
    if any(image.dtype != np.uint8 or image.shape != shape
           for image in (aligned, reference, handwriting)):
        reject("PAGE_IMAGE_SHAPE_CHANGED", page_id=page_id)
    if not _same_pixels(extraction.aligned_rgb, aligned):
        reject("ALIGNED_PAGE_CHANGED", page_id=page_id)
    if not _same_pixels(extraction.template_rgb, reference):
        reject("REFERENCE_PAGE_CHANGED", page_id=page_id)
    strict = bool(page.get("ocrEnabled", True)) and page.get("ocrProvider") == "openai"
    regions = config.mapped_ocr_regions() if strict else config.ocr_regions()
    region_ids = {region.region_id for region in regions}
    if not regions or len(region_ids) != len(regions) or set(extraction.crops) != region_ids:
        reject("CROP_REGION_SET_CHANGED", page_id=page_id)
    variants = create_variants(aligned, reference, handwriting)
    records = []
    for region in regions:
        crop = extraction.crops[region.region_id]
        x = region.x - region.padding
        y = region.y - region.padding
        width = region.width + 2 * region.padding
        height = region.height + 2 * region.padding
        if x < 0 or y < 0 or x + width > shape[1] or y + height > shape[0]:
            reject("CROP_OUT_OF_BOUNDS", page_id=page_id, region_id=region.region_id)
        if crop.config != region or crop.bbox != (x, y, width, height):
            reject("CROP_GEOMETRY_CHANGED", page_id=page_id, region_id=region.region_id)
        mask = None
        if region.vertices:
            mask = np.zeros((height, width), dtype=np.uint8)
            points = np.asarray([(p.x - x, p.y - y) for p in region.vertices], dtype=np.int32)
            cv2.fillPoly(mask, [points], (255,))
        hashes = {}
        for name in VARIANTS:
            expected = getattr(variants, name)[y:y + height, x:x + width]
            if mask is not None:
                expected = expected.copy()
                expected[mask == 0] = 0 if name == "ink_difference" else 255
            actual = getattr(crop, name)
            if not _same_pixels(actual, expected):
                reject(f"CROP_PIXELS_CHANGED:{name}", page_id=page_id, region_id=region.region_id)
            hashes[name] = hashlib.sha256(actual.tobytes(order="C")).hexdigest()
        records.append({"regionId": region.region_id, "bbox": list(crop.bbox),
                        "pixelSha256": hashes})
    return {
        "policy": POLICY, "check": "CROP_CORRESPONDENCE", "status": "VERIFIED",
        "pageId": page_id, "inputId": page["inputId"], "inputSha256": page["inputSha256"],
        "pageSnapshotSha256": digest(page),
        "referenceImageSha256": page["templateSnapshot"]["cleanTemplateSha256"],
        "pixelEncoding": "uint8-C-order", "regions": records,
        # Pixel correspondence proves neither registration accuracy nor ink ownership.
        "inkClearanceVerified": False, "productionQualified": False,
    }


def verify_before_provider(
    extraction: ExtractionOutput, *, session: Session, job: ProcessingJob,
    pages_service: JobPageService, settings: Settings, storage: LocalStorageService,
) -> dict[str, Any]:
    pages = pages_service.validate(session, job)
    snapshot = extraction.page_snapshot
    if not isinstance(snapshot, dict):
        reject("PAGE_SNAPSHOT_MISSING")
    matches = [page for page in pages if page["pageId"] == snapshot.get("pageId")]
    if len(matches) != 1 or digest(matches[0]) != digest(snapshot):
        reject("PAGE_SNAPSHOT_CHANGED")
    page = matches[0]
    page_id = page["pageId"]
    if extraction.job_id != job.id or extraction.attempt_number != job.attempt_number:
        reject("PAGE_ATTEMPT_CHANGED", page_id=page_id)
    loader = ImageLoader(settings.storage, settings.image_processing)

    def load(path: str, sha: str, root: str) -> NDArray[np.uint8]:
        try:
            resolved = storage.resolve(path)
            resolved.relative_to(storage.resolve(root))
            content = resolved.read_bytes()
            if hashlib.sha256(content).hexdigest() != sha:
                reject("PAGE_ARTIFACT_CHANGED", page_id=page_id)
            return loader.load(content, "image/png").rgb
        except (OSError, ValueError, StoragePathError) as exc:
            raise AppError(
                status=409, code=ERROR, title="Page artifact unavailable",
                detail="ページの保存画像を確認できないため、OCR送信を中止しました。",
                metadata={
                    "policy": POLICY, "reason": "PAGE_ARTIFACT_UNAVAILABLE", "pageId": page_id,
                },
            ) from exc

    identities = {}

    def owned(name: str) -> NDArray[np.uint8]:
        artifact = ArtifactRepository(session).get_by_name(
            job_id=job.id, attempt_number=job.attempt_number,
            artifact_name=f"page.{page_id}.{name}",
        )
        if artifact is None or artifact.media_type != "image/png":
            reject("PAGE_ARTIFACT_MISSING", page_id=page_id)
        identities[name] = {"artifactId": artifact.id, "sha256": artifact.sha256}
        return load(artifact.relative_path, artifact.sha256,
                    f"artifacts/{job.id}/attempts/{job.attempt_number}")

    reference_snapshot = page["templateSnapshot"]
    report = verify_pixels(
        extraction, page, aligned=owned("alignedSource"), handwriting=owned("handwritingOnly"),
        reference=load(reference_snapshot["cleanTemplatePath"],
                       reference_snapshot["cleanTemplateSha256"],
                       f"uploads/{job.id}/template_snapshot"),
    )
    report["artifacts"] = identities
    report["attemptNumber"] = job.attempt_number
    return report
