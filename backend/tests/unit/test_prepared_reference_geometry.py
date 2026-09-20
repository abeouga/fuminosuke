"""Evidence ownership/measurement fixtures, not native renderer qualification."""
import hashlib
import io
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw

from app.db.models import JobArtifact, ProcessingJob
from app.services import prepared_reference_geometry as service
from app.services import renderer_glyph_bridge as glyph
from app.services import renderer_vector_bridge as vector
from app.services.prepared_reference_evidence import PreparedReference
from tests.unit.test_border_probe_diagnostic import fixture as workbook_fixture
from tests.unit.test_reference_vector_measurement import paths
from tests.unit.test_renderer_glyph_bridge import evidence as glyph_evidence
from tests.unit.test_renderer_glyph_bridge import reference

reference = reference
EDGES = [((20, 30), (20, 80)), ((60, 30), (60, 80)),
         ((20, 30), (60, 30)), ((20, 80), (60, 80))]


def image_bytes(edges=EDGES, *, red_top=False):
    with Image.new("RGB", (150, 150), "white") as image:
        draw = ImageDraw.Draw(image)
        for edge in edges:
            draw.line(edge, fill="black", width=2)
        if red_top:
            draw.line(EDGES[2], fill="red", width=1)
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()


def vector_report(prepared, edges=EDGES):
    manifest = glyph.input_manifest(prepared.pdf, prepared.report)
    report = glyph_evidence(manifest)
    report["codeBundleSha256"] = hashlib.sha256(vector.code_bundle()).hexdigest()
    report["inputSha256"] = hashlib.sha256(glyph._bundle([
        ("reference.pdf", prepared.pdf), ("manifest.json", glyph.canonical_bytes(manifest)),
    ])).hexdigest()
    page = report["glyphs"]["pages"][0]
    page["contentOperations"] = 32
    strokes = paths(edges)
    report["vectors"] = {
        "policy": vector.POLICY, "pdfSha256": prepared.report["pdfSha256"], "pageCount": 1,
        "productionQualified": False, "visibilityVerified": False,
        "segmentCount": len(strokes["segments"]),
        "pages": [{"renderedPage": 1, "contentSha256": page["contentSha256"],
                   "contentOperations": 32, "mediaBox": [0, 0, 72, 72],
                   "cropBox": [0, 0, 72, 72], "effectiveBox": [0, 0, 72, 72],
                   "rotation": 0, "userUnit": 1, "strokes": strokes,
                   "textProgram": {"policy": "wsl-pdf-nonstroke-text-program-v1",
                                   "status": "RECORDED", "programSha256": "a" * 64,
                                   "fontResourcesSha256": "b" * 64, "retainedOperations": 4,
                                   "visibilityVerified": False}}],
    }
    return report


def bind_images(prepared, plan):
    prepared.report.update(sourceSha256=hashlib.sha256(prepared.workbook).hexdigest(),
                           pdfSha256=hashlib.sha256(prepared.pdf).hexdigest(),
                           imageSha256=[hashlib.sha256(i).hexdigest() for i in prepared.images])
    for page, sha in zip(plan["pages"], prepared.report["imageSha256"], strict=True):
        page["referenceImage"]["sha256"] = sha
    prepared.evidence["planSha256"] = service.digest(plan)


@pytest.fixture
def sample(reference):
    pdf, renderer = deepcopy(reference)
    renderer.update(rasterizer="poppler-pdftoppm", dpi=150, imageSizes=[[150, 150]])
    plan = {"sheetName": "Form", "pages": [{
        "page": 7, "range": "A1:B2", "width": 150, "height": 150,
        "referenceImage": {}, "fields": [{
            "cellId": "field-any", "sourceRange": "A1:B1",
            "rectangle": {"x": 20, "y": 30, "width": 40, "height": 50},
            "bindings": [{"mappingId": "mapping-any", "targetPath": "customFields.x"}],
        }],
    }]}
    prepared = PreparedReference(workbook_fixture()[0], pdf, [image_bytes()], renderer, {})
    bind_images(prepared, plan)
    return prepared, plan, service.GeometryEvidence(vector_report(prepared))


