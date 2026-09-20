"""API ownership/invalidation fixtures; not evidence of native font rendering."""
import copy
import hashlib
import json

import pytest
from sqlalchemy import select

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.services import print_preparation_service as preparation
from app.services import renderer_glyph_bridge as bridge
from tests.integration import test_prepared_reference_replay_api as replay_tests
from tests.unit.test_renderer_glyph_bridge import evidence

prepared_fixture = replay_tests.prepared_fixture
print_template = replay_tests.print_template
saved_template = replay_tests.saved_template


@pytest.fixture
def glyph_fixture(client, prepared_fixture, monkeypatch):
    fonts = [{"requestedFamily": "Arbitrary", "resolvedFamily": "Arbitrary",
              "file": "/reference-fonts/Arbitrary.ttc", "collectionIndex": "2",
              "sha256": "e" * 64}]
    original_import = preparation.import_wsl_geometry
    original_render = prepared_fixture[-1]

    def measure(*args, **kwargs):
        data, report = original_import(*args, **kwargs)
        return data, {**report, "resolvedFonts": copy.deepcopy(fonts)}

    def render(*args, **kwargs):
        pdf, images, report = original_render(*args, **kwargs)
        return pdf, images, {**report, "resolvedFonts": copy.deepcopy(fonts)}

    monkeypatch.setattr(preparation, "import_wsl_geometry", measure)
    fixture = (*prepared_fixture[:-1], render)
    replay = replay_tests.replay_fixture.__wrapped__(client, fixture, monkeypatch)
    calls = []

    def glyphs(pdf, reference, **kwargs):
        calls.append((pdf, copy.deepcopy(reference), kwargs))
        manifest = bridge.input_manifest(pdf, reference)
        report = evidence(manifest)
        report["codeBundleSha256"] = hashlib.sha256(bridge.code_bundle()).hexdigest()
        report["inputSha256"] = hashlib.sha256(bridge._bundle([
            ("reference.pdf", pdf), ("manifest.json", bridge.canonical_bytes(manifest)),
        ])).hexdigest()
        detail = report["glyphs"]
        count = manifest["pageCount"]
        detail["pageCount"] = detail["textInstructionGlyphs"] = count
        detail["pages"] = [{**detail["pages"][0], "renderedPage": i}
                           for i in range(1, count + 1)]
        return report

    monkeypatch.setattr(bridge, "validate_wsl_pdf_glyphs", glyphs)
    return *replay, calls, glyphs


def qualify(client, fixture):
    _, url, ready, *_ = fixture
    result = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert result.status_code == 200, result.text
    return result.json()


def test_glyph_api_saves_owned_evidence_and_get_does_not_execute(client, glyph_fixture):
    version, url, ready, _, _, _, calls, _ = glyph_fixture
    report = qualify(client, glyph_fixture)
    assert report["glyphVerification"]["status"] == "PASS"
    assert "FONT_GLYPH_UNVERIFIED" not in report["productionBlockers"]
    assert "WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED" in report["productionBlockers"]
    assert report["qualified"] is False and report["environmentQualified"] is False
    assert report["glyphVerification"]["visibilityAndLayoutVerified"] is False
    assert len(calls) == 1
    pdf, renderer, options = calls[0]
    assert hashlib.sha256(pdf).hexdigest() == ready["saved"]["printFiles"]["pdf"]["sha256"]
    assert options == {"distribution": "Fixture-Ubuntu"}
    assert renderer["runtimeImage"]["sha256"] == "a" * 64
    assert client.get(url + "/qualification").json() == report
    assert len(calls) == 1 and client.get(url).json()["saved"] == ready["saved"]
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).status == "DRAFT"
        item = db.get(JobArtifact, report["glyphEvidence"]["artifactId"])
        assert item.job_id == db.get(TemplateVersion, version).source_job_id


