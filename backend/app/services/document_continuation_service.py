"""Whether an uploaded image is a later photo of a document already read (written on since).

No external API is called and the uploaded image is not stored. The new image is aligned to
the template the earlier reading used, and the handwriting of the earlier reading is compared
with the same places on the new image: a later photo of the same sheet still carries every
stroke of the earlier one (more may have been added), while another sheet filled on the same
form has different writing there. The comparison is the correlation of the ink darkness (paper
level removed, slightly blurred) over the earlier reading's handwriting inside the form's
reading regions, outside the printed form, so a darker or brighter photo of the same sheet
still matches. Marks outside the regions (binder holes, the sheet's edge, shadows, the desk)
are left out: they differ between photos of one sheet and look alike between photos taken
the same way.

What the comparison needs of an earlier reading is kept as its 「照合用の指紋」 (a small
artifact of its attempt, made when it is confirmed or when it is first compared), and decoded
templates and prints are remembered across requests, so a comparison does not read every
earlier reading's full images again.
"""
from __future__ import annotations

import io
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, cast

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
from app.image_processing.artifacts import ArtifactWriter
from app.image_processing.document_boundary import DocumentNormalizer
from app.image_processing.image_loader import ImageLoader
from app.image_processing.photo_registration import register_document
from app.image_processing.photometric import flatten_paper
from app.image_processing.registration import TemplateAligner
from app.repositories.job_repository import JobRepository
from app.storage.local_storage import LocalStorageService

logger = logging.getLogger(__name__)

# Unconfirmed readings in the queue, or a template's latest confirmed documents.
MAX_JOBS = 24
# Measured on stored readings (within the reading regions): photos of one
# sheet taken again with writing added since 0.95-0.97 (whole-sheet comparison: 0.46-0.57),
# other sheets filled on the same form -0.26-0.41.
CONTINUATION_SIMILARITY = 0.75
# Fewer handwriting pixels than this in the earlier reading leave nothing to compare.
MIN_COMPARED_PIXELS = 300
# Photos are shrunk to this long edge before they are aligned for the comparison: telling one
# sheet from another needs far less detail than reading it, and aligning a full phone photo
# (a bent page in particular) is most of the time the comparison takes.
MATCH_LONG_EDGE = 1800
# The stored 「照合用の指紋」 of a reading (an artifact of its attempt); see _Print.
PRINT_ARTIFACT = "continuationPrint"
PRINT_VERSION = 1


def _shrink(rgb: NDArray[np.uint8]) -> NDArray[np.uint8]:
    height, width = rgb.shape[:2]
    if max(height, width) <= MATCH_LONG_EDGE:
        return rgb
    scale = MATCH_LONG_EDGE / max(height, width)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return np.asarray(cv2.resize(rgb, size, interpolation=cv2.INTER_AREA), dtype=np.uint8)


@dataclass(slots=True)
class _TemplateView:
    rgb: NDArray[np.uint8]
    gray: NDArray[np.float32]
    # The printed form (slightly widened): never compared, it is the same on every photo.
    printed: NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class _Print:
    """What the comparison needs of an earlier reading (「照合用の指紋」).

    The places compared (its handwriting inside the reading regions, slightly widened, outside
    the printed form) and its ink darkness there. With these, the earlier reading's aligned image
    and handwriting mask are not read again: the result is the same as comparing the images.
    """

    shape: tuple[int, int]
    packed: NDArray[np.uint8]
    values: NDArray[np.float16]

    def region(self) -> NDArray[np.bool_]:
        height, width = self.shape
        bits = np.unpackbits(self.packed, count=height * width)
        return bits.reshape(self.shape).astype(bool)


@dataclass(slots=True)
class _Earlier:
    job_id: str
    attempt_number: int
    template_path: str
    template_media_type: str | None
    template_key: str
    regions: tuple[tuple[int, int, int, int], ...]
    print_path: str | None
    print_key: str | None
    aligned_path: str | None
    aligned_media_type: str | None
    mask_path: str | None
    mask_media_type: str | None
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


