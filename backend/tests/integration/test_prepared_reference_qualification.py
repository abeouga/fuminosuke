import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.services import print_preparation_service as preparation
from app.services import reference_qualification_service as qualification
from tests.integration import test_print_preparation as existing
from tests.integration import test_wsl_frozen_runtime as runtime_tests

prepared_fixture = existing.prepared_fixture
print_template = existing.print_template
saved_template = existing.saved_template
installed_runtime = runtime_tests.installed_runtime


@pytest.fixture
def reference_fixture(client, prepared_fixture, monkeypatch):
    version, job, _, url, state, calls, original_render = prepared_fixture
    original_import = preparation.import_wsl_geometry

    def measure(data, **kwargs):
        fods, report = original_import(data, **kwargs)
        return fods, {**report, "sourceSha256": hashlib.sha256(data).hexdigest(),
                      "format": "fods", "fontSource": "windows"}

    def render(data, **kwargs):
        pdf, images, report = original_render(data, **kwargs)
        return pdf, images, {**report, "sourceSha256": hashlib.sha256(data).hexdigest(),
                            "pdfSha256": hashlib.sha256(pdf).hexdigest(),
                            "imageSha256": [hashlib.sha256(image).hexdigest() for image in images],
                            "status": "PASS", "format": "reference", "fontSource": "windows",
                            "policy": "wsl-bwrap-cgroup-v1", "dpi": 150,
                            "fontRequestPolicy": "wsl-fontconfig-face-v2"}

    monkeypatch.setattr(preparation, "import_wsl_geometry", measure)
    monkeypatch.setattr(preparation, "render_wsl_reference", render)
    prepared = client.post(url + "/prepare", params={"base_revision": state["revision"]})
    assert prepared.status_code == 200
    ready = prepared.json()
    for name in ("environment_identity", "renderer_identity", "render_office"):
        monkeypatch.setattr(qualification, name, lambda *a, **k: pytest.fail("No Windows renderer"))
    return version, job, url, ready, calls


def test_qualification_uses_all_owned_prepared_pages_without_windows_or_plan_mutation(
    client, reference_fixture,
):
    version, job, url, ready, calls = reference_fixture
    with client.app.state.database.session() as db:
        layout = copy.deepcopy(db.get(TemplateVersion, version).layout_json)
        snapshot = copy.deepcopy(db.get(ProcessingJob, job).config_snapshot_json)
    result = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert result.status_code == 200
    report = result.json()
    assert report["qualificationPolicy"] == "wsl-prepared-reference-v2"
    assert report["qualified"] is False and report["environmentQualified"] is False
    assert report["qualificationIssued"] is False
    assert report["qualificationBinding"]["templateVersionId"] == version
    assert report["deterministic"] is None
    assert report["productionBlockers"] == qualification.WSL_BLOCKERS
    assert len(report["pages"]) == len(report["cropSafety"]) == 2
    assert report["referenceSha256"] == [
        p["referenceImage"]["sha256"] for p in ready["saved"]["pages"]]
    assert len(calls) == 1
    assert client.get(url + "/qualification").json() == report
    assert client.get(url).json()["saved"] == ready["saved"]
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        assert record.layout_json == layout and record.status == "DRAFT"
        assert db.get(ProcessingJob, job).config_snapshot_json == snapshot
    assert client.post(f"/api/v1/template-versions/{version}/approve").json()["code"] \
        == "PRINT_LAYOUT_OCR_NOT_AVAILABLE"


@pytest.mark.parametrize("change", ["xlsx", "pdf", "report", "first_image", "last_image",
                                    "config", "artifact_id", "foreign_owner"])
