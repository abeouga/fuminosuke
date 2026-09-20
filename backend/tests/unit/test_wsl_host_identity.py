from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.services import wsl_host_identity as host
from tests.unit.test_wsl_environment_evidence import sample_host


@pytest.mark.parametrize("field,value", [
    ("policy", "wrong"), ("architecture", "ARM64"), ("windowsBuild", 26200),
    ("windowsBuild", "unknown"), ("windowsRevision", True), ("windowsRevision", -1),
    ("displayVersion", ""), ("launcherPath", "wsl.exe"),
    ("launcherPath", "C:\\Windows\\another.exe"), ("launcherSha256", "f" * 63),
    ("wslVersionOutputSha256", None),
])
def test_host_identity_requires_complete_typed_evidence(field, value):
    with pytest.raises(ValueError, match="host identity"):
        host.validate({**sample_host(), field: value})


@pytest.mark.parametrize("value", [None, {}, {**sample_host(), "qualified": True}])
def test_no_missing_or_promoted_host_identity(value):
    with pytest.raises(ValueError, match="host identity"):
        host.validate(value)


@pytest.mark.parametrize("fault", [None, "changed", "failed", "odd", "oversized",
                                  "invalid", "timeout"])
def test_collector_binds_raw_bytes_and_launcher_without_document(monkeypatch, fault):
    raw = "WSL バージョン: 2.6.3.0\r\nカーネル: 6.6.87.2\r\n".encode("utf-16-le")
    path = host.Path(sample_host()["launcherPath"])
    monkeypatch.setattr(host.platform, "system", lambda: "Windows")
    monkeypatch.setattr(host.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(host.shutil, "which", lambda _: str(path))
    monkeypatch.setattr(host.Path, "resolve", lambda self, **_: self)
    records = iter([{"available": True, "sha256": "a" * 64},
                    {"available": True, "sha256": ("c" if fault == "changed" else "a") * 64}])
    monkeypatch.setattr(host, "file_record", lambda _: next(records))
    registry = SimpleNamespace(HKEY_LOCAL_MACHINE="HKLM", OpenKey=lambda *_: nullcontext("key"),
        QueryValueEx=lambda _key, name: ({"CurrentBuildNumber": "26200", "UBR": 9445,
                                        "DisplayVersion": "25H2"}[name], 0))
    monkeypatch.setattr(host.importlib, "import_module", lambda _: registry)

    def run(command, **kwargs):
        assert command == [str(path), "--version"]
        assert kwargs["stdin"] == host.subprocess.DEVNULL
        assert kwargs["timeout"] == 10
        if fault == "timeout":
            raise host.subprocess.TimeoutExpired(command, 10)
        content = {"odd": b"x", "oversized": b"x" * 16386,
                   "invalid": "unknown".encode("utf-16-le")}.get(fault, raw)
        kwargs["stdout"].write(content)
        return SimpleNamespace(returncode=1 if fault == "failed" else 0)

    monkeypatch.setattr(host.subprocess, "run", run)
    if fault:
        with pytest.raises(ValueError, match="WSL"):
            host.current()
    else:
        assert host.current() == {**sample_host(),
                                 "wslVersionOutputSha256": host.hashlib.sha256(raw).hexdigest()}


def test_collector_refuses_other_operating_system_without_launch(monkeypatch):
    monkeypatch.setattr(host.platform, "system", lambda: "Linux")
    monkeypatch.setattr(host.shutil, "which", lambda _: pytest.fail("No launch"))
    with pytest.raises(ValueError, match="Windows"):
        host.current()