def _template_view(rgb: NDArray[np.uint8]) -> _TemplateView:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    printed = cv2.dilate((gray < 205).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    return _TemplateView(rgb=rgb, gray=gray.astype(np.float32), printed=printed)


def _make_print(
    aligned_rgb: NDArray[np.uint8],
    mask: NDArray[np.bool_],
    template: _TemplateView,
    regions: tuple[tuple[int, int, int, int], ...],
) -> _Print:
    writing = mask & _in_regions(mask.shape, regions)
    widened = cv2.dilate(writing.astype(np.uint8), np.ones((11, 11), np.uint8)) > 0
    region = widened & ~template.printed
    values = _darkness(aligned_rgb, template.gray)[region].astype(np.float16)
    height, width = region.shape
    return _Print(shape=(height, width), packed=np.packbits(region.ravel()), values=values)


def _encode_print(value: _Print) -> bytes:
    buffer = io.BytesIO()
    np.savez_compressed(
        buffer, version=np.array([PRINT_VERSION]), shape=np.array(value.shape),
        packed=value.packed, values=value.values,
    )
    return buffer.getvalue()


def _decode_print(content: bytes) -> _Print:
    with np.load(io.BytesIO(content), allow_pickle=False) as stored:
        if int(stored["version"][0]) != PRINT_VERSION:
            raise ValueError("continuation print version")
        height, width = (int(item) for item in stored["shape"])
        return _Print(
            shape=(height, width), packed=stored["packed"].astype(np.uint8),
            values=stored["values"].astype(np.float16),
        )


def _similarity(earlier: _Print, later_darkness: NDArray[np.float32]) -> float | None:
    if earlier.values.size < MIN_COMPARED_PIXELS or later_darkness.shape != earlier.shape:
        return None
    first = earlier.values.astype(np.float32)
    later = later_darkness[earlier.region()]
    if float(first.std()) < 1e-6 or float(later.std()) < 1e-6:
        return 0.0
    return float(np.corrcoef(first, later)[0, 1])


class _Cache:
    """Decoded templates and prints, kept across requests.

    Keys are content hashes taken from the reading's own records, so an entry is only reached
    through a reading the caller can see, and the same content always means the same entry.
    """

    def __init__(self, templates: int, prints: int) -> None:
        self._lock = threading.Lock()
        self._limits = {"template": templates, "print": prints}
        self._items: dict[str, OrderedDict[str, Any]] = {
            "template": OrderedDict(), "print": OrderedDict(),
        }

    def get(self, kind: str, key: str) -> Any:
        with self._lock:
            items = self._items[kind]
            value = items.get(key)
            if value is not None:
                items.move_to_end(key)
            return value

    def put(self, kind: str, key: str, value: Any) -> None:
        with self._lock:
            items = self._items[kind]
            items[key] = value
            items.move_to_end(key)
            while len(items) > self._limits[kind]:
                items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            for items in self._items.values():
                items.clear()


# A template is about 6 MB decoded, a print well under 1 MB.
_CACHE = _Cache(templates=8, prints=256)


class DocumentContinuationService:
    def __init__(
        self, database: Database, storage: LocalStorageService, settings: Settings,
    ) -> None:
        self._database = database
        self._storage = storage
        self._loader = ImageLoader(settings.storage, settings.image_processing)
        self._aligner = TemplateAligner(settings.registration)
        self._isolator = HandwritingIsolator(threshold=settings.segmentation.threshold)

    def _image(self, path: str, media_type: str | None) -> NDArray[np.uint8]:
        return self._loader.load(self._storage.read_bytes(path), media_type).rgb

    def _template(self, earlier: _Earlier) -> _TemplateView:
        view = _CACHE.get("template", earlier.template_key)
        if view is None:
            view = _template_view(self._image(earlier.template_path, earlier.template_media_type))
            _CACHE.put("template", earlier.template_key, view)
        return cast(_TemplateView, view)

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
                        JobArtifact.artifact_name.in_(
                            ("alignedSource", "handwritingMask", PRINT_ARTIFACT),
                        ),
                    )
                )
            }
            inputs = JobRepository(session).list_inputs(job_id)
            source = inputs[0] if len(inputs) == 1 else None
            aligned = artifacts.get("alignedSource")
            mask = artifacts.get("handwritingMask")
            both = aligned is not None and mask is not None
            stored_print = artifacts.get(PRINT_ARTIFACT)
            return _Earlier(
                job_id=job_id,
                attempt_number=job.attempt_number,
                template_path=path,
                template_media_type=template.get("cleanTemplateMediaType"),
                template_key=str(template.get("cleanTemplateSha256") or path),
                regions=tuple(
                    (r.x, r.y, r.width, r.height) for r in config.mapped_ocr_regions()
                ),
                print_path=stored_print.relative_path if stored_print else None,
                print_key=stored_print.sha256 if stored_print else None,
                aligned_path=aligned.relative_path if both and aligned else None,
                aligned_media_type=aligned.media_type if aligned else None,
                mask_path=mask.relative_path if both and mask else None,
                mask_media_type=mask.media_type if mask else None,
                input_path=source.relative_path if source else None,
                input_media_type=source.media_type if source else None,
            )

    def _print(self, earlier: _Earlier, template: _TemplateView) -> _Print:
        """The earlier reading's print: remembered, stored, or made now (and stored)."""
        if earlier.print_key is not None and earlier.print_path is not None:
            remembered = _CACHE.get("print", earlier.print_key)
            if remembered is not None:
                return cast(_Print, remembered)
            try:
                value: _Print | None = _decode_print(self._storage.read_bytes(earlier.print_path))
            except (OSError, ValueError, KeyError):
                value = None
            if value is not None:
                _CACHE.put("print", earlier.print_key, value)
                return value
        if earlier.aligned_path is not None and earlier.mask_path is not None:
            aligned = self._image(earlier.aligned_path, earlier.aligned_media_type)
            mask = self._image(earlier.mask_path, earlier.mask_media_type)[..., 0] > 0
            made = _make_print(aligned, mask, template, earlier.regions)
            self._store(earlier, made)
            return made
        # Not aligned yet (still waiting or reading): align its own image; nothing is stored.
        if earlier.input_path is None:
            raise LookupError
        own = self._align(
            _shrink(self._image(earlier.input_path, earlier.input_media_type)), earlier, template,
        )
        own_mask = self._isolator.isolate(own, template.rgb).mask > 0
        return _make_print(own, own_mask, template, earlier.regions)

    def _store(self, earlier: _Earlier, value: _Print) -> None:
        content = _encode_print(value)
        try:
            artifact = ArtifactWriter(self._database, self._storage).bytes(
                job_id=earlier.job_id, attempt_number=earlier.attempt_number,
                name=PRINT_ARTIFACT, file_name="continuation-print.npz",
                media_type="application/octet-stream", content=content, replace=True,
            )
        except Exception:
            # Storing only saves time later; the comparison goes on without it.
            logger.warning(
                "continuation print not stored", extra={"jobId": earlier.job_id}, exc_info=True,
            )
            return
        _CACHE.put("print", artifact.sha256, value)

    def prepare(self, job_id: str) -> None:
        """Make and store the print of a just-confirmed reading; failures are only logged."""
        try:
            earlier = self._earlier(job_id)
            if earlier is None or earlier.print_key is not None or earlier.aligned_path is None:
                return
            self._print(earlier, self._template(earlier))
        except Exception:
            logger.warning(
                "continuation print not prepared", extra={"jobId": job_id}, exc_info=True,
            )

    def _align(
        self, source: NDArray[np.uint8], earlier: _Earlier, template_view: _TemplateView,
    ) -> NDArray[np.uint8]:
        """The photo on the template, for the comparison only.

        The whole-page alignment is enough to tell one sheet from another, and a bent page that
        reading must align field by field (3-9 s) takes it in under a second (measured
        on stored photos: same sheet 0.93-1.0, other sheets unchanged). A photo
        it cannot align takes the reading's full registration.
        """
        template = template_view.rgb
        height, width = template.shape[:2]
        for allow_swap in (False, True):
            try:
                page = DocumentNormalizer(
                    canonical_width=width, canonical_height=height,
                    allow_orientation_swap=allow_swap,
                ).normalize(source).image_rgb
            except AppError:
                continue
            if page.shape[:2] != (height, width):
                page = np.asarray(cv2.rotate(page, cv2.ROTATE_90_CLOCKWISE), dtype=np.uint8)
            flattened = flatten_paper(page, template)
            for image in (flattened.image_rgb, page) if flattened.applied else (page,):
                try:
                    return self._aligner.align_global(image, template).aligned_rgb
                except AppError:
                    continue
        return register_document(
            source, template, aligner=self._aligner, validation_regions=earlier.regions,
        ).registered.aligned_rgb

    def compare(
        self, content: bytes, media_type: str | None, job_ids: list[str],
    ) -> dict[str, Any]:
        source = _shrink(self._loader.load(content, media_type).rgb)
        # The new photo's ink darkness, once per template.
        later_by_template: dict[str, NDArray[np.float32] | None] = {}
        items: list[dict[str, Any]] = []
        for job_id in list(dict.fromkeys(job_ids))[:MAX_JOBS]:
            similarity: float | None = None
            try:
                earlier = self._earlier(job_id)
                if earlier is not None:
                    template = self._template(earlier)
                    earlier_print = self._print(earlier, template)
                    key = earlier.template_key
                    if key not in later_by_template:
                        try:
                            later_by_template[key] = _darkness(
                                self._align(source, earlier, template), template.gray,
                            )
                        except AppError:
                            later_by_template[key] = None
                    later = later_by_template[key]
                    if later is not None:
                        similarity = _similarity(earlier_print, later)
            except (AppError, LookupError, OSError, ValueError):
                similarity = None
            items.append({
                "jobId": job_id,
                "similarity": None if similarity is None else round(similarity, 3),
                "continuation": similarity is not None and similarity >= CONTINUATION_SIMILARITY,
            })
        return {"items": items}
