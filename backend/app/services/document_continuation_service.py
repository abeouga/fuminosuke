"""Whether an uploaded image is a later photo of a document already read (written on since).

Nothing is stored and no external API is called. The new image is aligned to the template
the earlier reading used, and the handwriting of the earlier reading is compared with the
same places on the new image: a later photo of the same sheet still carries every stroke
of the earlier one (more may have been added), while another sheet filled on the same form
has different writing there. The comparison is the correlation of the ink darkness (paper
level removed, slightly blurred) over the earlier reading's handwriting inside the form's
reading regions, outside the printed form, so a darker or brighter photo of the same sheet
still matches. Marks outside the regions (binder holes, the sheet's edge, shadows, the desk)
are left out: they differ between photos of one sheet and look alike between photos taken
the same way.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray
from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob
from app.db.session import Database
from app.domain.template_config import FieldMappingConfig, TemplateConfig
from app.extraction.handwriting_isolator import HandwritingIsolator
from app.image_processing.image_loader import ImageLoader
from app.image_processing.photo_registration import register_document
from app.image_processing.registration import TemplateAligner
from app.repositories.job_repository import JobRepository
from app.storage.local_storage import LocalStorageService

# Unconfirmed readings in the queue, or a template's latest confirmed documents.
MAX_JOBS = 24
# Measured on the stored readings (2026-10-25, within the reading regions): photos of one
# sheet taken again with writing added since 0.95-0.97 (whole-sheet comparison: 0.46-0.57),
# other sheets filled on the same form -0.26-0.41.
CONTINUATION_SIMILARITY = 0.75
# Fewer handwriting pixels than this in the earlier reading leave nothing to compare.
MIN_COMPARED_PIXELS = 300


@dataclass(slots=True)
class _Earlier:
    job_id: str
    template_rgb: NDArray[np.uint8]
    template_key: str
    regions: tuple[tuple[int, int, int, int], ...]
    aligned_rgb: NDArray[np.uint8] | None
    mask: NDArray[np.bool_] | None
    input_path: str | None
    input_media_type: str | None


def _darkness(
    aligned_rgb: NDArray[np.uint8], template_gray: NDArray[np.float32],
) -> NDArray[np.float32]:
    difference = template_gray - cv2.cvtColor(aligned_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    darkness = np.clip(difference - float(np.median(difference)), 0, None).astype(np.float32)
    return np.asarray(cv2.GaussianBlur(darkness, (0, 0), 3), dtype=np.float32)


def _in_regions(
    shape: tuple[int, ...], regions: tuple[tuple[int, int, int, int], ...],
) -> NDArray[np.bool_]:
    inside = np.zeros(shape[:2], dtype=bool)
    for x, y, width, height in regions:
        inside[max(y, 0):max(y + height, 0), max(x, 0):max(x + width, 0)] = True
    return inside


def _similarity(
    earlier_rgb: NDArray[np.uint8],
    earlier_mask: NDArray[np.bool_],
    later_rgb: NDArray[np.uint8],
    template_rgb: NDArray[np.uint8],
    regions: tuple[tuple[int, int, int, int], ...],
) -> float | None:
    template_gray = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY)
    printed = cv2.dilate((template_gray < 205).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    writing = earlier_mask & _in_regions(earlier_mask.shape, regions)
    region = (cv2.dilate(writing.astype(np.uint8), np.ones((11, 11), np.uint8)) > 0) & ~printed
    if int(region.sum()) < MIN_COMPARED_PIXELS:
        return None
    gray = template_gray.astype(np.float32)
    earlier = _darkness(earlier_rgb, gray)[region]
    later = _darkness(later_rgb, gray)[region]
    if float(earlier.std()) < 1e-6 or float(later.std()) < 1e-6:
        return 0.0
    return float(np.corrcoef(earlier, later)[0, 1])


class DocumentContinuationService:
    def __init__(
        self, database: Database, storage: LocalStorageService, settings: Settings,
    ) -> None:
        self._database = database
        self._storage = storage
        self._loader = ImageLoader(settings.storage, settings.image_processing)
        self._aligner = TemplateAligner(settings.registration)
        self._isolator = HandwritingIsolator(threshold=settings.segmentation.threshold)
        self._templates: dict[str, NDArray[np.uint8]] = {}

    def _image(self, path: str, media_type: str | None) -> NDArray[np.uint8]:
        return self._loader.load(self._storage.read_bytes(path), media_type).rgb

    def _template(self, path: str, media_type: str | None) -> NDArray[np.uint8]:
        # Documents of one template share the image; it is read once per comparison.
        if path not in self._templates:
            self._templates[path] = self._image(path, media_type)
        return self._templates[path]

    def _earlier(self, job_id: str) -> _Earlier | None:
        with self._database.session() as session:
            job = session.get(ProcessingJob, job_id)
            if job is None:
                return None
            snapshot = dict(job.config_snapshot_json or {})
            template = snapshot.get("templateSnapshot")
            if "jobPages" in snapshot or not isinstance(template, dict):
                return None
            path = template.get("cleanTemplatePath")
            raw_config = template.get("config")
            if not isinstance(path, str) or not isinstance(raw_config, dict):
                return None
            config = TemplateConfig.model_validate(raw_config)
            raw_mappings = snapshot.get("fieldMappings")
            if isinstance(raw_mappings, list):
                config = config.model_copy(update={"field_mappings": [
                    FieldMappingConfig.model_validate(item) for item in raw_mappings
                ]}, deep=True)
            artifacts = {
                artifact.artifact_name: artifact
                for artifact in session.scalars(
                    select(JobArtifact)
                    .where(
                        JobArtifact.job_id == job_id,
                        JobArtifact.attempt_number == job.attempt_number,
                        JobArtifact.artifact_name.in_(("alignedSource", "handwritingMask")),
                    )
                )
            }
            inputs = JobRepository(session).list_inputs(job_id)
            source = inputs[0] if len(inputs) == 1 else None
            aligned = artifacts.get("alignedSource")
            mask = artifacts.get("handwritingMask")
            earlier = _Earlier(
                job_id=job_id,
                template_rgb=self._template(path, template.get("cleanTemplateMediaType")),
                template_key=str(template.get("cleanTemplateSha256") or path),
                regions=tuple(
                    (r.x, r.y, r.width, r.height) for r in config.mapped_ocr_regions()
                ),
                aligned_rgb=None,
                mask=None,
                input_path=source.relative_path if source else None,
                input_media_type=source.media_type if source else None,
            )
            if aligned is not None and mask is not None:
                earlier.aligned_rgb = self._image(aligned.relative_path, aligned.media_type)
                stored = self._loader.load(
                    self._storage.read_bytes(mask.relative_path), mask.media_type,
                ).rgb
                earlier.mask = stored[..., 0] > 0
        return earlier

    def _align(
        self, source: NDArray[np.uint8], earlier: _Earlier,
    ) -> tuple[NDArray[np.uint8], NDArray[np.bool_]]:
        document = register_document(
            source, earlier.template_rgb, aligner=self._aligner,
            validation_regions=earlier.regions,
        )
        aligned = document.registered.aligned_rgb
        return aligned, self._isolator.isolate(aligned, earlier.template_rgb).mask > 0

    def compare(
        self, content: bytes, media_type: str | None, job_ids: list[str],
    ) -> dict[str, Any]:
        source = self._loader.load(content, media_type).rgb
        aligned_by_template: dict[str, NDArray[np.uint8] | None] = {}
        items: list[dict[str, Any]] = []
        for job_id in list(dict.fromkeys(job_ids))[:MAX_JOBS]:
            similarity: float | None = None
            try:
                earlier = self._earlier(job_id)
                if earlier is not None:
                    if earlier.aligned_rgb is None or earlier.mask is None:
                        # Not aligned yet (still waiting or reading): align its own image.
                        if earlier.input_path is None:
                            raise LookupError
                        earlier.aligned_rgb, earlier.mask = self._align(
                            self._image(earlier.input_path, earlier.input_media_type), earlier,
                        )
                    key = earlier.template_key
                    if key not in aligned_by_template:
                        try:
                            aligned_by_template[key] = self._align(source, earlier)[0]
                        except AppError:
                            aligned_by_template[key] = None
                    later = aligned_by_template[key]
                    if later is not None:
                        similarity = _similarity(
                            earlier.aligned_rgb, earlier.mask, later, earlier.template_rgb,
                            earlier.regions,
                        )
            except (AppError, LookupError, OSError, ValueError):
                similarity = None
            items.append({
                "jobId": job_id,
                "similarity": None if similarity is None else round(similarity, 3),
                "continuation": similarity is not None and similarity >= CONTINUATION_SIMILARITY,
            })
        return {"items": items}
