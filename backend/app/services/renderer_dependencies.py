"""Observed loaded image inventory, explicitly not a complete execution trace."""

from __future__ import annotations

import ctypes as c
import hashlib
import json
import threading
from ctypes import wintypes as w
from pathlib import Path
from typing import Any

_CACHE: dict[tuple[str, int, int, int], str] = {}
_LOCK = threading.Lock()


def file_record(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        return {"path": str(path), "available": False}
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    with _LOCK:
        value = _CACHE.get(key)
    if value is None:
        with path.open("rb") as stream:
            value = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.stat()
        if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != key[1:]:
            raise ValueError("Dependency changed during hashing")
        with _LOCK:
            if len(_CACHE) > 2048:
                _CACHE.clear()
            _CACHE[key] = value
    return {"path": str(path), "available": True, "sha256": value}


class LoadedImages:
    def __init__(self) -> None:
        self.paths: set[str] = set()
        self.errors: set[int] = set()

    def observe_job(self, job: Any, kernel: Any) -> None:
        # JOBOBJECT_BASIC_PROCESS_ID_LIST: two DWORDs followed by ULONG_PTRs.
        data = c.create_string_buffer(8 + 256 * c.sizeof(c.c_size_t))
        if not kernel.QueryInformationJobObject(job, 3, data, len(data), None):
            self.errors.add(c.get_last_error())
            return
        count = w.DWORD.from_buffer(data, 4).value
        for pid in (c.c_size_t * min(count, 256)).from_buffer(data, 8):
            self.observe(pid)

    def observe(self, pid: int) -> None:
        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        psapi = c.WinDLL("psapi", use_last_error=True)
        psapi.EnumProcessModulesEx.argtypes = [
            w.HANDLE,
            c.c_void_p,
            w.DWORD,
            c.POINTER(w.DWORD),
            w.DWORD,
        ]
        psapi.GetModuleFileNameExW.argtypes = [w.HANDLE, w.HMODULE, w.LPWSTR, w.DWORD]
        process = kernel.OpenProcess(0x410, False, pid)
        if not process:
            self.errors.add(c.get_last_error())
            return
        try:
            modules = (w.HMODULE * 1024)()
            needed = w.DWORD()
            if not psapi.EnumProcessModulesEx(
                process, modules, c.sizeof(modules), c.byref(needed), 3
            ):
                self.errors.add(c.get_last_error())
                return
            if needed.value > c.sizeof(modules):
                self.errors.add(122)
            for handle in modules[: min(1024, needed.value // c.sizeof(w.HMODULE))]:
                name = c.create_unicode_buffer(32768)
                if psapi.GetModuleFileNameExW(process, handle, name, len(name)):
                    self.paths.add(name.value)
        finally:
            kernel.CloseHandle(process)

    def report(self) -> dict[str, Any]:
        paths = {str(Path(p).resolve()).casefold(): Path(p) for p in self.paths}
        files = [file_record(paths[key]) for key in sorted(paths)]
        windows = str(Path("C:/Windows").resolve()).casefold() + "\\"
        payload = {
            "application": [f for f in files if not f["path"].casefold().startswith(windows)],
            "operatingSystem": [f for f in files if f["path"].casefold().startswith(windows)],
        }
        return {
            **payload,
            "sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            "method": "job-pids-psapi-poll-v1",
            "errors": sorted(self.errors),
            "completeTrace": False,
        }


def validate_manifest(manifest: dict[str, Any]) -> bool:
    payload = {key: manifest[key] for key in ["application", "operatingSystem"]}
    if (
        manifest.get("sha256")
        != hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    ):
        return False
    return all(
        file_record(Path(item["path"])) == item for items in payload.values() for item in items
    )
