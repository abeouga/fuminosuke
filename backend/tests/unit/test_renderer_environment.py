from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services.renderer_environment import (
    THRESHOLD_POLICY,
    environment_report,
    file_identity,
    font_identity,
    validate_environment,
)


def identity():
    return {"renderer": {"sha": "binary"}, "geometry": "v1", "os": "build",
            "fonts": {"sha": "font", "codes": ["FONT_FALLBACK_UNVERIFIED"]},
            "thresholdPolicyVersion": THRESHOLD_POLICY, "rasterizer": "pdfium"}


@pytest.mark.parametrize("key", ["renderer", "geometry", "os", "fonts", "rasterizer",
                                 "thresholdPolicyVersion"])
def test_environment_changes_invalidate_even_forged_qualified(key):
    current = identity()
    report = environment_report(current)
    report["environmentQualified"] = True
    altered = copy.deepcopy(current)
    if key == "fonts":
        altered[key]["sha"] = "changed"
    else:
        altered[key] = "changed"
    codes = validate_environment(report, altered)
    assert "RENDERER_ENVIRONMENT_CHANGED" in codes
    assert "NETWORK_ISOLATION_UNVERIFIED" in codes


def test_correct_identity_cannot_override_missing_isolation_by_boolean():
    current = identity()
    report = environment_report(current)
    report.update(environmentQualified=True, codes=[])
    assert "FONT_FALLBACK_UNVERIFIED" in validate_environment(report, current)


def test_font_sha_mutation_is_observed(tmp_path):
    path = tmp_path / "font-identity-fixture"
    path.write_bytes(b"one")
    first = file_identity(path)
    path.write_bytes(b"two")
    assert file_identity(path)["sha256"] != first["sha256"]


@pytest.mark.parametrize("fixture", ["explicit843", "font-Aptos"])
def test_font_availability_never_proves_actual_office_selection(fixture):
    base = Path(__file__).parents[3] / "docs/reference-renderer-evidence/calibration" / fixture
    font = font_identity((base / "source.xlsx").read_bytes())
    assert font["actualOfficeFontVerified"] is False
    assert "FONT_FALLBACK_UNVERIFIED" in font["codes"]
    assert font["normal"]["family"] == json.loads(
        (base / "measurement.json").read_text())["normalFont"]["family"]
