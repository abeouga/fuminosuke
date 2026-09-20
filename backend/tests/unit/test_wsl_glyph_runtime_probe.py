import json
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts import probe_wsl_glyph_runtime as subject


def test_dependency_probe_uses_only_the_fixed_image_without_document_bindings(tmp_path):
    command = subject.glyph_probe_command(tmp_path)
    assert command.count("--ro-bind") == 1
    index = command.index("--ro-bind")
    assert command[index + 1:index + 3] == [str(tmp_path), "/"]
    assert command[-5:] == [subject.PREFIX + "/bin/python", "-I", "-B", "-c", subject.PROBE_CODE]
    assert "--unshare-all" in command and "--clearenv" in command
    assert command[command.index("--uid") + 1] == "65534"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "/mnt/c" not in command and "/reference-fonts" not in command
    compile(subject.PROBE_CODE, "dependency-probe", "exec")


@pytest.fixture
def pinned(monkeypatch, tmp_path):
    events = []

    @contextmanager
    def selected(runtime_id):
        events.append(("enter", runtime_id))
        yield tmp_path
        events.append(("exit", runtime_id))

    monkeypatch.setattr(subject, "pinned_runtime", selected)
    return events


def test_probe_requires_pinned_versions_and_never_issues_qualification(monkeypatch, pinned):
    def run(command, **kwargs):
        assert kwargs == {"capture_output": True, "check": True, "timeout": 20}
        assert command[-1] == subject.PROBE_CODE
        return SimpleNamespace(stdout=json.dumps({"versions": subject.VERSIONS}).encode())

    monkeypatch.setattr(subject.subprocess, "run", run)
    result = subject.probe("a" * 64)
    assert pinned == [("enter", "a" * 64), ("exit", "a" * 64)]
    assert result["runtimeId"] == "a" * 64
    assert result["productionQualified"] is False
    assert len(result["probeSha256"]) == 64


@pytest.mark.parametrize("body", [
    b"[]", b"{}", b'{"versions":{"fonttools":"4.64.0"}}', b" " * (64 * 1024 + 1),
], ids=["not-object", "missing-versions", "wrong-version", "oversized"])
def test_bad_dependency_report_is_rejected(monkeypatch, pinned, body):
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=body))
    with pytest.raises(RuntimeError):
        subject.probe("a" * 64)


def test_missing_dependency_cannot_fall_back_to_host_python(monkeypatch, pinned):
    commands = []

    def fail(command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subject.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        subject.probe("a" * 64)
    assert len(commands) == 1
    assert commands[0][-5] == subject.PREFIX + "/bin/python"
