"""Windows/WSL host revision binding, not a complete OS dependency inventory."""
from __future__ import annotations

import hashlib
import importlib
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Any

from app.services.renderer_dependencies import file_record

POLICY = "windows-wsl-host-revision-v1"


def validate(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {
        "policy", "architecture", "windowsBuild", "windowsRevision", "displayVersion",
        "launcherPath", "launcherSha256", "wslVersionOutputSha256",
    } or value.get("policy") != POLICY or value.get("architecture") != "AMD64"
            or not isinstance(value.get("windowsBuild"), str)
            or re.fullmatch(r"[0-9]{4,6}", value["windowsBuild"]) is None
            or type(value.get("windowsRevision")) is not int
            or not 0 <= value["windowsRevision"] <= 1000000
            or not isinstance(value.get("displayVersion"), str)
            or re.fullmatch(r"[A-Za-z0-9.]{1,32}", value["displayVersion"]) is None
            or not isinstance(value.get("launcherPath"), str)
            or not PureWindowsPath(value["launcherPath"]).is_absolute()
            or PureWindowsPath(value["launcherPath"]).name.lower() != "wsl.exe"
            or any(not isinstance(value.get(key), str)
                   or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
                   for key in ("launcherSha256", "wslVersionOutputSha256"))):
        raise ValueError("Invalid Windows/WSL host identity")
    return dict(value)


def current() -> dict[str, Any]:
    if platform.system() != "Windows":
        raise ValueError("Windows WSL host is required")
    found = shutil.which("wsl.exe")
    if found is None:
        raise ValueError("WSL launcher is unavailable")
    path = Path(found).resolve(strict=True)
    before = file_record(path)
    if before.get("available") is not True:
        raise ValueError("WSL launcher is unavailable")
    registry = importlib.import_module("winreg")
    with registry.OpenKey(registry.HKEY_LOCAL_MACHINE,
                          r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
        revisions = {name: registry.QueryValueEx(key, name)[0]
                     for name in ("CurrentBuildNumber", "UBR", "DisplayVersion")}
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                [str(path), "--version"], stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.DEVNULL, timeout=10, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("WSL version observation timed out") from exc
        output.seek(0)
        raw = output.read(16385)
    if result.returncode or not 0 < len(raw) <= 16384 or len(raw) % 2:
        raise ValueError("WSL version observation failed")
    # WSL writes UTF-16LE to redirected stdout. Keep its exact bytes in the
    # digest; console code-page conversion must not alter the identity.
    lines = raw.decode("utf-16-le").lstrip("\ufeff").splitlines()
    if not 1 <= len(lines) <= 64 or not lines[0].startswith("WSL "):
        raise ValueError("Invalid WSL version response")
    if file_record(path) != before:
        raise ValueError("WSL launcher changed during inspection")
    return validate({
        "policy": POLICY, "architecture": platform.machine(),
        "windowsBuild": revisions["CurrentBuildNumber"],
        "windowsRevision": revisions["UBR"], "displayVersion": revisions["DisplayVersion"],
        "launcherPath": str(path), "launcherSha256": before["sha256"],
        "wslVersionOutputSha256": hashlib.sha256(raw).hexdigest(),
    })
