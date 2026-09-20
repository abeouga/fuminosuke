"""Qualification API ownership fixtures; never certify their synthetic PDF/PNG."""
import copy
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.services import prepared_reference_geometry as geometry
from app.services import (
    prepared_reference_glyphs,
    prepared_reference_replay,
    print_preparation_service,
)
from app.services import reference_qualification_service as qualification
from app.services import renderer_glyph_bridge as glyph
from app.services import renderer_vector_bridge as vector
from app.services.renderer_pdf_strokes import inventory
from tests.integration import test_prepared_reference_glyph_api as existing
from tests.unit.test_renderer_glyph_bridge import evidence

glyph_fixture = existing.glyph_fixture
prepared_fixture = existing.prepared_fixture
print_template = existing.print_template
saved_template = existing.saved_template


@pytest.fixture(autouse=True)
def synthetic_helpers_do_not_take_the_machine_renderer_mutex(monkeypatch):
    # Every native operation in this file is a synthetic fixture. Keep the
    # process-local lock, but do not contend with real renderer tests elsewhere.
    # Real mutex/namespace behavior belongs to the dedicated helper integration.
    for module in (print_preparation_service, prepared_reference_replay,
                   prepared_reference_glyphs):
        monkeypatch.setattr(module, "renderer_mutex", nullcontext)
    monkeypatch.setattr(vector, "inspect_wsl_vectors",
                        lambda *a, **k: pytest.fail("No native vector helper in API fixture"))


@pytest.fixture
def geometry_fixture(client, glyph_fixture, monkeypatch):
    calls = []

    def measure(prepared, plan, distribution):
        calls.append((prepared, copy.deepcopy(plan), distribution))
        manifest = glyph.input_manifest(prepared.pdf, prepared.report)
        report = evidence(manifest)
        report["codeBundleSha256"] = hashlib.sha256(vector.code_bundle()).hexdigest()
        report["inputSha256"] = hashlib.sha256(glyph._bundle([
            ("reference.pdf", prepared.pdf), ("manifest.json", glyph.canonical_bytes(manifest)),
        ])).hexdigest()
        count = len(plan["pages"])
        report["glyphs"]["pageCount"] = report["glyphs"]["textInstructionGlyphs"] = count
        original = report["glyphs"]["pages"][0]
        report["glyphs"]["pages"] = [{**original, "renderedPage": i, "contentOperations": 1000}
                                      for i in range(1, count + 1)]
        pages = []
        for number, page in enumerate(plan["pages"], 1):
            box = [0, 0, page["width"] * .48 - 1e-6, page["height"] * .48 - 1e-6]
            operations = []
            for field in page["fields"]:
                if not field.get("bindings"):
                    continue
                rect = field["rectangle"]
                x, y, right, bottom = (rect["x"], rect["y"], rect["x"] + rect["width"],
                                       rect["y"] + rect["height"])
                for start, end in (((x, y), (x, bottom)), ((right, y), (right, bottom)),
                                   ((x, y), (right, y)), ((x, bottom), (right, bottom))):
                    operations += [([start[0] * .48, box[3] - start[1] * .48], b"m"),
                                   ([end[0] * .48, box[3] - end[1] * .48], b"l")]
            pages.append({"renderedPage": number, "contentSha256": original["contentSha256"],
                          "contentOperations": 1000, "mediaBox": box, "cropBox": box,
                          "effectiveBox": box, "userUnit": 1, "rotation": 0,
                          "strokes": inventory([*operations, ([], b"S")])})
        report["vectors"] = {
            "policy": vector.POLICY, "pdfSha256": prepared.report["pdfSha256"],
            "pageCount": count, "productionQualified": False, "visibilityVerified": False,
            "segmentCount": sum(len(p["strokes"]["segments"]) for p in pages), "pages": pages,
        }
        return geometry.GeometryEvidence(report)

    monkeypatch.setattr(qualification, "run_prepared_geometry", measure, raising=False)
    return glyph_fixture, calls, measure


def qualify(client, fixture):
    return existing.qualify(client, fixture[0])


def rewrite_report(client, version_id, report):
    with client.app.state.database.session() as db:
        version = db.get(TemplateVersion, version_id)
        ref = version.quality_json["referenceQualification"]
        payload = glyph.canonical_bytes(report)
        client.app.state.storage.write_bytes(ref["relativePath"], payload)
        version.quality_json = {**version.quality_json, "referenceQualification": {
            **ref, "sha256": hashlib.sha256(payload).hexdigest()}}
        db.commit()


