"""Version-bound field-edge evidence for the WSL reference qualification path.

Saved measurements are recomputed from owned vector/raster inputs. Measurement
copies never replace a version's workbook, print files, coordinates or mappings.
This supplies geometry observations, not environment or handwritten-ink approval.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.domain.print_layout import print_error
from app.services import reference_stroke_differential as differential
from app.services import renderer_glyph_bridge as glyph
from app.services import renderer_vector_bridge as vector
from app.services import renderer_wsl
from app.services.calc_reference_precision import compare_coordinates
from app.services.office_reference_renderer import _RENDER_LOCK
from app.services.prepared_reference_evidence import PreparedReference, _replay_identity
from app.services.print_layout_service import digest
from app.services.reference_border_probe import SIDES, compare_raster, probe_copy
from app.services.reference_field_raster import observe_field_ink
from app.services.reference_splash_raster import observe_adjusted_ink
from app.services.reference_vector_raster import observe_raster
from app.services.renderer_process import renderer_mutex
from app.storage.base import StorageService

POLICY = "wsl-prepared-field-geometry-v1"
MAX_BYTES = 64 * 1024 * 1024
PAGE_GEOMETRY = ("mediaBox", "cropBox", "effectiveBox", "rotation", "userUnit")


@dataclass
class GeometryEvidence:
    original_vectors: dict[str, Any]
    probe: PreparedReference | None = None
    probe_vectors: dict[str, Any] | None = None


def _vectors(pdf: bytes, renderer: dict[str, Any], report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Invalid geometry vector report")
    manifest = glyph.input_manifest(pdf, renderer)
    payload = glyph._bundle(
        [("reference.pdf", pdf), ("manifest.json", glyph.canonical_bytes(manifest))]
    )
    if len(payload) > glyph.MAX_INPUT or len(glyph.canonical_bytes(report)) > glyph.MAX_REPORT:
        raise ValueError("Geometry vector evidence exceeds budget")
    glyph.validate_result(
        report,
        manifest,
        hashlib.sha256(vector.code_bundle()).hexdigest(),
        hashlib.sha256(payload).hexdigest(),
    )
    vector.validate_vectors(report, renderer)


def _reference(prepared: PreparedReference, plan: dict[str, Any]) -> None:
    if prepared.evidence.get("planSha256") != digest(plan):
        raise ValueError("Geometry evidence print plan changed")
    _replay_identity(prepared.report)
    _images(prepared)
    if (
        prepared.report["sourceSha256"] != hashlib.sha256(prepared.workbook).hexdigest()
        or prepared.report["pageCount"] != len(plan["pages"])
        or prepared.report["imageSha256"] != [p["referenceImage"]["sha256"] for p in plan["pages"]]
        or prepared.report["imageSizes"] != [[p["width"], p["height"]] for p in plan["pages"]]
    ):
        raise ValueError("Geometry reference page binding changed")


def _images(prepared: PreparedReference) -> None:
    report = prepared.report
    if not isinstance(report, dict) or len(glyph.canonical_bytes(report)) > glyph.MAX_REPORT:
        raise ValueError("Invalid geometry renderer report")
    count = report.get("pageCount")
    if (
        type(count) is not int
        or not 1 <= count <= 50
        or len(prepared.images) != count
        or sum(map(len, [prepared.workbook, prepared.pdf, *prepared.images])) > MAX_BYTES
        or not prepared.workbook
        or not prepared.pdf
        or report.get("pdfSha256") != hashlib.sha256(prepared.pdf).hexdigest()
        or report.get("imageSha256") != [hashlib.sha256(i).hexdigest() for i in prepared.images]
        or any(not image for image in prepared.images)
    ):
        raise ValueError("Geometry evidence image or PDF identity changed")


def _observations(
    prepared: PreparedReference, plan: dict[str, Any], vectors: dict[str, Any]
) -> list[dict[str, Any]]:
    observations = []
    probes = vectors.get("strokeProbes")
    for page, item, image in zip(
        plan["pages"], vectors["vectors"]["pages"], prepared.images, strict=True
    ):
        observation = observe_raster(page, item, image)
        # _vectors validates the complete manifest first. Reconstruct deltas
        # against each owned PNG here, including on GET, never saved summaries.
        observation["strokeDifferential"] = (
            differential.observe_page(page, item, image, probes["probes"]) if probes else []
        )
        observation["splashRasterObservation"] = observe_adjusted_ink(
            page, item, image, observation, vectors.get("splashCalibration"))
        observation["fieldRasterObservation"] = observe_field_ink(
            page, item, observation, observation["splashRasterObservation"])
        precision = compare_coordinates(plan, page, item, observation)
        if precision is not None:
            observation["coordinatePrecision"] = precision
        observations.append(observation)
    return observations


def _selection(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "cellId": field["cellId"],
            "sourceRange": field["sourceRange"],
            "sides": [
                side
                for side, edge in zip(SIDES, field["edges"], strict=True)
                if edge["status"] == "UNOBSERVED"
            ],
        }
        for page in observations
        for field in page["fields"]
        if any(edge["status"] == "UNOBSERVED" for edge in field["edges"])
    ]


def assess_geometry(
    prepared: PreparedReference, plan: dict[str, Any], evidence: GeometryEvidence
) -> list[dict[str, Any]]:
    """Derive every field's status anew; no persisted PASS/summary is trusted."""
    _reference(prepared, plan)
    _vectors(prepared.pdf, prepared.report, evidence.original_vectors)
    observations = _observations(prepared, plan, evidence.original_vectors)
    selected = _selection(observations)
    probe = evidence.probe
    comparison: list[dict[str, Any]] = []
    if selected:
        if probe is None or evidence.probe_vectors is None:
            raise ValueError("Geometry measurement copy is missing")
        expected, _ = probe_copy(prepared.workbook, plan["sheetName"], selected)
        if probe.workbook != expected:
            raise ValueError("Geometry measurement copy differs from source selection")
        _images(probe)
        before, after = _replay_identity(prepared.report), _replay_identity(probe.report)
        before["sourceSha256"] = hashlib.sha256(expected).hexdigest()
        if before != after or prepared.report["imageSizes"] != probe.report["imageSizes"]:
            raise ValueError("Geometry probe changed the fixed renderer or page dimensions")
        _vectors(probe.pdf, probe.report, evidence.probe_vectors)
        projected = deepcopy(plan)
        for page, image in zip(projected["pages"], probe.images, strict=True):
            page["referenceImage"]["sha256"] = hashlib.sha256(image).hexdigest()
        observations = _observations(probe, projected, evidence.probe_vectors)
        for original, changed, a, b in zip(
            prepared.images,
            probe.images,
            evidence.original_vectors["vectors"]["pages"],
            evidence.probe_vectors["vectors"]["pages"],
            strict=True,
        ):
            trace_a, trace_b = a.get("textProgram"), b.get("textProgram")
            if (
                not isinstance(trace_a, dict)
                or not isinstance(trace_b, dict)
                or trace_a.get("status") != "RECORDED"
                or trace_b.get("status") != "RECORDED"
                or trace_a != trace_b
                or any(a[key] != b[key] for key in PAGE_GEOMETRY)
            ):
                raise ValueError("Geometry probe changed the original text program")
            raster = compare_raster(original, changed)
            if raster["changedOutsideRedNeighborhood"]:
                raise ValueError("Geometry probe changed pixels outside measurement lines")
            comparison.append(raster)
    elif probe is not None or evidence.probe_vectors is not None:
        raise ValueError("Unexpected geometry measurement copy")
    pages = []
    for index, (page, observation) in enumerate(zip(plan["pages"], observations, strict=True)):
        fields = observation["fields"]
        codes = set()
        if not fields or any(
            any(edge["status"] != "OBSERVED" for edge in f["edges"]) for f in fields
        ):
            codes.add("REFERENCE_FIELD_EDGES_UNVERIFIED")
        if any(f["status"] == "EXCEEDS_DIAGNOSTIC_BUDGET" for f in fields):
            codes.add("MAX_ERROR_EXCEEDED")
        raster_observation = observation["rasterObservation"]
        if not observation["fieldRasterObservation"]["allRequiredInkObserved"]:
            codes.add("REFERENCE_RASTER_INK_UNVERIFIED")
        precision = observation.get("coordinatePrecision")
        if precision is not None and precision["status"] != "MATCH":
            codes.add("REFERENCE_PDF_COORDINATES_UNVERIFIED")
        errors = [abs(e["errorPx"]) for f in fields for e in f["edges"] if e["errorPx"] is not None]
        pages.append(
            {
                "page": page["page"],
                "range": page["range"],
                "policy": POLICY,
                "status": "FAIL" if codes else "MEASURED",
                "codes": sorted(codes),
                "fieldCount": len(fields),
                "fields": deepcopy(fields),
                "maxErrorPx": max(errors, default=None),
                "meanErrorPx": sum(errors) / len(errors) if errors else None,
                "edgeOrder": list(SIDES),
                "thresholdPx": observation["thresholdPx"],
                "thresholdQualified": False,
                "productionQualified": False,
                "visibilityVerified": False,
                "rasterObservation": deepcopy(raster_observation),
                "splashRasterObservation": deepcopy(observation["splashRasterObservation"]),
                "fieldRasterObservation": deepcopy(observation["fieldRasterObservation"]),
                "strokeDifferential": deepcopy(observation["strokeDifferential"]),
                "referenceImageSha256": page["referenceImage"]["sha256"],
                "measurementImageSha256": observation["referenceImageSha256"],
                "probeComparison": comparison[index] if comparison else None,
                **({"coordinatePrecision": deepcopy(precision)} if precision is not None else {}),
            }
        )
    return pages


