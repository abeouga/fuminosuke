"""Orchestration with a synthetic renderer, not evidence of native visibility."""
import hashlib
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services import renderer_stroke_probe_worker as worker
from app.services.renderer_stroke_probe_pixels import unpack_delta
from tests.unit.test_renderer_glyph_pdf import fixture_pdf, validate


def dashed():
    return b"BT /F1 12 Tf <01> Tj ET .2 w [.3325 .3325] 0 d 10 20 m 80 20 l S"


def test_page_and_operation_identity_bound_deltas_keep_original_bytes(tmp_path, monkeypatch):
    pdf = fixture_pdf([dashed() + b" 10 30 m 80 30 l S", dashed()])
    original = bytes([255, 255, 255, 0, 0, 0])
    changed = bytes([255, 255, 255, 128, 128, 128])
    calls = []

    def render(path, page, prefix, remaining):
        calls.append((path.name, page, remaining))
        assert path.exists() and remaining > 0
        if path.name == "source.pdf":
            assert path.read_bytes() == pdf
        return 2, 1, original if path.name == "source.pdf" else changed

    monkeypatch.setattr(worker, "_render", render)
    report = worker.inspect_stroke_probes(pdf, validate(pdf), tmp_path)
    assert report["sourcePdfSha256"] == hashlib.sha256(pdf).hexdigest()
    assert report["productionQualified"] is False
    probes = report["probes"]
    assert [probe["renderedPage"] for probe in probes] == [1, 1, 2]
    assert probes[0]["operation"] != probes[1]["operation"]
    assert probes[0]["mutation"]["probePdfSha256"] != probes[1]["mutation"]["probePdfSha256"]
    assert all(unpack_delta(original, p["delta"]) == changed for p in probes)
    assert [name for name, _, _ in calls] == [
        "source.pdf", "probe.pdf", "probe.pdf", "source.pdf", "probe.pdf"]
    assert not (tmp_path / "stroke-probes").exists()


def test_no_dashed_stroke_does_not_call_renderer(tmp_path, monkeypatch):
    pdf = fixture_pdf([b"BT /F1 12 Tf <01> Tj ET 1 w 10 20 m 80 20 l S"])
    monkeypatch.setattr(worker, "_render", lambda *a: pytest.fail("No raster needed"))
    report = worker.inspect_stroke_probes(pdf, validate(pdf), tmp_path)
    assert report["probes"] == [] and report["visibilityVerified"] is False


def test_combined_fill_stroke_is_explicitly_unsupported_not_removed(tmp_path, monkeypatch):
    pdf = fixture_pdf([dashed().replace(b"l S", b"l B")])
    monkeypatch.setattr(worker, "_render", lambda *a: pytest.fail("No partial raster"))
    report = worker.inspect_stroke_probes(pdf, validate(pdf), tmp_path)
    assert report["probes"][0]["status"] == "UNSUPPORTED"
    assert report["probes"][0]["reason"] == "COMBINED_FILL"


def test_probe_budget_rejects_before_files_or_renderer(tmp_path, monkeypatch):
    pdf = fixture_pdf([dashed()])
    monkeypatch.setattr(worker, "MAX_PROBES", 0)
    monkeypatch.setattr(worker, "_render", lambda *a: pytest.fail("No raster"))
    with pytest.raises(ValueError, match="count"):
        worker.inspect_stroke_probes(pdf, validate(pdf), tmp_path)
    assert not (tmp_path / "stroke-probes").exists()


def test_render_failure_cleans_only_its_private_files(tmp_path, monkeypatch):
    pdf = fixture_pdf([dashed()])
    marker = tmp_path / "not-owned.pdf"
    marker.write_bytes(b"retain")
    monkeypatch.setattr(worker, "_render", Mock(side_effect=RuntimeError("native failure")))
    with pytest.raises(RuntimeError):
        worker.inspect_stroke_probes(pdf, validate(pdf), tmp_path)
    assert marker.read_bytes() == b"retain"
    assert not (tmp_path / "stroke-probes").exists()


def test_existing_probe_directory_is_not_overwritten_or_removed(tmp_path):
    directory = tmp_path / "stroke-probes"
    directory.mkdir()
    marker = directory / "source.pdf"
    marker.write_bytes(b"not owned")
    pdf = fixture_pdf([dashed()])
    with pytest.raises(FileExistsError):
        worker.inspect_stroke_probes(pdf, validate(pdf), tmp_path)
    assert marker.read_bytes() == b"not owned"


def test_native_command_is_fixed_and_partial_output_is_removed(tmp_path, monkeypatch):
    calls = []

    def fail(args, **options):
        calls.append((args, options))
        Path(args[-1]).with_suffix(".ppm").write_bytes(b"partial")
        raise subprocess.TimeoutExpired(args, options["timeout"])

    monkeypatch.setattr(worker.subprocess, "run", fail)
    with pytest.raises(subprocess.TimeoutExpired):
        worker._render(tmp_path / "source.pdf", 2, tmp_path / "probe", 3.0)
    assert calls[0][0][:9] == ["/usr/bin/pdftoppm", "-f", "2", "-l", "2", "-r", "150",
                              "-singlefile", str(tmp_path / "source.pdf")]
    assert calls[0][1]["timeout"] == 3.0
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    assert not (tmp_path / "probe.ppm").exists()
