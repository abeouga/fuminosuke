"""Version-bound diagnostic evidence, deliberately fail-closed for production."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import JobArtifact, TemplateVersion
from app.domain.print_layout import print_error
from app.services.office_reference_renderer import render_office, renderer_identity
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


class ReferenceQualificationService(PrintLayoutService):
    def _fingerprint(self, version: TemplateVersion) -> str:
        return digest({"versionId": version.id, "printRevision": self.revision(version),
                       "geometryModelVersion": GEOMETRY_MODEL_VERSION,
                       "configSha256": hashlib.sha256(self.storage.read_bytes(
                           version.config_relative_path)).hexdigest()})

    def status(self, version_id: str) -> dict[str, Any]:
        with self.database.session() as session:
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
                if (not isinstance(report, dict) or report.get("schemaVersion") != 1
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
                plan = version.quality_json["printLayout"]
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
                if report.get("renderer") and report["renderer"] != renderer_identity():
                    return {"status": "UNVERIFIED", "qualified": False,
                            "codes": ["RENDERER_CHANGED"]}
                environment_ref = report["environmentQualification"]
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
            except (OSError, ValueError, KeyError, TypeError, AppError):
                return {"status": "UNVERIFIED", "qualified": False,
                        "codes": ["REFERENCE_EVIDENCE_CHANGED"]}

    def qualify(self, version_id: str, expected_revision: str) -> dict[str, Any]:
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
        report: dict[str, Any] = {
            "schemaVersion": 1, "templateVersionId": version_id, "fingerprint": fingerprint,
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
        try:
            content = self.storage.read_bytes(source_path)
            if hashlib.sha256(content).hexdigest() != source_sha:
                raise print_error("元XLSXのSHAが変わりました。", "SOURCE_CHANGED")
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
            report["pages"] = [measure_page(p, i) for p, i in
                               zip(plan["pages"], images, strict=True)]
            report["cropSafety"] = [qualify_crop(p, m) for p, m in
                                    zip(plan["pages"], report["pages"], strict=True)]
            report["codes"] = sorted({code for p in report["pages"]
                                       for code in p.get("codes", [p.get("code")]) if code})
            if not report["deterministic"]:
                report["codes"].append("RENDER_NOT_DETERMINISTIC")
            report["status"] = "FAIL" if report["codes"] else "UNSUPPORTED"
        except AppError as exc:
            report["codes"] = [exc.code]
            report["detail"] = exc.detail
            report["status"] = "UNSUPPORTED" if exc.code in {
                "UNSUPPORTED_XLSX_FEATURE", "RENDERER_UNAVAILABLE"} else "FAIL"
        except (OSError, ValueError, RuntimeError) as exc:
            # Do not expose file contents, external library messages or paths.
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
            generation = uuid4().hex
            report["diagnosticImages"] = []
            for index, image in enumerate(images, 1):
                path = self.storage.write_bytes(
                    f"artifacts/{job_id}/qualification/{generation}/{index}.png", image)
                report["diagnosticImages"].append({"page": index, "relativePath": path,
                                                  "sha256": hashlib.sha256(image).hexdigest()})
            payload = json.dumps(report, ensure_ascii=False, sort_keys=True).encode()
            path = self.storage.write_bytes(
                f"artifacts/{job_id}/qualification/{generation}/report.json", payload)
            version.quality_json = {**version.quality_json, "referenceQualification": {
                "fingerprint": fingerprint, "relativePath": path,
                "sha256": hashlib.sha256(payload).hexdigest()}}
            session.commit()
        return report