def run_prepared_geometry(
    prepared: PreparedReference, plan: dict[str, Any], distribution: str
) -> GeometryEvidence | None:
    if prepared.report.get("runtimeImage") is None:
        return None  # Never retrofit a current environment onto an old print file.
    _reference(prepared, plan)
    glyph.input_manifest(prepared.pdf, prepared.report)
    if not _RENDER_LOCK.acquire(blocking=False):
        raise print_error("別の印刷検証を実行中です。", "RENDERER_BUSY", 409)
    try:
        with renderer_mutex():
            original = vector.inspect_wsl_vectors(
                prepared.pdf, prepared.report, distribution=distribution
            )
            _vectors(prepared.pdf, prepared.report, original)
            selected = _selection(_observations(prepared, plan, original))
            result = GeometryEvidence(original)
            if selected:
                copied, _ = probe_copy(prepared.workbook, plan["sheetName"], selected)
                pdf, images, report = renderer_wsl.render_wsl_reference(
                    copied,
                    distribution=distribution,
                    runtime_id=prepared.report["runtimeImage"]["sha256"],
                )
                result.probe = PreparedReference(copied, pdf, images, report, {})
                result.probe_vectors = vector.inspect_wsl_vectors(
                    pdf, report, distribution=distribution
                )
    finally:
        _RENDER_LOCK.release()
    assess_geometry(prepared, plan, result)
    return result


