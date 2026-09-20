"""Prepared geometry and reference must bind the same explicit runtime/font set."""
import copy
import json

import pytest
from sqlalchemy import select

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.services import print_preparation_service as preparation
from tests.bootstrap_python_fixture import bootstrap_python_evidence
from tests.integration import test_print_preparation as existing
from tests.integration import test_wsl_frozen_runtime as runtime_tests

prepared_fixture = existing.prepared_fixture
print_template = existing.print_template
saved_template = existing.saved_template
installed_runtime = runtime_tests.installed_runtime

RUNTIME_ID = "a" * 64
RUNTIME = {"policy": "wsl-squashfs-runtime-v1", "sha256": RUNTIME_ID,
           "mountsReadOnly": True, "filesystem": "squashfs", "kernel": "fixture-kernel"}
FONTS = {"policy": "wsl-font-directory-v1", "fileCount": 2, "totalBytes": 10,
         "sha256": "b" * 64}
WORKER = {"policy": "wsl-worker-source-v1", "sha256": "c" * 64}


@pytest.mark.parametrize("change", [None, "runtime", "kernel", "font_set", "missing_import",
                                    "missing_reference", "both_fonts_missing", "worker_changed",
                                    "missing_import_worker", "missing_reference_worker",
                                    "both_workers_missing", "bootstrap_changed",
                                    "bootstrap_import_missing", "bootstrap_reference_missing",
                                    "native_changed", "native_import_missing",
                                    "native_reference_missing"])
def test_preparation_pins_both_calls_and_rejects_changed_environment(
    client, prepared_fixture, monkeypatch, change,
):
    version, job, _, url, state, _, original_render = prepared_fixture
    monkeypatch.setattr(client.app.state.settings.app, "print_wsl_runtime_id", RUNTIME_ID)
    original_import = preparation.import_wsl_geometry
    calls = []

    def evidence(report):
        return {**report, "runtimeImage": copy.deepcopy(RUNTIME),
                "fontDirectoryIdentity": copy.deepcopy(FONTS),
                "workerIdentity": copy.deepcopy(WORKER),
                "bootstrapPythonIdentity": bootstrap_python_evidence()}

    def measure(content, **options):
        calls.append(("geometry", options))
        data, report = original_import(content, **options)
        report = evidence(report)
        if change == "missing_import":
            report.pop("runtimeImage")
        if change == "both_fonts_missing":
            report.pop("fontDirectoryIdentity")
        if change in {"missing_import_worker", "both_workers_missing"}:
            report.pop("workerIdentity")
        if change == "bootstrap_import_missing":
            report.pop("bootstrapPythonIdentity")
        if change == "native_import_missing":
            report["bootstrapPythonIdentity"].pop("nativeDependencies")
        return data, report

    def render(content, **options):
        calls.append(("reference", options))
        pdf, images, report = original_render(content, **options)
        report = evidence(report)
        if change == "runtime":
            report["runtimeImage"]["sha256"] = "c" * 64
        elif change == "kernel":
            report["runtimeImage"]["kernel"] = "different"
        elif change == "font_set":
            report["fontDirectoryIdentity"]["sha256"] = "c" * 64
        elif change == "missing_reference":
            report.pop("runtimeImage")
        elif change == "both_fonts_missing":
            report.pop("fontDirectoryIdentity")
        elif change == "worker_changed":
            report["workerIdentity"]["sha256"] = "e" * 64
        elif change in {"missing_reference_worker", "both_workers_missing"}:
            report.pop("workerIdentity")
        elif change == "bootstrap_changed":
            report["bootstrapPythonIdentity"]["sha256"] = "d" * 64
        elif change == "bootstrap_reference_missing":
            report.pop("bootstrapPythonIdentity")
        elif change == "native_changed":
            report["bootstrapPythonIdentity"]["nativeDependencies"]["sha256"] = "e" * 64
        elif change == "native_reference_missing":
            report["bootstrapPythonIdentity"].pop("nativeDependencies")
        return pdf, images, report

    monkeypatch.setattr(preparation, "import_wsl_geometry", measure)
    monkeypatch.setattr(preparation, "render_wsl_reference", render)
    with client.app.state.database.session() as db:
        original_quality = copy.deepcopy(db.get(TemplateVersion, version).quality_json)
        original_layout = copy.deepcopy(db.get(TemplateVersion, version).layout_json)
        original_job = copy.deepcopy(db.get(ProcessingJob, job).config_snapshot_json)
        artifacts = list(db.scalars(select(JobArtifact.id)))
    response = client.post(url + "/prepare", params={"base_revision": state["revision"]})
    assert [name for name, _ in calls] == ["geometry", "reference"]
    assert all(options["runtime_id"] == RUNTIME_ID for _, options in calls)
    if change is not None:
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "PRINT_GEOMETRY_CHANGED"
        with client.app.state.database.session() as db:
            record = db.get(TemplateVersion, version)
            assert record.quality_json == original_quality and record.layout_json == original_layout
            assert db.get(ProcessingJob, job).config_snapshot_json == original_job
            assert list(db.scalars(select(JobArtifact.id))) == artifacts
        return
    assert response.status_code == 200, response.text
    ready = response.json()
    plan = ready["saved"]
    measured = plan["geometryEvidence"]["importReport"]
    report = json.loads(client.app.state.storage.read_bytes(
        plan["printFiles"]["report"]["relativePath"]))
    assert measured["runtimeImage"] == report["runtimeImage"] == RUNTIME
    assert measured["fontDirectoryIdentity"] == report["fontDirectoryIdentity"] == FONTS
    assert measured["workerIdentity"] == report["workerIdentity"] == WORKER
    assert measured["bootstrapPythonIdentity"] == report["bootstrapPythonIdentity"]
    # An app setting change must not replace previously prepared download bytes.
    monkeypatch.setattr(client.app.state.settings.app, "print_wsl_runtime_id", "d" * 64)
    monkeypatch.setattr(preparation, "render_wsl_reference",
                        lambda *a, **k: pytest.fail("No rerender"))
    file = client.get(url + "/files/pdf", params={"base_revision": ready["revision"]})
    assert file.status_code == 200
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        assert record.layout_json == original_layout and record.status == "DRAFT"
    approval = client.post(f"/api/v1/template-versions/{version}/approve")
    assert approval.json()["code"] == "PRINT_LAYOUT_OCR_NOT_AVAILABLE"


