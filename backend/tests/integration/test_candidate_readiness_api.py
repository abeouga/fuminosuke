"""Ownership/revision/admission wiring with synthetic renderer evidence."""

import copy
import hashlib
import json
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import select

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.domain.template_config import FieldMappingConfig, parse_template_config
from app.services import candidate_readiness_service as candidate
from app.services.reference_qualification_service import ReferenceQualificationService
from tests.integration.test_calc_qualification_plan import (  # noqa: F401
    _candidate_submission,
    calc_fixture,
    calc_reference,
    candidate_calc_activation,
    prepared_fixture,
    print_template,
    qualified_calc_activation,
    saved_template,
)
from tests.unit.test_candidate_readiness import candidate_sample
from tests.unit.test_wsl_environment_qualification import sample_host


@pytest.fixture
def reviewed_pages(client, request, monkeypatch):
    """Prepare two mapped pages, with no formal qualification at any point."""
    from app.services import print_preparation_service as preparation
    from tests.integration import test_print_layout_api
    from tests.unit.test_print_layout import workbook_bytes

    content = workbook_bytes(rows=33, manual=9)
    monkeypatch.setattr(test_print_layout_api, "workbook_bytes", lambda: content)
    version_id = request.getfixturevalue("print_template")[0]
    with client.app.state.database.session() as db:
        version = db.get(TemplateVersion, version_id)
        config = parse_template_config(
            client.app.state.storage.read_bytes(version.config_relative_path).decode())
        cell = next(c for c in version.layout_json["cells"] if c["id"] == "xlsx_C2")
        config.field_mappings.append(FieldMappingConfig(
            mapping_id="layout_xlsx_C2", target_path="document.cattleName", label="Name",
            **{k: cell[k] for k in ("x", "y", "width", "height")},
        ))
        client.app.state.storage.write_text(version.config_relative_path,
            yaml.safe_dump(config.model_dump(mode="json", by_alias=True)))
    request.getfixturevalue("calc_fixture")
    original_render = preparation.render_wsl_reference

    def rendered(*args, **kwargs):
        pdf, images, report = original_render(*args, **kwargs)
        return pdf, images, {**report, "rasterizer": "poppler-pdftoppm"}

    monkeypatch.setattr(preparation, "render_wsl_reference", rendered)
    return request.getfixturevalue("calc_reference")


def test_reviewed_pages_activate_and_accept_without_formal_or_native_replay(
    client, reviewed_pages, monkeypatch,
):
    version_id, url, ready, measurements = reviewed_pages

    def no_native(*args, **kwargs):
        pytest.fail("Normal preparation/activation/admission must not run native inspection")

    monkeypatch.setattr(candidate, "run_candidate_replay", no_native)
    monkeypatch.setattr(candidate.wsl_host_identity, "current", no_native)
    monkeypatch.setattr(candidate.wsl_environment_evidence, "inspect_current", no_native)
    with client.app.state.database.session() as db:
        version = db.get(TemplateVersion, version_id)
        before = copy.deepcopy(version.quality_json)
        layout_before = copy.deepcopy(version.layout_json)
        config_before = client.app.state.storage.read_bytes(version.config_relative_path)
        assert "referenceQualification" not in before
    response = client.post(url + "/ocr-readiness", params={"base_revision": ready["revision"]})
    assert response.status_code == 200, response.text
    assert response.json()["qualificationPolicy"] == candidate.REVIEW_POLICY
    assert response.json()["pageBindingsVerified"] is True
    assert response.json()["geometryQualified"] is False
    assert response.json()["qualified"] is False
    assert response.json()["reviewRequired"] is True
    assert client.get(url + "/qualification").json() == response.json()
    approval = client.post(f"/api/v1/template-versions/{version_id}/approve",
                           params={"base_revision": ready["revision"]})
    assert approval.status_code == 200, approval.text
    jobs, catalog, submission, uploads = _candidate_submission(client, version_id)
    assert catalog["available"] is True
    job = jobs.accept(submission, uploads)
    assert job.status == "QUEUED"
    with client.app.state.database.session() as db:
        version = db.get(TemplateVersion, version_id)
        assert version.layout_json == layout_before
        assert client.app.state.storage.read_bytes(version.config_relative_path) == config_before
        assert {k: v for k, v in version.quality_json.items()
                if k != "candidateReadiness"} == before
        assert len(jobs.validate(db, job)) == 2
    assert measurements == []


