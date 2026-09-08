"""Capability-free AppContainer launch; no automatic unrestricted fallback."""

from __future__ import annotations

import ctypes as c
import hashlib
import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes as w
from pathlib import Path
from typing import Any, ClassVar

POLICY = "appcontainer-zero-capabilities-v1"


def render_environment(directory: Path) -> dict[str, str]:
    # Never pass provider credentials, proxy settings or arbitrary backend env.
    env = {key: os.environ[key] for key in ["SYSTEMROOT", "WINDIR", "COMSPEC",
           "PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)", "USERPROFILE",
           "APPDATA", "LOCALAPPDATA"] if key in os.environ}
    env["PATH"] = str(Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32")
    env.update({key: str(directory) for key in ["TEMP", "TMP"]})
    return env


def profile_name(directory: Path) -> str:
    return "Utsushibi.Render." + hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:32]


def clean_profile(directory: Path) -> None:
    marker = directory / "appcontainer.json"
    if not marker.is_file() or marker.lstat().st_file_attributes & 0x400:
        return
    data = json.loads(marker.read_text(encoding="utf-8"))
    name = profile_name(directory)
    if data != {"profile": name, "policy": POLICY}:
        raise ValueError("Invalid AppContainer ownership marker")
    api = c.WinDLL("userenv", use_last_error=True)
    api.DeleteAppContainerProfile.argtypes = [w.LPCWSTR]
    api.DeleteAppContainerProfile.restype = c.c_long
    result = api.DeleteAppContainerProfile(name)
    if result < 0 and result & 0xFFFFFFFF not in {0x80070002, 0x80070003}:
        raise OSError(f"DeleteAppContainerProfile HRESULT {result & 0xFFFFFFFF:08x}")
    marker.unlink()