def test_every_field_is_measured_without_five_anchor_requirement_or_mutation(sample):
    before = deepcopy(sample)
    result = service.assess_geometry(*sample)
    assert sample == before
    assert len(result) == 1 and result[0]["page"] == 7
    assert result[0]["status"] == "MEASURED" and result[0]["maxErrorPx"] == 0
    assert result[0]["fieldCount"] == 1
    assert result[0]["fields"][0]["bindings"] == sample[1]["pages"][0]["fields"][0]["bindings"]
    assert result[0]["visibilityVerified"] is False
    assert result[0]["productionQualified"] is False
    assert result[0]["thresholdQualified"] is False
    assert result[0]["rasterObservation"]["sampleCount"] > 0


def test_vector_coordinates_without_final_raster_ink_are_not_success(sample):
    prepared, plan, _ = sample
    prepared.images = [image_bytes([])]
    bind_images(prepared, plan)
    sample[2].original_vectors = vector_report(prepared)
    result = service.assess_geometry(*sample)[0]
    assert result["maxErrorPx"] == 0
    assert result["status"] == "FAIL"
    assert "REFERENCE_RASTER_INK_UNVERIFIED" in result["codes"]


@pytest.mark.parametrize("change", ["pdf", "image", "workbook", "plan", "network", "code"])
def test_changed_input_or_helper_evidence_is_rejected(sample, change):
    prepared, plan, evidence = sample
    if change in {"pdf", "workbook"}:
        setattr(prepared, change, getattr(prepared, change) + b"changed")
    elif change == "image":
        prepared.images[0] += b"changed"
    elif change == "plan":
        plan["pages"][0]["fields"][0]["bindings"][0]["mappingId"] = "different"
    elif change == "network":
        evidence.original_vectors["networkDenied"] = False
    else:
        evidence.original_vectors["codeBundleSha256"] = "0" * 64
    with pytest.raises((ValueError, RuntimeError)):
        service.assess_geometry(*sample)


def test_displaced_field_is_not_fitted_back(sample):
    sample[1]["pages"][0]["fields"][0]["rectangle"]["x"] += 3
    sample[0].evidence["planSha256"] = service.digest(sample[1])
    result = service.assess_geometry(*sample)[0]
    assert result["status"] == "FAIL" and result["maxErrorPx"] == 3
    assert "MAX_ERROR_EXCEEDED" in result["codes"]


@pytest.fixture
def probed(sample):
    prepared, plan, evidence = deepcopy(sample)
    edges = EDGES[:2] + EDGES[3:]
    prepared.images = [image_bytes(edges)]
    bind_images(prepared, plan)
    evidence.original_vectors = vector_report(prepared, edges)
    selected = [{"cellId": "field-any", "sourceRange": "A1:B1", "sides": ["top"]}]
    copied, _ = service.probe_copy(prepared.workbook, "Form", selected)
    probe = PreparedReference(copied, b"probe PDF fixture", [image_bytes(edges, red_top=True)],
                              deepcopy(prepared.report), {})
    bind_images(probe, deepcopy(plan))
    evidence.probe = probe
    evidence.probe_vectors = vector_report(probe)
    return prepared, plan, evidence


def test_missing_border_probe_preserves_original_and_uses_explicit_field_identity(probed):
    before = deepcopy(probed)
    result = service.assess_geometry(*probed)[0]
    assert probed == before
    assert result["fieldCount"] == 1 and result["maxErrorPx"] == 0
    assert result["probeComparison"]["changedOutsideRedNeighborhood"] == 0
    assert result["measurementImageSha256"] != result["referenceImageSha256"]
    assert result["productionQualified"] is False


@pytest.mark.parametrize("change", ["missing", "workbook", "text", "font", "kernel", "pixels"])
def test_probe_cannot_change_text_fonts_environment_or_unrelated_pixels(probed, change):
    prepared, plan, evidence = probed
    probe = evidence.probe
    if change == "missing":
        evidence.probe = None
    elif change == "workbook":
        probe.workbook += b"changed"
    elif change in {"text", "font"}:
        key = "programSha256" if change == "text" else "fontResourcesSha256"
        evidence.probe_vectors["vectors"]["pages"][0]["textProgram"][key] = "0" * 64
    elif change == "kernel":
        probe.report["runtimeImage"]["kernel"] = "different"
    else:
        with Image.open(io.BytesIO(probe.images[0])) as opened:
            image = opened.copy()
        image.putpixel((100, 100), (0, 0, 0))
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        image.close()
        probe.images = [buffer.getvalue()]
        bind_images(probe, deepcopy(plan))
        evidence.probe_vectors = vector_report(probe)
    with pytest.raises((ValueError, RuntimeError)):
        service.assess_geometry(prepared, plan, evidence)


