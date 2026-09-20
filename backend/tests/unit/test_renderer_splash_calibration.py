"""Synthetic protocol tests; actual Poppler agreement needs native integration."""
import hashlib
import io
import math
from copy import deepcopy

import pytest
from pypdf import PdfReader

from app.services import renderer_splash_calibration as calibration
from app.services import renderer_stroke_probe_worker as worker
from app.services.renderer_stroke_probe_pixels import pack_delta


def pixels(*, shift=0, omit=None):
    rgb = bytearray(b"\xff" * (calibration.WIDTH * calibration.HEIGHT * 3))
    for case in calibration.cases():
        if case["case"] == omit:
            continue
        n = math.floor(case["center"] - case["width"] / 2 + .5) + shift
        axis = 1 if case["vertical"] else 0
        lo, hi = sorted([case["from"][axis], case["to"][axis]])
        for k in range(math.floor(lo), math.ceil(hi) + 1):
            x, y = (n, k) if case["vertical"] else (k, n)
            offset = (y * calibration.WIDTH + x) * 3
            rgb[offset:offset + 3] = bytes(case["color"])
    return bytes(rgb)


def evidence(rgb):
    return {"policy": calibration.POLICY, "dpi": 150,
            "fixtureSha256": hashlib.sha256(calibration.fixture_pdf()).hexdigest(),
            "raster": pack_delta(b"\xff" * len(rgb), rgb, calibration.WIDTH, calibration.HEIGHT),
            "visibilityVerified": False, "productionQualified": False}


def test_fixed_pdf_is_deterministic_and_contains_only_the_declared_synthetic_paths():
    content = calibration.fixture_pdf()
    assert content == calibration.fixture_pdf() and len(content) < 64 * 1024
    # Only our generated fixed fixture is parsed here, never an uploaded PDF.
    reader = PdfReader(io.BytesIO(content), strict=True)
    assert len(reader.pages) == 1 and dict(reader.pages[0]["/Resources"]) == {}
    page = reader.pages[0]
    assert list(page.mediabox) == [0, 0, 144, 528]
    ops = page.get_contents().operations
    assert len(calibration.cases()) == 256
    assert len(ops) == 256 * 8
    assert {op for _, op in ops} == {b"q", b"RG", b"J", b"w", b"m", b"l", b"S", b"Q"}


@pytest.mark.parametrize("shift", [-1, 0, 1])
def test_exact_sampled_pixels_not_saved_flags_decide_model_observation(shift):
    report = evidence(pixels(shift=shift))
    before = deepcopy(report)
    observed = calibration.assess(report)
    assert report == before
    assert observed["caseCount"] == 256 and observed["sampleCount"] == 256 * 17
    assert observed["status"] == ("OBSERVED" if shift == 0 else "UNVERIFIED")
    assert observed["visibilityVerified"] is observed["productionQualified"] is False
    assert len(observed["unmatchedCases"]) == (0 if shift == 0 else 256)


def test_one_missing_case_cannot_be_hidden_by_other_successful_cases():
    assert calibration.assess(evidence(pixels(omit=123)))["unmatchedCases"] == [123]


@pytest.mark.parametrize("change", ["policy", "pdf", "dpi", "dimensions", "pixels",
                                   "qualified", "missing"])
def test_changed_or_incomplete_evidence_is_rejected(change):
    report = evidence(pixels())
    if change == "policy":
        report["policy"] = "different"
    elif change == "pdf":
        report["fixtureSha256"] = "0" * 64
    elif change == "dpi":
        report["dpi"] = 300
    elif change == "dimensions":
        report["raster"]["width"] += 1
    elif change == "pixels":
        report["raster"]["probePixelSha256"] = "0" * 64
    elif change == "qualified":
        report["productionQualified"] = True
    else:
        report.pop("raster")
    with pytest.raises(ValueError):
        calibration.assess(report)


def test_collection_uses_existing_bounded_renderer_and_removes_only_its_files(
    tmp_path, monkeypatch,
):
    marker = tmp_path / "keep.pdf"
    marker.write_bytes(b"unrelated")
    calls = []

    def render(path, page, prefix, remaining):
        calls.append((path, page, prefix, remaining))
        assert path.read_bytes() == calibration.fixture_pdf()
        return calibration.WIDTH, calibration.HEIGHT, pixels()

    monkeypatch.setattr(worker, "_render", render)
    report = calibration.collect(tmp_path)
    assert calibration.assess(report)["status"] == "OBSERVED"
    assert len(calls) == 1 and calls[0][1] == 1 and calls[0][3] == 10.0
    assert marker.read_bytes() == b"unrelated"
    assert not (tmp_path / "splash-calibration").exists()


def test_existing_calibration_directory_is_never_overwritten(tmp_path):
    owned = tmp_path / "splash-calibration"
    owned.mkdir()
    (owned / "fixture.pdf").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        calibration.collect(tmp_path)
    assert (owned / "fixture.pdf").read_bytes() == b"keep"


def test_native_failure_preserves_other_files_and_has_no_success_fallback(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError("native failed")

    monkeypatch.setattr(worker, "_render", fail)
    with pytest.raises(RuntimeError, match="native failed"):
        calibration.collect(tmp_path)
    assert not (tmp_path / "splash-calibration").exists()