@pytest.mark.parametrize("missing", [False, True])
def test_real_pinned_preparation_persists_environment_or_keeps_draft_unchanged(
    client, saved_template, installed_runtime, monkeypatch, missing,
):
    version, job, _, url, state = saved_template
    identity = installed_runtime[1 if missing else 0]
    monkeypatch.setattr(preparation.PrintLayoutService, "_source", existing.REAL_SOURCE)
    monkeypatch.setattr(client.app.state.settings.app, "print_pdf_renderer", "wsl")
    monkeypatch.setattr(client.app.state.settings.app, "print_wsl_runtime_id", identity)
    with client.app.state.database.session() as db:
        quality = copy.deepcopy(db.get(TemplateVersion, version).quality_json)
        layout = copy.deepcopy(db.get(TemplateVersion, version).layout_json)
        job_snapshot = copy.deepcopy(db.get(ProcessingJob, job).config_snapshot_json)
        artifacts = list(db.scalars(select(JobArtifact.id)))
    response = client.post(url + "/prepare", params={"base_revision": state["revision"]})
    if missing:
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "PRINT_PREPARATION_FAILED"
        with client.app.state.database.session() as db:
            record = db.get(TemplateVersion, version)
            assert record.quality_json == quality and record.layout_json == layout
            assert db.get(ProcessingJob, job).config_snapshot_json == job_snapshot
            assert list(db.scalars(select(JobArtifact.id))) == artifacts
        return
    assert response.status_code == 200, response.text
    ready = response.json()
    plan = ready["saved"]
    measured = plan["geometryEvidence"]["importReport"]
    report = json.loads(client.app.state.storage.read_bytes(
        plan["printFiles"]["report"]["relativePath"]))
    assert report["runtimeImage"] == measured["runtimeImage"]
    assert report["runtimeImage"]["sha256"] == identity
    assert report["fontDirectoryIdentity"] == measured["fontDirectoryIdentity"]
    assert report["fontDirectoryIdentity"]["policy"] == "wsl-font-directory-v1"
    assert report["workerIdentity"] == measured["workerIdentity"]
    assert report["workerIdentity"]["policy"] == "wsl-worker-source-v1"
    assert len(plan["pages"]) == report["pageCount"] == 2
    file = client.get(url + "/files/pdf", params={"base_revision": ready["revision"]})
    assert file.status_code == 200 and file.content.startswith(b"%PDF-")
    assert client.get(url).json()["saved"] == plan
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        assert record.layout_json == layout and record.status == "DRAFT"
    approval = client.post(f"/api/v1/template-versions/{version}/approve")
    assert approval.json()["code"] == "PRINT_LAYOUT_OCR_NOT_AVAILABLE"