def test_changed_artifact_or_ownership_invalidates_prepared_qualification(
    client, reference_fixture, monkeypatch, change,
):
    version, _, url, ready, _ = reference_fixture
    assert client.post(url + "/qualification", params={
        "base_revision": ready["revision"]}).status_code == 200
    storage = client.app.state.storage
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        plan = copy.deepcopy(record.quality_json["printLayout"])
        if change == "config":
            storage.write_bytes(record.config_relative_path,
                                storage.read_bytes(record.config_relative_path) + b"\n")
        elif change == "artifact_id":
            plan["printFiles"]["pdf"]["artifactId"] = plan["printFiles"]["xlsx"]["artifactId"]
            record.quality_json = {**record.quality_json, "printLayout": plan}
            db.commit()
        elif change == "foreign_owner":
            original_get = Session.get
            selected_id = plan["printFiles"]["pdf"]["artifactId"]

            def get(session, entity, ident, **kwargs):
                item = original_get(session, entity, ident, **kwargs)
                if entity is JobArtifact and ident == selected_id:
                    return SimpleNamespace(job_id="another-document")
                return item

            monkeypatch.setattr(Session, "get", get)
        else:
            ref = (plan["pages"][0 if change == "first_image" else -1]["referenceImage"]
                   if change.endswith("image") else plan["printFiles"][change])
            storage.write_bytes(ref["relativePath"], b"changed artifact")
    result = client.get(url + "/qualification").json()
    assert result["status"] == "UNVERIFIED" and result["qualified"] is False
    assert result["codes"]


def test_saved_flags_cannot_certify_prepared_artifact_integrity(client, reference_fixture):
    version, _, url, ready, _ = reference_fixture
    assert client.post(url + "/qualification", params={
        "base_revision": ready["revision"]}).status_code == 200
    with client.app.state.database.session() as db:
        version_record = db.get(TemplateVersion, version)
        ref = version_record.quality_json["referenceQualification"]
        storage = client.app.state.storage
        report = json.loads(storage.read_bytes(ref["relativePath"]))
        report.update(qualified=True, environmentQualified=True,
                      status="PASS", productionBlockers=[])
        forged = json.dumps(report).encode()
        storage.write_bytes(ref["relativePath"], forged)
        version_record.quality_json = {**version_record.quality_json, "referenceQualification": {
            **ref, "sha256": hashlib.sha256(forged).hexdigest()}}
        db.commit()
    report = client.get(url + "/qualification").json()
    assert report["qualified"] is False and report["environmentQualified"] is False
    assert report["status"] == "FAIL"
    assert report["qualificationIssued"] is False
    assert report["productionBlockers"] == qualification.WSL_BLOCKERS


@pytest.mark.parametrize("field", ["sourceSha256", "pdfSha256", "imageSha256",
                                   "imageSizes", "runtimeImage"])
def test_rehashed_report_must_still_match_the_owned_artifact_graph(
    client, reference_fixture, field,
):
    version, _, url, _, _ = reference_fixture
    storage = client.app.state.storage
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        plan = copy.deepcopy(record.quality_json["printLayout"])
        ref = plan["printFiles"]["report"]
        report = json.loads(storage.read_bytes(ref["relativePath"]))
        report[field] = "different-but-rehashed"
        changed = json.dumps(report).encode()
        storage.write_bytes(ref["relativePath"], changed)
        ref["sha256"] = hashlib.sha256(changed).hexdigest()
        db.get(JobArtifact, ref["artifactId"]).sha256 = ref["sha256"]
        record.quality_json = {**record.quality_json, "printLayout": plan}
        db.commit()
    revision = client.get(url).json()["revision"]
    response = client.post(url + "/qualification", params={"base_revision": revision})
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == "FAIL" and report["qualified"] is False
    assert report["codes"] == ["RENDER_FAILED"]
    assert "preparedEvidence" not in report


@pytest.mark.parametrize("change", ["config", "pdf"])
def test_changes_during_measurement_do_not_publish_stale_evidence(
    client, reference_fixture, monkeypatch, change,
):
    version, _, url, ready, _ = reference_fixture
    original_measure = qualification.measure_page
    storage = client.app.state.storage
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        before = copy.deepcopy(record.quality_json)
        path = (record.config_relative_path if change == "config"
                else ready["saved"]["printFiles"]["pdf"]["relativePath"])

    def measure(page, image):
        result = original_measure(page, image)
        storage.write_bytes(path, storage.read_bytes(path) + b"\n")
        return result

    monkeypatch.setattr(qualification, "measure_page", measure)
    response = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert response.status_code == 409
    assert response.json()["code"] == (
        "PRINT_LAYOUT_CHANGED" if change == "config" else "REFERENCE_EVIDENCE_CHANGED")
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).quality_json == before


