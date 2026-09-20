"""Replay must use the saved workbook/environment and preserve the original version."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import JobArtifact, TemplateVersion
from app.services import print_preparation_service as preparation
from app.services import reference_qualification_service as qualification
from app.services import renderer_wsl, renderer_wsl_worker
from tests.integration import test_prepared_reference_qualification as existing

prepared_fixture = existing.prepared_fixture
print_template = existing.print_template
saved_template = existing.saved_template


@pytest.fixture
def replay_fixture(client, prepared_fixture, monkeypatch):
    # This fixture contains a synthetic PDF and tests replay, not native geometry.
    # Geometry ownership and actual helpers have separate integration coverage.
    monkeypatch.setattr(qualification, "run_prepared_geometry", lambda *a: None)
    fields = {
        "runtimeImage": {"policy": "wsl-squashfs-runtime-v1", "sha256": "a" * 64,
                         "filesystem": "squashfs", "mountsReadOnly": True, "kernel": "fixture"},
        "fontDirectoryIdentity": {"policy": "wsl-font-directory-v1", "sha256": "b" * 64,
                                  "fileCount": 2, "totalBytes": 100},
        "workerIdentity": {"policy": "wsl-worker-source-v1", "sha256": hashlib.sha256(
            Path(renderer_wsl_worker.__file__).read_bytes()).hexdigest()},
        "rasterizer": "poppler-pdftoppm",
    }
    original_import = preparation.import_wsl_geometry
    original_render = prepared_fixture[-1]

    def measure(*args, **kwargs):
        data, report = original_import(*args, **kwargs)
        return data, {**report, **copy.deepcopy(fields)}

    def render(*args, **kwargs):
        pdf, images, report = original_render(*args, **kwargs)
        return pdf, images, {**report, **copy.deepcopy(fields)}

    monkeypatch.setattr(preparation, "import_wsl_geometry", measure)
    monkeypatch.setattr(client.app.state.settings.app, "print_wsl_runtime_id", "a" * 64)
    monkeypatch.setattr(client.app.state.settings.app, "print_wsl_distribution", "Fixture-Ubuntu")
    fixture = (*prepared_fixture[:-1], render)
    version, job, url, ready, preparation_calls = existing.reference_fixture.__wrapped__(
        client, fixture, monkeypatch)
    renderer = preparation.render_wsl_reference
    calls = []

    def replay(data, **kwargs):
        calls.append((data, kwargs))
        return renderer(data, **kwargs)

    monkeypatch.setattr(renderer_wsl, "render_wsl_reference", replay)
    return version, url, ready, calls, replay, preparation_calls


def test_replay_uses_saved_workbook_and_environment_without_replacing_files(client, replay_fixture):
    version, url, ready, calls, _, _ = replay_fixture
    original = copy.deepcopy(ready["saved"])
    response = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert response.status_code == 200
    report = response.json()
    assert report["deterministic"] is True
    assert len(calls) == 1
    data, options = calls[0]
    assert hashlib.sha256(data).hexdigest() == original["printFiles"]["xlsx"]["sha256"]
    assert options == {"distribution": "Fixture-Ubuntu", "runtime_id": "a" * 64}
    assert "RENDER_DETERMINISM_UNVERIFIED" not in report["productionBlockers"]
    assert report["qualified"] is False and report["environmentQualified"] is False
    assert client.get(url + "/qualification").json()["deterministic"] is True
    assert client.get(url).json()["saved"] == original
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).status == "DRAFT"


def test_a_changed_page_is_a_failed_replay_not_a_replacement(client, replay_fixture, monkeypatch):
    _, url, ready, _, original_replay, _ = replay_fixture

    def changed(data, **kwargs):
        pdf, images, report = original_replay(data, **kwargs)
        # Comparison fixture only; no assertion of a valid PNG/native render.
        images[-1] += b"different rendered page"
        report["imageSha256"][-1] = hashlib.sha256(images[-1]).hexdigest()
        return pdf, images, report

    monkeypatch.setattr(renderer_wsl, "render_wsl_reference", changed)
    response = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert response.status_code == 200
    report = response.json()
    assert report["deterministic"] is False and report["qualified"] is False
    assert "RENDER_NOT_DETERMINISTIC" in report["codes"]
    assert client.get(url + "/qualification").json()["deterministic"] is False
    assert client.get(url).json()["saved"] == ready["saved"]


@pytest.mark.parametrize("change", ["bytes", "image_id", "ordinal", "report_hash"])
def test_changed_replay_evidence_invalidates_status_without_rerender(
    client, replay_fixture, change,
):
    version, url, ready, calls, _, _ = replay_fixture
    assert client.post(url + "/qualification", params={
        "base_revision": ready["revision"]}).status_code == 200
    storage = client.app.state.storage
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        ref = record.quality_json["referenceQualification"]
        report = json.loads(storage.read_bytes(ref["relativePath"]))
        replay = report["replayEvidence"]
        if change == "bytes":
            storage.write_bytes(replay["images"][0]["relativePath"], b"changed diagnostic image")
        else:
            if change == "image_id":
                replay["images"][0]["artifactId"] = ready["saved"]["pages"][0][
                    "referenceImage"]["artifactId"]
            elif change == "ordinal":
                replay["images"][0]["renderedPage"] = True
            else:
                replay["report"]["imageSha256"][0] = "0" * 64
            changed = json.dumps(report).encode()
            storage.write_bytes(ref["relativePath"], changed)
            record.quality_json = {**record.quality_json, "referenceQualification": {
                **ref, "sha256": hashlib.sha256(changed).hexdigest()}}
            db.commit()
    status = client.get(url + "/qualification").json()
    assert status["status"] == "UNVERIFIED" and status["qualified"] is False
    assert status["codes"] == ["REFERENCE_EVIDENCE_CHANGED"]
    assert len(calls) == 1
    assert client.get(url).json()["saved"] == ready["saved"]


def test_saved_deterministic_flag_without_replay_evidence_stays_unmeasured(client, replay_fixture):
    version, url, ready, calls, _, _ = replay_fixture
    assert client.post(url + "/qualification", params={
        "base_revision": ready["revision"]}).status_code == 200
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        ref = record.quality_json["referenceQualification"]
        storage = client.app.state.storage
        report = json.loads(storage.read_bytes(ref["relativePath"]))
        report.pop("replayEvidence")
        report["deterministic"] = True
        changed = json.dumps(report).encode()
        storage.write_bytes(ref["relativePath"], changed)
        record.quality_json = {**record.quality_json, "referenceQualification": {
            **ref, "sha256": hashlib.sha256(changed).hexdigest()}}
        db.commit()
    status = client.get(url + "/qualification").json()
    assert status == {
        "status": "UNVERIFIED",
        "qualified": False,
        "codes": ["REFERENCE_EVIDENCE_CHANGED"],
    }
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["config", "busy"])
def test_config_change_or_busy_replay_does_not_publish_new_evidence(
    client, replay_fixture, monkeypatch, change,
):
    from contextlib import nullcontext

    from app.services.office_reference_renderer import _RENDER_LOCK

    version, url, ready, _, original_replay, _ = replay_fixture
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        before = copy.deepcopy(record.quality_json)
        path = record.config_relative_path
        artifacts = list(db.scalars(select(JobArtifact.id)))

    def replay(data, **kwargs):
        result = original_replay(data, **kwargs)
        storage = client.app.state.storage
        storage.write_bytes(path, storage.read_bytes(path) + b"\n")
        return result

    monkeypatch.setattr(renderer_wsl, "render_wsl_reference", replay)
    with _RENDER_LOCK if change == "busy" else nullcontext():
        response = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert response.status_code == 409
    assert response.json()["code"] == (
        "RENDERER_BUSY" if change == "busy" else "PRINT_LAYOUT_CHANGED")
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).quality_json == before
        assert list(db.scalars(select(JobArtifact.id))) == artifacts


def test_replay_in_changed_environment_is_not_certified_even_if_images_match(
    client, replay_fixture, monkeypatch,
):
    _, url, ready, _, original_replay, _ = replay_fixture

    def changed(data, **kwargs):
        pdf, images, report = original_replay(data, **kwargs)
        report["runtimeImage"]["kernel"] = "another-kernel"
        return pdf, images, report

    monkeypatch.setattr(renderer_wsl, "render_wsl_reference", changed)
    response = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert response.status_code == 200
    report = response.json()
    assert report["deterministic"] is not True
    assert "RENDERER_ENVIRONMENT_CHANGED" in report["codes"]
    assert report["qualified"] is False
    assert client.get(url).json()["saved"] == ready["saved"]