@pytest.fixture(params=[False, True])
def saved(request, sample, probed):
    prepared, plan, evidence = probed if request.param else sample
    version = SimpleNamespace(id="version-a", source_job_id="job-a",
                              quality_json={"printLayout": plan})
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
    ref = service.save_prepared_geometry(session, storage, version, "f" * 32, prepared, evidence)
    assert all(part["relativePath"] == f"artifacts/job-a/qualification/{'f' * 32}/{part['file']}"
               for part in ref["artifacts"])
    return session, storage, version, prepared, evidence, ref, data


def test_owned_roundtrip_recomputes_without_starting_any_helper(saved, monkeypatch):
    session, storage, version, prepared, evidence, ref, _ = saved
    monkeypatch.setattr(vector, "inspect_wsl_vectors", lambda *a, **k: pytest.fail("No process"))
    monkeypatch.setattr(service.renderer_wsl, "render_wsl_reference",
                        lambda *a, **k: pytest.fail("No process"))
    expected = service.assess_geometry(prepared, version.quality_json["printLayout"], evidence)
    assert service.load_prepared_geometry(session, storage, version, prepared, ref) == expected


@pytest.mark.parametrize("change", ["owner", "name", "path", "media", "attempt", "bytes",
                                    "version", "graph", "duplicate", "rehashed"])
def test_owned_saved_evidence_is_revalidated(saved, change):
    session, storage, version, prepared, _, ref, data = saved
    part = ref["artifacts"][0]
    item = session.get(JobArtifact, part["artifactId"])
    attrs = {"owner": "job_id", "name": "artifact_name", "path": "relative_path",
             "media": "media_type", "attempt": "attempt_number"}
    if change in attrs:
        setattr(item, attrs[change], "changed")
    elif change == "bytes":
        data[item.relative_path] += b"changed"
    elif change == "version":
        version.id = "other"
    elif change == "graph":
        prepared.evidence["planSha256"] = "0" * 64
    elif change == "duplicate":
        ref["artifacts"].append(deepcopy(part))
    else:
        value = json.loads(data[item.relative_path])
        value["networkDenied"] = False
        data[item.relative_path] = glyph.canonical_bytes(value)
        item.sha256 = part["sha256"] = hashlib.sha256(data[item.relative_path]).hexdigest()
    with pytest.raises((ValueError, RuntimeError)):
        service.load_prepared_geometry(session, storage, version, prepared, ref)


def test_unselected_legacy_runtime_does_not_execute_or_upgrade(sample, monkeypatch):
    prepared, plan, _ = sample
    prepared.report.pop("runtimeImage")
    monkeypatch.setattr(vector, "inspect_wsl_vectors", lambda *a, **k: pytest.fail("No process"))
    assert service.run_prepared_geometry(prepared, plan, "Fixture-Ubuntu") is None


def test_busy_never_starts_a_helper(sample, monkeypatch):
    from app.core.errors import AppError

    monkeypatch.setattr(vector, "inspect_wsl_vectors", lambda *a, **k: pytest.fail("No process"))
    with service._RENDER_LOCK, pytest.raises(AppError) as caught:
        service.run_prepared_geometry(sample[0], sample[1], "Fixture-Ubuntu")
    assert caught.value.code == "RENDERER_BUSY"


