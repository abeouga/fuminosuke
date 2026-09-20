import copy
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.services import wsl_environment_evidence as evidence
from app.services import wsl_host_identity as host_identity
from app.services.prepared_reference_evidence import PreparedReference
from tests.bootstrap_python_fixture import bootstrap_python_evidence
from tests.core_guard_fixture import core_guard_evidence
from tests.unit.test_prepared_reference_replay import sample
from tests.wsl_execution_fixture import wsl_execution_evidence


@pytest.fixture
def prepared(monkeypatch):
    report, images = sample()
    report.update(bootstrapPythonIdentity=bootstrap_python_evidence(),
                  coreDumpGuard=core_guard_evidence(), brokerCoreDumpGuard=core_guard_evidence())
    result = PreparedReference(b"xlsx", b"pdf", images, report, {"owned": "prepared-graph"})
    monkeypatch.setattr(evidence, "inspect_current", lambda *_: evidence.identity(report))
    monkeypatch.setattr(evidence.time, "time", lambda: 100000)
    monkeypatch.setattr(host_identity, "current", sample_host)
    return result


def sample_host():
    return {"policy": host_identity.POLICY, "architecture": "AMD64",
            "windowsBuild": "26200", "windowsRevision": 9445, "displayVersion": "25H2",
            "launcherPath": "C:\\Windows\\System32\\wsl.exe", "launcherSha256": "a" * 64,
            "wslVersionOutputSha256": "b" * 64}


def test_new_lease_rejects_host_change_during_linux_inspection(prepared, monkeypatch):
    observations = iter([sample_host(), {**sample_host(), "windowsRevision": 9446}])
    monkeypatch.setattr(host_identity, "current", lambda: next(observations))
    with pytest.raises(ValueError, match="host"):
        evidence.new_lease(prepared, "version", "Ubuntu-22.04")


def test_lease_requires_explicit_host_evidence_without_backfill(prepared):
    record = evidence.new_lease(prepared, "version", "Ubuntu-22.04")
    record.pop("hostIdentity", None)
    with pytest.raises(ValueError, match="lease"):
        evidence.validate_lease(record, prepared, "version", now=100000)


def test_identity_lease_is_bound_but_does_not_issue_production_permission(prepared):
    record = evidence.new_lease(prepared, "version", "Ubuntu-22.04")
    evidence.validate_lease(record, prepared, "version", now=100000)
    assert record["expiresAt"] == 100000 + evidence.LIFETIME_SECONDS
    assert record["scope"] == "identity-freshness-only"
    assert not {"qualified", "environmentQualified", "productionQualified"} & record.keys()


@pytest.mark.parametrize("field,value", [
    ("uid", 0), ("networkInterfaces", ["lo", "eth0"]), ("networkDenied", False),
    ("hostFilesystemHidden", False), ("noNewPrivileges", False),
    ("referenceFontMountReadOnly", False), ("resourceLimits", {"pids.max": "max"}),
])
def test_lease_refuses_uncontained_execution_even_when_identity_matches(
    prepared, monkeypatch, field, value,
):
    prepared.report.update(wsl_execution_evidence())
    prepared.report[field] = value
    monkeypatch.setattr(evidence, "inspect_current",
                        lambda *_: pytest.fail("Reject execution before current identity lookup"))
    with pytest.raises((ValueError, RuntimeError), match="execution|isolation"):
        evidence.new_lease(prepared, "version", "Ubuntu-22.04")


@pytest.mark.parametrize("change", ["missing", "kernel", "fonts", "bootstrap", "worker"])
def test_new_lease_requires_current_installation_not_saved_flags(prepared, monkeypatch, change):
    current = evidence.identity(prepared.report)
    if change == "missing":
        current.clear()
    else:
        key, field = {"kernel": ("runtimeImage", "kernel"),
                      "fonts": ("fontDirectoryIdentity", "sha256"),
                      "bootstrap": ("bootstrapPythonIdentity", "sha256"),
                      "worker": ("workerIdentity", "sha256")}[change]
        current[key][field] = "changed"
    monkeypatch.setattr(evidence, "inspect_current", lambda *_: current)
    with pytest.raises(ValueError, match="current installation"):
        evidence.new_lease(prepared, "version", "Ubuntu-22.04")


@pytest.mark.parametrize("change", ["expired", "future", "lifetime", "bool", "version",
                                    "graph", "identity", "extra", "distribution"])
def test_lease_cannot_be_extended_rebound_or_promoted(prepared, change):
    record = evidence.new_lease(prepared, "version", "Ubuntu-22.04")
    now = 100000
    if change == "expired":
        now = record["expiresAt"]
    elif change == "future":
        now -= 1
    elif change == "lifetime":
        record["expiresAt"] += 1
    elif change == "bool":
        record["issuedAt"] = True
    elif change == "version":
        record["templateVersionId"] = "another"
    elif change == "graph":
        prepared.evidence["owned"] = "another"
    elif change == "identity":
        record["identity"]["runtimeImage"]["sha256"] = "f" * 64
    elif change == "extra":
        record["qualified"] = True
    else:
        record["distribution"] = "--invalid"
    with pytest.raises(ValueError, match="lease"):
        evidence.validate_lease(record, prepared, "version", now=now)


def test_old_report_is_not_backfilled_or_sent_to_current_environment(prepared, monkeypatch):
    prepared.report.pop("bootstrapPythonIdentity")
    before = copy.deepcopy(prepared)
    monkeypatch.setattr(evidence, "inspect_current", lambda *_: pytest.fail("No backfill"))
    assert evidence.new_lease(prepared, "version", "Ubuntu-22.04") is None
    assert prepared == before


