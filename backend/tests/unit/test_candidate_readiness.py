import copy

import pytest

from app.services.candidate_readiness_service import compare_candidate_replay
from app.services.prepared_reference_evidence import compare_reference_replay
from app.services.template_version_service import without_current_qualification
from tests.bootstrap_python_fixture import bootstrap_python_evidence
from tests.core_guard_fixture import core_guard_evidence
from tests.unit.test_prepared_reference_replay import sample


def candidate_sample():
    report, images = sample()
    report.update(
        bootstrapPythonIdentity=bootstrap_python_evidence(),
        coreDumpGuard=core_guard_evidence(),
        brokerCoreDumpGuard=core_guard_evidence(),
    )
    return report, images


def test_native_update_can_be_replayed_without_claiming_formal_environment_equality():
    original, images = candidate_sample()
    before = copy.deepcopy(original)
    replay = copy.deepcopy(original)
    replay["bootstrapPythonIdentity"]["nativeDependencies"]["sha256"] = "9" * 64
    result = compare_candidate_replay(original, replay, images)
    assert result["changed"] is True
    assert result["prepared"] != result["replayed"]
    assert original == before
    with pytest.raises(ValueError):
        compare_reference_replay(original, replay, images)


def test_fork_drops_candidate_evidence_without_modifying_source_quality():
    quality = {"candidateReadiness": {"artifactId": "old-owned-artifact"}, "other": [1]}
    original = copy.deepcopy(quality)
    assert without_current_qualification(quality) == {"other": [1]}
    assert quality == original


@pytest.mark.parametrize(
    "change",
    [
        "font",
        "runtime",
        "worker",
        "python",
        "network",
        "uid",
        "resources",
        "preload",
        "core",
        "page_order",
        "pixels",
    ],
)
def test_candidate_policy_preserves_non_native_boundaries(change):
    original, images = candidate_sample()
    replay = copy.deepcopy(original)
    if change in {"font", "runtime", "worker", "python"}:
        key = {
            "font": "fontDirectoryIdentity",
            "runtime": "runtimeImage",
            "worker": "workerIdentity",
            "python": "bootstrapPythonIdentity",
        }[change]
        replay[key]["sha256"] = "9" * 64
    elif change == "network":
        replay["networkDenied"] = False
    elif change == "uid":
        replay["uid"] = 0
    elif change == "resources":
        replay["resourceLimits"]["memory.max"] = "max"
    elif change == "preload":
        replay["bootstrapPythonIdentity"]["nativeDependencies"]["preloadAbsent"] = False
    elif change == "core":
        replay["coreDumpGuard"]["noNewPrivileges"] = False
    elif change == "page_order":
        replay["imageSha256"].reverse()
        images.reverse()
    else:
        images[0] += b"changed"
    with pytest.raises((ValueError, RuntimeError)):
        compare_candidate_replay(original, replay, images)
