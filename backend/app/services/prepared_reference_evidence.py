"""Verify the complete owned artifact graph of a prepared WSL reference.

Artifact integrity is not an environment, glyph, geometry or crop certificate.
No Office invocation, PDF parser, database mutation or coordinate repair here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobArtifact, TemplateVersion
from app.domain.calc_print_grid import MODEL as CALC_GEOMETRY_MODEL
from app.services.prepared_print_contract import render_key
from app.services.print_layout_service import digest
from app.services.renderer_wsl_worker import validate_bootstrap_python, validate_core_guard
from app.services.wsl_execution_evidence import FIELDS as EXECUTION_FIELDS
from app.services.wsl_execution_evidence import validate_execution
from app.services.wsl_workbook_geometry import MODEL
from app.storage.base import StorageService


@dataclass
class PreparedReference:
    workbook: bytes
    pdf: bytes
    images: list[bytes]
    report: dict[str, Any]
    evidence: dict[str, Any]


def _replay_identity(report: dict[str, Any]) -> dict[str, Any]:
    constants = {"policy": "wsl-bwrap-cgroup-v1", "format": "reference", "status": "PASS",
                 "fontSource": "windows", "fontRequestPolicy": "wsl-fontconfig-face-v2",
                 "rasterizer": "poppler-pdftoppm", "dpi": 150}
    if any(report.get(key) != value for key, value in constants.items()):
        raise ValueError("Invalid replay environment")
    source = report.get("sourceSha256")
    if not isinstance(source, str) or re.fullmatch(r"[0-9a-f]{64}", source) is None:
        raise ValueError("Invalid replay input identity")
    policies = {"runtimeImage": "wsl-squashfs-runtime-v1",
                "fontDirectoryIdentity": "wsl-font-directory-v1",
                "workerIdentity": "wsl-worker-source-v1"}
    for key, policy in policies.items():
        item = report.get(key)
        if (not isinstance(item, dict) or item.get("policy") != policy
                or not isinstance(item.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None):
            raise ValueError("Invalid replay environment identity")
    runtime, fonts = report["runtimeImage"], report["fontDirectoryIdentity"]
    if (runtime.get("filesystem") != "squashfs" or runtime.get("mountsReadOnly") is not True
            or not isinstance(runtime.get("kernel"), str) or not runtime["kernel"]
            or type(fonts.get("fileCount")) is not int or not 1 <= fonts["fileCount"] <= 4096
            or type(fonts.get("totalBytes")) is not int
            or not 0 <= fonts["totalBytes"] <= 1024 * 1024 * 1024
            or not isinstance(report.get("resolvedFonts"), list) or not report["resolvedFonts"]):
        raise ValueError("Invalid replay environment conditions")
    identity = {key: report[key] for key in (
        *constants, "sourceSha256", *policies, "resolvedFonts")}
    for key, validate in (("coreDumpGuard", validate_core_guard),
                          ("brokerCoreDumpGuard", validate_core_guard),
                          ("bootstrapPythonIdentity", validate_bootstrap_python)):
        # Old saved reports stay old; do not fabricate current proof on read.
        if key in report:
            try:
                validate(report[key])
            except RuntimeError as exc:
                raise ValueError("Invalid replay execution evidence") from exc
            identity[key] = copy.deepcopy(report[key])
    if "bootstrapPythonIdentity" in report or any(key in report for key in EXECUTION_FIELDS):
        identity["executionBoundary"] = validate_execution(report)
    return identity


def compare_reference_replay(original: dict[str, Any], replay: dict[str, Any],
                             images: list[bytes]) -> bool:
    """Compare every ordered raster under the same measured input/environment.

    This does not certify the environment, glyphs or crop safety, and does not
    assert byte-identical PDFs (producer timestamps may differ).
    """
    if _replay_identity(original) != _replay_identity(replay):
        raise ValueError("Replay input or environment changed")
    for report in (original, replay):
        count, hashes, sizes = (
            report.get(key) for key in ("pageCount", "imageSha256", "imageSizes"))
        if (type(count) is not int or not 1 <= count <= 50
                or not isinstance(hashes, list) or len(hashes) != count
                or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in hashes)
                or not isinstance(sizes, list) or len(sizes) != count
                or any(not isinstance(size, list) or len(size) != 2
                       or any(type(value) is not int or value <= 0 for value in size)
                       or size[0] * size[1] > 20_000_000 for size in sizes)):
            raise ValueError("Invalid replay image evidence")
    if [hashlib.sha256(image).hexdigest() for image in images] != replay["imageSha256"]:
        raise ValueError("Replay image bytes changed")
    return bool(original["imageSha256"] == replay["imageSha256"]
                and original["imageSizes"] == replay["imageSizes"])


def load_prepared_reference(session: Session, storage: StorageService,
                            version: TemplateVersion) -> PreparedReference:
    plan = version.quality_json["printLayout"]
    if (plan.get("rendererVersion") != "wsl-print-reference-v1"
            or plan.get("geometryModelVersion") not in {MODEL, CALC_GEOMETRY_MODEL}
            or plan.get("layoutSha256") != digest(version.layout_json)
            or plan["printFiles"].get("renderKey") != render_key(plan)):
        raise ValueError("Prepared reference plan changed")
    pages = plan["pages"]
    if not isinstance(pages, list) or not 1 <= len(pages) <= 50:
        raise ValueError("Invalid prepared page count")
    source = session.scalars(select(JobArtifact).where(
        JobArtifact.job_id == version.source_job_id,
        JobArtifact.artifact_name == "sourceWorkbook",
    ).order_by(JobArtifact.created_at.desc())).first()
    if source is None:
        raise ValueError("Prepared source missing")

    def content(artifact: JobArtifact, limit: int) -> bytes:
        data = storage.read_bytes(artifact.relative_path)
        if not 0 < len(data) <= limit or hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ValueError("Prepared artifact contents changed")
        return data

    content(source, 64 * 1024 * 1024)
    geometry = plan["geometryEvidence"]
    measured = geometry["importReport"]
    if (source.sha256 != plan["sourceSha256"]
            or source.sha256 != geometry["sourceSha256"]
            or measured.get("sourceSha256") != source.sha256
            or measured.get("format") != "fods"
            or measured.get("fontSource") != "windows"):
        raise ValueError("Prepared source linkage changed")

    artifact_ids: set[str] = set()

    def owned(ref: dict[str, Any], media_type: str, limit: int,
              size: tuple[int, int] | None = None) -> bytes:
        item = session.get(JobArtifact, ref["artifactId"])
        if (item is None or item.job_id != version.source_job_id
                or item.id in artifact_ids or item.sha256 != ref["sha256"]
                or item.relative_path != ref["relativePath"]
                or item.artifact_name != ref["name"] or item.media_type != media_type
                or (size is not None and (item.width, item.height) != size)):
            raise ValueError("Prepared artifact ownership changed")
        artifact_ids.add(item.id)
        return content(item, limit)

    files = plan["printFiles"]
    workbook = owned(files["xlsx"],
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     64 * 1024 * 1024)
    pdf = owned(files["pdf"], "application/pdf", 64 * 1024 * 1024)
    report = json.loads(owned(files["report"], "application/json", 1024 * 1024))
    if (not isinstance(report, dict) or report.get("status") != "PASS"
            or report.get("format") != "reference" or report.get("fontSource") != "windows"
            or report.get("policy") != "wsl-bwrap-cgroup-v1"
            or report.get("fontRequestPolicy") != "wsl-fontconfig-face-v2"
            or report.get("sourceSha256") != hashlib.sha256(workbook).hexdigest()
            or report.get("pdfSha256") != files["pdf"]["sha256"]
            or type(report.get("pageCount")) is not int
            or report["pageCount"] != len(pages) or report.get("dpi") != 150
            or report.get("imageSha256") != [p["referenceImage"]["sha256"] for p in pages]
            or report.get("imageSizes") != [[p["width"], p["height"]] for p in pages]
            or report.get("resolvedFonts") != measured.get("resolvedFonts")):
        raise ValueError("Prepared renderer linkage changed")
    for key in ("runtimeImage", "fontDirectoryIdentity", "workerIdentity",
                "coreDumpGuard", "brokerCoreDumpGuard", "bootstrapPythonIdentity"):
        # Missing old evidence is not upgraded into a certificate. The caller
        # must separately require/validate current environment qualification.
        if report.get(key) != measured.get(key):
            raise ValueError("Prepared execution environments differ")
    # Historical artifacts lacking observations remain readable but unqualified.
    # New bootstrap reports and any partially present control record must be complete.
    if (any("bootstrapPythonIdentity" in item or any(key in item for key in EXECUTION_FIELDS)
            for item in (report, measured))
            and validate_execution(report) != validate_execution(measured)):
        raise ValueError("Prepared execution isolation conditions differ")
    images = []
    reference_bytes = len(pdf)
    page_numbers = set()
    for page in pages:
        number = page["page"]
        if type(number) is not int or number < 1 or number in page_numbers:
            raise ValueError("Prepared page identity is ambiguous")
        page_numbers.add(number)
        image = owned(page["referenceImage"], "image/png", 64 * 1024 * 1024,
                      (page["width"], page["height"]))
        reference_bytes += len(image)
        if reference_bytes > 64 * 1024 * 1024:
            raise ValueError("Prepared reference size limit exceeded")
        images.append(image)
    return PreparedReference(workbook=workbook, pdf=pdf, images=images, report=report, evidence={
        "policy": "wsl-prepared-artifact-graph-v1", "planSha256": digest(plan),
        "sourceArtifactId": source.id, "sourceSha256": source.sha256,
        "importReportSha256": digest(measured),
        "printFiles": copy.deepcopy(files),
        "pages": [{"page": p["page"], "referenceImage": copy.deepcopy(p["referenceImage"])}
                  for p in pages],
    })
