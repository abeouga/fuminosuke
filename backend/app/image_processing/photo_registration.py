"""Normalize and register a document image, tolerant of phone photos.

The existing path (boundary normalization in the template orientation, then the
unchanged TemplateAligner acceptance checks) runs first. Phone photos additionally get
paper-background flattening when their paper is darker or less even than the template,
and a page photographed sideways is retried in both quarter-turn orientations.
Every candidate must pass the same registration checks; nothing is relaxed.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from app.core.errors import AppError
from app.image_processing.document_boundary import DocumentNormalizer, NormalizedDocument
from app.image_processing.photometric import flatten_paper, restore_ink
from app.image_processing.registration import (
    RegistrationFailure,
    RegistrationResult,
    TemplateAligner,
)


@dataclass(slots=True)
class DocumentRegistration:
    normalized: NormalizedDocument
    # The image passed to the aligner (rotated / flattened normalized page).
    registration_input: NDArray[np.uint8]
    registered: RegistrationResult
    # source image pixel -> template pixel
    source_to_template: NDArray[np.float64]
    info: dict[str, Any] = field(default_factory=dict)


_ROTATIONS = {
    90: cv2.ROTATE_90_CLOCKWISE,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def _rotation_matrix(degrees: int, width: int, height: int) -> NDArray[np.float64]:
    """Pixel map of cv2.rotate for an image of the given (pre-rotation) size."""
    if degrees == 90:
        return np.array([[0, -1, height - 1], [1, 0, 0], [0, 0, 1]], dtype=np.float64)
    if degrees == 270:
        return np.array([[0, 1, 0], [-1, 0, width - 1], [0, 0, 1]], dtype=np.float64)
    return np.eye(3, dtype=np.float64)


def register_document(
    source_rgb: NDArray[np.uint8],
    template_rgb: NDArray[np.uint8],
    *,
    aligner: TemplateAligner,
    validation_regions: Sequence[tuple[int, int, int, int]],
) -> DocumentRegistration:
    height, width = template_rgb.shape[:2]
    first_failure: AppError | None = None
    first_page: NDArray[np.uint8] | None = None
    attempts: list[dict[str, Any]] = []
    for allow_swap in (False, True):
        normalizer = DocumentNormalizer(
            canonical_width=width, canonical_height=height,
            allow_orientation_swap=allow_swap,
        )
        try:
            normalized = normalizer.normalize(source_rgb)
        except AppError as exc:
            if exc.code != "DOCUMENT_BOUNDARY_NOT_FOUND":
                raise
            if first_failure is None:
                first_failure = exc
            attempts.append({"orientationSwap": allow_swap, "error": exc.code})
            continue
        swapped = normalized.image_rgb.shape[:2] != (height, width)
        if allow_swap and not swapped:
            # Same orientation as the first attempt; it already failed.
            continue
        rotations = (90, 270) if swapped else (0,)
        for degrees in rotations:
            page = (
                cv2.rotate(normalized.image_rgb, _ROTATIONS[degrees])
                if degrees else normalized.image_rgb
            )
            flattened = flatten_paper(page, template_rgb)
            # The flattened page first; the page as photographed if that is rejected.
            candidates = [(flattened.image_rgb, flattened.applied)]
            if flattened.applied:
                candidates.append((page, False))
            for image, applied in candidates:
                attempt = {
                    "orientationSwap": allow_swap, "rotation": degrees,
                    **flattened.to_dict(), "photometricFlattening": applied,
                }
                try:
                    registered = aligner.align(
                        image, template_rgb, validation_regions=validation_regions,
                    )
                except RegistrationFailure as exc:
                    if first_page is None:
                        first_failure, first_page = exc, image
                    attempts.append({**attempt, "error": exc.code,
                                     "reason": exc.metadata.get("rejectionReason")})
                    continue
                rotation = _rotation_matrix(
                    degrees, normalized.image_rgb.shape[1], normalized.image_rgb.shape[0],
                )
                attempts.append({**attempt, "accepted": True})
                if applied:
                    registered = replace(
                        registered,
                        aligned_rgb=restore_ink(registered.aligned_rgb, flattened.white_level),
                    )
                return DocumentRegistration(
                    normalized=normalized,
                    registration_input=image,
                    registered=registered,
                    source_to_template=registered.homography @ rotation @ normalized.transform,
                    info={
                        "documentRotation": degrees,
                        "photometricFlattening": applied,
                        "paperLevel": round(flattened.paper_level, 1),
                        "paperSpread": round(flattened.paper_spread, 1),
                        "registrationAttempts": attempts,
                    },
                )
    assert first_failure is not None
    if isinstance(first_failure, RegistrationFailure):
        first_failure.metadata = {**first_failure.metadata, "registrationAttempts": attempts}
        first_failure.normalized_rgb = first_page
    raise first_failure
