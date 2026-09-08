"""Windows process containment for the new reference renderer, not network isolation."""
from __future__ import annotations

import ctypes as c
import importlib
import os
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes as w
from typing import Any, ClassVar

from app.domain.print_layout import print_error

SANDBOX_VERSION = "windows-job-v1"
RESOURCE_POLICY = "diagnostic-job-1g-4proc-20cpu-45s-v1"
NETWORK_POLICY = "uncontained-v1"
MEMORY_BYTES = 1024 * 1024 * 1024
ACTIVE_PROCESSES = 4
CPU_RATE = 2000  # 20% hard cap of system CPU; not a per-core rate.


class BasicLimits(c.Structure):
    _fields_: ClassVar = [("processTime", c.c_int64), ("jobTime", c.c_int64), ("flags", w.DWORD),
                ("minWorkingSet", c.c_size_t), ("maxWorkingSet", c.c_size_t),
                ("activeProcesses", w.DWORD), ("affinity", c.c_size_t),
                ("priority", w.DWORD), ("scheduling", w.DWORD)]


class ExtendedLimits(c.Structure):
    _fields_: ClassVar = [("basic", BasicLimits), ("io", c.c_uint64 * 6),
                ("processMemory", c.c_size_t), ("jobMemory", c.c_size_t),
                ("peakProcessMemory", c.c_size_t), ("peakJobMemory", c.c_size_t)]


class Accounting(c.Structure):
    _fields_: ClassVar = [("times", c.c_int64 * 4), ("faults", w.DWORD),
                ("total", w.DWORD), ("active", w.DWORD), ("terminated", w.DWORD)]


def kernel() -> Any:
    if os.name != "nt":
        raise print_error("Windows job containment is required.", "RENDERER_UNAVAILABLE", 409)
    dll = c.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
        "QueryInformationJobObject":
            ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "ResumeThread": ([w.HANDLE], w.DWORD),
        "CreateMutexW": ([c.c_void_p, w.BOOL, w.LPCWSTR], w.HANDLE),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "ReleaseMutex": ([w.HANDLE], w.BOOL),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, result
    return dll


def checked(value: Any) -> None:
    if not value:
        raise c.WinError(c.get_last_error())


@contextmanager
def renderer_mutex(name: str = "Global\\UtsushibiReferenceRenderer-v1") -> Iterator[None]:
    api = kernel()
    handle = api.CreateMutexW(None, False, name)
    checked(handle)
    acquired = False
    try:
        state = api.WaitForSingleObject(handle, 0)
        acquired = state in (0, 0x80)  # abandoned owner also transfers ownership
        if not acquired:
            raise print_error("Another Office render is running.", "RENDERER_BUSY", 409)
        yield
    finally:
        if acquired:
            api.ReleaseMutex(handle)
        api.CloseHandle(handle)


def run_contained(command: list[str], *, timeout: float = 45,
                  memory_bytes: int = MEMORY_BYTES, process_limit: int = ACTIVE_PROCESSES,
                  cancel: Any = None, stdout: Any = None, stdin: Any = None,
                  appcontainer: dict[str, Any] | None = None,
                  observer: Any = None, loaded_images: Any = None,
                  environment: dict[str, str] | None = None) -> dict[str, Any]:
    """Assign while suspended, then resume; no PID-tree guessing or global kill."""
    api, win = kernel(), importlib.import_module("_winapi")
    job = api.CreateJobObjectW(None, None)
    checked(job)
    process = thread = None
    started = time.monotonic()
    try:
        limits = ExtendedLimits()
        # KILL_ON_JOB_CLOSE, JOB_MEMORY, ACTIVE_PROCESS, JOB_TIME. No breakaway.
        limits.basic.flags = 0x2000 | 0x200 | 0x8 | 0x4
        limits.basic.activeProcesses = process_limit
        limits.basic.jobTime = int(max(1, timeout) * 10_000_000)
        limits.jobMemory = memory_bytes
        checked(api.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
        cpu = (w.DWORD * 2)(1 | 4, CPU_RATE)  # ENABLE | HARD_CAP
        checked(api.SetInformationJobObject(job, 15, c.byref(cpu), c.sizeof(cpu)))
        startup = subprocess.STARTUPINFO()
        handles = []
        if stdout is not None and stdin is not None:
            crt = importlib.import_module("msvcrt")
            handles = [crt.get_osfhandle(stdout.fileno()), crt.get_osfhandle(stdin.fileno())]
            startup.dwFlags |= subprocess.STARTF_USESTDHANDLES
            startup.hStdOutput = startup.hStdError = handles[0]
            startup.hStdInput = handles[1]
            startup.lpAttributeList = {"handle_list": handles}
        try:
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            if appcontainer is not None:
                from app.services.renderer_appcontainer import launch
                process, thread, pid, _ = launch(command, appcontainer, handles)
            else:
                process, thread, pid, _ = win.CreateProcess(
                    command[0], subprocess.list2cmdline(command), None, None, bool(handles),
                    0x4 | 0x08000000, environment, None, startup)
        finally:
            for handle in handles:
                os.set_handle_inheritable(handle, False)
        # An assignment failure leaves the process suspended and is fail-closed.
        checked(api.AssignProcessToJobObject(job, process))
        if api.ResumeThread(thread) == 0xFFFFFFFF:
            raise c.WinError(c.get_last_error())
        api.CloseHandle(thread)
        thread = None
        accounting = Accounting()
        while True:
            checked(api.QueryInformationJobObject(job, 1, c.byref(accounting),
                                                  c.sizeof(accounting), None))
            if accounting.active == 0:
                break
            if observer is not None:
                observer(pid)
            if loaded_images is not None:
                loaded_images.observe_job(job, api)
            if cancel is not None and cancel.is_set():
                raise print_error("Render cancelled.", "RENDER_CANCELLED", 409)
            if time.monotonic() - started > timeout:
                raise print_error("Office render timed out.", "RENDER_TIMEOUT", 409)
            time.sleep(.025)
        checked(api.QueryInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits), None))
        return {"pid": pid, "exitCode": win.GetExitCodeProcess(process),
                "peakJobMemoryBytes": limits.peakJobMemory,
                "totalProcesses": accounting.total, "activeProcesses": accounting.active,
                "elapsedSeconds": time.monotonic() - started}
    finally:
        # Close kills descendants even when the service dies. Explicit terminate
        # also lets normal Python exceptions wait before removing the profile.
        api.TerminateJobObject(job, 1)
        if process is not None:
            # Covers failure before assignment, without ever resuming that child.
            if win.GetExitCodeProcess(process) == 259:
                win.TerminateProcess(process, 1)
            api.WaitForSingleObject(process, 5000)
        deadline = time.monotonic() + 5
        accounting = Accounting()
        while (api.QueryInformationJobObject(job, 1, c.byref(accounting),
                                            c.sizeof(accounting), None)
               and accounting.active and time.monotonic() < deadline):
            time.sleep(.025)
        if thread is not None:
            api.CloseHandle(thread)
        if process is not None:
            api.CloseHandle(process)
        api.CloseHandle(job)


def capture_contained(command: list[str], *, timeout: float = 10) -> bytes:
    with tempfile.TemporaryFile() as output, open(os.devnull, "rb") as stdin:
        result = run_contained(command, timeout=timeout, stdout=output, stdin=stdin)
        if result["exitCode"] or output.tell() > 65536:
            raise print_error("Renderer identity query failed.", "RENDERER_UNAVAILABLE", 409)
        output.seek(0)
        return output.read(65536)