@pytest.mark.parametrize("change", ["bytes", "owner", "version", "count", "code", "attempt"])
def test_glyph_evidence_tampering_invalidates_get_even_when_rehashed(
    client, glyph_fixture, change,
):
    version, url, _, _, _, _, calls, _ = glyph_fixture
    report = qualify(client, glyph_fixture)
    storage = client.app.state.storage
    with client.app.state.database.session() as db:
        item = db.get(JobArtifact, report["glyphEvidence"]["artifactId"])
        if change == "bytes":
            storage.write_bytes(item.relative_path, b"changed")
        elif change == "owner":
            db.add(ProcessingJob(id="different-job", job_type="SPREADSHEET_TEMPLATE",
                                 request_hash="f" * 64))
            db.flush()
            item.job_id = "different-job"
        elif change == "attempt":
            item.attempt_number += 1
        else:
            payload = json.loads(storage.read_bytes(item.relative_path))
            if change == "version":
                payload["templateVersionId"] = "other"
            elif change == "count":
                payload["report"]["glyphs"]["textInstructionGlyphs"] += 1
            else:
                payload["report"]["codeBundleSha256"] = "0" * 64
            raw = bridge.canonical_bytes(payload)
            storage.write_bytes(item.relative_path, raw)
            item.sha256 = hashlib.sha256(raw).hexdigest()
            report["glyphEvidence"]["sha256"] = item.sha256
            record = db.get(TemplateVersion, version)
            outer = record.quality_json["referenceQualification"]
            encoded = bridge.canonical_bytes(report)
            storage.write_bytes(outer["relativePath"], encoded)
            record.quality_json = {**record.quality_json, "referenceQualification": {
                **outer, "sha256": hashlib.sha256(encoded).hexdigest()}}
        db.commit()
    status = client.get(url + "/qualification").json()
    assert status["status"] == "UNVERIFIED" and status["qualified"] is False
    assert status["codes"] == ["REFERENCE_EVIDENCE_CHANGED"] and len(calls) == 1


def test_summary_flags_without_owned_report_do_not_remove_blocker(client, glyph_fixture):
    version, url, *_ = glyph_fixture
    report = qualify(client, glyph_fixture)
    report.pop("glyphEvidence")
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        outer = record.quality_json["referenceQualification"]
        payload = bridge.canonical_bytes(report)
        client.app.state.storage.write_bytes(outer["relativePath"], payload)
        record.quality_json = {**record.quality_json, "referenceQualification": {
            **outer, "sha256": hashlib.sha256(payload).hexdigest()}}
        db.commit()
    status = client.get(url + "/qualification").json()
    assert status == {
        "status": "UNVERIFIED",
        "qualified": False,
        "codes": ["REFERENCE_EVIDENCE_CHANGED"],
    }


def test_helper_failure_keeps_other_measurements_and_does_not_disclose_output(
    client, glyph_fixture, monkeypatch,
):
    def fail(*args, **kwargs):
        raise RuntimeError("private source text and local paths")

    monkeypatch.setattr(bridge, "validate_wsl_pdf_glyphs", fail)
    report = qualify(client, glyph_fixture)
    assert report["deterministic"] is True and report["glyphVerification"] is None
    assert len(report["pages"]) == 2
    assert "glyphEvidence" not in report
    assert "FONT_GLYPH_UNVERIFIED" in report["productionBlockers"]
    assert "private" not in json.dumps(report)
    assert report["glyphDiagnostic"]["status"] == "UNVERIFIED"


@pytest.mark.parametrize("change", ["config", "pdf"])
def test_input_change_during_glyph_run_does_not_publish(
    client, glyph_fixture, monkeypatch, change,
):
    version, url, ready, _, _, _, _, original = glyph_fixture
    with client.app.state.database.session() as db:
        record = db.get(TemplateVersion, version)
        before, path = copy.deepcopy(record.quality_json), record.config_relative_path
        artifacts = list(db.scalars(select(JobArtifact.id)))
    if change == "pdf":
        path = ready["saved"]["printFiles"]["pdf"]["relativePath"]

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        storage = client.app.state.storage
        storage.write_bytes(path, storage.read_bytes(path) + b"\n")
        return result

    monkeypatch.setattr(bridge, "validate_wsl_pdf_glyphs", changed)
    result = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert result.status_code == 409 and result.json()["code"] == (
        "PRINT_LAYOUT_CHANGED" if change == "config" else "REFERENCE_EVIDENCE_CHANGED")
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).quality_json == before
        assert list(db.scalars(select(JobArtifact.id))) == artifacts


def test_proven_fallback_face_still_needs_fallback_qualification(
    client, glyph_fixture, monkeypatch,
):
    original = glyph_fixture[-1]

    def fallback(*args, **kwargs):
        report = original(*args, **kwargs)
        report["sourceFaces"][0]["sha256"] = "f" * 64
        report["glyphs"]["fonts"][0]["sourceSha256"] = "f" * 64
        report["allMatchedFacesDeclared"] = False
        return report

    monkeypatch.setattr(bridge, "validate_wsl_pdf_glyphs", fallback)
    report = qualify(client, glyph_fixture)
    assert report["glyphVerification"]["allMatchedFacesDeclared"] is False
    assert "FONT_FALLBACK_UNQUALIFIED" in report["productionBlockers"]
    assert "FONT_GLYPH_UNVERIFIED" not in report["productionBlockers"]
    assert report["qualified"] is False
    assert client.get(glyph_fixture[1] + "/qualification").json() == report