def test_geometry_api_saves_owned_evidence_and_recomputes_on_get(client, geometry_fixture):
    fixture, calls, _ = geometry_fixture
    version, url, ready, *_ = fixture
    report = qualify(client, geometry_fixture)
    assert "geometryEvidence" in report
    assert len(calls) == 1 and calls[0][2] == "Fixture-Ubuntu"
    assert calls[0][1] == ready["saved"]
    assert len(report["fieldGeometry"]) == 2
    assert report["fieldGeometry"][-1]["fieldCount"] == 1
    assert report["fieldGeometry"][-1]["maxErrorPx"] < 1e-6
    # Exact vectors plus the deliberately blank fixture PNG are NOT visible ink.
    assert report["fieldGeometry"][-1]["status"] == "FAIL"
    assert "REFERENCE_RASTER_INK_UNVERIFIED" in report["fieldGeometry"][-1]["codes"]
    assert "REFERENCE_RASTER_INK_UNVERIFIED" in report["codes"]
    assert report["qualified"] is False and report["environmentQualified"] is False
    assert "GEOMETRY_TOLERANCE_UNQUALIFIED" in report["productionBlockers"]
    assert client.get(url + "/qualification").json() == report
    assert len(calls) == 1 and client.get(url).json()["saved"] == ready["saved"]
    with client.app.state.database.session() as db:
        row = db.get(TemplateVersion, version)
        assert row.status == "DRAFT"
        for ref in report["geometryEvidence"]["artifacts"]:
            assert db.get(JobArtifact, ref["artifactId"]).job_id == row.source_job_id


@pytest.mark.parametrize("change", ["bytes", "owner", "attempt", "network", "code", "count"])
def test_geometry_evidence_changes_invalidate_get_even_when_rehashed(
    client, geometry_fixture, change,
):
    version, url, *_ = geometry_fixture[0]
    report = qualify(client, geometry_fixture)
    part = report["geometryEvidence"]["artifacts"][0]
    storage = client.app.state.storage
    with client.app.state.database.session() as db:
        item = db.get(JobArtifact, part["artifactId"])
        if change == "bytes":
            storage.write_bytes(item.relative_path, b"changed")
        elif change == "owner":
            db.add(ProcessingJob(id="foreign-job", job_type="SPREADSHEET_TEMPLATE",
                                 request_hash="e" * 64))
            db.flush()
            item.job_id = "foreign-job"
        elif change == "attempt":
            item.attempt_number += 1
        else:
            payload = json.loads(storage.read_bytes(item.relative_path))
            if change == "network":
                payload["networkDenied"] = False
            elif change == "code":
                payload["codeBundleSha256"] = "0" * 64
            else:
                payload["vectors"]["pageCount"] = 1
            raw = glyph.canonical_bytes(payload)
            storage.write_bytes(item.relative_path, raw)
            item.sha256 = part["sha256"] = hashlib.sha256(raw).hexdigest()
        db.commit()
    rewrite_report(client, version, report)
    status = client.get(url + "/qualification").json()
    assert status["status"] == "UNVERIFIED" and status["qualified"] is False
    assert status["codes"] == ["REFERENCE_EVIDENCE_CHANGED"]
    assert len(geometry_fixture[1]) == 1


@pytest.mark.parametrize("remove", [False, True])
def test_summary_flags_are_recomputed_or_cleared_without_evidence(client, geometry_fixture, remove):
    version, url, *_ = geometry_fixture[0]
    report = qualify(client, geometry_fixture)
    if remove:
        report.pop("geometryEvidence")
    report["fieldGeometry"] = [{"status": "PASS", "productionQualified": True}]
    rewrite_report(client, version, report)
    status = client.get(url + "/qualification").json()
    assert status["qualified"] is False
    if remove:
        assert status == {
            "status": "UNVERIFIED",
            "qualified": False,
            "codes": ["REFERENCE_EVIDENCE_CHANGED"],
        }
    else:
        assert len(status["fieldGeometry"]) == 2
        assert status["fieldGeometry"][-1]["status"] == "FAIL"


def test_field_ink_summary_cannot_override_owned_blank_raster(client, geometry_fixture):
    version, url, *_ = geometry_fixture[0]
    report = qualify(client, geometry_fixture)
    before = copy.deepcopy(report["fieldGeometry"])
    for page in report["fieldGeometry"]:
        page["fieldRasterObservation"] = {
            "allRequiredInkObserved": True, "productionQualified": True,
            "visibilityVerified": True, "observations": [],
        }
    rewrite_report(client, version, report)
    status = client.get(url + "/qualification").json()
    assert status["fieldGeometry"] == before
    assert status["qualified"] is False
    assert "REFERENCE_RASTER_INK_UNVERIFIED" in status["codes"]


@pytest.mark.parametrize("module", ["reference_field_raster.py", "reference_splash_raster.py",
                                    "renderer_splash_calibration.py"])
def test_field_ink_implementation_change_invalidates_saved_qualification(
    client, geometry_fixture, monkeypatch, module,
):
    _, url, *_ = geometry_fixture[0]
    qualify(client, geometry_fixture)
    original = Path.read_bytes

    def changed(path):
        payload = original(path)
        if path.name == module:
            return payload + b"\n# Different implementation fixture\n"
        return payload

    monkeypatch.setattr(Path, "read_bytes", changed)
    status = client.get(url + "/qualification").json()
    assert status == {"status": "UNVERIFIED", "qualified": False,
                      "codes": ["PRINT_LAYOUT_CHANGED"]}
    assert len(geometry_fixture[1]) == 1


