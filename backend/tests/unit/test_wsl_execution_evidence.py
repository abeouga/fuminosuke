import copy

import pytest

from app.services.prepared_reference_evidence import _replay_identity, compare_reference_replay
from app.services.wsl_execution_evidence import FIELDS, validate_execution
from tests.bootstrap_python_fixture import bootstrap_python_evidence
from tests.unit.test_prepared_reference_replay import sample


@pytest.mark.parametrize("field", FIELDS)
def test_missing_control_is_not_replaced_by_policy_or_pass_flag(field):
    report, images = sample()
    report.pop(field)
    with pytest.raises(ValueError, match="execution isolation"):
        validate_execution(report)
    with pytest.raises(ValueError, match="execution isolation"):
        compare_reference_replay(report, copy.deepcopy(report), images)


@pytest.mark.parametrize("field,value", [
    ("uid", True), ("uid", 65534.0), ("uid", "65534"), ("uid", 0),
    ("networkInterfaces", "lo"), ("networkInterfaces", []),
    ("networkInterfaces", ["lo", "eth0"]), ("networkDenied", 1),
    ("noNewPrivileges", "true"), ("hostFilesystemHidden", 1),
    ("referenceFontMountReadOnly", 1), ("referenceFontMountReadOnly", False),
    ("resourceLimits", {"memory.max": "max"}),
    ("status", "UNVERIFIED"), ("policy", "unknown"),
    ("format", "glyph"), ("fontSource", "unverified"),
])
def test_evidence_has_strict_types_and_fixed_execution_conditions(field, value):
    report, _ = sample()
    report[field] = value
    with pytest.raises(ValueError, match="execution isolation"):
        validate_execution(report)


@pytest.mark.parametrize("limit", ["memory.max", "memory.swap.max", "pids.max", "cpu.max"])
@pytest.mark.parametrize("value", ["max", 64, None])
def test_each_resource_control_is_checked_in_saved_proof(limit, value):
    report, images = sample()
    report["resourceLimits"][limit] = value
    with pytest.raises(ValueError, match="execution isolation"):
        compare_reference_replay(report, copy.deepcopy(report), images)


def test_current_bootstrap_cannot_disguise_missing_controls_as_historical_report():
    report, _ = sample()
    report["bootstrapPythonIdentity"] = bootstrap_python_evidence()
    for field in FIELDS:
        report.pop(field)
    with pytest.raises(ValueError, match="execution isolation"):
        _replay_identity(report)


def test_historical_report_is_not_backfilled_and_cannot_pass_current_validator():
    report, images = sample()
    for field in FIELDS:
        report.pop(field)
    before = copy.deepcopy(report)
    assert compare_reference_replay(report, copy.deepcopy(report), images) is True
    assert "executionBoundary" not in _replay_identity(report)
    assert report == before
    with pytest.raises(ValueError, match="execution isolation"):
        validate_execution(report)


def test_validated_controls_are_copied_without_creating_production_permission():
    report, _ = sample()
    controls = validate_execution(report)
    assert set(controls) == set(FIELDS)
    assert "qualified" not in controls
    assert _replay_identity(report)["executionBoundary"] == controls
    controls["resourceLimits"]["pids.max"] = "max"
    assert report["resourceLimits"]["pids.max"] == "64"


def test_live_linux_font_mode_does_not_fabricate_windows_font_mount():
    report, _ = sample()
    report.update(fontSource="linux", referenceFontMountReadOnly=False)
    assert validate_execution(report)["referenceFontMountReadOnly"] is False