@pytest.mark.parametrize("probe_needed", [False, True])
def test_run_uses_exact_saved_pdf_and_optional_copy_with_same_runtime(
    sample, probed, monkeypatch, probe_needed,
):
    from contextlib import nullcontext

    prepared, plan, expected = probed if probe_needed else sample
    monkeypatch.setattr(service, "renderer_mutex", nullcontext)
    inspect = Mock(side_effect=[deepcopy(expected.original_vectors)] + (
        [deepcopy(expected.probe_vectors)] if probe_needed else []))
    render = Mock()
    if probe_needed:
        probe = expected.probe
        render.return_value = (probe.pdf, probe.images, probe.report)
    monkeypatch.setattr(vector, "inspect_wsl_vectors", inspect)
    monkeypatch.setattr(service.renderer_wsl, "render_wsl_reference", render)
    result = service.run_prepared_geometry(prepared, plan, "Fixture-Ubuntu")
    assert service.assess_geometry(prepared, plan, result) == service.assess_geometry(
        prepared, plan, expected)
    assert inspect.call_args_list[0].args == (prepared.pdf, prepared.report)
    assert inspect.call_args_list[0].kwargs == {"distribution": "Fixture-Ubuntu"}
    if probe_needed:
        render.assert_called_once_with(probe.workbook, distribution="Fixture-Ubuntu",
                                       runtime_id=prepared.report["runtimeImage"]["sha256"])
        assert inspect.call_args_list[1].args == (probe.pdf, probe.report)
    else:
        render.assert_not_called()
    assert service._RENDER_LOCK.acquire(blocking=False)
    service._RENDER_LOCK.release()


def test_bad_helper_evidence_fails_before_render_and_releases_lock(probed, monkeypatch):
    from contextlib import nullcontext

    prepared, plan, evidence = probed
    evidence.original_vectors["networkDenied"] = False
    monkeypatch.setattr(service, "renderer_mutex", nullcontext)
    monkeypatch.setattr(vector, "inspect_wsl_vectors",
                        Mock(return_value=evidence.original_vectors))
    monkeypatch.setattr(service.renderer_wsl, "render_wsl_reference",
                        lambda *a, **k: pytest.fail("No renderer after invalid helper"))
    with pytest.raises(RuntimeError):
        service.run_prepared_geometry(prepared, plan, "Fixture-Ubuntu")
    assert service._RENDER_LOCK.acquire(blocking=False)
    service._RENDER_LOCK.release()


def test_ambiguous_line_is_not_replaced_by_a_measurement_probe(sample):
    prepared, plan, evidence = sample
    evidence.original_vectors = vector_report(prepared, [*EDGES, ((21, 30), (21, 80))])
    result = service.assess_geometry(prepared, plan, evidence)[0]
    assert result["status"] == "FAIL" and result["probeComparison"] is None
    assert result["fields"][0]["edges"][0]["status"] == "AMBIGUOUS"


def test_unrequested_probe_is_rejected(sample, probed):
    sample[2].probe = probed[2].probe
    sample[2].probe_vectors = probed[2].probe_vectors
    with pytest.raises(ValueError, match="Unexpected"):
        service.assess_geometry(*sample)


def test_absent_text_program_cannot_justify_a_probe(probed):
    probed[2].probe_vectors["vectors"]["pages"][0].pop("textProgram")
    with pytest.raises(ValueError, match="text program"):
        service.assess_geometry(*probed)


def test_subpixel_dash_without_interior_samples_remains_unverified(sample):
    for segment in sample[2].original_vectors["vectors"]["pages"][0]["strokes"]["segments"]:
        segment["dash"] = [.3325, .3325]
    result = service.assess_geometry(*sample)[0]
    assert result["maxErrorPx"] == 0 and result["status"] == "FAIL"
    assert "REFERENCE_RASTER_INK_UNVERIFIED" in result["codes"]
    assert all(s["reason"] == "NO_INTERIOR_PAINT_SAMPLES"
               for s in result["rasterObservation"]["segments"])


def test_overpainting_cannot_be_promoted_to_visibility_certificate(sample):
    prepared, plan, _ = sample
    with Image.new("RGB", (150, 150), "black") as image:
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        prepared.images = [buffer.getvalue()]
    bind_images(prepared, plan)
    sample[2].original_vectors = vector_report(prepared)
    result = service.assess_geometry(*sample)[0]
    assert result["maxErrorPx"] == 0
    assert result["visibilityVerified"] is False
    assert result["productionQualified"] is False
    assert result["rasterObservation"]["objectIdentityVerified"] is False
    assert all(s["contrastCoverage"] == 0 for s in result["rasterObservation"]["segments"])


@pytest.mark.parametrize("part", [None, {"file": None}, {"file": []}])
def test_malformed_saved_part_is_a_validation_error(saved, part):
    session, storage, version, prepared, _, ref, _ = saved
    ref["artifacts"][0] = part
    with pytest.raises(ValueError):
        service.load_prepared_geometry(session, storage, version, prepared, ref)
