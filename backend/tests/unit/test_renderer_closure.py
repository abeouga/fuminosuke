from __future__ import annotations

import copy
import hashlib
import json

import pytest

from app.services.reference_crop_safety import clearance, qualify_crop
from app.services.renderer_dependencies import file_record, validate_manifest
from app.services.renderer_environment import environment_report, validate_environment
from tests.unit.test_renderer_environment import identity


@pytest.mark.parametrize("dpi", [96, 150, 300])
def test_same_physical_clearance_policy_with_independent_geometry_failure(dpi):
    wide = dict(
        widthMm=20,
        heightMm=10,
        contentInsetMm=2,
        neighborGapMm=2,
        borderStrokeMm=0.1,
        inkStrokeMm=0.3,
        paddingMm=0.5,
    )
    error = 0.05 * dpi / 25.4
    assert clearance(wide, error, dpi)["status"] == "PASS"
    narrow = {**wide, "contentInsetMm": 0.25}
    assert clearance(narrow, error, dpi)["status"] == "FAIL"
    assert clearance(wide, 20, dpi)["status"] == "FAIL"
    assert clearance({**wide, "paddingMm": 2}, error, dpi)["status"] == "FAIL"


def test_blank_reference_does_not_invent_handwriting_clearance():
    result = qualify_crop({"fields": [{"cellId": "id", "sourceRange": "A1"}]}, {"status": "PASS"})
    assert result["qualified"] is False
    assert result["fields"][0]["code"] == "CROP_CLEARANCE_UNVERIFIED"


def test_loaded_dependency_mutation_and_forged_manifest(tmp_path):
    path = tmp_path / "runtime.dll"
    path.write_bytes(b"initial")
    files = {"application": [file_record(path)], "operatingSystem": []}
    manifest = {
        **files,
        "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
    }
    assert validate_manifest(manifest)
    forged = copy.deepcopy(manifest)
    forged["application"][0]["sha256"] = "0" * 64
    assert not validate_manifest(forged)
    path.write_bytes(b"changed")
    assert not validate_manifest(manifest)


def test_boolean_cannot_certify_new_sandbox_crop_and_helper_boundaries():
    current = identity()
    report = environment_report(current)
    report.update(environmentQualified=True, codes=[], cropSafety={"qualified": True})
    codes = validate_environment(report, current)
    assert "CROP_CLEARANCE_UNVERIFIED" in codes
    assert "RASTER_NETWORK_ISOLATION_UNVERIFIED" in codes