def save_prepared_geometry(
    session: Session,
    storage: StorageService,
    version: TemplateVersion,
    generation: str,
    prepared: PreparedReference,
    evidence: GeometryEvidence,
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{32}", generation) is None:
        raise ValueError("Invalid geometry generation")
    assess_geometry(prepared, version.quality_json["printLayout"], evidence)
    job = session.get(ProcessingJob, version.source_job_id)
    if job is None:
        raise ValueError("Geometry source job is missing")
    refs = []

    def save(name: str, data: bytes, media: str) -> None:
        if not 0 < len(data) <= MAX_BYTES:
            raise ValueError("Geometry artifact size is invalid")
        path = storage.write_bytes(
            f"artifacts/{job.id}/qualification/{generation}/{name}", data
        )
        artifact = JobArtifact(
            id=str(uuid4()),
            job_id=job.id,
            attempt_number=job.attempt_number,
            artifact_name=f"qualification-{generation}-geometry-{name}",
            relative_path=path,
            sha256=hashlib.sha256(data).hexdigest(),
            media_type=media,
        )
        session.add(artifact)
        refs.append(
            {
                "file": name,
                "artifactId": artifact.id,
                "relativePath": path,
                "sha256": artifact.sha256,
                "name": artifact.artifact_name,
                "mediaType": media,
                "attemptNumber": job.attempt_number,
            }
        )

    save(
        "original-vectors.json",
        glyph.canonical_bytes(evidence.original_vectors),
        "application/json",
    )
    if evidence.probe is not None:
        probe = evidence.probe
        save(
            "probe.xlsx",
            probe.workbook,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        save("probe.pdf", probe.pdf, "application/pdf")
        save("probe-renderer.json", glyph.canonical_bytes(probe.report), "application/json")
        save(
            "probe-vectors.json", glyph.canonical_bytes(evidence.probe_vectors), "application/json"
        )
        for number, image in enumerate(probe.images, 1):
            save(f"probe-{number}.png", image, "image/png")
    return {
        "policy": POLICY,
        "templateVersionId": version.id,
        "preparedEvidence": deepcopy(prepared.evidence),
        "artifacts": refs,
    }


def load_prepared_geometry(
    session: Session,
    storage: StorageService,
    version: TemplateVersion,
    prepared: PreparedReference,
    ref: dict[str, Any],
) -> list[dict[str, Any]]:
    if (
        not isinstance(ref, dict)
        or ref.get("policy") != POLICY
        or ref.get("templateVersionId") != version.id
        or ref.get("preparedEvidence") != prepared.evidence
        or not isinstance(ref.get("artifacts"), list)
        or not 1 <= len(ref["artifacts"]) <= 55
    ):
        raise ValueError("Geometry version or source graph changed")
    files, ids, total = {}, set(), 0
    for part in ref["artifacts"]:
        if not isinstance(part, dict) or not isinstance(part.get("file"), str):
            raise ValueError("Invalid geometry artifact reference")
        item = session.get(JobArtifact, part["artifactId"])
        if (
            item is None
            or item.id in ids
            or part["file"] in files
            or item.job_id != version.source_job_id
            or item.relative_path != part["relativePath"]
            or item.artifact_name != part["name"]
            or item.sha256 != part["sha256"]
            or item.media_type != part["mediaType"]
            or type(part.get("attemptNumber")) is not int
            or item.attempt_number != part["attemptNumber"]
        ):
            raise ValueError("Geometry artifact ownership changed")
        content = storage.read_bytes(item.relative_path)
        total += len(content)
        if (
            not content
            or total > MAX_BYTES + 3 * glyph.MAX_REPORT
            or hashlib.sha256(content).hexdigest() != item.sha256
        ):
            raise ValueError("Geometry artifact bytes changed")
        if part["file"].endswith(".json") and len(content) > glyph.MAX_REPORT:
            raise ValueError("Geometry JSON exceeds budget")
        files[part["file"]] = content
        ids.add(item.id)
    evidence = GeometryEvidence(json.loads(files["original-vectors.json"]))
    expected = {"original-vectors.json"}
    if "probe.xlsx" in files:
        names = [f"probe-{i}.png" for i in range(1, len(prepared.images) + 1)]
        expected.update(
            ["probe.xlsx", "probe.pdf", "probe-renderer.json", "probe-vectors.json", *names]
        )
        evidence.probe = PreparedReference(
            files["probe.xlsx"],
            files["probe.pdf"],
            [files[name] for name in names],
            json.loads(files["probe-renderer.json"]),
            {},
        )
        evidence.probe_vectors = json.loads(files["probe-vectors.json"])
    if set(files) != expected:
        raise ValueError("Geometry artifact set is incomplete or unexpected")
    return assess_geometry(prepared, version.quality_json["printLayout"], evidence)