class Startup(c.Structure):
    _fields_: ClassVar = [
        ("cb", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        *[(n, w.DWORD) for n in ("x", "y", "xs", "ys", "xc", "yc", "fill", "flags")],
        ("show", w.WORD),
        ("reservedSize", w.WORD),
        ("reserved2", c.c_void_p),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class StartupEx(c.Structure):
    _fields_: ClassVar = [("startup", Startup), ("attributes", c.c_void_p)]


class ProcessInfo(c.Structure):
    _fields_: ClassVar = [
        ("process", w.HANDLE),
        ("thread", w.HANDLE),
        ("pid", w.DWORD),
        ("tid", w.DWORD),
    ]


class Capabilities(c.Structure):
    _fields_: ClassVar = [
        ("sid", c.c_void_p),
        ("capabilities", c.c_void_p),
        ("count", w.DWORD),
        ("reserved", w.DWORD),
    ]


def _acl(path: Path, sid: str, rights: str) -> None:
    subprocess.run(
        ["icacls", str(path), "/grant", f"*{sid}:{rights}"],
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


@contextmanager
def container(directory: Path) -> Iterator[dict[str, Any]]:
    """Unique SID per request, grant only its scratch tree; delete profile on exit."""
    user = c.WinDLL("userenv", use_last_error=True)
    user.CreateAppContainerProfile.argtypes = [
        w.LPCWSTR,
        w.LPCWSTR,
        w.LPCWSTR,
        c.c_void_p,
        w.DWORD,
        c.POINTER(c.c_void_p),
    ]
    user.CreateAppContainerProfile.restype = c.c_long
    user.DeleteAppContainerProfile.argtypes = [w.LPCWSTR]
    user.DeleteAppContainerProfile.restype = c.c_long
    adv = c.WinDLL("advapi32", use_last_error=True)
    adv.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(w.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = w.BOOL
    adv.FreeSid.argtypes = [c.c_void_p]
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [c.c_void_p]
    name = profile_name(directory)
    marker = directory / "appcontainer.json"
    # Write before profile creation so a killed broker leaves reclaimable ownership.
    marker.write_text(json.dumps({"profile": name, "policy": POLICY}), encoding="utf-8")
    sid = c.c_void_p()
    result = user.CreateAppContainerProfile(name, name, "Reference renderer", None, 0, c.byref(sid))
    if result < 0:
        raise OSError(f"CreateAppContainerProfile HRESULT {result & 0xFFFFFFFF:08x}")
    value = w.LPWSTR()
    try:
        if not adv.ConvertSidToStringSidW(sid, c.byref(value)):
            raise c.WinError(c.get_last_error())
        sid_text = str(value.value)
        _acl(directory, sid_text, "(OI)(CI)M")
        yield {
            "sid": sid.value,
            "sidText": sid_text,
            "directory": directory,
            "policy": POLICY,
            "capabilityCount": 0,
        }
    finally:
        if value:
            kernel.LocalFree(value)
        adv.FreeSid(sid)
        # A profile deletion failure is observable, not silently certified clean.
        clean_profile(directory)


def launch(
    command: list[str], config: dict[str, Any], handles: list[int] | None = None
) -> tuple[int, int, int, int]:
    """Create suspended with SECURITY_CAPABILITIES, allowing Job assignment first."""
    api = c.WinDLL("kernel32", use_last_error=True)
    api.InitializeProcThreadAttributeList.argtypes = [
        c.c_void_p,
        w.DWORD,
        w.DWORD,
        c.POINTER(c.c_size_t),
    ]
    api.UpdateProcThreadAttribute.argtypes = [
        c.c_void_p,
        w.DWORD,
        c.c_size_t,
        c.c_void_p,
        c.c_size_t,
        c.c_void_p,
        c.c_void_p,
    ]
    api.DeleteProcThreadAttributeList.argtypes = [c.c_void_p]
    api.CreateProcessW.argtypes = [
        w.LPCWSTR,
        w.LPWSTR,
        c.c_void_p,
        c.c_void_p,
        w.BOOL,
        w.DWORD,
        c.c_void_p,
        w.LPCWSTR,
        c.POINTER(StartupEx),
        c.POINTER(ProcessInfo),
    ]
    size = c.c_size_t()
    handles = handles or []
    count = 2 if handles else 1
    api.InitializeProcThreadAttributeList(None, count, 0, c.byref(size))
    buffer = c.create_string_buffer(size.value)
    if not api.InitializeProcThreadAttributeList(buffer, count, 0, c.byref(size)):
        raise c.WinError(c.get_last_error())
    try:
        caps = Capabilities(config["sid"], None, 0, 0)
        if not api.UpdateProcThreadAttribute(
            buffer, 0, 0x20009, c.byref(caps), c.sizeof(caps), None, None
        ):
            raise c.WinError(c.get_last_error())
        startup = StartupEx()
        startup.startup.cb = c.sizeof(startup)
        startup.attributes = c.cast(buffer, c.c_void_p)
        if handles:
            inherited = (w.HANDLE * len(handles))(*handles)
            if not api.UpdateProcThreadAttribute(
                buffer, 0, 0x20002, inherited, c.sizeof(inherited), None, None
            ):
                raise c.WinError(c.get_last_error())
            startup.startup.flags = 0x100
            startup.startup.stdout = startup.startup.stderr = handles[0]
            startup.startup.stdin = handles[1]
        info = ProcessInfo()
        env = render_environment(config["directory"])
        env.update({key: str(config["directory"]) for key in ["APPDATA", "LOCALAPPDATA"]})
        block = c.create_unicode_buffer(
            "\0".join(f"{k}={v}" for k, v in sorted(env.items())) + "\0\0"
        )
        if not api.CreateProcessW(
            command[0],
            c.create_unicode_buffer(subprocess.list2cmdline(command)),
            None,
            None,
            bool(handles),
            0x4 | 0x08000000 | 0x80000 | 0x400,
            block,
            str(config["directory"]),
            c.byref(startup),
            c.byref(info),
        ):
            raise c.WinError(c.get_last_error())
        return info.process, info.thread, info.pid, info.tid
    finally:
        api.DeleteProcThreadAttributeList(buffer)