@pytest.mark.parametrize("change", ["revision", "source", "reference", "binding"])
def test_reviewed_pages_reject_changed_source_or_bindings(client, reviewed_pages, change):
    version_id, url, ready, measurements = reviewed_pages
    revision = ready["revision"]
    with client.app.state.database.session() as db:
        version = db.get(TemplateVersion, version_id)
        before = copy.deepcopy(version.quality_json)
        if change == "revision":
            revision = "0" * 64
        elif change == "source":
            source = db.scalar(select(JobArtifact).where(
                JobArtifact.job_id == version.source_job_id,
                JobArtifact.artifact_name == "sourceWorkbook"))
            client.app.state.storage.write_bytes(source.relative_path, b"changed")
        elif change == "reference":
            ref = before["printLayout"]["pages"][0]["referenceImage"]
            client.app.state.storage.write_bytes(ref["relativePath"], b"changed")
        else:
            quality = copy.deepcopy(before)
            field = next(f for p in quality["printLayout"]["pages"]
                         for f in p["fields"] if f["bindings"])
            field["bindings"][0]["mappingId"] = "wrong-cell"
            version.quality_json = quality
            db.commit()
            revision = candidate.CandidateReadinessService.revision(version)
    response = client.post(url + "/ocr-readiness", params={"base_revision": revision})
    assert response.status_code == 409, response.text
    with client.app.state.database.session() as db:
        version = db.get(TemplateVersion, version_id)
        assert version.status == "DRAFT"
        assert "candidateReadiness" not in version.quality_json
    assert measurements == []


@pytest.fixture
def candidate_fixture(client, request, monkeypatch):
    version, url, ready, _ = request.getfixturevalue("candidate_calc_activation")
    original_prepared = candidate.CandidateReadinessService._prepared

    # Test-only native reports; the original source/plan/artifact validator still
    # runs, and geometry/glyph issuers are the existing fixture's native stubs.
    def prepared(self, session, record):
        value = original_prepared(self, session, record)
        native, _ = candidate_sample()
        value.report = {
            **value.report,
            **native,
            "sourceSha256": hashlib.sha256(value.workbook).hexdigest(),
            "pageCount": len(value.images),
            "imageSha256": [hashlib.sha256(i).hexdigest() for i in value.images],
            "imageSizes": [
                [p["width"], p["height"]] for p in record.quality_json["printLayout"]["pages"]
            ],
        }
        return value

    monkeypatch.setattr(candidate.CandidateReadinessService, "_prepared", prepared)
    # Patch the imported native evidence readers, not the candidate validator.
    from app.services import reference_qualification_service as formal

    monkeypatch.setattr(candidate, "load_prepared_geometry", formal.load_prepared_geometry)
    monkeypatch.setattr(candidate, "load_prepared_glyphs", formal.load_prepared_glyphs)
    monkeypatch.setattr(candidate, "qualify_geometry", formal.geometry_qualification.issue)
    state = {"renders": 0}
    service = candidate.CandidateReadinessService(
        client.app.state.database, client.app.state.storage
    )
    with service.database.session() as db:
        record = db.get(TemplateVersion, version)
        job = db.get(ProcessingJob, record.source_job_id)
        formal_ref = copy.deepcopy(record.quality_json["referenceQualification"])
        formal_report = json.loads(service.storage.read_bytes(formal_ref["relativePath"]))

        def evidence_ref(name: str) -> dict[str, object]:
            content = f"candidate fixture {name}".encode()
            path = service.storage.write_bytes(
                f"artifacts/{job.id}/qualification/candidate-fixture/{name}.json", content
            )
            artifact = JobArtifact(
                id=str(uuid4()), job_id=job.id, attempt_number=job.attempt_number,
                artifact_name=f"candidate-fixture-{name}", relative_path=path,
                sha256=hashlib.sha256(content).hexdigest(), media_type="application/json",
            )
            db.add(artifact)
            return {
                "artifactId": artifact.id, "relativePath": path, "sha256": artifact.sha256,
                "name": artifact.artifact_name, "mediaType": artifact.media_type,
                "attemptNumber": artifact.attempt_number,
            }

        glyph_ref = evidence_ref("glyph")
        geometry_ref = evidence_ref("geometry")
        formal_report["glyphEvidence"] = glyph_ref
        formal_report["geometryEvidence"] = {"artifacts": [geometry_ref]}
        formal_payload = json.dumps(formal_report, ensure_ascii=False, sort_keys=True).encode()
        service.storage.write_bytes(formal_ref["relativePath"], formal_payload)
        formal_ref["sha256"] = hashlib.sha256(formal_payload).hexdigest()
        record.quality_json = {**record.quality_json, "referenceQualification": formal_ref}
        db.commit()
        source = prepared(service, db, record)
        state["originalQuality"] = copy.deepcopy(record.quality_json)
    report = copy.deepcopy(source.report)
    report["bootstrapPythonIdentity"]["nativeDependencies"]["sha256"] = "9" * 64
    state["report"] = report

    def rendered(*args, **kwargs):
        state["renders"] += 1
        return b"pdf", source.images, copy.deepcopy(state["report"])

    monkeypatch.setattr(candidate.renderer_wsl, "render_wsl_reference", rendered)
    monkeypatch.setattr(candidate.wsl_host_identity, "current", sample_host)
    monkeypatch.setattr(
        candidate.wsl_environment_evidence,
        "inspect_current",
        lambda *_: candidate.wsl_environment_evidence.identity(state["report"]),
    )
    return version, url, ready, service, state


