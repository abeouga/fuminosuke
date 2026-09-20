from copy import deepcopy

import pytest

from app.services import wsl_environment_evidence as lease_service
from app.services import wsl_environment_qualification as qualification
from app.services import wsl_host_identity as host_identity
from app.services.prepared_reference_evidence import PreparedReference
from tests.bootstrap_python_fixture import bootstrap_python_evidence
from tests.core_guard_fixture import core_guard_evidence
from tests.unit.test_prepared_reference_replay import sample


def sample_host():
    return {
        "policy": host_identity.POLICY,
        "architecture": "AMD64",
        "windowsBuild": "26200",
        "windowsRevision": 9445,
        "displayVersion": "25H2",
        "launcherPath": "C:\\Windows\\System32\\wsl.exe",
        "launcherSha256": "a" * 64,
        "wslVersionOutputSha256": "b" * 64,
    }


@pytest.fixture
def evidence(monkeypatch):
    report, images = sample()
    report.update(
        bootstrapPythonIdentity=bootstrap_python_evidence(),
        coreDumpGuard=core_guard_evidence(),
        brokerCoreDumpGuard=core_guard_evidence(),
    )
    prepared = PreparedReference(b"xlsx", b"pdf", images, report, {"owned": "graph"})
    monkeypatch.setattr(lease_service, "inspect_current", lambda *_: lease_service.identity(report))
    monkeypatch.setattr(host_identity, "current", sample_host)
    monkeypatch.setattr(lease_service.time, "time", lambda: 100_000)
    lease = lease_service.new_lease(prepared, "version-a", "Ubuntu-22.04")
    return prepared, lease


def test_formal_environment_issuer_combines_fresh_identity_and_execution(evidence):
    prepared, lease = evidence
    before = deepcopy(evidence)
    result = qualification.issue(prepared, lease, "version-a", "f" * 64, now=100_000)
    assert evidence == before
    assert result["status"] == "PASS" and result["qualified"] is True
    assert result["scope"] == "fixed-prepared-reference-invocation-v1"
    assert result["issuedAt"] == 100_000
    assert result["leaseIssuedAt"] == 100_000
    assert result["expiresAt"] == 100_000 + lease_service.LIFETIME_SECONDS
    assert all(result["requirements"].values())
    assert result["unresolvedBlockers"] == []


@pytest.mark.parametrize("change", ["expired", "identity", "graph", "root", "unit"])
def test_formal_environment_issuer_rejects_stale_or_incomplete_evidence(
    evidence, change,
):
    prepared, lease = evidence
    now = 100_000
    if change == "expired":
        now = lease["expiresAt"]
    elif change == "identity":
        lease["identity"]["runtimeImage"]["sha256"] = "0" * 64
    elif change == "graph":
        prepared.evidence["owned"] = "changed"
    elif change == "root":
        prepared.report["uid"] = 0
    else:
        prepared.report["unitControls"]["properties"]["MemoryMax"] = "max"
    with pytest.raises((ValueError, RuntimeError)):
        qualification.issue(prepared, lease, "version-a", "f" * 64, now=now)


def test_environment_implementation_fingerprint_changes_with_issuer_code(monkeypatch):
    before = qualification.implementation_fingerprint()
    original = qualification.Path.read_bytes

    def changed(path):
        content = original(path)
        return (
            content + b"# changed"
            if path.name == "wsl_environment_qualification.py" else content
        )

    monkeypatch.setattr(qualification.Path, "read_bytes", changed)
    assert qualification.implementation_fingerprint() != before


def test_environment_issuer_preserves_saved_issuance_time_on_revalidation(evidence):
    prepared, lease = evidence
    issued = qualification.issue(
        prepared,
        lease,
        "version-a",
        "f" * 64,
        now=100_010,
        qualification_issued_at=100_005,
    )
    assert issued["issuedAt"] == 100_005
    with pytest.raises(ValueError, match="issuance time"):
        qualification.issue(
            prepared,
            lease,
            "version-a",
            "f" * 64,
            now=100_010,
            qualification_issued_at=100_011,
        )