def test_owned_artifact_is_checked_before_live_inspection(prepared, monkeypatch):
    record = evidence.new_lease(prepared, "version", "Ubuntu-22.04")
    payload = evidence.canonical(record)
    artifact = SimpleNamespace(job_id="job", relative_path="owned/environment.json",
        sha256=evidence.hashlib.sha256(payload).hexdigest(), artifact_name="environment",
        media_type="application/json", attempt_number=1)
    ref = {"policy": evidence.POLICY, "artifactId": "artifact",
           "relativePath": artifact.relative_path,
           "sha256": artifact.sha256, "name": artifact.artifact_name, "attemptNumber": 1}
    db = SimpleNamespace(get=lambda *_: artifact)
    storage = SimpleNamespace(read_bytes=lambda *_: payload)
    version = SimpleNamespace(id="version", source_job_id="job")
    evidence.load_lease(db, storage, version, prepared, ref)
    monkeypatch.setattr(evidence, "inspect_current",
                        lambda *_: pytest.fail("No invalid inspection"))
    monkeypatch.setattr(host_identity, "current", lambda: pytest.fail("No invalid host probe"))
    artifact.job_id = "another-job"
    with pytest.raises(ValueError, match="ownership"):
        evidence.load_lease(db, storage, version, prepared, ref)
    artifact.job_id = "job"
    monkeypatch.setattr(evidence.time, "time", lambda: record["expiresAt"])
    with pytest.raises(ValueError, match="expired"):
        evidence.load_lease(db, storage, version, prepared, ref)


@pytest.mark.parametrize("stage", ["before", "during", "old-policy", "missing"])
def test_saved_lease_rejects_host_drift_and_old_records(prepared, monkeypatch, stage):
    record = evidence.new_lease(prepared, "version", "Ubuntu-22.04")
    if stage == "old-policy":
        record["policy"] = "wsl-environment-identity-lease-v1"
    elif stage == "missing":
        record.pop("hostIdentity")
    payload = evidence.canonical(record)
    artifact = SimpleNamespace(job_id="job", relative_path="owned/environment.json",
        sha256=evidence.hashlib.sha256(payload).hexdigest(), artifact_name="environment",
        media_type="application/json", attempt_number=1)
    ref = {"policy": evidence.POLICY, "artifactId": "artifact",
           "relativePath": artifact.relative_path, "sha256": artifact.sha256,
           "name": artifact.artifact_name, "attemptNumber": 1}
    updated = {**sample_host(), "windowsRevision": 9446}
    observations = iter([sample_host(), updated] if stage == "during" else [updated])
    monkeypatch.setattr(host_identity, "current", lambda: next(observations))
    if stage != "during":
        monkeypatch.setattr(evidence, "inspect_current", lambda *_: pytest.fail("No Linux probe"))
    if stage in {"old-policy", "missing"}:
        monkeypatch.setattr(host_identity, "current", lambda: pytest.fail("No host probe"))
    with pytest.raises(ValueError, match="host|lease"):
        evidence.load_lease(SimpleNamespace(get=lambda *_: artifact),
                            SimpleNamespace(read_bytes=lambda *_: payload),
                            SimpleNamespace(id="version", source_job_id="job"), prepared, ref)


@pytest.mark.parametrize("runtime,distribution", [("not-sha", "Ubuntu-22.04"),
                                                  ("a" * 64, "--user"),
                                                  ("a" * 64, "bad/name")])
def test_inspector_selector_is_fixed_before_launch(runtime, distribution, monkeypatch):
    monkeypatch.setattr(evidence.subprocess, "run", lambda *_a, **_k: pytest.fail("No launch"))
    with pytest.raises(ValueError, match="selector"):
        evidence.inspect_current(runtime, distribution)


@pytest.mark.parametrize("change", [None, "worker", "runtime", "policy", "error", "timeout"])
def test_inspector_uses_private_code_without_document_and_rejects_bad_responses(
    prepared, monkeypatch, tmp_path, change,
):
    # Use the real public inspector instead of the fixture's current-state stub.
    inspector = REAL_INSPECTOR
    monkeypatch.setattr(evidence, "render_directory", lambda: nullcontext(tmp_path))
    response = {"policy": evidence.CURRENT_POLICY, **evidence.identity(prepared.report)}
    source = evidence.Path(evidence.__file__).with_name("renderer_wsl_worker.py").read_bytes()
    response["workerIdentity"]["sha256"] = evidence.hashlib.sha256(source).hexdigest()
    runtime_id = response["runtimeImage"]["sha256"]
    if change in {"worker", "runtime"}:
        response["workerIdentity" if change == "worker" else "runtimeImage"]["sha256"] = "0" * 64
    elif change == "policy":
        response["policy"] = "wrong"

    def run(command, **kwargs):
        assert command[-2:] == ["--inspect-environment", runtime_id]
        start = command.index("-I")
        assert command[start:start + 3] == ["-I", "-S", "-B"]
        assert (tmp_path / "worker.py").read_bytes() == source
        assert not (tmp_path / "page.xlsx").exists()
        if change == "timeout":
            raise evidence.subprocess.TimeoutExpired(command, 45)
        kwargs["stdout"].write(evidence.canonical(response))
        return SimpleNamespace(returncode=1 if change == "error" else 0)

    monkeypatch.setattr(evidence.subprocess, "run", run)
    if change is None:
        assert inspector(runtime_id, "Ubuntu-22.04") == evidence.identity(response)
    else:
        with pytest.raises(ValueError, match="[Ee]nvironment"):
            inspector(runtime_id, "Ubuntu-22.04")


REAL_INSPECTOR = evidence.inspect_current