def test_candidate_replay_to_activation_and_acceptance_preserves_original_evidence(
    client,
    candidate_fixture,
):
    version, url, ready, service, state = candidate_fixture
    response = client.post(
        url + "/candidate-readiness", params={"base_revision": ready["revision"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CANDIDATE_READY"
    assert response.json()["qualificationPolicy"] == candidate.POLICY
    assert response.json()["validUntilChange"] is True
    assert response.json()["expiresAt"] is None
    assert response.json()["qualified"] is False
    assert response.json()["environmentDifference"]["changed"] is True
    assert state["renders"] == 1
    assert client.get(url + "/qualification").json() == response.json()
    assert state["renders"] == 1  # GET never invokes Office.
    with service.database.session() as db:
        record = db.get(TemplateVersion, version)
        assert record.status == "DRAFT"
        assert {k: v for k, v in record.quality_json.items() if k != "candidateReadiness"} == state[
            "originalQuality"
        ]
    approval = client.post(
        f"/api/v1/template-versions/{version}/approve", params={"base_revision": ready["revision"]}
    )
    assert approval.status_code == 200, approval.text
    jobs, catalog, submission, uploads = _candidate_submission(client, version)
    assert catalog["available"] is True
    job = jobs.accept(submission, uploads)
    admission = job.config_snapshot_json["jobPages"]["admission"]
    assert admission["referenceQualification"]["qualificationPolicy"] == candidate.POLICY
    assert admission["referenceQualification"]["validUntilChange"] is True
    assert admission["referenceQualification"]["expiresAt"] is None
    assert admission["inkClearanceVerified"] is False
    assert job.status == "QUEUED"
    assert state["renders"] == 1


@pytest.mark.parametrize("change", ["artifact", "source", "layout", "implementation"])
def test_changed_candidate_evidence_cannot_activate(client, candidate_fixture, monkeypatch, change):
    version, url, ready, service, state = candidate_fixture
    assert (
        client.post(
            url + "/candidate-readiness", params={"base_revision": ready["revision"]}
        ).status_code
        == 200
    )
    with service.database.session() as db:
        record = db.get(TemplateVersion, version)
        before_jobs = list(db.scalars(select(ProcessingJob.id)))
        if change == "artifact":
            service.storage.write_bytes(
                record.quality_json["candidateReadiness"]["relativePath"], b"changed"
            )
        elif change == "source":
            item = db.scalar(
                select(JobArtifact).where(
                    JobArtifact.job_id == record.source_job_id,
                    JobArtifact.artifact_name == "sourceWorkbook",
                )
            )
            service.storage.write_bytes(item.relative_path, b"changed")
        elif change == "layout":
            record.layout_json = {**record.layout_json, "changed": True}
            db.commit()
    if change == "implementation":
        monkeypatch.setattr(service.__class__, "_candidate_fingerprint", lambda *_: "0" * 64)
    result = client.get(url + "/qualification").json()
    assert result["status"] == "UNVERIFIED"
    response = client.post(
        f"/api/v1/template-versions/{version}/approve", params={"base_revision": ready["revision"]}
    )
    assert response.status_code == 409
    with service.database.session() as db:
        assert db.get(TemplateVersion, version).status == "DRAFT"
        assert list(db.scalars(select(ProcessingJob.id))) == before_jobs


def test_failed_replay_keeps_formal_record_and_does_not_publish_candidate(
    client, candidate_fixture
):
    version, url, ready, service, state = candidate_fixture
    state["report"]["networkDenied"] = False
    response = client.post(
        url + "/candidate-readiness", params={"base_revision": ready["revision"]}
    )
    assert response.status_code == 409
    with service.database.session() as db:
        assert db.get(TemplateVersion, version).quality_json == state["originalQuality"]


def test_candidate_does_not_change_formal_validation(client, candidate_fixture, monkeypatch):
    version, url, ready, service, _ = candidate_fixture
    assert (
        client.post(
            url + "/candidate-readiness", params={"base_revision": ready["revision"]}
        ).status_code
        == 200
    )
    from app.services import reference_qualification_service as formal

    monkeypatch.setattr(
        formal.environment_evidence,
        "load_lease",
        lambda *_: (_ for _ in ()).throw(ValueError("native changed")),
    )
    assert (
        ReferenceQualificationService(service.database, service.storage).status(version)[
            "qualified"
        ]
        is False
    )
    assert client.get(url + "/qualification").json()["status"] == "CANDIDATE_READY"


def test_current_environment_changes_during_replay_return_conflict_without_publish(
    client, candidate_fixture, monkeypatch
):
    version, url, ready, service, state = candidate_fixture
    current = copy.deepcopy(candidate.wsl_environment_evidence.identity(state["report"]))
    current["bootstrapPythonIdentity"]["nativeDependencies"]["sha256"] = "7" * 64
    monkeypatch.setattr(candidate.wsl_environment_evidence, "inspect_current", lambda *_: current)
    response = client.post(
        url + "/candidate-readiness", params={"base_revision": ready["revision"]}
    )
    assert response.status_code == 409, response.text
    with service.database.session() as db:
        assert db.get(TemplateVersion, version).quality_json == state["originalQuality"]


def test_missing_geometry_evidence_does_not_run_office(client, candidate_fixture):
    version, url, ready, service, state = candidate_fixture
    with service.database.session() as db:
        record = db.get(TemplateVersion, version)
        record.quality_json = {
            k: v for k, v in record.quality_json.items() if k != "referenceQualification"
        }
        db.commit()
    response = client.post(
        url + "/candidate-readiness", params={"base_revision": ready["revision"]}
    )
    assert response.status_code == 409, response.text
    assert state["renders"] == 0


def test_normal_use_does_not_repeat_native_or_geometry_preparation(client, candidate_fixture,
                                                                   monkeypatch):
    version, url, ready, service, state = candidate_fixture
    assert (
        client.post(
            url + "/candidate-readiness", params={"base_revision": ready["revision"]}
        ).status_code
        == 200
    )
    monkeypatch.setattr(
        candidate.CandidateReadinessService,
        "_prepared",
        lambda *_: (_ for _ in ()).throw(AssertionError("prepared graph recomputed")),
    )
    monkeypatch.setattr(
        candidate, "load_prepared_geometry",
        lambda *_: (_ for _ in ()).throw(AssertionError("geometry recomputed")),
    )
    monkeypatch.setattr(
        candidate, "load_prepared_glyphs",
        lambda *_: (_ for _ in ()).throw(AssertionError("glyphs recomputed")),
    )
    monkeypatch.setattr(
        candidate.wsl_environment_evidence,
        "inspect_current",
        lambda *_: (_ for _ in ()).throw(AssertionError("environment reinspected")),
    )
    monkeypatch.setattr(
        candidate.wsl_host_identity,
        "current",
        lambda: (_ for _ in ()).throw(AssertionError("host reinspected")),
    )
    assert client.get(url + "/qualification").json()["status"] == "CANDIDATE_READY"
    assert (
        client.post(
            f"/api/v1/template-versions/{version}/approve",
            params={"base_revision": ready["revision"]},
        ).status_code
        == 200
    )
    jobs, catalog, submission, uploads = _candidate_submission(client, version)
    assert catalog["available"] is True
    with service.database.session() as db:
        before = set(db.scalars(select(ProcessingJob.id)))
    state["report"]["bootstrapPythonIdentity"]["nativeDependencies"]["sha256"] = "8" * 64
    assert jobs.catalog(version)["available"] is True
    job = jobs.accept(submission, uploads)
    assert job.status == "QUEUED"
    with service.database.session() as db:
        assert set(db.scalars(select(ProcessingJob.id))) == before | {job.id}


def test_preparation_observation_expiry_does_not_expire_immutable_candidate(
    client, candidate_fixture, monkeypatch,
):
    version, url, ready, service, _ = candidate_fixture
    assert client.post(
        url + "/candidate-readiness", params={"base_revision": ready["revision"]}
    ).status_code == 200
    monkeypatch.setattr(candidate.time, "time", lambda: 9_000_000_000)
    result = client.get(url + "/qualification").json()
    assert result["status"] == "CANDIDATE_READY"
    assert result["expiresAt"] is None
    assert result["validUntilChange"] is True


def test_existing_v1_candidate_is_read_as_change_bound_without_republishing(
    client, candidate_fixture, monkeypatch,
):
    version, url, ready, service, _ = candidate_fixture
    assert client.post(
        url + "/candidate-readiness", params={"base_revision": ready["revision"]}
    ).status_code == 200
    with service.database.session() as db:
        record = db.get(TemplateVersion, version)
        ref = copy.deepcopy(record.quality_json["candidateReadiness"])
        artifact = db.get(JobArtifact, ref["artifactId"])
        report = json.loads(service.storage.read_bytes(artifact.relative_path))
        report["policy"] = candidate.LEGACY_POLICY
        report["fingerprint"] = service._candidate_fingerprint(
            record, candidate.LEGACY_POLICY
        )
        report.pop("preparedExecutionEvidence")
        payload = candidate.wsl_environment_evidence.canonical(report)
        service.storage.write_bytes(artifact.relative_path, payload)
        artifact.sha256 = hashlib.sha256(payload).hexdigest()
        ref["sha256"] = artifact.sha256
        record.quality_json = {**record.quality_json, "candidateReadiness": ref}
        db.commit()
    monkeypatch.setattr(candidate.time, "time", lambda: 9_000_000_000)
    result = client.get(url + "/qualification").json()
    assert result["status"] == "CANDIDATE_READY"
    assert result["qualificationPolicy"] == candidate.POLICY
    assert result["sourceQualificationPolicy"] == candidate.LEGACY_POLICY
    assert result["validUntilChange"] is True


def test_revision_change_during_replay_does_not_publish_stale_evidence(
    client, candidate_fixture, monkeypatch
):
    version, url, ready, service, state = candidate_fixture
    render = candidate.renderer_wsl.render_wsl_reference

    def changed(*args, **kwargs):
        result = render(*args, **kwargs)
        with service.database.session() as db:
            record = db.get(TemplateVersion, version)
            record.layout_json = {**record.layout_json, "userChange": True}
            db.commit()
        return result

    monkeypatch.setattr(candidate.renderer_wsl, "render_wsl_reference", changed)
    response = client.post(
        url + "/candidate-readiness", params={"base_revision": ready["revision"]}
    )
    assert response.status_code == 409, response.text
    with service.database.session() as db:
        record = db.get(TemplateVersion, version)
        assert record.layout_json["userChange"] is True
        assert record.quality_json == state["originalQuality"]


def test_successful_formal_reissue_supersedes_candidate_without_deleting_artifacts(
    client, candidate_fixture, monkeypatch
):
    version, url, ready, service, _ = candidate_fixture
    assert (
        client.post(
            url + "/candidate-readiness", params={"base_revision": ready["revision"]}
        ).status_code
        == 200
    )
    with service.database.session() as db:
        ref = copy.deepcopy(db.get(TemplateVersion, version).quality_json["candidateReadiness"])
    # The native augmentation belongs to the candidate replay fixture only.
    # Formal issuance and the inherited GET must now observe the SAME prepared
    # report, as they do in production (neither class overrides _prepared there).
    monkeypatch.setattr(
        candidate.CandidateReadinessService,
        "_prepared",
        ReferenceQualificationService._prepared,
    )
    result = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert result.status_code == 200, result.text
    assert result.json()["qualified"] is True
    with service.database.session() as db:
        assert "candidateReadiness" not in db.get(TemplateVersion, version).quality_json
        assert db.get(JobArtifact, ref["artifactId"]).sha256 == ref["sha256"]
    assert client.get(url + "/qualification").json()["status"] == "PASS"
