"""Persisted glyph checks never parse a PDF or rerun Office on status reads."""
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.db.models import JobArtifact, ProcessingJob
from app.services import prepared_reference_glyphs as service
from app.services import renderer_glyph_bridge as bridge
from app.services.prepared_reference_evidence import PreparedReference
from tests.unit.test_renderer_glyph_bridge import evidence, reference

reference = reference


@pytest.fixture
def saved(reference):
    pdf, renderer = reference
    prepared = PreparedReference(b"workbook", pdf, [b"image"], renderer, {"planSha256": "a" * 64})
    manifest = bridge.input_manifest(pdf, renderer)
    report = evidence(manifest)
    report["codeBundleSha256"] = hashlib.sha256(bridge.code_bundle()).hexdigest()
    report["inputSha256"] = hashlib.sha256(bridge._bundle([
        ("reference.pdf", pdf), ("manifest.json", bridge.canonical_bytes(manifest)),
    ])).hexdigest()
    version = SimpleNamespace(id="version-a", source_job_id="job-a")
    job = SimpleNamespace(id="job-a", attempt_number=1)
    items, data = {}, {}
    session, storage = Mock(), Mock()
    session.get.side_effect = lambda model, id: (
        job if model is ProcessingJob and id == job.id else items.get(id))
    session.add.side_effect = lambda item: items.update({item.id: item})

    def write(path, content):
        data[path] = content
        return path

    storage.write_bytes.side_effect = write
    storage.read_bytes.side_effect = lambda path: data[path]
    ref = service.save_prepared_glyphs(session, storage, version, "f" * 32, prepared, report)
    return session, storage, version, prepared, report, ref, data


def test_owned_roundtrip_revalidates_exact_input_and_code_without_execution(saved, monkeypatch):
    session, storage, version, prepared, expected, ref, _ = saved
    monkeypatch.setattr(bridge, "validate_wsl_pdf_glyphs",
                        lambda *a, **k: pytest.fail("No process"))
    assert service.load_prepared_glyphs(session, storage, version, prepared, ref) == expected
    assert expected["productionQualified"] is False


@pytest.mark.parametrize("change", ["version", "graph", "pdf", "runtime", "font_set", "code"])
def test_changed_owned_inputs_or_code_invalidate_report(saved, monkeypatch, change):
    session, storage, version, prepared, _, ref, _ = saved
    if change == "version":
        version.id = "another-version"
    elif change == "graph":
        prepared.evidence["planSha256"] = "b" * 64
    elif change == "pdf":
        prepared.pdf += b"changed"
    elif change in {"runtime", "font_set"}:
        key = "runtimeImage" if change == "runtime" else "fontDirectoryIdentity"
        prepared.report[key]["sha256"] = "0" * 64
    else:
        monkeypatch.setattr(bridge, "code_bundle", lambda: b"changed code")
    with pytest.raises((ValueError, RuntimeError)):
        service.load_prepared_glyphs(session, storage, version, prepared, ref)


@pytest.mark.parametrize("change", ["job", "name", "path", "media", "attempt", "bytes"])
def test_artifact_identity_and_bytes_are_owned(saved, change):
    session, storage, version, prepared, _, ref, data = saved
    item = session.get(JobArtifact, ref["artifactId"])
    attr = {"job": "job_id", "name": "artifact_name", "path": "relative_path",
            "media": "media_type", "attempt": "attempt_number"}
    if change == "bytes":
        data[ref["relativePath"]] += b"corrupt"
    else:
        setattr(item, attr[change], "changed")
    with pytest.raises(ValueError):
        service.load_prepared_glyphs(session, storage, version, prepared, ref)


@pytest.mark.parametrize("change", ["version", "graph", "glyph_count", "network", "code", "policy"])
def test_rehashed_report_is_not_trusted_without_semantic_validation(saved, change):
    session, storage, version, prepared, _, ref, data = saved
    item = session.get(JobArtifact, ref["artifactId"])
    value = json.loads(data[ref["relativePath"]])
    if change == "version":
        value["templateVersionId"] = "other"
    elif change == "graph":
        value["preparedEvidence"] = {}
    elif change == "glyph_count":
        value["report"]["glyphs"]["textInstructionGlyphs"] += 1
    elif change == "network":
        value["report"]["networkDenied"] = False
    elif change == "code":
        value["report"]["codeBundleSha256"] = "0" * 64
    else:
        value["policy"] = "other"
    payload = bridge.canonical_bytes(value)
    data[item.relative_path] = payload
    item.sha256 = ref["sha256"] = hashlib.sha256(payload).hexdigest()
    with pytest.raises((ValueError, RuntimeError)):
        service.load_prepared_glyphs(session, storage, version, prepared, ref)


def test_old_live_runtime_is_not_implicitly_upgraded(saved, monkeypatch):
    prepared = saved[3]
    prepared.report.pop("runtimeImage")
    monkeypatch.setattr(bridge, "validate_wsl_pdf_glyphs",
                        lambda *a, **k: pytest.fail("No process"))
    assert service.run_prepared_glyphs(prepared, "Fixture-Ubuntu") is None


def test_runtime_run_uses_saved_pdf_and_distribution_and_revalidates_return(saved, monkeypatch):
    from contextlib import nullcontext

    prepared, report = saved[3:5]
    callback = Mock(return_value=deepcopy(report))
    monkeypatch.setattr(service, "renderer_mutex", nullcontext)
    monkeypatch.setattr(bridge, "validate_wsl_pdf_glyphs", callback)
    assert service.run_prepared_glyphs(prepared, "Fixture-Ubuntu") == report
    callback.assert_called_once_with(prepared.pdf, prepared.report, distribution="Fixture-Ubuntu")
    callback.return_value["networkDenied"] = False
    with pytest.raises(RuntimeError):
        service.run_prepared_glyphs(prepared, "Fixture-Ubuntu")


def test_busy_does_not_start_helper(saved, monkeypatch):
    from app.core.errors import AppError

    monkeypatch.setattr(bridge, "validate_wsl_pdf_glyphs",
                        lambda *a, **k: pytest.fail("No process"))
    with service._RENDER_LOCK, pytest.raises(AppError) as caught:
        service.run_prepared_glyphs(saved[3], "Fixture-Ubuntu")
    assert caught.value.code == "RENDERER_BUSY"
