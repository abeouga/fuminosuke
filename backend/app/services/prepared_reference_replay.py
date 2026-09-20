"""Run and retain a version-bound raster replay, without replacing print files."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.domain.print_layout import print_error
from app.services import renderer_wsl
from app.services.office_reference_renderer import _RENDER_LOCK
from app.services.prepared_reference_evidence import (
    PreparedReference,
    _replay_identity,
    compare_reference_replay,
)
from app.services.renderer_process import renderer_mutex
from app.storage.base import StorageService

POLICY = "wsl-reference-replay-v1"


@dataclass
class ReferenceReplay:
    report: dict[str, Any]
    images: list[bytes]
    deterministic: bool


def run_reference_replay(prepared: PreparedReference, distribution: str) -> ReferenceReplay | None:
    # Older live-runtime preparations need explicit preparation with a pinned
    # runtime. Never backfill identity or switch to current app runtime settings.
    if prepared.report.get("runtimeImage") is None:
        return None
    try:
        expected = _replay_identity(prepared.report)
    except ValueError as exc:
        raise print_error("再描画に必要な固定環境の証拠がありません。",
                          "RENDERER_ENVIRONMENT_CHANGED", 409) from exc
    if not _RENDER_LOCK.acquire(blocking=False):
        raise print_error("別の印刷検証を実行中です。", "RENDERER_BUSY", 409)
    try:
        with renderer_mutex():
            _, images, report = renderer_wsl.render_wsl_reference(
                prepared.workbook, distribution=distribution,
                runtime_id=expected["runtimeImage"]["sha256"],
            )
    finally:
        _RENDER_LOCK.release()
    try:
        if _replay_identity(report) != expected:
            raise ValueError("Replay environment changed")
    except ValueError as exc:
        raise print_error("印刷確定時と再描画時の実行条件が異なります。",
                          "RENDERER_ENVIRONMENT_CHANGED", 409) from exc
    try:
        matched = compare_reference_replay(prepared.report, report, images)
    except ValueError as exc:
        raise print_error("再描画画像と検証情報の対応を確認できません。",
                          "REFERENCE_EVIDENCE_CHANGED", 409) from exc
    return ReferenceReplay(report, images, matched)


def save_reference_replay(session: Session, storage: StorageService, version: TemplateVersion,
                          generation: str, replay: ReferenceReplay) -> dict[str, Any]:
    job = session.get(ProcessingJob, version.source_job_id)
    if job is None:
        raise ValueError("Replay source job missing")
    refs = []
    for number, (image, size) in enumerate(zip(
            replay.images, replay.report["imageSizes"], strict=True), 1):
        path = storage.write_bytes(
            f"artifacts/{job.id}/qualification/{generation}/replay-{number}.png", image)
        item = JobArtifact(
            id=str(uuid4()), job_id=job.id, attempt_number=job.attempt_number,
            artifact_name=f"qualification-{generation}-replay-{number}",
            relative_path=path, sha256=hashlib.sha256(image).hexdigest(),
            media_type="image/png", width=size[0], height=size[1],
        )
        session.add(item)
        refs.append({"renderedPage": number, "artifactId": item.id,
                     "relativePath": path, "sha256": item.sha256, "name": item.artifact_name,
                     "width": size[0], "height": size[1]})
    return {"policy": POLICY, "report": replay.report, "images": refs}


def load_reference_replay(session: Session, storage: StorageService, version: TemplateVersion,
                          original: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if not isinstance(evidence, dict):
        raise ValueError("Invalid replay artifact evidence")
    refs = evidence.get("images")
    if (evidence.get("policy") != POLICY or not isinstance(refs, list)
            or not 1 <= len(refs) <= 50 or not isinstance(evidence.get("report"), dict)):
        raise ValueError("Invalid replay artifact evidence")
    images = []
    used = set()
    total = 0
    for number, ref in enumerate(refs, 1):
        item = session.get(JobArtifact, ref["artifactId"])
        if (item is None or item.job_id != version.source_job_id or item.id in used
                or type(ref.get("renderedPage")) is not int or ref["renderedPage"] != number
                or item.relative_path != ref["relativePath"] or item.sha256 != ref["sha256"]
                or item.artifact_name != ref["name"] or item.media_type != "image/png"
                or (item.width, item.height) != (ref["width"], ref["height"])):
            raise ValueError("Replay artifact ownership changed")
        used.add(item.id)
        image = storage.read_bytes(item.relative_path)
        total += len(image)
        if (not image or total > 64 * 1024 * 1024
                or hashlib.sha256(image).hexdigest() != item.sha256):
            raise ValueError("Replay artifact content changed")
        images.append(image)
    return compare_reference_replay(original, evidence["report"], images)