def test_geometry_failure_keeps_other_diagnostics_and_hides_raw_output(
    client, geometry_fixture, monkeypatch,
):
    def fail(*args):
        raise RuntimeError("private path and source text")

    monkeypatch.setattr(qualification, "run_prepared_geometry", fail)
    report = qualify(client, geometry_fixture)
    assert report["fieldGeometry"] is None and "geometryEvidence" not in report
    assert report["glyphVerification"]["status"] == "PASS" and report["deterministic"] is True
    assert len(report["pages"]) == 2 and report["qualified"] is False
    assert report["geometryDiagnostic"]["code"] == "REFERENCE_FIELD_EDGES_UNVERIFIED"
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize("change", ["config", "pdf"])
def test_change_during_measurement_does_not_publish_stale_geometry(
    client, geometry_fixture, monkeypatch, change,
):
    fixture, _, measure = geometry_fixture
    version, url, ready, *_ = fixture
    with client.app.state.database.session() as db:
        row = db.get(TemplateVersion, version)
        before, path = copy.deepcopy(row.quality_json), row.config_relative_path
        artifacts = list(db.scalars(select(JobArtifact.id)))
    if change == "pdf":
        path = ready["saved"]["printFiles"]["pdf"]["relativePath"]

    def changed(*args):
        result = measure(*args)
        storage = client.app.state.storage
        storage.write_bytes(path, storage.read_bytes(path) + b"changed")
        return result

    monkeypatch.setattr(qualification, "run_prepared_geometry", changed)
    response = client.post(url + "/qualification", params={"base_revision": ready["revision"]})
    assert response.status_code == 409
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).quality_json == before
        assert list(db.scalars(select(JobArtifact.id))) == artifacts


def test_saved_differential_manifest_is_checked_on_get_even_when_rehashed(
    client, geometry_fixture, monkeypatch,
):
    from app.services.renderer_stroke_probe_pixels import DIFFERENTIAL_POLICY

    fixture, _, measure = geometry_fixture
    version, url, *_ = fixture

    def with_differential(*args):
        result = measure(*args)
        result.original_vectors["strokeProbes"] = {
            "policy": DIFFERENTIAL_POLICY,
            "sourcePdfSha256": result.original_vectors["vectors"]["pdfSha256"],
            "dpi": 150, "rasterizer": "poppler-pdftoppm", "probes": [],
            "visibilityVerified": False, "productionQualified": False,
        }
        return result

    monkeypatch.setattr(qualification, "run_prepared_geometry", with_differential)
    report = qualify(client, geometry_fixture)
    assert all(page["strokeDifferential"] == [] for page in report["fieldGeometry"])
    assert client.get(url + "/qualification").json() == report
    part = report["geometryEvidence"]["artifacts"][0]
    storage = client.app.state.storage
    with client.app.state.database.session() as db:
        item = db.get(JobArtifact, part["artifactId"])
        payload = json.loads(storage.read_bytes(item.relative_path))
        payload["strokeProbes"]["sourcePdfSha256"] = "0" * 64
        raw = glyph.canonical_bytes(payload)
        storage.write_bytes(item.relative_path, raw)
        item.sha256 = part["sha256"] = hashlib.sha256(raw).hexdigest()
        db.commit()
    rewrite_report(client, version, report)
    status = client.get(url + "/qualification").json()
    assert status["qualified"] is False and status["status"] == "UNVERIFIED"
    assert status["codes"] == ["REFERENCE_EVIDENCE_CHANGED"]
    assert len(geometry_fixture[1]) == 1  # GET never starts a helper.


def test_saved_splash_pixels_are_rechecked_on_get_even_when_artifact_is_rehashed(
    client, geometry_fixture, monkeypatch,
):
    from tests.unit.test_renderer_splash_calibration import evidence, pixels

    fixture, calls, measure = geometry_fixture
    version, url, *_ = fixture

    def calibrated(*args):
        result = measure(*args)
        result.original_vectors["splashCalibration"] = evidence(pixels())
        return result

    monkeypatch.setattr(qualification, "run_prepared_geometry", calibrated)
    report = qualify(client, geometry_fixture)
    assert all(p["splashRasterObservation"]["calibration"]["status"] == "OBSERVED"
               for p in report["fieldGeometry"])
    assert report["qualified"] is False
    assert client.get(url + "/qualification").json() == report
    part = report["geometryEvidence"]["artifacts"][0]
    storage = client.app.state.storage
    with client.app.state.database.session() as db:
        item = db.get(JobArtifact, part["artifactId"])
        payload = json.loads(storage.read_bytes(item.relative_path))
        payload["splashCalibration"]["raster"]["probePixelSha256"] = "0" * 64
        raw = glyph.canonical_bytes(payload)
        storage.write_bytes(item.relative_path, raw)
        item.sha256 = part["sha256"] = hashlib.sha256(raw).hexdigest()
        db.commit()
    rewrite_report(client, version, report)
    status = client.get(url + "/qualification").json()
    assert status["qualified"] is False and status["status"] == "UNVERIFIED"
    assert status["codes"] == ["REFERENCE_EVIDENCE_CHANGED"]
    assert len(calls) == 1
