"""Version-bound diagnostic evidence, deliberately fail-closed for production."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from contextlib import nullcontext, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import JobArtifact, TemplateVersion
from app.domain.calc_print_grid import MODEL as CALC_GEOMETRY_MODEL
from app.domain.print_layout import PrintLayoutRequest, page_plan, print_error
from app.services import calc_geometry_qualification as geometry_qualification
from app.services import wsl_environment_evidence as environment_evidence
from app.services import wsl_environment_qualification as environment_qualification
from app.services.office_reference_renderer import render_office, renderer_identity
from app.services.paged_safety_stages import INPUT_CHECKS, deferred_crop_checks, stage_assessment
from app.services.prepared_reference_evidence import PreparedReference, load_prepared_reference
from app.services.prepared_reference_geometry import (
    GeometryEvidence,
    assess_geometry,
    load_prepared_geometry,
    run_prepared_geometry,
    save_prepared_geometry,
)
from app.services.prepared_reference_glyphs import (
    load_prepared_glyphs,
    run_prepared_glyphs,
    save_prepared_glyphs,
)
from app.services.prepared_reference_replay import (
    ReferenceReplay,
    load_reference_replay,
    run_reference_replay,
    save_reference_replay,
)
from app.services.print_layout_service import PrintLayoutService, digest
from app.services.reference_crop_safety import qualify_crop
from app.services.reference_geometry import measure_page
from app.services.renderer_dependencies import validate_manifest
from app.services.renderer_environment import (
    environment_identity,
    environment_report,
    validate_environment,
)
from app.services.template_mutation import begin_immediate, require_draft, template_mutation_lock
from app.services.workbook_geometry import GEOMETRY_MODEL_VERSION

WSL_POLICY = "wsl-prepared-reference-v2"
WSL_BLOCKERS = ["WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED", "FONT_GLYPH_UNVERIFIED",
                "RENDER_DETERMINISM_UNVERIFIED", "GEOMETRY_TOLERANCE_UNQUALIFIED"]


def wsl_blockers(deterministic: bool | None,
                 glyph_report: dict[str, Any] | None = None, *,
                 environment_qualified: bool = False,
                 geometry_qualified: bool = False) -> list[str]:
    blockers = list(WSL_BLOCKERS)
    if deterministic is not None:
        blockers.remove("RENDER_DETERMINISM_UNVERIFIED")
    if deterministic is False:
        blockers.append("RENDER_NOT_DETERMINISTIC")
    if glyph_report is not None:
        blockers.remove("FONT_GLYPH_UNVERIFIED")
        if glyph_report["allMatchedFacesDeclared"] is not True:
            blockers.append("FONT_FALLBACK_UNQUALIFIED")
    if environment_qualified:
        blockers.remove("WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED")
    if geometry_qualified:
        blockers.remove("GEOMETRY_TOLERANCE_UNQUALIFIED")
    return blockers


def glyph_summary(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {"status": "PASS", "policy": report["policy"],
            "pdfSha256": report["glyphs"]["pdfSha256"],
            "textInstructionGlyphs": report["glyphs"]["textInstructionGlyphs"],
            "distinctFontCodes": report["glyphs"]["distinctFontCodes"],
            "allMatchedFacesDeclared": report["allMatchedFacesDeclared"],
            "visibilityAndLayoutVerified": False}


def qualification_implementation_fingerprint() -> str:
    return digest({
        "policy": WSL_POLICY,
        "referenceQualificationService": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "environmentIssuer": environment_qualification.implementation_fingerprint(),
        "geometryIssuer": geometry_qualification.implementation_fingerprint(),
    })


def _unverified_component(policy: str, implementation: str, blocker: str) -> dict[str, Any]:
    return {
        "policy": policy,
        "status": "UNVERIFIED",
        "qualified": False,
        "implementationFingerprint": implementation,
        "unresolvedBlockers": [blocker],
    }


def derive_wsl_qualification(
    *,
    prepared: PreparedReference,
    plan: dict[str, Any],
    pages: list[dict[str, Any]],
    field_geometry: list[dict[str, Any]] | None,
    deterministic: bool | None,
    glyph_report: dict[str, Any] | None,
    environment_lease: dict[str, Any] | None,
    version_id: str,
    version_revision: str,
    fingerprint: str,
    qualification_issued_at: int | None = None,
    additional_codes: list[str] | None = None,
) -> dict[str, Any]:
    """Recompute formal WSL qualification; saved booleans are never inputs."""
    implementation = qualification_implementation_fingerprint()
    environment = _unverified_component(
        environment_qualification.POLICY,
        environment_qualification.implementation_fingerprint(),
        "WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED",
    )
    if environment_lease is not None:
        with suppress(KeyError, TypeError, ValueError, RuntimeError):
            environment = environment_qualification.issue(
                prepared,
                environment_lease,
                version_id,
                fingerprint,
                qualification_issued_at=qualification_issued_at,
            )
    geometry = _unverified_component(
        geometry_qualification.POLICY,
        geometry_qualification.implementation_fingerprint(),
        "GEOMETRY_TOLERANCE_UNQUALIFIED",
    )
    if field_geometry is not None:
        with suppress(KeyError, TypeError, ValueError, RuntimeError):
            geometry = geometry_qualification.issue(
                prepared, plan, field_geometry, version_id, fingerprint
            )

    page_codes = sorted({
        code for page in pages for code in page.get("codes", [page.get("code")]) if code
    })
    geometry_codes = sorted({
        code for page in field_geometry or [] for code in page.get("codes", []) if code
    })
    anchor = geometry_qualification.anchor_diagnostic(
        page_codes, geometry["qualified"], plan.get("geometryModelVersion")
    )
    codes = (
        set(anchor["blockingCodes"])
        | set(geometry_codes)
        | set(additional_codes or [])
    )
    if deterministic is False:
        codes.add("RENDER_NOT_DETERMINISTIC")
    blockers = wsl_blockers(
        deterministic,
        glyph_report,
        environment_qualified=environment["qualified"],
        geometry_qualified=geometry["qualified"],
    )
    qualified = bool(
        environment["qualified"]
        and geometry["qualified"]
        and deterministic is True
        and glyph_report is not None
        and glyph_report.get("allMatchedFacesDeclared") is True
        and not codes
        and not blockers
    )
    prepared_sha = hashlib.sha256(environment_evidence.canonical(prepared.evidence)).hexdigest()
    binding = {
        "templateVersionId": version_id,
        "templateVersionRevision": version_revision,
        "sourceSha256": prepared.evidence["sourceSha256"],
        "printLayoutSha256": digest(plan),
        "geometryModelVersion": plan.get("geometryModelVersion"),
        "preparedEvidenceSha256": prepared_sha,
        "rendererEvidenceSha256": prepared.evidence["printFiles"]["report"]["sha256"],
        "referenceArtifactSha256": [
            page["referenceImage"]["sha256"] for page in prepared.evidence["pages"]
        ],
        "replayEvidenceSha256": digest({"deterministic": deterministic}),
        "glyphEvidenceSha256": digest(glyph_report) if glyph_report is not None else None,
        "geometryEvidenceSha256": (
            digest(field_geometry) if field_geometry is not None else None
        ),
        "environmentEvidenceSha256": (
            digest(environment_lease) if environment_lease is not None else None
        ),
        "qualificationFingerprint": fingerprint,
        "implementationFingerprint": implementation,
        "qualificationPolicy": WSL_POLICY,
    }
    issued_at = environment.get("issuedAt") if qualified else None
    expires_at = environment.get("expiresAt") if qualified else None
    unresolved = sorted(set(codes) | set(blockers))
    return {
        "qualificationPolicy": WSL_POLICY,
        "qualificationImplementationFingerprint": implementation,
        "templateVersionRevision": version_revision,
        "preparedEvidenceSha256": prepared_sha,
        "qualificationBinding": binding,
        "environmentQualification": environment,
        "geometryQualification": geometry,
        "anchorDiagnostic": anchor,
        "diagnosticCodes": sorted(set(page_codes) | set(geometry_codes)),
        "codes": sorted(codes),
        "productionBlockers": blockers,
        "unresolvedBlockers": unresolved,
        "environmentQualified": environment["qualified"],
        "geometryQualified": geometry["qualified"],
        "qualified": qualified,
        "qualificationIssued": qualified,
        "issuedAt": issued_at,
        "expiresAt": expires_at,
        "status": "PASS" if qualified else "FAIL",
        "cropSafety": deferred_crop_checks(plan["pages"]),
        "safetyStages": stage_assessment(
            sorted(codes), blockers, certificate_issued=qualified
        ),
    }


class ReferenceQualificationService(PrintLayoutService):
    def _fingerprint(self, version: TemplateVersion) -> str:
        identity: dict[str, Any] = {
            "versionId": version.id, "printRevision": self.revision(version),
            "geometryModelVersion": GEOMETRY_MODEL_VERSION,
            "configSha256": hashlib.sha256(self.storage.read_bytes(
                version.config_relative_path)).hexdigest(),
        }
        plan = version.quality_json.get("printLayout", {})
        if plan.get("rendererVersion") == "wsl-print-reference-v1":
            if plan.get("geometryModelVersion") == CALC_GEOMETRY_MODEL:
                identity["calcGeometryImplementation"] = {
                    name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for name, path in {
                        "calc_workbook_geometry": Path(__file__).with_name(
                            "calc_workbook_geometry.py"),
                        "wsl_workbook_geometry": Path(__file__).with_name(
                            "wsl_workbook_geometry.py"),
                        "print_layout_service": Path(__file__).with_name("print_layout_service.py"),
                        "print_layout_source": Path(__file__).with_name("print_layout_source.py"),
                        "spreadsheet_import": Path(__file__).with_name(
                            "spreadsheet_template_import_service.py"),
                        "spreadsheet_preview_geometry": Path(__file__).with_name(
                            "spreadsheet_preview_geometry.py"),
                        "page_authoring_validation": Path(__file__).with_name(
                            "page_authoring_validation.py"),
                        "calc_print_grid": Path(__file__).parents[1] / "domain/calc_print_grid.py",
                        "calc_pdf_coordinates": Path(__file__).parents[1]
                        / "domain/calc_pdf_coordinates.py",
                        "print_layout": Path(__file__).parents[1] / "domain/print_layout.py",
                    }.items()
                }
            identity.update(
                geometryModelVersion=plan.get("geometryModelVersion"),
                qualificationPolicy=WSL_POLICY,
                implementation={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes())
                                .hexdigest() for name in (
                                    "reference_qualification_service.py",
                                    "wsl_environment_evidence.py",
                                    "wsl_host_identity.py",
                                    "wsl_execution_evidence.py",
                                    "prepared_reference_evidence.py", "renderer_wsl_worker.py",
                                    "prepared_reference_replay.py", "renderer_wsl.py",
                                    "prepared_reference_glyphs.py", "renderer_glyph_bridge.py",
                                    "renderer_glyph_worker.py", "renderer_glyph_fonts.py",
                                    "renderer_glyph_pdf.py", "renderer_glyph_font_sources.py",
                                    "reference_geometry.py", "reference_crop_safety.py",
                                    "paged_safety_stages.py",
                                    "prepared_reference_geometry.py", "reference_border_probe.py",
                                    "calc_reference_precision.py",
                                    "reference_vector_measurement.py", "reference_vector_raster.py",
                                    "reference_field_raster.py",
                                    "reference_splash_raster.py", "renderer_splash_calibration.py",
                                    "renderer_vector_bridge.py", "renderer_vector_worker.py",
                                    "renderer_vector_pdf.py", "renderer_pdf_strokes.py",
                                    "renderer_stroke_probe_pdf.py",
                                    "renderer_stroke_probe_pixels.py",
                                    "renderer_stroke_probe_worker.py",
                                    "reference_stroke_differential.py",
                                    "print_xml.py", "preserving_print_workbook.py",
                                    "paged_workbook.py",
                                )},
            )
        return digest(identity)

    def _prepared(self, session: Session, version: TemplateVersion) -> PreparedReference:
        plan = version.quality_json["printLayout"]
        if plan.get("geometryModelVersion") == CALC_GEOMETRY_MODEL:
            # Hash linkage alone cannot establish that saved field coordinates
            # and bindings still derive from the owned source/config. Recompute
            # only this explicitly selected model; never migrate legacy plans.
            try:
                source = self._source(session, version)
                from app.services.calc_workbook_geometry import verify_dimension_observation

                verify_dimension_observation(
                    plan["geometryEvidence"], plan["geometryEvidence"]["importReport"],
                    plan["sheetName"], plan["sourceSha256"])
                request = PrintLayoutRequest.model_validate({
                    **{key: plan[key] for key in (
                        "paperSize", "orientation", "margins",
                        "horizontalCentering", "verticalCentering")},
                    "baseRevision": self.revision(version),
                    "breakAfterRows": [p["endRow"] for p in plan["pages"][:-1]],
                })
                expected = page_plan(source, request, plan)
                keys = ("schemaVersion", "geometryModelVersion", "geometryEvidence", "unit",
                        "dpi", "sourceSha256", "sheetName", "targetPagesWide", "commonScale",
                        "printScalePolicy", "printAdapter")
                saved_pages = [{k: v for k, v in p.items() if k != "referenceImage"}
                               for p in plan["pages"]]
                # The legacy source reader omits this presentation-only false
                # default; the draft reader emits it. Both render identically.
                # Do not write it back or normalize any identity/geometry key.
                def comparable(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
                    return [{**p, "cells": [{"shrinkToFit": False, **c} for c in p["cells"]]}
                            for p in pages]

                if (any(plan.get(key) != expected.get(key) for key in keys)
                        or comparable(saved_pages) != comparable(expected["pages"])):
                    raise ValueError("Saved Calc plan differs from source-derived plan")
            except (AppError, ValueError, KeyError, TypeError) as exc:
                raise print_error(
                    "保存した印刷座標・項目の対応が原本と一致しません。印刷設定を再確定してください。",
                    "PRINT_GEOMETRY_CHANGED", 409,
                ) from exc
        return load_prepared_reference(session, self.storage, version)

    def _reusable_wsl_evidence(
        self,
        session: Session,
        version: TemplateVersion,
        plan: dict[str, Any],
        prepared: PreparedReference,
    ) -> dict[str, Any] | None:
        """Load independently bound WSL diagnostic evidence for formal reevaluation.

        The former report is not treated as a certificate. Its summary flags and
        former qualification fingerprint are ignored; every referenced artifact
        is loaded through its current ownership/hash/content validator.
        """
        record = version.quality_json.get("referenceQualification")
        if not isinstance(record, dict):
            return None
        try:
            payload = self.storage.read_bytes(record["relativePath"])
            if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                return None
            prior = json.loads(payload)
            expected_diagnostics = [
                {"page": page["page"], **page["referenceImage"]}
                for page in plan["pages"]
            ]
            if (
                not isinstance(prior, dict)
                or prior.get("qualificationPolicy")
                not in {"wsl-prepared-reference-v1", WSL_POLICY}
                or prior.get("templateVersionId") != version.id
                or prior.get("sourceSha256") != prepared.evidence["sourceSha256"]
                or prior.get("planSha256") != digest(plan)
                or prior.get("preparedEvidence") != prepared.evidence
                or prior.get("executionEvidence") != [prepared.report]
                or prior.get("referenceSha256")
                != [hashlib.sha256(image).hexdigest() for image in prepared.images]
                or prior.get("diagnosticImages") != expected_diagnostics
                or not all(
                    key in prior
                    for key in (
                        "replayEvidence",
                        "glyphEvidence",
                        "geometryEvidence",
                        "environmentEvidence",
                    )
                )
            ):
                return None
            deterministic = load_reference_replay(
                session,
                self.storage,
                version,
                prepared.report,
                prior["replayEvidence"],
            )
            glyph_report = load_prepared_glyphs(
                session, self.storage, version, prepared, prior["glyphEvidence"]
            )
            field_geometry = load_prepared_geometry(
                session, self.storage, version, prepared, prior["geometryEvidence"]
            )
            lease = environment_evidence.load_lease(
                session,
                self.storage,
                version,
                prepared,
                prior["environmentEvidence"],
            )
            return {
                "deterministic": deterministic,
                "glyphReport": glyph_report,
                "fieldGeometry": field_geometry,
                "environmentLease": lease,
                "replayEvidence": prior["replayEvidence"],
                "glyphEvidence": prior["glyphEvidence"],
                "geometryEvidence": prior["geometryEvidence"],
                "environmentEvidence": prior["environmentEvidence"],
            }
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, AppError):
            return None

    def status(
        self,
        version_id: str,
        *,
        session: Session | None = None,
    ) -> dict[str, Any]:
        # Approval supplies its BEGIN IMMEDIATE session so the exact evidence
        # checked here is the evidence atomically promoted with the DRAFT.  A
        # normal GET retains the same read-only validation in its own session.
        scope = nullcontext(session) if session is not None else self.database.session()
        with scope as session:
            assert session is not None
            version = self._version(session, version_id)
            record = version.quality_json.get("referenceQualification")
            if not isinstance(record, dict) or not record:
                return {"status": "UNVERIFIED", "qualified": False}
            if record.get("fingerprint") != self._fingerprint(version):
                return {"status": "UNVERIFIED", "qualified": False,
                        "codes": ["PRINT_LAYOUT_CHANGED"]}
            try:
                content = self.storage.read_bytes(record["relativePath"])
                if hashlib.sha256(content).hexdigest() != record["sha256"]:
                    raise ValueError("Evidence SHA changed")
                report: dict[str, Any] = json.loads(content)
                plan = version.quality_json["printLayout"]
                is_wsl = plan.get("rendererVersion") == "wsl-print-reference-v1"
                if (not isinstance(report, dict)
                        or report.get("schemaVersion") != (2 if is_wsl else 1)
                        or report["fingerprint"] != record["fingerprint"]):
                    raise ValueError("Evidence fingerprint changed")
                if report["templateVersionId"] != version.id:
                    raise ValueError("Evidence belongs to another version")
                source = session.scalars(select(JobArtifact).where(
                    JobArtifact.job_id == version.source_job_id,
                    JobArtifact.artifact_name == "sourceWorkbook").order_by(
                        JobArtifact.created_at.desc())).first()
                if source is None or hashlib.sha256(self.storage.read_bytes(
                    source.relative_path)).hexdigest() != report["sourceSha256"]:
                    return {"status": "UNVERIFIED", "qualified": False,
                            "codes": ["SOURCE_CHANGED"]}
                if report["planSha256"] != digest(plan):
                    raise ValueError("Print plan changed")
                for page in plan["pages"]:
                    ref = page["referenceImage"]
                    if hashlib.sha256(self.storage.read_bytes(ref["relativePath"])).hexdigest() \
                            != ref["sha256"]:
                        raise ValueError("Reference image changed")
                for ref in report.get("diagnosticImages", []):
                    if hashlib.sha256(self.storage.read_bytes(ref["relativePath"])).hexdigest() \
                            != ref["sha256"]:
                        raise ValueError("Diagnostic image changed")
                if is_wsl:
                    prepared = self._prepared(session, version)
                    if (report.get("qualificationPolicy") != WSL_POLICY
                            or report.get("preparedEvidence") != prepared.evidence
                            or report.get("executionEvidence") != [prepared.report]):
                        raise ValueError("Prepared reference evidence changed")
                    current_revision = self.revision(version)
                    implementation = qualification_implementation_fingerprint()
                    prepared_sha = hashlib.sha256(
                        environment_evidence.canonical(prepared.evidence)
                    ).hexdigest()
                    if (
                        report.get("templateVersionRevision") != current_revision
                        or report.get("qualificationImplementationFingerprint") != implementation
                        or report.get("preparedEvidenceSha256") != prepared_sha
                    ):
                        raise ValueError("Formal qualification binding changed")
                    environment_lease = (
                        environment_evidence.load_lease(
                            session,
                            self.storage,
                            version,
                            prepared,
                            report["environmentEvidence"],
                        )
                        if "environmentEvidence" in report else None
                    )
                    deterministic = (load_reference_replay(
                        session, self.storage, version, prepared.report, report["replayEvidence"])
                        if "replayEvidence" in report else None)
                    glyph_report = (load_prepared_glyphs(
                        session, self.storage, version, prepared, report["glyphEvidence"])
                        if "glyphEvidence" in report else None)
                    field_geometry = (load_prepared_geometry(
                        session, self.storage, version, prepared, report["geometryEvidence"])
                        if "geometryEvidence" in report else None)
                    pages = [
                        measure_page(page, image)
                        for page, image in zip(plan["pages"], prepared.images, strict=True)
                    ]
                    expected_diagnostics = [
                        {"page": page["page"], **page["referenceImage"]}
                        for page in plan["pages"]
                    ]
                    if report.get("diagnosticImages") != expected_diagnostics:
                        raise ValueError("Reference diagnostics changed")
                    derived = derive_wsl_qualification(
                        prepared=prepared,
                        plan=plan,
                        pages=pages,
                        field_geometry=field_geometry,
                        deterministic=deterministic,
                        glyph_report=glyph_report,
                        environment_lease=environment_lease,
                        version_id=version.id,
                        version_revision=current_revision,
                        fingerprint=record["fingerprint"],
                        qualification_issued_at=report.get(
                            "environmentQualification", {}
                        ).get("issuedAt"),
                    )
                    if report.get("qualificationBinding") != derived["qualificationBinding"]:
                        raise ValueError("Formal qualification record changed")
                    record_fields = {
                        "schemaVersion": 2,
                        "qualificationPolicy": WSL_POLICY,
                        "templateVersionRevision": current_revision,
                        "implementationFingerprint": implementation,
                        "preparedEvidenceSha256": prepared_sha,
                        "issuedAt": report.get("issuedAt"),
                        "expiresAt": report.get("expiresAt"),
                    }
                    if any(record.get(key) != value for key, value in record_fields.items()):
                        raise ValueError("Qualification record summary changed")
                    report.update(
                        preparedEvidence=prepared.evidence,
                        executionEvidence=[prepared.report],
                        referenceSha256=[hashlib.sha256(image).hexdigest()
                                         for image in prepared.images],
                        pages=pages,
                        deterministic=deterministic,
                        glyphVerification=glyph_summary(glyph_report),
                        fieldGeometry=field_geometry,
                        **derived,
                    )
                    return report
                if report.get("renderer") and report["renderer"] != renderer_identity():
                    return {"status": "UNVERIFIED", "qualified": False,
                            "codes": ["RENDERER_CHANGED"]}
                environment_ref = report.get("environmentQualification")
                if not isinstance(environment_ref, dict):
                    # A legacy diagnostic can fail while deriving the renderer
                    # environment itself (for example, during workbook
                    # preflight).  Such a report deliberately has no
                    # environment artifact.  Preserve its verified failure
                    # reason instead of misreporting the missing success-only
                    # evidence as REFERENCE_EVIDENCE_CHANGED.
                    if (
                        report.get("status") in {"FAIL", "UNSUPPORTED"}
                        and report.get("qualified") is False
                        and isinstance(report.get("codes"), list)
                        and report["codes"]
                    ):
                        return report
                    raise ValueError("Environment qualification evidence is missing")
                environment_bytes = self.storage.read_bytes(environment_ref["relativePath"])
                if hashlib.sha256(environment_bytes).hexdigest() != environment_ref["sha256"]:
                    raise ValueError("Environment artifact changed")
                environment = json.loads(environment_bytes)
                if environment_ref["fingerprint"] != environment["fingerprint"]:
                    raise ValueError("Environment identity linkage changed")
                current = environment_identity(self.storage.read_bytes(source.relative_path))
                blockers = validate_environment(environment, current)
                for execution in report.get("executionEvidence", []):
                    manifests = [execution["loadedDependencies"]]
                    if execution.get("rasterizer"):
                        manifests.append(execution["rasterizer"]["loadedDependencies"])
                    if not all(validate_manifest(item) for item in manifests):
                        blockers.append("RENDERER_ENVIRONMENT_CHANGED")
                if plan.get("geometryModelVersion") != GEOMETRY_MODEL_VERSION:
                    blockers.append("GEOMETRY_MODEL_CHANGED")
                if "RENDERER_ENVIRONMENT_CHANGED" in blockers:
                    return {"status": "UNVERIFIED", "qualified": False,
                            "codes": ["RENDERER_ENVIRONMENT_CHANGED"]}
                report["productionBlockers"] = blockers
                # No production renderer/security/tolerance has been qualified.
                # A edited JSON qualified flag cannot open this policy boundary.
                report["qualified"] = False
                if report["status"] == "PASS":
                    report["status"] = "UNSUPPORTED"
                return report
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, AppError):
                return {"status": "UNVERIFIED", "qualified": False,
                        "codes": ["REFERENCE_EVIDENCE_CHANGED"]}

    def require_for_activation(
        self,
        session: Session,
        version: TemplateVersion,
        expected_revision: str,
    ) -> dict[str, Any]:
        """Revalidate and require the formal qualification certificate for approval.

        This deliberately calls the same full validator as GET.  The returned
        booleans are accepted only as outputs of that current evidence check;
        no summary stored in ``quality_json`` is an admission input.
        """
        self._check_revision(version, expected_revision)
        report = self.status(version.id, session=session)
        environment = report.get("environmentQualification")
        geometry = report.get("geometryQualification")
        binding = report.get("qualificationBinding")
        activation = (report.get("safetyStages") or {}).get("activation")
        execution = (report.get("safetyStages") or {}).get("execution")
        now = int(time.time())
        issued_at = report.get("issuedAt")
        expires_at = report.get("expiresAt")
        implementation = qualification_implementation_fingerprint()
        environment_implementation = environment_qualification.implementation_fingerprint()
        geometry_implementation = geometry_qualification.implementation_fingerprint()
        valid = (
            report.get("status") == "PASS"
            and report.get("qualificationPolicy") == WSL_POLICY
            and report.get("qualificationImplementationFingerprint")
            == implementation
            and report.get("qualificationIssued") is True
            and report.get("qualified") is True
            and report.get("environmentQualified") is True
            and report.get("geometryQualified") is True
            and report.get("codes") == []
            and report.get("productionBlockers") == []
            and report.get("unresolvedBlockers") == []
            and type(issued_at) is int
            and type(expires_at) is int
            and issued_at <= now < expires_at
            and isinstance(environment, dict)
            and environment.get("policy") == environment_qualification.POLICY
            and environment.get("implementationFingerprint")
            == environment_implementation
            and environment.get("status") == "PASS"
            and environment.get("qualified") is True
            and environment.get("unresolvedBlockers") == []
            and isinstance(geometry, dict)
            and geometry.get("policy") == geometry_qualification.POLICY
            and geometry.get("implementationFingerprint")
            == geometry_implementation
            and geometry.get("status") == "PASS"
            and geometry.get("qualified") is True
            and geometry.get("unresolvedBlockers") == []
            and isinstance(binding, dict)
            and binding.get("templateVersionId") == version.id
            and binding.get("templateVersionRevision") == expected_revision
            and binding.get("qualificationPolicy") == WSL_POLICY
            and binding.get("implementationFingerprint")
            == implementation
            and binding.get("geometryModelVersion") == CALC_GEOMETRY_MODEL
            and activation == {
                "status": "QUALIFIED",
                "codes": [],
                "requiresSubmittedScan": False,
                "certificateIssued": True,
            }
            and execution == {
                "status": "NOT_EVALUATED",
                "requiredBefore": "PROVIDER_REQUEST",
                "checks": list(INPUT_CHECKS),
                "requiresSubmittedScan": True,
            }
        )
        if not valid:
            raise print_error(
                "現在の原本・印刷設定・成果物・実行環境に有効な正式資格がありません。"
                "参照検証を再実行してください。",
                "PAGED_TEMPLATE_QUALIFICATION_INVALID",
                409,
            )
        return report

    def qualify(self, version_id: str, expected_revision: str,
                *, wsl_distribution: str = "Ubuntu-22.04") -> dict[str, Any]:
        reusable: dict[str, Any] | None = None
        prepared_for_reuse: PreparedReference | None = None
        with self.database.session() as session:
            version = self._version(session, version_id)
            require_draft(version)
            self._check_revision(version, expected_revision)
            plan = copy.deepcopy(version.quality_json.get("printLayout"))
            if not plan:
                raise print_error("印刷ページ設定を先に保存してください。", "PRINT_LAYOUT_CHANGED")
            fingerprint = self._fingerprint(version)
            source = session.scalars(select(JobArtifact).where(
                JobArtifact.job_id == version.source_job_id,
                JobArtifact.artifact_name == "sourceWorkbook").order_by(
                    JobArtifact.created_at.desc())).first()
            if source is None:
                raise print_error("元XLSXがありません。", "SOURCE_CHANGED")
            source_path, source_sha = source.relative_path, source.sha256
            job_id = version.source_job_id
            is_wsl = plan.get("rendererVersion") == "wsl-print-reference-v1"
            if is_wsl:
                try:
                    prepared_for_reuse = self._prepared(session, version)
                    reusable = self._reusable_wsl_evidence(
                        session, version, plan, prepared_for_reuse
                    )
                except (OSError, ValueError, KeyError, TypeError, RuntimeError, AppError):
                    prepared_for_reuse = None
        report: dict[str, Any] = {
            "schemaVersion": 2 if is_wsl else 1,
            "templateVersionId": version_id,
            "templateVersionRevision": expected_revision,
            "fingerprint": fingerprint,
            "geometryModelVersion": plan.get("geometryModelVersion", "fixed-7px-plus-5-v1"),
            "geometryImplementationVersion": GEOMETRY_MODEL_VERSION,
            "createdAt": datetime.now(UTC).isoformat(), "qualified": False,
            "status": "UNSUPPORTED", "sourceSha256": source_sha,
            "planSha256": digest(plan), "pageRanges": [p["range"] for p in plan["pages"]],
            "fieldCount": sum(len(p["fields"]) for p in plan["pages"]),
            "renderer": None, "pages": [], "codes": [],
            "productionBlockers": ["NETWORK_ISOLATION_UNVERIFIED",
                                   "PROCESS_RESOURCE_ISOLATION_UNVERIFIED",
                                   "GEOMETRY_TOLERANCE_UNQUALIFIED"],
        }
        images: list[bytes] = []
        replay: ReferenceReplay | None = None
        glyph_report: dict[str, Any] | None = None
        geometry_evidence: GeometryEvidence | None = None
        environment_lease: dict[str, Any] | None = None
        operational_codes: list[str] = []
        try:
            content = self.storage.read_bytes(source_path)
            if hashlib.sha256(content).hexdigest() != source_sha:
                raise print_error("元XLSXのSHAが変わりました。", "SOURCE_CHANGED")
            if is_wsl:
                if prepared_for_reuse is None:
                    with self.database.session() as session:
                        version = self._version(session, version_id)
                        if self._fingerprint(version) != fingerprint:
                            raise print_error(
                                "検証中に設定が変更されました。",
                                "PRINT_LAYOUT_CHANGED",
                                409,
                            )
                        prepared_for_reuse = self._prepared(session, version)
                prepared = prepared_for_reuse
                images = prepared.images
                report.update(
                    qualificationPolicy=WSL_POLICY, preparedEvidence=prepared.evidence,
                    executionEvidence=[prepared.report], environmentQualified=False,
                    productionBlockers=list(WSL_BLOCKERS), deterministic=None,
                    glyphVerification=None, fieldGeometry=None,
                    geometryImplementationVersion=plan["geometryModelVersion"],
                    referenceSha256=[hashlib.sha256(i).hexdigest() for i in images],
                    qualificationImplementationFingerprint=(
                        qualification_implementation_fingerprint()
                    ),
                    preparedEvidenceSha256=hashlib.sha256(
                        environment_evidence.canonical(prepared.evidence)
                    ).hexdigest(),
                )
                if reusable is not None:
                    report.update(
                        deterministic=reusable["deterministic"],
                        fieldGeometry=reusable["fieldGeometry"],
                        replayEvidence=reusable["replayEvidence"],
                        glyphEvidence=reusable["glyphEvidence"],
                        geometryEvidence=reusable["geometryEvidence"],
                        environmentEvidence=reusable["environmentEvidence"],
                        evidenceSource="SAVED_PREPARED_GRAPH",
                    )
                    glyph_report = reusable["glyphReport"]
                    environment_lease = reusable["environmentLease"]
                else:
                    replay = run_reference_replay(prepared, wsl_distribution)
                    if replay is not None:
                        report["deterministic"] = replay.deterministic
                        report["productionBlockers"] = wsl_blockers(replay.deterministic)
                    try:
                        glyph_report = run_prepared_glyphs(prepared, wsl_distribution)
                    except (OSError, ValueError, RuntimeError) as exc:
                        # Preserve other measured evidence, but never certify a failed
                        # glyph run or disclose the helper's raw library/file output.
                        report["glyphDiagnostic"] = {
                            "status": "UNVERIFIED",
                            "code": "FONT_GLYPH_UNVERIFIED",
                            "failureType": type(exc).__name__,
                        }
                    try:
                        candidate = run_prepared_geometry(prepared, plan, wsl_distribution)
                        if candidate is not None:
                            report["fieldGeometry"] = assess_geometry(
                                prepared, plan, candidate
                            )
                            geometry_evidence = candidate
                    except (OSError, ValueError, RuntimeError) as exc:
                        report["geometryDiagnostic"] = {
                            "status": "UNVERIFIED",
                            "code": "REFERENCE_FIELD_EDGES_UNVERIFIED",
                            "failureType": type(exc).__name__,
                        }
                    try:
                        environment_lease = environment_evidence.new_lease(
                            prepared, version_id, wsl_distribution
                        )
                    except (OSError, ValueError, RuntimeError) as exc:
                        report["environmentDiagnostic"] = {
                            "status": "UNVERIFIED",
                            "code": "WSL_ENVIRONMENT_CHANGED",
                            "failureType": type(exc).__name__,
                        }
                        operational_codes.append("WSL_ENVIRONMENT_CHANGED")
                        report["productionBlockers"].append("WSL_ENVIRONMENT_CHANGED")
                report["glyphVerification"] = glyph_summary(glyph_report)
                report["productionBlockers"] = wsl_blockers(
                    report["deterministic"], glyph_report
                )
            else:
                environment = environment_report(environment_identity(content))
                environment_bytes = json.dumps(environment, sort_keys=True).encode()
                environment_path = self.storage.write_bytes(
                    f"renderer-environments/{environment['fingerprint']}/report.json",
                    environment_bytes)
                report["environmentQualification"] = {
                    "relativePath": environment_path,
                    "sha256": hashlib.sha256(environment_bytes).hexdigest(),
                    "fingerprint": environment["fingerprint"], "environmentQualified": False}
                report["productionBlockers"] = environment["codes"]
                if plan.get("geometryModelVersion") != GEOMETRY_MODEL_VERSION:
                    report["productionBlockers"].append("GEOMETRY_MODEL_CHANGED")
                execution: list[dict[str, Any]] = []
                identity, images = render_office(content, plan, execution_metrics=execution)
                report["executionEvidence"] = execution
                repeat_identity, repeated = render_office(content, plan)
                report["renderer"] = identity
                report["referenceSha256"] = [hashlib.sha256(i).hexdigest() for i in images]
                report["deterministic"] = (identity == repeat_identity and
                                           report["referenceSha256"] ==
                                           [hashlib.sha256(i).hexdigest() for i in repeated])
            # Keep the prior raster/anchor diagnostic separate until field-edge
            # visibility and the production tolerance are qualified. Vector
            # observations alone must not erase an existing rejection.
            report["pages"] = [measure_page(p, i) for p, i in
                               zip(plan["pages"], images, strict=True)]
            report["cropSafety"] = (
                deferred_crop_checks(plan["pages"])
                if report.get("qualificationPolicy") == WSL_POLICY else
                [qualify_crop(p, m) for p, m in
                 zip(plan["pages"], report["pages"], strict=True)])
            report["codes"] = sorted({code for p in [*report["pages"],
                                                      *(report.get("fieldGeometry") or [])]
                                       for code in p.get("codes", [p.get("code")]) if code})
            if report["deterministic"] is False:
                report["codes"].append("RENDER_NOT_DETERMINISTIC")
            report["status"] = "FAIL" if report["codes"] else "UNSUPPORTED"
        except AppError as exc:
            if report.get("qualificationPolicy") == WSL_POLICY and exc.code == "RENDERER_BUSY":
                raise
            operational_codes = [exc.code]
            report["codes"] = [exc.code]
            report["detail"] = exc.detail
            report["status"] = "UNSUPPORTED" if exc.code in {
                "UNSUPPORTED_XLSX_FEATURE", "RENDERER_UNAVAILABLE"} else "FAIL"
        except (OSError, ValueError, RuntimeError) as exc:
            # Do not expose file contents, external library messages or paths.
            operational_codes = ["RENDER_FAILED"]
            report["codes"] = ["RENDER_FAILED"]
            report["detail"] = type(exc).__name__
            report["status"] = "FAIL"
        with template_mutation_lock(), self.database.session() as session:
            begin_immediate(session)
            version = self._version(session, version_id)
            require_draft(version)
            self._check_revision(version, expected_revision)
            if self._fingerprint(version) != fingerprint:
                raise print_error("検証中に設定が変更されました。", "PRINT_LAYOUT_CHANGED", 409)
            if hashlib.sha256(self.storage.read_bytes(source_path)).hexdigest() != source_sha:
                raise print_error("検証中に元XLSXが変更されました。", "SOURCE_CHANGED", 409)
            if report.get("qualificationPolicy") == WSL_POLICY:
                try:
                    current = self._prepared(session, version)
                    if (current.evidence != report["preparedEvidence"]
                            or [current.report] != report["executionEvidence"]):
                        raise ValueError("Prepared artifacts changed during measurement")
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise print_error(
                        "検証中に印刷用成果物が変更されました。",
                        "REFERENCE_EVIDENCE_CHANGED", 409,
                    ) from exc
            generation = uuid4().hex
            if replay is not None and reusable is None:
                report["replayEvidence"] = save_reference_replay(
                    session, self.storage, version, generation, replay)
            if glyph_report is not None and reusable is None:
                report["glyphEvidence"] = save_prepared_glyphs(
                    session, self.storage, version, generation, current, glyph_report)
            if geometry_evidence is not None and reusable is None:
                report["geometryEvidence"] = save_prepared_geometry(
                    session, self.storage, version, generation, current, geometry_evidence)
            if environment_lease is not None and reusable is None:
                report["environmentEvidence"] = environment_evidence.save_lease(
                    session, self.storage, version, generation, current, environment_lease)
            if reusable is not None:
                # Recheck immutable artifact ownership/content inside the publish
                # transaction. The live environment identity was checked before
                # the lock and will be checked again by every status read.
                if load_reference_replay(
                    session,
                    self.storage,
                    version,
                    current.report,
                    report["replayEvidence"],
                ) != report["deterministic"]:
                    raise print_error(
                        "検証中に再現性証拠が変更されました。",
                        "REFERENCE_EVIDENCE_CHANGED",
                        409,
                    )
                if load_prepared_glyphs(
                    session,
                    self.storage,
                    version,
                    current,
                    report["glyphEvidence"],
                ) != glyph_report:
                    raise print_error(
                        "検証中に文字証拠が変更されました。",
                        "REFERENCE_EVIDENCE_CHANGED",
                        409,
                    )
                if load_prepared_geometry(
                    session,
                    self.storage,
                    version,
                    current,
                    report["geometryEvidence"],
                ) != report["fieldGeometry"]:
                    raise print_error(
                        "検証中に座標証拠が変更されました。",
                        "REFERENCE_EVIDENCE_CHANGED",
                        409,
                    )
                if environment_evidence.load_saved_lease(
                    session,
                    self.storage,
                    version,
                    current,
                    report["environmentEvidence"],
                ) != environment_lease:
                    raise print_error(
                        "検証中に環境証拠が変更されました。",
                        "REFERENCE_EVIDENCE_CHANGED",
                        409,
                    )
            report["diagnosticImages"] = []
            if report.get("qualificationPolicy") == WSL_POLICY:
                # These are the exact prepared reference images, not a new
                # rendering. Retain their ownership; do not duplicate large PNGs.
                report["diagnosticImages"] = [
                    {"page": p["page"], **p["referenceImage"]} for p in plan["pages"]]
                report.update(derive_wsl_qualification(
                    prepared=current,
                    plan=plan,
                    pages=report["pages"],
                    field_geometry=report.get("fieldGeometry"),
                    deterministic=report.get("deterministic"),
                    glyph_report=glyph_report,
                    environment_lease=environment_lease,
                    version_id=version.id,
                    version_revision=self.revision(version),
                    fingerprint=fingerprint,
                    qualification_issued_at=int(datetime.now(UTC).timestamp()),
                    additional_codes=operational_codes,
                ))
            else:
                for index, image in enumerate(images, 1):
                    path = self.storage.write_bytes(
                        f"artifacts/{job_id}/qualification/{generation}/{index}.png", image)
                    report["diagnosticImages"].append({"page": index, "relativePath": path,
                                                      "sha256": hashlib.sha256(image).hexdigest()})
            payload = json.dumps(report, ensure_ascii=False, sort_keys=True).encode()
            path = self.storage.write_bytes(
                f"artifacts/{job_id}/qualification/{generation}/report.json", payload)
            record: dict[str, Any] = {
                "fingerprint": fingerprint,
                "relativePath": path,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            if report.get("qualificationPolicy") == WSL_POLICY:
                record.update(
                    schemaVersion=2,
                    qualificationPolicy=WSL_POLICY,
                    templateVersionRevision=report["templateVersionRevision"],
                    implementationFingerprint=report[
                        "qualificationImplementationFingerprint"
                    ],
                    preparedEvidenceSha256=report["preparedEvidenceSha256"],
                    issuedAt=report["issuedAt"],
                    expiresAt=report["expiresAt"],
                )
            version.quality_json = {
                **version.quality_json,
                "referenceQualification": record,
            }
            if report.get("qualified") is True:
                # A newly issued full certificate supersedes candidate-only
                # readiness. Failed rechecks must retain the candidate record.
                version.quality_json = {
                    key: value for key, value in version.quality_json.items()
                    if key != "candidateReadiness"
                }
            session.commit()
        return report
