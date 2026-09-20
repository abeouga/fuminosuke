"""Bind isolated glyph evidence to a version's complete owned print artifact graph."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.domain.print_layout import print_error
from app.services import renderer_glyph_bridge as bridge
from app.services.office_reference_renderer import _RENDER_LOCK
from app.services.prepared_reference_evidence import PreparedReference
from app.services.renderer_process import renderer_mutex
from app.storage.base import StorageService

POLICY = "wsl-prepared-glyph-evidence-v1"


def run_prepared_glyphs(prepared: PreparedReference, distribution: str) -> dict[str, Any] | None:
    # Old live-runtime reports are not upgraded or moved to the current app runtime.
    if prepared.report.get("runtimeImage") is None:
        return None
    # Reject missing identities before starting any external process.
    bridge.input_manifest(prepared.pdf, prepared.report)
    if not _RENDER_LOCK.acquire(blocking=False):
        raise print_error("別の印刷検証を実行中です。", "RENDERER_BUSY", 409)
    try:
        with renderer_mutex():
            report = bridge.validate_wsl_pdf_glyphs(
                prepared.pdf, prepared.report, distribution=distribution)
    finally:
        _RENDER_LOCK.release()
    bridge.validate_saved_result(prepared.pdf, prepared.report, report)
    return report


def save_prepared_glyphs(
    session: Session, storage: StorageService, version: TemplateVersion,
    generation: str, prepared: PreparedReference, report: dict[str, Any],
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{32}", generation) is None:
        raise ValueError("Invalid glyph evidence generation")
    bridge.validate_saved_result(prepared.pdf, prepared.report, report)
    job = session.get(ProcessingJob, version.source_job_id)
    if job is None:
        raise ValueError("Glyph evidence source job missing")
    payload = bridge.canonical_bytes({
        "policy": POLICY, "templateVersionId": version.id,
        "preparedEvidence": prepared.evidence, "report": report,
    })
    if len(payload) > bridge.MAX_REPORT:
        raise ValueError("Glyph evidence is oversized")
    name = f"qualification-{generation}-glyphs"
    path = storage.write_bytes(
        f"artifacts/{job.id}/qualification/{generation}/glyphs.json", payload)
    artifact = JobArtifact(
        id=str(uuid4()), job_id=job.id, attempt_number=job.attempt_number,
        artifact_name=name, relative_path=path, sha256=hashlib.sha256(payload).hexdigest(),
        media_type="application/json",
    )
    session.add(artifact)
    return {"policy": POLICY, "artifactId": artifact.id, "relativePath": path,
            "sha256": artifact.sha256, "name": name, "attemptNumber": job.attempt_number}


def load_prepared_glyphs(
    session: Session, storage: StorageService, version: TemplateVersion,
    prepared: PreparedReference, ref: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(ref, dict) or ref.get("policy") != POLICY:
        raise ValueError("Invalid glyph artifact reference")
    item = session.get(JobArtifact, ref["artifactId"])
    if (item is None or item.job_id != version.source_job_id
            or item.relative_path != ref["relativePath"] or item.sha256 != ref["sha256"]
            or item.artifact_name != ref["name"] or item.media_type != "application/json"
            or type(ref.get("attemptNumber")) is not int
            or item.attempt_number != ref["attemptNumber"]):
        raise ValueError("Glyph artifact ownership changed")
    content = storage.read_bytes(item.relative_path)
    if (not 0 < len(content) <= bridge.MAX_REPORT
            or hashlib.sha256(content).hexdigest() != item.sha256):
        raise ValueError("Glyph artifact contents changed")
    record = json.loads(content)
    if (not isinstance(record, dict) or record.get("policy") != POLICY
            or record.get("templateVersionId") != version.id
            or record.get("preparedEvidence") != prepared.evidence
            or not isinstance(record.get("report"), dict)):
        raise ValueError("Glyph artifact version or print graph changed")
    report: dict[str, Any] = record["report"]
    bridge.validate_saved_result(prepared.pdf, prepared.report, report)
    return report
