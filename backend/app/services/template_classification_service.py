"""Guess which in-use template an uploaded document image was filled on.

Nothing is stored and no external API is called. Every in-use single-image template is
ranked by mutual ORB matches against its clean template image (cheap), and the best few
are then checked with the same register_document the reading itself uses. The guess is
"decided" only when exactly one template passes that check; otherwise the caller asks
the user to pick from the candidates.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray
from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import JobArtifact, Template, TemplateVersion
from app.db.session import Database
from app.domain.enums import TemplateVersionStatus
from app.domain.template_quality import has_full_image_boundary_fallback
from app.image_processing.image_loader import ImageLoader
from app.image_processing.photo_registration import register_document
from app.image_processing.registration import TemplateAligner, _ratio_matches, _scaled_gray
from app.storage.local_storage import LocalStorageService

# Templates checked with full registration after the cheap ranking.
VERIFY_LIMIT = 3
# A template below this share of the best ranking score is not worth verifying.
VERIFY_SHARE = 0.35
MIN_RANK_INLIERS = 15
CANDIDATE_LIMIT = 4


@dataclass(slots=True)
class _Reference:
    template_id: str
    template_name: str
    version_id: str
    image_rgb: NDArray[np.uint8]
    thumbnail_url: str
    keypoints: Any = None
    descriptors: NDArray[np.uint8] | None = None


_feature_cache: dict[str, tuple[Any, NDArray[np.uint8] | None]] = {}


def _features(gray: NDArray[np.uint8]) -> tuple[Any, NDArray[np.uint8] | None]:
    orb = cv2.ORB_create(nfeatures=3000, scaleFactor=1.2, nlevels=8)
    return orb.detectAndCompute(gray, None)


def _rank_inliers(
    source: tuple[Any, NDArray[np.uint8] | None],
    reference: tuple[Any, NDArray[np.uint8] | None],
) -> int:
    source_points, source_descriptors = source
    template_points, template_descriptors = reference
    if source_descriptors is None or template_descriptors is None:
        return 0
    if len(source_points) < 2 or len(template_points) < 2:
        return 0
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    forward = _ratio_matches(matcher.knnMatch(source_descriptors, template_descriptors, k=2))
    backward = _ratio_matches(matcher.knnMatch(template_descriptors, source_descriptors, k=2))
    reverse = {match.queryIdx: match.trainIdx for match in backward}
    matches = [m for m in forward if reverse.get(m.trainIdx) == m.queryIdx]
    if len(matches) < 8:
        return 0
    src = np.float32([source_points[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([template_points[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 6.0)
    return 0 if mask is None else int(mask.ravel().sum())


class TemplateClassificationService:
    def __init__(
        self, database: Database, storage: LocalStorageService, settings: Settings,
    ) -> None:
        self._database = database
        self._storage = storage
        self._loader = ImageLoader(settings.storage, settings.image_processing)
        self._aligner = TemplateAligner(settings.registration)

    def _references(self) -> list[_Reference]:
        references: list[_Reference] = []
        with self._database.session() as session:
            rows = session.execute(
                select(Template, TemplateVersion)
                .join(TemplateVersion, Template.active_version_id == TemplateVersion.id)
            ).all()
            for template, version in rows:
                quality = version.quality_json or {}
                if version.status != TemplateVersionStatus.ACTIVE.value:
                    continue
                if quality.get("printLayout") is not None or not quality.get("layoutVerified"):
                    continue
                if has_full_image_boundary_fallback(quality):
                    continue
                if not version.clean_template_artifact_id:
                    continue
                artifact = session.get(JobArtifact, version.clean_template_artifact_id)
                if artifact is None:
                    continue
                try:
                    content = self._storage.read_bytes(artifact.relative_path)
                except (OSError, AppError):
                    continue
                if hashlib.sha256(content).hexdigest() != artifact.sha256:
                    continue
                image = self._loader.load(content, artifact.media_type).rgb
                reference = _Reference(
                    template_id=template.id,
                    template_name=template.name,
                    version_id=version.id,
                    image_rgb=image,
                    thumbnail_url=(
                        f"/api/v1/jobs/{artifact.job_id}/artifacts/"
                        f"{artifact.artifact_name}/thumbnail"
                    ),
                )
                cached = _feature_cache.get(artifact.sha256)
                if cached is None:
                    cached = _features(_scaled_gray(image)[0])
                    _feature_cache[artifact.sha256] = cached
                reference.keypoints, reference.descriptors = cached
                references.append(reference)
        return references

    def classify(self, content: bytes, media_type: str | None) -> dict[str, Any]:
        source = self._loader.load(content, media_type).rgb
        source_features = _features(_scaled_gray(source)[0])
        references = self._references()
        ranked = sorted(
            (
                (_rank_inliers(source_features, (r.keypoints, r.descriptors)), r)
                for r in references
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        best = ranked[0][0] if ranked else 0
        verified: set[str] = set()
        for score, reference in ranked[:VERIFY_LIMIT]:
            if score < max(MIN_RANK_INLIERS, best * VERIFY_SHARE):
                break
            try:
                register_document(
                    source, reference.image_rgb, aligner=self._aligner, validation_regions=(),
                )
            except AppError:
                continue
            verified.add(reference.version_id)

        def item(score: int, reference: _Reference) -> dict[str, Any]:
            return {
                "templateId": reference.template_id,
                "templateName": reference.template_name,
                "templateVersionId": reference.version_id,
                "thumbnailUrl": reference.thumbnail_url,
                "matched": reference.version_id in verified,
                "score": score,
            }

        matched = [item(s, r) for s, r in ranked if r.version_id in verified]
        others = [
            item(s, r) for s, r in ranked
            if r.version_id not in verified and s >= MIN_RANK_INLIERS
        ]
        candidates = (matched + others)[:CANDIDATE_LIMIT]
        decided = matched[0]["templateVersionId"] if len(matched) == 1 else None
        return {
            "decidedTemplateVersionId": decided,
            "candidates": candidates,
            "checkedTemplateCount": len(references),
        }