def test_real_frozen_preparation_reaches_wsl_qualification_without_windows(
    client, saved_template, installed_runtime, monkeypatch,
):
    version, _, _, url, state = saved_template
    monkeypatch.setattr(preparation.PrintLayoutService, "_source", existing.REAL_SOURCE)
    monkeypatch.setattr(client.app.state.settings.app, "print_pdf_renderer", "wsl")
    monkeypatch.setattr(client.app.state.settings.app, "print_wsl_runtime_id", installed_runtime[0])
    response = client.post(url + "/prepare", params={"base_revision": state["revision"]})
    assert response.status_code == 200
    ready = response.json()
    for name in ("environment_identity", "renderer_identity", "render_office"):
        monkeypatch.setattr(qualification, name, lambda *a, **k: pytest.fail("No Windows renderer"))
    result = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert result.status_code == 200
    report = result.json()
    assert report["qualificationPolicy"] == "wsl-prepared-reference-v2"
    assert len(report["pages"]) == 2 and report["qualified"] is False, report
    assert report["executionEvidence"][0]["runtimeImage"]["sha256"] == installed_runtime[0]
    assert report["deterministic"] is True
    assert len(report["replayEvidence"]["images"]) == 2
    assert report["environmentEvidence"]["policy"] == "wsl-environment-identity-lease-v2"
    assert "RENDER_DETERMINISM_UNVERIFIED" not in report["productionBlockers"]
    current_report = client.get(url + "/qualification").json()
    assert current_report == report
    assert client.get(url).json()["saved"] == ready["saved"]
    lease = json.loads(client.app.state.storage.read_bytes(
        report["environmentEvidence"]["relativePath"]))
    assert lease["hostIdentity"] == qualification.environment_evidence.host_identity.current()
    with client.app.state.database.session() as db:
        before_expiry = copy.deepcopy(db.get(TemplateVersion, version).quality_json)
    monkeypatch.setattr(qualification.environment_evidence.time, "time",
                        lambda: lease["expiresAt"])
    expired = client.get(url + "/qualification").json()
    assert expired["qualified"] is False
    assert expired["codes"] == ["REFERENCE_EVIDENCE_CHANGED"]
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).quality_json == before_expiry


@pytest.mark.parametrize("field,value", [
    ("uid", 0), ("networkDenied", False), ("resourceLimits", {"pids.max": "max"}),
    ("missing_all_reference_controls", None),
])
def test_owned_report_hashes_cannot_substitute_for_valid_execution_controls(
    client, reference_fixture, field, value,
):
    from app.services.prepared_print_contract import render_key
    from app.services.prepared_reference_evidence import load_prepared_reference
    from tests.wsl_execution_fixture import wsl_execution_evidence

    version, _, _, _, _ = reference_fixture
    storage = client.app.state.storage
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        plan = copy.deepcopy(record.quality_json["printLayout"])
        ref = plan["printFiles"]["report"]
        report = json.loads(storage.read_bytes(ref["relativePath"]))
        measured = plan["geometryEvidence"]["importReport"]
        measured.update(policy="wsl-bwrap-cgroup-v1", status="PASS")
        report.update(wsl_execution_evidence())
        measured.update(wsl_execution_evidence())

        def persist_graph():
            content = json.dumps(report).encode()
            storage.write_bytes(ref["relativePath"], content)
            ref["sha256"] = hashlib.sha256(content).hexdigest()
            db.get(JobArtifact, ref["artifactId"]).sha256 = ref["sha256"]
            plan["printFiles"]["renderKey"] = render_key(plan)
            record.quality_json = {**record.quality_json, "printLayout": copy.deepcopy(plan)}
            db.flush()

        persist_graph()
        assert load_prepared_reference(db, storage, record).report["uid"] == 65534
        # Correct hashes/ownership cannot rescue identical invalid controls or
        # one-sided deletion of all observations.
        if field == "missing_all_reference_controls":
            for key in wsl_execution_evidence():
                report.pop(key)
        else:
            report[field] = measured[field] = value
        persist_graph()
        before = copy.deepcopy(record.quality_json)
        with pytest.raises(ValueError, match="execution isolation"):
            load_prepared_reference(db, storage, record)
        assert record.quality_json == before
