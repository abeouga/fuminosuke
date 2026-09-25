"""Review-only admission bound to prepared pages, separate from certification.

V3 prepares existing owned pages for human review without issuing a formal
geometry/glyph/environment certificate. V1/V2 replay preparation remains for
compatibility. Normal use validates immutable bindings without native inspection.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from contextlib import nullcontext
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.domain.calc_print_grid import MODEL
from app.domain.print_layout import print_error
from app.services import (
    calc_geometry_qualification,
    renderer_wsl,
    wsl_environment_evidence,
    wsl_host_identity,
)
from app.services.calc_geometry_qualification import issue as qualify_geometry
from app.services.office_reference_renderer import _RENDER_LOCK
from app.services.prepared_reference_evidence import (
    PreparedReference,
    _replay_identity,
    compare_reference_replay,
)
from app.services.prepared_reference_geometry import load_prepared_geometry
from app.services.prepared_reference_glyphs import load_prepared_glyphs
from app.services.prepared_reference_replay import (
    ReferenceReplay,
    load_reference_replay,
    save_reference_replay,
)
from app.services.print_layout_service import digest
from app.services.reference_qualification_service import ReferenceQualificationService
from app.services.renderer_process import renderer_mutex
from app.services.template_mutation import begin_immediate, require_draft, template_mutation_lock

LEGACY_POLICY = "paged-candidate-readiness-v1"
POLICY = "paged-candidate-readiness-v2"
REVIEW_POLICY = "paged-candidate-readiness-v3"
RECORD_KEY = "candidateReadiness"
LIFETIME_SECONDS = 24 * 60 * 60
LEGACY_IMPLEMENTATION_SHA256 = "2ecb79db9e61f0d1145bf566cd5846a82e24b7771079af3b65f4321e122304f3"
# Bump POLICY and this marker together when the persisted admission semantics change.
ADMISSION_IMPLEMENTATION = "immutable-candidate-admission-v2"


def compare_candidate_manifests(
    original: dict[str, Any], replay: dict[str, Any]
) -> dict[str, Any]:
    """Compare saved execution and ordered page manifests without raster work."""
    identities = [copy.deepcopy(_replay_identity(report)) for report in (original, replay)]
    native = [
        identity["bootstrapPythonIdentity"].pop("nativeDependencies")
        for identity in identities
    ]
    if identities[0] != identities[1]:
        raise ValueError("Candidate replay changed more than bootstrap native inventory")
    for report in (original, replay):
        count = report.get("pageCount")
        hashes = report.get("imageSha256")
        sizes = report.get("imageSizes")
        if (
            type(count) is not int
            or not 1 <= count <= 50
            or not isinstance(hashes, list)
            or len(hashes) != count
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in hashes
            )
            or not isinstance(sizes, list)
            or len(sizes) != count
            or any(
                not isinstance(size, list)
                or len(size) != 2
                or any(type(value) is not int or value <= 0 for value in size)
                or size[0] * size[1] > 20_000_000
                for size in sizes
            )
        ):
            raise ValueError("Invalid candidate page manifest")
    if (
        original["imageSha256"] != replay["imageSha256"]
        or original["imageSizes"] != replay["imageSizes"]
        or original["pageCount"] != replay["pageCount"]
    ):
        raise ValueError("Candidate replay changed reference pixels or page order")
    return {
        "scope": "bootstrap-native-inventory",
        "changed": native[0] != native[1],
        "prepared": native[0],
        "replayed": native[1],
    }


def compare_candidate_replay(
    original: dict[str, Any], replay: dict[str, Any], images: list[bytes]
) -> dict[str, Any]:
    """Validate both executions, then compare all ordered reference pixels.

    Native identities are retained as a difference record, not overwritten with
    the former value. Each renderer invocation still enforces its own complete
    before/after inventory, root ownership and isolation checks.
    """
    difference = compare_candidate_manifests(original, replay)
    # Self-comparison validates report/image correspondence without pretending
    # that historical and current execution environments were identical.
    if not compare_reference_replay(replay, replay, images):
        raise ValueError("Candidate replay changed reference pixels or page order")
    return difference


def run_candidate_replay(prepared: PreparedReference, distribution: str) -> ReferenceReplay:
    expected = _replay_identity(prepared.report)
    if not _RENDER_LOCK.acquire(blocking=False):
        raise print_error("別の印刷検証を実行中です。", "RENDERER_BUSY", 409)
    try:
        with renderer_mutex():
            _, images, report = renderer_wsl.render_wsl_reference(
                prepared.workbook,
                distribution=distribution,
                runtime_id=expected["runtimeImage"]["sha256"],
            )
    finally:
        _RENDER_LOCK.release()
    compare_candidate_replay(prepared.report, report, images)
    return ReferenceReplay(report, images, True)


class CandidateReadinessService(ReferenceQualificationService):
    def _candidate_fingerprint(
        self, version: TemplateVersion, policy: str = POLICY
    ) -> str:
        if policy == REVIEW_POLICY:
            return digest({
                "policy": REVIEW_POLICY,
                "revision": self.revision(version),
                "configSha256": hashlib.sha256(
                    self.storage.read_bytes(version.config_relative_path)
                ).hexdigest(),
                "implementation": "prepared-pages-human-review-v1",
            })
        if policy not in {POLICY, LEGACY_POLICY}:
            raise ValueError("Unsupported candidate policy")
        return digest(
            {
                "policy": policy,
                "reference": self._fingerprint(version),
                "geometryIssuer": calc_geometry_qualification.implementation_fingerprint(),
                "implementation": (
                    LEGACY_IMPLEMENTATION_SHA256
                    if policy == LEGACY_POLICY
                    else ADMISSION_IMPLEMENTATION
                ),
            }
        )

    def _basis(
        self,
        session: Session,
        version: TemplateVersion,
        prepared: PreparedReference,
        refs: dict[str, Any],
    ) -> dict[str, Any]:
        # These loaders recompute from owned artifacts, including helper code
        # identity and all field edges. Historical PASS flags are not inputs.
        glyphs = load_prepared_glyphs(
            session, self.storage, version, prepared, refs["glyphEvidence"]
        )
        if glyphs is None or glyphs.get("allMatchedFacesDeclared") is not True:
            raise ValueError("Candidate glyph evidence is incomplete")
        fields = load_prepared_geometry(
            session, self.storage, version, prepared, refs["geometryEvidence"]
        )
        geometry = qualify_geometry(
            prepared,
            version.quality_json["printLayout"],
            fields,
            version.id,
            self._candidate_fingerprint(version),
        )
        if geometry.get("qualified") is not True or geometry.get("unresolvedBlockers") != []:
            raise ValueError("Candidate geometry is not qualified")
        return geometry

    def _preparation_environment(self, record: dict[str, Any]) -> None:
        issued, expires = record["issuedAt"], record["expiresAt"]
        if (
            type(issued) is not int
            or type(expires) is not int
            or expires != issued + LIFETIME_SECONDS
            or not issued <= time.time() < expires
        ):
            raise ValueError("Candidate evidence has expired")
        host = record["hostIdentity"]
        wsl_host_identity.validate(host)
        if wsl_host_identity.current() != host:
            raise ValueError("Candidate host changed")
        identity = wsl_environment_evidence.identity(record["replayEvidence"]["report"])
        if (
            wsl_environment_evidence.inspect_current(
                identity["runtimeImage"]["sha256"],
                record["distribution"],
            )
            != identity
        ):
            raise ValueError("Candidate replay environment changed")
        if wsl_host_identity.current() != host or time.time() >= expires:
            raise ValueError("Candidate evidence changed during validation")

    def _validate_preparation(
        self, session: Session, version: TemplateVersion, report: dict[str, Any]
    ) -> None:
        if (
            report.get("policy") != POLICY
            or report.get("templateVersionId") != version.id
            or report.get("templateVersionRevision") != self.revision(version)
            or report.get("fingerprint") != self._candidate_fingerprint(version, POLICY)
        ):
            raise ValueError("Candidate version or implementation changed")
        prepared = self._prepared(session, version)
        if (
            prepared.evidence != report["preparedEvidence"]
            or prepared.report != report.get("preparedExecutionEvidence")
        ):
            raise ValueError("Candidate prepared graph changed")
        geometry = self._basis(session, version, prepared, report)
        replay = report["replayEvidence"]
        if not load_reference_replay(session, self.storage, version, replay["report"], replay):
            raise ValueError("Candidate replay artifacts changed")
        difference = compare_candidate_replay(prepared.report, replay["report"], prepared.images)
        if difference != report["environmentDifference"]:
            raise ValueError("Candidate difference record changed")
        self._preparation_environment(report)
        if geometry.get("qualified") is not True:
            raise ValueError("Candidate geometry is not qualified")

    @staticmethod
    def _artifact_metadata(
        session: Session,
        version: TemplateVersion,
        ref: dict[str, Any],
        *,
        media_type: str | None = None,
        artifact_name: str | None = None,
        label: str = "artifact",
    ) -> JobArtifact:
        if not isinstance(ref, dict):
            raise ValueError("Invalid candidate artifact reference")
        artifact_id = ref.get("artifactId")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError(f"Candidate {label} ID is missing")
        item = session.get(JobArtifact, artifact_id)
        if (
            item is None
            or item.job_id != version.source_job_id
            or item.sha256 != ref.get("sha256")
            or ("relativePath" in ref and item.relative_path != ref["relativePath"])
            or ("name" in ref and item.artifact_name != ref["name"])
            or ("mediaType" in ref and item.media_type != ref["mediaType"])
            or ("attemptNumber" in ref and item.attempt_number != ref["attemptNumber"])
            or ("width" in ref and item.width != ref["width"])
            or ("height" in ref and item.height != ref["height"])
            or (media_type is not None and item.media_type != media_type)
            or (artifact_name is not None and item.artifact_name != artifact_name)
        ):
            raise ValueError(f"Candidate {label} ownership changed")
        return item

    def _validate_use_manifest(
        self, session: Session, version: TemplateVersion, report: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate current immutable bindings without native/helper re-execution."""
        source_policy = report.get("policy")
        if (
            source_policy not in {POLICY, LEGACY_POLICY, REVIEW_POLICY}
            or report.get("templateVersionId") != version.id
            or report.get("templateVersionRevision") != self.revision(version)
            or report.get("fingerprint")
            != self._candidate_fingerprint(version, source_policy)
        ):
            raise ValueError("Candidate version or implementation changed")
        issued = report.get("issuedAt")
        observed_until = report.get("expiresAt")
        if (
            type(issued) is not int
            or (source_policy != REVIEW_POLICY and (
                type(observed_until) is not int
                or observed_until != issued + LIFETIME_SECONDS
            ))
            or time.time() < issued
        ):
            raise ValueError("Invalid candidate preparation time")
        if source_policy != REVIEW_POLICY:
            wsl_host_identity.validate(report.get("hostIdentity"))

        plan = version.quality_json.get("printLayout")
        prepared = report.get("preparedEvidence")
        if not isinstance(plan, dict) or not isinstance(prepared, dict):
            raise ValueError("Candidate print plan is missing")
        expected_prepared = {
            "policy": "wsl-prepared-artifact-graph-v1",
            "planSha256": digest(plan),
            "sourceArtifactId": prepared.get("sourceArtifactId"),
            "sourceSha256": plan.get("sourceSha256"),
            "importReportSha256": digest(plan.get("geometryEvidence", {}).get("importReport")),
            "printFiles": copy.deepcopy(plan.get("printFiles")),
            "pages": [
                {"page": page["page"], "referenceImage": copy.deepcopy(page["referenceImage"])}
                for page in plan.get("pages", [])
            ],
        }
        if prepared != expected_prepared:
            raise ValueError("Candidate prepared graph changed")
        source = self._artifact_metadata(
            session,
            version,
            {"artifactId": prepared["sourceArtifactId"], "sha256": prepared["sourceSha256"]},
            artifact_name="sourceWorkbook",
            label="source workbook",
        )
        current_source = session.scalars(
            select(JobArtifact)
            .where(
                JobArtifact.job_id == version.source_job_id,
                JobArtifact.artifact_name == "sourceWorkbook",
            )
            .order_by(JobArtifact.created_at.desc())
        ).first()
        if current_source is None or current_source.id != source.id:
            raise ValueError("Candidate source workbook binding changed")
        source_content = self.storage.read_bytes(source.relative_path)
        if (
            not 0 < len(source_content) <= 64 * 1024 * 1024
            or hashlib.sha256(source_content).hexdigest() != source.sha256
        ):
            raise ValueError("Candidate source workbook changed")

        files = prepared["printFiles"]
        for key, media_type in (
            ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("pdf", "application/pdf"),
        ):
            self._artifact_metadata(
                session, version, files[key], media_type=media_type, label=f"print {key}"
            )
        renderer_item = self._artifact_metadata(
            session, version, files["report"], media_type="application/json",
            label="renderer report",
        )
        renderer_content = self.storage.read_bytes(renderer_item.relative_path)
        if (
            not 0 < len(renderer_content) <= 1024 * 1024
            or hashlib.sha256(renderer_content).hexdigest() != renderer_item.sha256
        ):
            raise ValueError("Candidate renderer report changed")
        original_report = json.loads(renderer_content)
        page_refs = prepared["pages"]
        expected_hashes = [page["referenceImage"]["sha256"] for page in page_refs]
        expected_sizes = [[page["width"], page["height"]] for page in plan["pages"]]
        if (
            not isinstance(original_report, dict)
            or original_report.get("sourceSha256") != files["xlsx"]["sha256"]
            or original_report.get("imageSha256") != expected_hashes
            or original_report.get("imageSizes") != expected_sizes
        ):
            raise ValueError("Candidate renderer binding changed")
        for page in page_refs:
            self._artifact_metadata(
                session, version, page["referenceImage"], media_type="image/png",
                label="reference image",
            )

        if source_policy == REVIEW_POLICY:
            if original_report != report.get("preparedExecutionEvidence"):
                raise ValueError("Candidate prepared execution changed")
            _replay_identity(original_report)
            return self._use_result(version, report)

        glyph = report.get("glyphEvidence")
        geometry = report.get("geometryEvidence")
        replay = report.get("replayEvidence")
        if (
            not isinstance(glyph, dict)
            or not isinstance(geometry, dict)
            or not isinstance(geometry.get("artifacts"), list)
            or not geometry["artifacts"]
            or not isinstance(replay, dict)
            or not isinstance(replay.get("report"), dict)
            or not isinstance(replay.get("images"), list)
        ):
            raise ValueError("Candidate evidence manifest is incomplete")
        self._artifact_metadata(
            session, version, glyph, media_type="application/json", label="glyph evidence"
        )
        prepared_report = report.get("preparedExecutionEvidence")
        if prepared_report is None and source_policy == LEGACY_POLICY:
            prepared_report = copy.deepcopy(replay["report"])
            prepared_report["bootstrapPythonIdentity"]["nativeDependencies"] = copy.deepcopy(
                report["environmentDifference"]["prepared"]
            )
        if (
            not isinstance(prepared_report, dict)
            or prepared_report.get("sourceSha256") != files["xlsx"]["sha256"]
            or prepared_report.get("imageSha256") != expected_hashes
            or prepared_report.get("imageSizes") != expected_sizes
        ):
            raise ValueError("Candidate prepared execution binding changed")
        for ref in geometry["artifacts"]:
            self._artifact_metadata(session, version, ref, label="geometry evidence")
        for ref in replay["images"]:
            self._artifact_metadata(
                session, version, ref, media_type="image/png", label="replay image"
            )
        difference = compare_candidate_manifests(prepared_report, replay["report"])
        if difference != report.get("environmentDifference"):
            raise ValueError("Candidate difference record changed")
        return self._use_result(version, report, difference)

    def _use_result(
        self,
        version: TemplateVersion,
        report: dict[str, Any],
        difference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prepared = report["preparedEvidence"]
        return {
            "status": "CANDIDATE_READY",
            "qualified": False,
            "qualificationIssued": False,
            "reviewRequired": True,
            "qualificationPolicy": REVIEW_POLICY if report["policy"] == REVIEW_POLICY else POLICY,
            "sourceQualificationPolicy": report["policy"],
            "validUntilChange": True,
            "qualificationBinding": {
                "templateVersionId": version.id,
                "templateVersionRevision": self.revision(version),
                "preparedEvidenceSha256": digest(prepared),
                "candidateEvidenceSha256": digest(report),
            },
            "templateVersionRevision": self.revision(version),
            "issuedAt": report["issuedAt"],
            "expiresAt": None,
            "preparationEvidenceExpiresAt": report["expiresAt"],
            "geometryQualified": report["policy"] != REVIEW_POLICY,
            "pageBindingsVerified": True,
            "environmentDifference": difference or report.get("environmentDifference"),
            "codes": [],
            "productionBlockers": [],
            "unresolvedBlockers": [],
        }

    def status_for_use(self, version_id: str, *, session: Session | None = None) -> dict[str, Any]:
        scope = nullcontext(session) if session is not None else self.database.session()
        with scope as db:
            assert db is not None
            version = self._version(db, version_id)
            ref = version.quality_json.get(RECORD_KEY)
            if ref is None:
                return super().status(version_id, session=db)
            try:
                item = db.get(JobArtifact, ref["artifactId"])
                if (
                    item is None
                    or item.job_id != version.source_job_id
                    or item.sha256 != ref["sha256"]
                    or item.relative_path != ref["relativePath"]
                    or item.media_type != "application/json"
                ):
                    raise ValueError("Candidate record owner changed")
                payload = self.storage.read_bytes(item.relative_path)
                if (
                    len(payload) > 4 * 1024 * 1024
                    or hashlib.sha256(payload).hexdigest() != item.sha256
                ):
                    raise ValueError("Candidate record changed")
                return self._validate_use_manifest(db, version, json.loads(payload))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, AppError):
                return {
                    "status": "UNVERIFIED",
                    "qualified": False,
                    "codes": ["CANDIDATE_EVIDENCE_CHANGED"],
                }

    def prepare_for_ocr(self, version_id: str, expected_revision: str) -> dict[str, Any]:
        """Bind existing printed pages for human review; no qualification/replay."""
        with template_mutation_lock(), self.database.session() as db:
            begin_immediate(db)
            version = self._version(db, version_id)
            require_draft(version)
            self._check_revision(version, expected_revision)
            if version.quality_json.get("printLayout", {}).get("geometryModelVersion") != MODEL:
                raise print_error("印刷見本を作成してから続けてください。",
                                  "CANDIDATE_PREPARATION_REQUIRED", 409)
            try:
                # Reuse the local source/plan/owned-artifact checks. These do not
                # launch Office, inspect the current host or certify precision.
                prepared = self._prepared(db, version)
                _replay_identity(prepared.report)
                from app.domain.template_config import parse_template_config
                from app.services.job_page_service import page_config

                config = parse_template_config(
                    self.storage.read_bytes(version.config_relative_path).decode())
                mappings = [m.mapping_id
                            for p in version.quality_json["printLayout"]["pages"]
                            for m in page_config(config, p).field_mappings]
                if len(mappings) != len(set(mappings)) or set(mappings) != {
                    m.mapping_id for m in config.field_mappings if m.enabled
                }:
                    raise ValueError("Page field bindings are incomplete")
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                raise print_error(
                    "保存したページと読み取る欄の対応を確認できません。"
                    "印刷見本を作り直してください。",
                    "CANDIDATE_EVIDENCE_CHANGED", 409,
                ) from exc
            report = {
                "policy": REVIEW_POLICY,
                "templateVersionId": version.id,
                "templateVersionRevision": expected_revision,
                "fingerprint": self._candidate_fingerprint(version, REVIEW_POLICY),
                "preparedEvidence": prepared.evidence,
                "preparedExecutionEvidence": prepared.report,
                "issuedAt": int(time.time()),
                "expiresAt": None,
            }
            result = self._validate_use_manifest(db, version, report)
            generation = uuid4().hex
            payload = wsl_environment_evidence.canonical(report)
            path = self.storage.write_bytes(
                f"artifacts/{version.source_job_id}/qualification/{generation}/candidate.json",
                payload,
            )
            job = db.get(ProcessingJob, version.source_job_id)
            if job is None:
                raise ValueError("Template source job is missing")
            artifact = JobArtifact(
                id=str(uuid4()), job_id=job.id, attempt_number=job.attempt_number,
                artifact_name=f"candidate-{generation}", relative_path=path,
                sha256=hashlib.sha256(payload).hexdigest(), media_type="application/json",
            )
            db.add(artifact)
            version.quality_json = {**version.quality_json, RECORD_KEY: {
                "artifactId": artifact.id, "relativePath": path, "sha256": artifact.sha256,
            }}
            db.commit()
            return result

    def prepare_candidate(
        self, version_id: str, expected_revision: str, distribution: str = "Ubuntu-22.04"
    ) -> dict[str, Any]:
        with self.database.session() as db:
            version = self._version(db, version_id)
            require_draft(version)
            self._check_revision(version, expected_revision)
            if version.quality_json.get("printLayout", {}).get("geometryModelVersion") != MODEL:
                raise print_error(
                    "正式な印刷用ファイルを先に準備してください。",
                    "CANDIDATE_PREPARATION_REQUIRED",
                    409,
                )
            prepared = self._prepared(db, version)
            fingerprint = self._candidate_fingerprint(version, POLICY)
            # Reuse only validated, independently owned geometry/glyph evidence.
            # An expired formal environment lease does not expire these pixels.
            ref = version.quality_json.get("referenceQualification", {})
            try:
                payload = self.storage.read_bytes(ref["relativePath"])
                if hashlib.sha256(payload).hexdigest() != ref["sha256"]:
                    raise ValueError("Qualification artifact changed")
                prior = json.loads(payload)
                if (
                    prior["templateVersionId"] != version_id
                    or prior["preparedEvidence"] != prepared.evidence
                ):
                    raise ValueError("Qualification graph changed")
                refs = {key: prior[key] for key in ("glyphEvidence", "geometryEvidence")}
                self._basis(db, version, prepared, refs)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                raise print_error(
                    "座標・文字の検証証拠が不足しています。先に正式資格を確認してください。",
                    "CANDIDATE_GEOMETRY_REQUIRED",
                    409,
                ) from exc
        host = wsl_host_identity.current()
        try:
            replay = run_candidate_replay(prepared, distribution)
            difference = compare_candidate_replay(prepared.report, replay.report, replay.images)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            raise print_error(
                "再描画・座標・隔離条件の一致を確認できません。", "CANDIDATE_REPLAY_FAILED", 409
            ) from exc
        issued = int(time.time())
        report = {
            "policy": POLICY,
            "templateVersionId": version_id,
            "templateVersionRevision": expected_revision,
            "fingerprint": fingerprint,
            "preparedEvidence": prepared.evidence,
            "preparedExecutionEvidence": prepared.report,
            **refs,
            "environmentDifference": difference,
            "hostIdentity": host,
            "distribution": distribution,
            "issuedAt": issued,
            "expiresAt": issued + LIFETIME_SECONDS,
            "replayEvidence": {"report": replay.report},
        }
        try:
            self._preparation_environment(report)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            raise print_error(
                "再描画中に実行環境が変わりました。再確認してください。",
                "CANDIDATE_EVIDENCE_CHANGED",
                409,
            ) from exc
        with template_mutation_lock(), self.database.session() as db:
            begin_immediate(db)
            version = self._version(db, version_id)
            require_draft(version)
            self._check_revision(version, expected_revision)
            generation = uuid4().hex
            report["replayEvidence"] = save_reference_replay(
                db, self.storage, version, generation, replay
            )
            db.flush()
            try:
                self._validate_preparation(db, version, report)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                raise print_error(
                    "検証中に対象の版・証拠が変わりました。再確認してください。",
                    "CANDIDATE_EVIDENCE_CHANGED",
                    409,
                ) from exc
            job = db.get(ProcessingJob, version.source_job_id)
            assert job is not None
            payload = wsl_environment_evidence.canonical(report)
            path = self.storage.write_bytes(
                f"artifacts/{job.id}/qualification/{generation}/candidate.json", payload
            )
            artifact = JobArtifact(
                id=str(uuid4()),
                job_id=job.id,
                attempt_number=job.attempt_number,
                artifact_name=f"candidate-{generation}",
                relative_path=path,
                sha256=hashlib.sha256(payload).hexdigest(),
                media_type="application/json",
            )
            db.add(artifact)
            version.quality_json = {
                **version.quality_json,
                RECORD_KEY: {
                    "artifactId": artifact.id,
                    "relativePath": path,
                    "sha256": artifact.sha256,
                    "preparationEvidenceExpiresAt": report["expiresAt"],
                },
            }
            db.commit()
            result = self._use_result(version, report)
        return result


def require_for_use(
    service: ReferenceQualificationService,
    session: Session,
    version: TemplateVersion,
    expected_revision: str,
) -> dict[str, Any]:
    if RECORD_KEY not in version.quality_json:
        return service.require_for_activation(session, version, expected_revision)
    service._check_revision(version, expected_revision)
    report = CandidateReadinessService(service.database, service.storage).status_for_use(
        version.id, session=session
    )
    if report.get("status") != "CANDIDATE_READY":
        raise print_error(
            "候補OCRの利用条件が変わりました。準備を再確認してください。",
            "PAGED_TEMPLATE_QUALIFICATION_INVALID",
            409,
        )
    return report
