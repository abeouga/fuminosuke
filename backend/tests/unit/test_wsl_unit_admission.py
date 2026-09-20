"""Input must wait for the owned transient unit's effective controls."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import renderer_wsl_worker as worker
from app.services.wsl_execution_evidence import validate_execution
from tests.wsl_execution_fixture import unit_control_evidence, wsl_execution_evidence


def test_pinned_child_has_a_readonly_admission_directory(tmp_path):
    command = worker.sandbox_command(
        tmp_path, "llmocr-render-" + "a" * 32, "pdf", "windows",
        tmp_path / "runtime", "b" * 64,
    )
    bindings = {command[i + 2]: command[i + 1] for i, value in enumerate(command)
                if value == "--ro-bind"}
    assert bindings.get("/admission") == str(tmp_path / "admission")


def test_execution_without_actual_unit_controls_is_not_accepted():
    report = dict(wsl_execution_evidence(), policy="wsl-bwrap-cgroup-v1",
                  status="PASS", format="reference", fontSource="windows")
    report.pop("unitControls", None)
    with pytest.raises(ValueError, match="isolation"):
        validate_execution(report)


@pytest.mark.parametrize("key", list(unit_control_evidence()["properties"]))
@pytest.mark.parametrize("value", [None, "unexpected"])
def test_every_effective_control_is_required_and_exact(key, value):
    proof = unit_control_evidence()
    if value is None:
        proof["properties"].pop(key)
    else:
        proof["properties"][key] = value
    with pytest.raises(ValueError, match="unit controls"):
        worker.validate_unit_controls(proof)


def unit_properties(unit):
    return dict(unit_control_evidence()["properties"], Id=unit + ".service",
                LoadState="loaded", ActiveState="active", SubState="running",
                ControlGroup=f"/system.slice/{unit}.service")


def mock_show(monkeypatch, properties, *, duplicate="", returncode=0):
    def run(command, **kwargs):
        assert command[:2] == ["/usr/bin/systemctl", "show"]
        assert kwargs["timeout"] == 2
        assert kwargs["env"]["LC_ALL"] == "C.UTF-8"
        raw = "".join(f"{key}={value}\n" for key, value in properties.items()) + duplicate
        kwargs["stdout"].write(raw.encode())
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(worker.subprocess, "run", run)


def test_effective_unit_query_returns_only_stable_verified_controls(monkeypatch):
    unit = "llmocr-render-" + "c" * 32
    mock_show(monkeypatch, unit_properties(unit))
    assert worker.read_unit_controls(unit) == unit_control_evidence()


@pytest.mark.parametrize("key,value", [
    ("Id", "another.service"), ("ControlGroup", "/another"),
    ("SubState", "exited"), ("ActiveState", "failed"),
    ("DropInPaths", "/etc/systemd/system/service.d/override.conf"),
    ("RuntimeMaxUSec", "infinity"), ("LimitFSIZESoft", "infinity"),
    ("SendSIGKILL", "no"), ("Restart", "always"),
])
def test_mismatched_effective_unit_never_issues_admission(monkeypatch, key, value):
    unit = "llmocr-glyph-" + "d" * 32
    properties = unit_properties(unit)
    properties[key] = value
    mock_show(monkeypatch, properties)
    with pytest.raises((RuntimeError, ValueError)):
        worker.read_unit_controls(unit)


def test_duplicate_missing_and_nonzero_query_are_rejected(monkeypatch):
    unit = "llmocr-render-" + "a" * 32
    for duplicate, omitted, returncode in [("Restart=no\n", None, 0),
                                           ("", "KillMode", 0), ("", None, 1)]:
        properties = unit_properties(unit)
        if omitted:
            properties.pop(omitted)
        mock_show(monkeypatch, properties, duplicate=duplicate, returncode=returncode)
        with pytest.raises(RuntimeError):
            worker.read_unit_controls(unit)


def test_not_yet_loaded_unit_is_not_admitted(monkeypatch):
    mock_show(monkeypatch, {"LoadState": "not-found"}, returncode=1)
    assert worker.read_unit_controls("llmocr-render-" + "a" * 32) is None


def test_admission_is_published_as_complete_file_and_invalid_proof_is_not_written(tmp_path):
    (tmp_path / "admission").mkdir()
    with pytest.raises(ValueError):
        worker.publish_unit_admission(tmp_path, {})
    assert not list((tmp_path / "admission").iterdir())
    worker.publish_unit_admission(tmp_path, unit_control_evidence())
    content = (tmp_path / "admission/controls.json").read_bytes()
    assert json.loads(content) == unit_control_evidence()
    assert not (tmp_path / "admission-pending.json").exists()


def admission_reader(tmp_path, monkeypatch, *, mount_mode="ro", limits=None):
    proof = tmp_path / "proof.json"
    proof.write_text(json.dumps(unit_control_evidence()))
    proof.chmod(0o444)
    resource = SimpleNamespace(RLIMIT_FSIZE=1, getrlimit=lambda _: (
        limits if limits is not None else (worker.MAX_FILE, worker.MAX_FILE)
    ))
    monkeypatch.setitem(sys.modules, "resource", resource)
    original_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kwargs: (
        f"1 2 0:1 / /admission {mount_mode} - ext4 /dev/fixture rw\n"
        if str(path).replace("\\", "/") == "/proc/self/mountinfo"
        else original_read(path, *args, **kwargs)
    ))
    original_open = worker.os.open
    opened = []

    def open_admission(path, flags):
        opened.append(str(path).replace("\\", "/"))
        assert opened[-1] == "/admission/controls.json"
        return original_open(proof, flags)

    monkeypatch.setattr(worker.os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(worker.os, "open", open_admission)
    return opened


def test_inner_accepts_only_complete_readonly_admission_with_effective_file_limit(
    tmp_path, monkeypatch,
):
    opened = admission_reader(tmp_path, monkeypatch)
    assert worker.await_unit_admission() == unit_control_evidence()
    assert opened == ["/admission/controls.json"]


def test_inner_rejects_writeable_mount_before_opening_admission(tmp_path, monkeypatch):
    opened = admission_reader(tmp_path, monkeypatch, mount_mode="rw")
    with pytest.raises(RuntimeError, match="mount is not read-only"):
        worker.await_unit_admission()
    assert opened == []


def test_inner_checks_actual_file_limit_not_only_broker_report(tmp_path, monkeypatch):
    admission_reader(tmp_path, monkeypatch, limits=(-1, -1))
    with pytest.raises(RuntimeError, match="file-size limits were not enforced"):
        worker.await_unit_admission()


def test_missing_admission_has_a_bounded_wait(tmp_path, monkeypatch):
    admission_reader(tmp_path, monkeypatch)

    def missing(*args):
        raise FileNotFoundError

    clock = iter([10, 15])
    monkeypatch.setattr(worker.os, "open", missing)
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="admission timed out"):
        worker.await_unit_admission()
