"""Linux-only fixed-protocol renderer broker and isolated child.

Invoked by the Windows broker through WSL; not an HTTP/RPC command runner.
Only page.xlsx is read. A fixed PDF/FODS output and renderer-report.json are returned.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import select
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

MAX_FILE = 64 * 1024 * 1024
POLICY = "wsl-bwrap-cgroup-v1"
FONT_REQUEST_POLICY = "wsl-fontconfig-face-v2"
RUNTIME_POLICY = "wsl-squashfs-runtime-v1"
RUNTIME_STORE = Path("/opt/llmocr-renderer-runtimes")
RUNTIME_PATHS = ("/usr", "/lib", "/lib64", "/bin", "/etc/fonts", "/etc/libreoffice")
FONT_DIRECTORY_POLICY = "wsl-font-directory-v1"
MAX_FONT_DIRECTORY_FILES = 4096
MAX_FONT_DIRECTORY_BYTES = 1024 * 1024 * 1024
WORKER_POLICY = "wsl-worker-source-v1"
DIMENSION_POLICY = "wsl-uno-dimension-v1"

CORE_GUARD_POLICY = "linux-core-limit-seccomp-v1"
CORE_GUARD_FILTER_SHA = "9382165a2839306b47868187ad837d0674a97cc3b6cb94bb59b3d9c21efd1c36"
BOOTSTRAP_PYTHON_POLICY = "wsl-bootstrap-python-tree-v2"
NATIVE_BOOTSTRAP_POLICY = "wsl-bootstrap-elf-startup-v1"
NATIVE_LOADER = "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
NATIVE_TOOLS = (
    "/usr/bin/python3", "/usr/bin/unshare", "/usr/bin/mount", "/usr/bin/umount",
    "/usr/bin/nsenter", "/usr/bin/bwrap", "/usr/bin/systemd-run", "/usr/bin/systemctl",
    "/usr/lib/systemd/systemd", "/usr/bin/bash",
    "/usr/lib/x86_64-linux-gnu/libseccomp.so.2",
)

UNIT_CONTROL_POLICY = "wsl-effective-unit-controls-v1"
UNIT_CONTROL_VALUES = {
    "Restart": "no", "RemainAfterExit": "no", "KillMode": "control-group",
    "SendSIGKILL": "yes", "RuntimeMaxUSec": "1min", "TimeoutStopUSec": "2s",
    "LimitFSIZE": "67108864", "LimitFSIZESoft": "67108864",
    "LimitCORE": "1", "LimitCORESoft": "1",
    "MemoryMax": "1073741824", "MemorySwapMax": "0", "TasksMax": "64",
    "RootDirectory": "", "RootImage": "", "User": "", "Group": "",
    "DropInPaths": "", "Transient": "yes",
}


def validate_unit_controls(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {"policy", "properties"}
            or value.get("policy") != UNIT_CONTROL_POLICY
            or value.get("properties") != UNIT_CONTROL_VALUES):
        raise ValueError("Invalid effective unit controls")
    return {"policy": UNIT_CONTROL_POLICY, "properties": dict(UNIT_CONTROL_VALUES)}


def read_unit_controls(unit: str) -> dict[str, Any] | None:
    """Query only the owned unit; absent/not-started is not admission."""
    if re.fullmatch(r"llmocr-(?:render|glyph)-[0-9a-f]{32}", unit) is None:
        raise ValueError("Invalid owned renderer unit")
    lifecycle = {
        "Id": unit + ".service", "LoadState": "loaded", "ActiveState": "active",
        "SubState": "running", "ControlGroup": f"/system.slice/{unit}.service",
    }
    keys = [*UNIT_CONTROL_VALUES, *lifecycle]
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            ["/usr/bin/systemctl", "show", "--no-pager", "--all",
             "--property=" + ",".join(keys), unit + ".service"],
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}, timeout=2,
        )
        output.seek(0)
        raw = output.read(16385)
    if len(raw) > 16384:
        raise RuntimeError("Oversized unit control response")
    properties = {}
    for line in raw.decode("utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in keys or key in properties:
            raise RuntimeError("Invalid unit control response")
        properties[key] = value
    if properties.get("LoadState") == "not-found":
        return None
    if result.returncode or set(properties) != set(keys):
        raise RuntimeError("Incomplete unit control response")
    if properties["ActiveState"] in {"inactive", "activating"}:
        return None
    if any(properties[key] != value for key, value in lifecycle.items()):
        raise RuntimeError("Renderer unit identity or lifecycle mismatch")
    return validate_unit_controls({
        "policy": UNIT_CONTROL_POLICY,
        "properties": {key: properties[key] for key in UNIT_CONTROL_VALUES},
    })


def publish_unit_admission(root: Path, controls: dict[str, Any]) -> None:
    """Publish atomically through a broker-owned directory mounted read-only."""
    payload = json.dumps(validate_unit_controls(controls), sort_keys=True).encode()
    pending = root / "admission-pending.json"
    with pending.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            raise RuntimeError("Unit admission is not owned by the root broker")
    pending.chmod(0o444)
    os.replace(pending, root / "admission" / "controls.json")


def await_unit_admission() -> dict[str, Any]:
    """No input/font/parser access until the broker verifies effective limits."""
    import resource

    mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
    admission_mounts = [entry for entry in mounts if len(entry) > 5 and entry[4] == "/admission"]
    if len(admission_mounts) != 1 or "ro" not in admission_mounts[0][5].split(","):
        raise RuntimeError("Unit admission mount is not read-only")
    deadline = time.monotonic() + 5
    path = Path("/admission/controls.json")
    while True:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Renderer unit admission timed out") from None
            time.sleep(0.02)
            continue
        try:
            info = os.fstat(descriptor)
            # The root-owned host inode can appear as overflow uid 65534 in
            # bwrap's user namespace. Ownership is checked by the root broker;
            # here enforce the actual read-only mount and immutable file mode.
            if (not stat.S_ISREG(info.st_mode)
                    or info.st_mode & 0o222 or info.st_nlink != 1
                    or not 0 < info.st_size <= 4096):
                raise RuntimeError(
                    f"Invalid unit admission file: uid={info.st_uid}, "
                    f"mode={info.st_mode:o}, links={info.st_nlink}, bytes={info.st_size}"
                )
            content = os.read(descriptor, 4097)
            after = os.fstat(descriptor)
            if len(content) != info.st_size or any(
                getattr(after, key) != getattr(info, key)
                for key in ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink",
                            "st_size", "st_mtime_ns", "st_ctime_ns")
            ):
                raise RuntimeError("Unit admission changed during read")
            controls = validate_unit_controls(json.loads(content))
        finally:
            os.close(descriptor)
        if resource.getrlimit(resource.RLIMIT_FSIZE) != (MAX_FILE, MAX_FILE):
            raise RuntimeError("Renderer file-size limits were not enforced")
        return controls


def native_dependency_paths(output: str) -> list[str]:
    """Parse fixed glibc --list output; never silently discard unknown lines."""
    if output.strip() == "statically linked":
        return []
    paths = set()
    for line in output.splitlines():
        line = line.strip()
        if re.fullmatch(r"linux-vdso\.so\.1 \(0x[0-9a-f]+\)", line):
            continue
        match = re.fullmatch(r"(?:\S+ => )?(/[^\s]+) \(0x[0-9a-f]+\)", line)
        if match is None or ".." in Path(match[1]).parts:
            raise RuntimeError("Unrecognized bootstrap ELF dependency")
        paths.add(match[1])
    if not paths:
        raise RuntimeError("Missing bootstrap ELF dependencies")
    return sorted(paths)


def _native_file_identity(path: Path) -> tuple[dict[str, Any], bytes]:
    resolved = path.resolve(strict=True)
    if not any(resolved.is_relative_to(root) for root in (
        Path("/usr/bin"), Path("/usr/lib"), Path("/etc"),
    )):
        raise RuntimeError("Bootstrap ELF input escapes installation")
    # Check both alias and resolved ancestry, including merged-/usr links.
    for item in (path, *path.parents, resolved, *resolved.parents):
        link = item.lstat()
        info = item.stat()
        if link.st_uid != 0 or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("Unsafe bootstrap ELF ownership or permissions")
    before = resolved.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 128 * 1024 * 1024:
        raise RuntimeError("Invalid bootstrap ELF file")

    def signature(info: os.stat_result) -> tuple[int, ...]:
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    sha, size, header = hashlib.sha256(), 0, b""
    try:
        if signature(os.fstat(descriptor)) != signature(before):
            raise RuntimeError("Bootstrap ELF input changed before read")
        while block := os.read(descriptor, 1024 * 1024):
            if not size:
                header = block[:20]
            size += len(block)
            if size > before.st_size:
                raise RuntimeError("Bootstrap ELF input grew")
            sha.update(block)
        if (size != before.st_size or signature(os.fstat(descriptor)) != signature(before)
                or signature(resolved.stat()) != signature(before)
                or path.resolve(strict=True) != resolved):
            raise RuntimeError("Bootstrap ELF input changed during read")
    finally:
        os.close(descriptor)
    return {"path": str(path), "resolved": str(resolved), "sha256": sha.hexdigest(),
            "bytes": size, "mode": before.st_mode & 0o7777,
            "uid": before.st_uid, "gid": before.st_gid}, header


def native_bootstrap_identity() -> dict[str, Any]:
    """Startup linkage of trusted installed ELF files, not a full dlopen trace.

    No document path is accepted. The fixed loader lists dependencies without
    starting the target tool. Whole-host/systemd configuration qualification is
    separate, as is the immutable runtime mounted inside the sandbox.
    """
    if (sys.platform != "linux" or os.getuid() != 0
            or any(key.startswith("LD_") or key == "GLIBC_TUNABLES" for key in os.environ)
            or os.path.lexists("/etc/ld.so.preload")):
        raise RuntimeError("Unexpected bootstrap ELF loader policy")
    started = time.monotonic()

    def inputs() -> tuple[list[str], list[str]]:
        roots = sorted({*NATIVE_TOOLS, NATIVE_LOADER,
                        *(str(p) for p in Path("/usr/lib/python3.10").rglob("*.so"))})
        configs = sorted({"/etc/ld.so.cache", "/etc/ld.so.conf",
                          *(str(p) for p in Path("/etc/ld.so.conf.d").glob("*.conf"))})
        if len(roots) > 256 or len(configs) > 128:
            raise RuntimeError("Bootstrap ELF inventory budget exceeded")
        return roots, configs

    roots, configs = inputs()
    records: dict[str, dict[str, Any]] = {}
    total = 0

    def record(name: str, *, elf: bool = False) -> None:
        nonlocal total
        value, header = _native_file_identity(Path(name))
        if elf and (header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00"):
            raise RuntimeError("Unsupported bootstrap ELF architecture")
        if name in records:
            if records[name] != value:
                raise RuntimeError("Bootstrap ELF input changed during inventory")
        else:
            total += value["bytes"]
            records[name] = value
        if len(records) > 1024 or total > 512 * 1024 * 1024:
            raise RuntimeError("Bootstrap ELF byte budget exceeded")

    # Hash the fixed loader and every target before any loader invocation.
    for name in roots:
        record(name, elf=True)
    for name in configs:
        record(name)
    graph = []
    for name in roots:
        if time.monotonic() - started > 20:
            raise RuntimeError("Bootstrap ELF inventory timeout")
        with tempfile.TemporaryFile() as output:
            result = subprocess.run([NATIVE_LOADER, "--list", name],
                                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                    timeout=5, check=False)
            output.seek(0)
            content = output.read(128 * 1024 + 1)
        if result.returncode or len(content) > 128 * 1024:
            raise RuntimeError("Bootstrap ELF dependency inspection failed")
        dependencies = native_dependency_paths(content.decode("ascii", errors="strict"))
        for dependency in dependencies:
            record(dependency, elf=True)
        graph.append({"path": name, "dependencies": dependencies,
                      "kind": "dynamic" if dependencies else "statically-linked-report"})
    for name in list(records):
        record(name)
    if inputs() != (roots, configs) or os.path.lexists("/etc/ld.so.preload"):
        raise RuntimeError("Bootstrap ELF inventory changed")
    payload = json.dumps({"files": [records[n] for n in sorted(records)], "graph": graph,
                          "preloadAbsent": True}, sort_keys=True, separators=(",", ":")).encode()
    return {"policy": NATIVE_BOOTSTRAP_POLICY, "sha256": hashlib.sha256(payload).hexdigest(),
            "rootCount": len(roots), "fileCount": len(records), "totalBytes": total,
            "preloadAbsent": True}


def validate_native_bootstrap(value: object) -> None:
    if (not isinstance(value, dict) or value.get("policy") != NATIVE_BOOTSTRAP_POLICY
            or value.get("preloadAbsent") is not True
            or not isinstance(value.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
            or any(type(value.get(key)) is not int or not 1 <= value[key] <= upper
                   for key, upper in (("rootCount", 256), ("fileCount", 1024),
                                      ("totalBytes", 512 * 1024 * 1024)))
            or value["fileCount"] < value["rootCount"]):
        raise RuntimeError("Bootstrap ELF evidence mismatch")


def validate_bootstrap_python(value: object) -> None:
    if not isinstance(value, dict):
        raise RuntimeError("Bootstrap Python evidence is missing")
    if (value.get("policy") != BOOTSTRAP_PYTHON_POLICY
            or value.get("searchPaths") != ["/usr/lib/python310.zip", "/usr/lib/python3.10",
                                            "/usr/lib/python3.10/lib-dynload"]
            or any(value.get(key) is not True for key in (
                "isolated", "noSite", "noBytecodeWrite"))
            or not isinstance(value.get("pythonVersion"), str)
            or re.fullmatch(r"3\.10\.[0-9]+", value["pythonVersion"]) is None
            or not isinstance(value.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
            or any(type(value.get(key)) is not int or not 1 <= value[key] <= upper
                   for key, upper in (("entryCount", 20000), ("fileCount", 20000),
                                      ("totalBytes", 512 * 1024 * 1024)))
            or value["fileCount"] > value["entryCount"]):
        raise RuntimeError("Bootstrap Python evidence mismatch")
    validate_native_bootstrap(value.get("nativeDependencies"))


def bootstrap_python_identity(code_archive: Path | None = None) -> dict[str, Any]:
    """Content identity of the root interpreter's complete import search tree.

    This covers Python inputs, file-link targets and installed ELF startup
    dependencies, not a complete dynamic-load trace or an environment certificate.
    """
    expected = ["/usr/lib/python310.zip", "/usr/lib/python3.10",
                "/usr/lib/python3.10/lib-dynload"]
    paths = list(sys.path)
    if code_archive is not None and (
        not paths or Path(paths.pop(0)).resolve(strict=True)
        != code_archive.resolve(strict=True) or code_archive.suffix != ".pyz"
    ):
        raise RuntimeError("Unexpected bootstrap code archive")
    if (paths != expected or sys.version_info[:2] != (3, 10) or os.getuid() != 0
            or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode):
        raise RuntimeError("Unexpected bootstrap Python search policy")
    records: dict[str, dict[str, Any]] = {}
    visiting: set[str] = set()
    total = 0

    def signature(info: os.stat_result) -> tuple[int, ...]:
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def ancestors(path: Path) -> None:
        for parent in path.parents:
            info = parent.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022):
                raise RuntimeError("Unsafe bootstrap Python ancestor")

    def visit(path: Path, *, linked: bool = False) -> None:
        nonlocal total
        name = str(path)
        if name in visiting:
            raise RuntimeError("Cyclic bootstrap Python link")
        if name in records:
            if linked and records[name]["kind"] in {"absent", "directory"}:
                raise RuntimeError("Unsupported bootstrap Python link target")
            return
        if len(records) + len(visiting) >= 20000:
            raise RuntimeError("Bootstrap Python inventory budget exceeded")
        if not any(path.is_relative_to(root) for root in (
            Path("/usr/lib"), Path("/usr/bin"), Path("/etc/python3.10"),
        )) and name != "/usr/pyvenv.cfg":
            raise RuntimeError("Bootstrap Python link escapes installation")
        ancestors(path)
        try:
            before = path.lstat()
        except FileNotFoundError:
            if linked:
                raise RuntimeError("Dangling bootstrap Python link") from None
            records[name] = {"path": name, "kind": "absent"}
            return
        if before.st_uid != 0:
            raise RuntimeError("Bootstrap Python input is not root-owned")
        record = {"path": name, "uid": before.st_uid, "gid": before.st_gid,
                  "mode": before.st_mode & 0o7777}
        visiting.add(name)
        try:
            if stat.S_ISLNK(before.st_mode):
                target = os.readlink(path)
                resolved = Path(os.path.normpath(str(path.parent / target)))
                record.update(kind="symlink", target=target)
                visit(resolved, linked=True)
            elif before.st_mode & 0o022:
                raise RuntimeError("Bootstrap Python input is writable")
            elif stat.S_ISDIR(before.st_mode):
                if linked:
                    raise RuntimeError("Unsupported bootstrap directory link")
                names = sorted(os.listdir(path))
                record.update(kind="directory")
                for child in names:
                    visit(path / child)
                if sorted(os.listdir(path)) != names:
                    raise RuntimeError("Bootstrap Python inventory changed")
            elif stat.S_ISREG(before.st_mode):
                if not 0 <= before.st_size <= 128 * 1024 * 1024:
                    raise RuntimeError("Bootstrap Python file budget exceeded")
                total += before.st_size
                if total > 512 * 1024 * 1024:
                    raise RuntimeError("Bootstrap Python byte budget exceeded")
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    if signature(os.fstat(descriptor)) != signature(before):
                        raise RuntimeError("Bootstrap Python input changed before read")
                    sha, size = hashlib.sha256(), 0
                    while block := os.read(descriptor, 1024 * 1024):
                        size += len(block)
                        if size > before.st_size:
                            raise RuntimeError("Bootstrap Python input grew")
                        sha.update(block)
                    if (size != before.st_size
                            or signature(os.fstat(descriptor)) != signature(before)):
                        raise RuntimeError("Bootstrap Python input changed during read")
                finally:
                    os.close(descriptor)
                record.update(kind="file", bytes=size, sha256=sha.hexdigest())
            else:
                raise RuntimeError("Unsupported bootstrap Python input")
            if signature(path.lstat()) != signature(before):
                raise RuntimeError("Bootstrap Python input changed")
            records[name] = record
        finally:
            visiting.remove(name)

    for name in (expected[0], expected[1], "/usr/bin/python3", "/usr/bin/python3.10",
                 "/usr/bin/pyvenv.cfg", "/usr/pyvenv.cfg"):
        visit(Path(name))
    payload = json.dumps([records[name] for name in sorted(records)], sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
    return {"policy": BOOTSTRAP_PYTHON_POLICY, "pythonVersion": sys.version.split()[0],
            "searchPaths": expected, "isolated": True, "noSite": True, "noBytecodeWrite": True,
            "sha256": hashlib.sha256(payload).hexdigest(), "entryCount": len(records),
            "fileCount": sum(r["kind"] == "file" for r in records.values()), "totalBytes": total,
            "nativeDependencies": native_bootstrap_identity()}


def validate_core_guard(value: object) -> None:
    """Validate current worker evidence, not a saved production certificate."""
    if not isinstance(value, dict):
        raise RuntimeError("Core dump guard evidence is missing")
    limits = value.get("coreLimits")
    if (
        value.get("policy") != CORE_GUARD_POLICY
        or value.get("architecture") != "x86_64"
        or not isinstance(limits, list) or limits != [1, 1]
        or any(type(limit) is not int for limit in limits)
        or value.get("noNewPrivileges") is not True
        or any(type(value.get(key)) is not int or value[key] != expected for key, expected in (
            ("seccompMode", 2), ("filterBytes", 192), ("deniedMutationProbes", 4),
        ))
        or value.get("filterSha256") != CORE_GUARD_FILTER_SHA
        or not isinstance(value.get("librarySha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["librarySha256"]) is None
    ):
        raise RuntimeError("Core dump guard evidence mismatch")


def install_core_guard(*, expected_uid: int = 65534) -> dict[str, Any]:
    # Local imports keep the Windows bridge/root bootstrap importable without
    # loading Linux-only resource or any additional native dependency.
    import ctypes
    import errno
    import platform
    import resource

    if (platform.system() != "Linux" or platform.machine() != "x86_64"
            or ctypes.sizeof(ctypes.c_ulong) != 8
            or type(expected_uid) is not int or expected_uid not in {0, 65534}
            or os.getuid() != expected_uid):
        raise RuntimeError("Unsupported core guard process")

    class Comparison(ctypes.Structure):
        _fields_: ClassVar[list[tuple[str, Any]]] = [
            ("arg", ctypes.c_uint), ("op", ctypes.c_int),
            ("a", ctypes.c_uint64), ("b", ctypes.c_uint64),
        ]

    class Limits(ctypes.Structure):
        _fields_: ClassVar[list[tuple[str, Any]]] = [
            ("soft", ctypes.c_ulong), ("hard", ctypes.c_ulong),
        ]

    def checked(code: int) -> None:
        if code != 0:
            raise RuntimeError("Core guard operation failed")

    path = Path("/usr/lib/x86_64-linux-gnu/libseccomp.so.2")

    def library_bytes() -> bytes:
        with path.open("rb") as stream:
            content = stream.read(4 * 1024 * 1024 + 1)
        if not 0 < len(content) <= 4 * 1024 * 1024:
            raise RuntimeError("Invalid core guard library")
        return content

    before = library_bytes()
    lib = ctypes.CDLL(str(path), use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.restype = None
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_attr_set.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
    lib.seccomp_attr_set.restype = ctypes.c_int
    lib.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint,
        ctypes.POINTER(Comparison),
    ]
    lib.seccomp_rule_add_array.restype = ctypes.c_int
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_load.restype = ctypes.c_int
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_export_bpf.restype = ctypes.c_int
    context = lib.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("Core guard allocation failed")
    numbers = {}
    try:
        checked(lib.seccomp_attr_set(context, 2, 0x80000000))  # BADARCH: KILL_PROCESS
        checked(lib.seccomp_attr_set(context, 3, 1))  # no_new_privs
        checked(lib.seccomp_attr_set(context, 4, 1))  # TSYNC
        for name, comparisons in (
            (b"setrlimit", [Comparison(0, 7, 0xffffffff, resource.RLIMIT_CORE)]),
            (b"prlimit64", [Comparison(1, 7, 0xffffffff, resource.RLIMIT_CORE),
                            Comparison(2, 1, 0, 0)]),
        ):
            number = lib.seccomp_syscall_resolve_name(name)
            if number < 0:
                raise RuntimeError("Unknown core limit syscall")
            numbers[name] = number
            comparisons_array = (Comparison * len(comparisons))(*comparisons)
            checked(lib.seccomp_rule_add_array(context, 0x50000 | errno.EPERM,
                                              number, len(comparisons_array), comparisons_array))
        descriptor = os.memfd_create("llmocr-core-filter", os.MFD_CLOEXEC)
        try:
            checked(lib.seccomp_export_bpf(context, descriptor))
            os.lseek(descriptor, 0, os.SEEK_SET)
            bpf = os.read(descriptor, 32769)
            if not bpf or len(bpf) > 32768 or len(bpf) % 8:
                raise RuntimeError("Invalid core guard filter")
            if hashlib.sha256(bpf).hexdigest() != CORE_GUARD_FILTER_SHA:
                raise RuntimeError("Unverified core guard filter")
        finally:
            os.close(descriptor)
        # Linux's pipe core handler bypasses RLIMIT_CORE=0; the sentinel value
        # 1 suppresses that helper. Both limits must remain 1 after installation.
        resource.setrlimit(resource.RLIMIT_CORE, (1, 1))
        checked(lib.seccomp_load(context))
    finally:
        lib.seccomp_release(context)

    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    candidate = Limits(0, 1)
    denied = 0
    for name, number in numbers.items():
        for high in (0, 1 << 32):
            resource_value = ctypes.c_ulong(resource.RLIMIT_CORE | high)
            syscall_arguments = (
                [resource_value, ctypes.byref(candidate)] if name == b"setrlimit" else
                [ctypes.c_ulong(0), resource_value, ctypes.byref(candidate), ctypes.c_void_p(0)]
            )
            ctypes.set_errno(0)
            result = libc.syscall(ctypes.c_long(number), *syscall_arguments)
            if result != -1 or ctypes.get_errno() != errno.EPERM:
                raise RuntimeError("Core limit remains mutable")
            denied += 1
    status = Path("/proc/thread-self/status").read_text()
    if (resource.getrlimit(resource.RLIMIT_CORE) != (1, 1)
            or "NoNewPrivs:\t1" not in status or "Seccomp:\t2" not in status
            or library_bytes() != before):
        raise RuntimeError("Core guard verification failed")
    evidence = {
        "policy": CORE_GUARD_POLICY, "architecture": "x86_64", "coreLimits": [1, 1],
        "noNewPrivileges": True, "seccompMode": 2,
        "filterSha256": hashlib.sha256(bpf).hexdigest(),
        "filterBytes": len(bpf), "deniedMutationProbes": denied,
        "librarySha256": hashlib.sha256(before).hexdigest(),
    }
    validate_core_guard(evidence)
    return evidence


def dimension_request(value: object, format: str, runtime_id: str | None) -> dict[str, Any]:
    """Bounded, explicit prefix dimensions; never a path or UNO expression."""
    if (format != "fods" or runtime_id is None or not isinstance(value, dict)
            or set(value) != {"sheetName", "rows", "columns"}
            or not isinstance(value["sheetName"], str)
            or not 1 <= len(value["sheetName"]) <= 128
            or any(ord(c) < 32 or ord(c) == 127 for c in value["sheetName"])
            or any(type(value[k]) is not int or not 1 <= value[k] <= 20000
                   for k in ("rows", "columns"))
            or value["columns"] > 16384
            or value["rows"] + value["columns"] > 20000):
        raise ValueError("Invalid dimension request")
    return dict(value)


def observe_dimensions(sheet: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Range Size retains wide dimensions where scalar Width/Height can wrap.

    Records are ordered from index zero: [scalar mm100, range size mm100, visible].
    These are raw observations, not accepted print geometry or qualification.
    """
    records = {}
    for axis, collection, property_name in (
        ("columns", sheet.Columns, "Width"), ("rows", sheet.Rows, "Height"),
    ):
        values = []
        for index in range(request[axis]):
            cell = (sheet.getCellRangeByPosition(index, 0, index, 0) if axis == "columns"
                    else sheet.getCellRangeByPosition(0, index, 0, index))
            dimension = collection.getByIndex(index)
            scalar = getattr(dimension, property_name)
            size = getattr(cell.Size, property_name)
            visible = dimension.IsVisible
            if (type(scalar) is not int or not 0 <= scalar <= 2**31 - 1
                    or type(size) is not int or not 0 <= size <= 2**31 - 1
                    or type(visible) is not bool):
                raise RuntimeError("Invalid UNO dimension observation")
            values.append([scalar, size, visible])
        records[axis] = values
    return records


def import_observed_fods(request: dict[str, Any]) -> dict[str, Any]:
    # Only called inside the already-probed namespace. No UNO in root bootstrap.
    # Native UNO modules exist only in the pinned Linux image, not the host venv.
    import uno  # type: ignore[import-not-found]
    from com.sun.star.document.MacroExecMode import NEVER_EXECUTE  # type: ignore[import-not-found]
    from com.sun.star.document.UpdateDocMode import NO_UPDATE  # type: ignore[import-not-found]

    def properties(**values: Any) -> tuple[Any, ...]:
        result = []
        for name, value in values.items():
            prop = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
            prop.Name, prop.Value = name, value
            result.append(prop)
        return tuple(result)

    office = subprocess.Popen([
        "/usr/bin/libreoffice", "-env:UserInstallation=file:///tmp/profile",
        "--headless", "--nologo", "--nodefault", "--norestore",
        "--accept=pipe,name=llmocr_dimensions;urp;StarOffice.ComponentContext",
    ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    document = desktop = None
    try:
        context = uno.getComponentContext()
        resolver = context.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", context)
        deadline = time.monotonic() + 15
        while True:
            if office.poll() is not None:
                raise RuntimeError("UNO renderer exited before connection")
            try:
                remote = resolver.resolve(
                    "uno:pipe,name=llmocr_dimensions;urp;StarOffice.ComponentContext")
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise RuntimeError("UNO renderer connection timed out") from None
                time.sleep(0.05)
        desktop = remote.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", remote)
        document = desktop.loadComponentFromURL("file:///page.xlsx", "_blank", 0, properties(
            Hidden=True, ReadOnly=True, MacroExecutionMode=NEVER_EXECUTE,
            UpdateDocMode=NO_UPDATE))
        if document is None or not document.isReadonly():
            raise RuntimeError("UNO import is not read-only")
        sheet = document.Sheets.getByName(request["sheetName"])
        before = observe_dimensions(sheet, request)
        # Export the SAME imported document; a second CLI import is not evidence.
        document.storeToURL("file:///output/page.fods", properties(
            FilterName="OpenDocument Spreadsheet Flat XML", Overwrite=False))
        if observe_dimensions(sheet, request) != before:
            raise RuntimeError("UNO dimensions changed during export")
        return {"policy": DIMENSION_POLICY, "request": dict(request), **before}
    finally:
        try:
            if document is not None:
                document.close(True)
            if desktop is not None:
                desktop.terminate()
        finally:
            try:
                returncode = office.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                office.kill()
                office.wait(timeout=5)
                raise RuntimeError("UNO renderer did not exit before deadline") from exc
            if returncode != 0:
                raise RuntimeError("UNO renderer failed during shutdown")


def worker_identity(path: Path) -> dict[str, str]:
    with path.open("rb") as stream:
        source = stream.read(1024 * 1024 + 1)
    if not 0 < len(source) <= 1024 * 1024:
        raise RuntimeError("Invalid renderer worker size")
    return {"policy": WORKER_POLICY, "sha256": hashlib.sha256(source).hexdigest()}


def _font_signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _font_entries(root: Path) -> dict[str, os.stat_result]:
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise RuntimeError("Unsupported font directory entry")
    entries = {}
    total = 0
    with os.scandir(root) as directory:
        for entry in directory:
            info = (root / entry.name).lstat()
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("Unsupported font directory entry")
            total += info.st_size
            entries[entry.name] = info
            if (len(entries) > MAX_FONT_DIRECTORY_FILES or len(entry.name) > 256
                    or info.st_size > MAX_FILE or total > MAX_FONT_DIRECTORY_BYTES):
                raise RuntimeError("Font directory resource limit exceeded")
    if not entries:
        raise RuntimeError("Font directory file count limit exceeded")
    return entries


def font_directory_identity(root: Path) -> dict[str, str | int]:
    """Hash the whole mounted set, including metadata, without a stat-based cache.

    Linux uses no-follow/nonblocking opens; the optional flags permit Windows
    unit tests to exercise the same stream and race checks in binary mode.
    This detects ordinary changes, not arbitrary concurrent host-root attacks.
    """
    entries = _font_entries(root)
    records = []
    for name, before in sorted(entries.items()):
        path = root / name
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(descriptor)
            # Windows lstat/fstat can report different ctime semantics. Check
            # each API's own ctime before/after; Linux additionally cross-checks it.
            if (_font_signature(opened)[:-1] != _font_signature(before)[:-1]
                    or (os.name != "nt" and opened.st_ctime_ns != before.st_ctime_ns)):
                raise RuntimeError("Font directory changed while reading")
            digest = hashlib.sha256()
            size = 0
            while block := os.read(descriptor, 1024 * 1024):
                size += len(block)
                if size > MAX_FILE or size > before.st_size:
                    raise RuntimeError("Font directory changed while reading")
                digest.update(block)
            if (size != before.st_size
                    or _font_signature(os.fstat(descriptor)) != _font_signature(opened)
                    or _font_signature(path.lstat()) != _font_signature(before)):
                raise RuntimeError("Font directory changed while reading")
        finally:
            os.close(descriptor)
        records.append({"name": name, "bytes": size, "sha256": digest.hexdigest()})
    final = _font_entries(root)
    if ({name: _font_signature(info) for name, info in final.items()}
            != {name: _font_signature(info) for name, info in entries.items()}):
        raise RuntimeError("Font directory changed while reading")
    payload = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return {"policy": FONT_DIRECTORY_POLICY, "fileCount": len(entries),
            "totalBytes": sum(info.st_size for info in entries.values()),
            "sha256": hashlib.sha256(payload).hexdigest()}


def verify_font_directory(root: Path, expected: dict[str, str | int]) -> None:
    if font_directory_identity(root) != expected:
        raise RuntimeError("Font directory changed during rendering")


def verify_runtime_descriptor(descriptor: int, runtime_id: str) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while block := os.read(descriptor, 1024 * 1024):
        digest.update(block)
    if digest.hexdigest() != runtime_id:
        raise RuntimeError("Runtime image identity changed")


@contextmanager
def pinned_runtime(runtime_id: str | None) -> Iterator[Path | None]:
    if runtime_id is None:
        yield None
        return
    if re.fullmatch(r"[0-9a-f]{64}", runtime_id) is None:
        raise ValueError("Invalid runtime identity")
    if os.geteuid() != 0 or os.readlink("/proc/self/ns/mnt") == os.readlink("/proc/1/ns/mnt"):
        raise RuntimeError("Private root-owned mount namespace required")
    mount_info = Path("/proc/self/mountinfo").read_text().splitlines()
    if any(part.startswith("shared:") for line in mount_info
           for part in line.split()):
        raise RuntimeError("Shared mount propagation is forbidden")
    for directory in (RUNTIME_STORE, *RUNTIME_STORE.parents):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("Unsafe runtime store")
    path = RUNTIME_STORE / (runtime_id + ".squashfs")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222
                or info.st_nlink != 1 or not 0 < info.st_size <= 8 * 1024**3):
            raise RuntimeError("Unsafe runtime image")

        verify_runtime_descriptor(descriptor, runtime_id)
        # Hold the exact inode while loop mounting; a path replacement cannot
        # change which image this invocation uses. No persistent/global mount.
        mounted = Path(tempfile.mkdtemp(prefix="llmocr-runtime-"))
        subprocess.run([
            "/usr/bin/mount", "--no-canonicalize", "-t", "squashfs", "-o",
            "loop,ro,nodev,nosuid", f"/proc/{os.getpid()}/fd/{descriptor}", str(mounted),
        ], check=True, timeout=10)
        try:
            yield mounted
            verify_runtime_descriptor(descriptor, runtime_id)
        finally:
            # If unmount fails, retain the directory. Never recursively traverse
            # a mounted image. This private namespace dies with its broker.
            subprocess.run(["/usr/bin/umount", str(mounted)], check=True, timeout=10)
            mounted.rmdir()
    finally:
        os.close(descriptor)


def sandbox_command(
    root: Path, unit: str, format: str = "pdf", font_source: str = "linux",
    runtime_root: Path | None = None, runtime_id: str | None = None,
    dimensions: dict[str, Any] | None = None,
) -> list[str]:
    if dimensions is not None:
        dimensions = dimension_request(dimensions, format, runtime_id)
    if (runtime_root is None) != (runtime_id is None):
        raise ValueError("Runtime mount and identity must be provided together")
    args = [
        "/usr/bin/bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--uid",
        "65534",
        "--gid",
        "65534",
        "--clearenv",
    ]
    for source in RUNTIME_PATHS:
        origin = str(runtime_root / source.lstrip("/")) if runtime_root is not None else source
        args += ["--ro-bind", origin, source]
    if font_source == "windows":
        # Fixed read-only system font directory only; never expose /mnt/c or a
        # user-selected host path inside the renderer. Fonts are not copied out.
        args += ["--ro-bind", "/mnt/c/Windows/Fonts", "/reference-fonts"]
    if runtime_id is not None:
        args += ["--ro-bind", str(root / "admission"), "/admission"]
    for limit in ("memory.max", "memory.swap.max", "pids.max", "cpu.max"):
        args += [
            "--ro-bind",
            f"/sys/fs/cgroup/system.slice/{unit}.service/{limit}",
            f"/limits/{limit}",
        ]
    args += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/home",
        "--dir",
        "/run",
        "--dir",
        "/var",
        "--ro-bind",
        str(root / "page.xlsx"),
        "/page.xlsx",
        "--ro-bind",
        str(root / "worker.py" if runtime_id is not None else Path(__file__).resolve()),
        "/worker.py",
        "--bind",
        str(root / "output"),
        "/output",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/tmp/home",
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        "--setenv",
        "SAL_USE_VCLPLUGIN",
        "svp",
        "--chdir",
        "/tmp",
        "/usr/bin/python3",
        "-I",
        "/worker.py",
        "--inner",
        format,
        font_source,
    ]
    if runtime_id is not None:
        args.append(runtime_id)
    if dimensions is not None:
        args.append(json.dumps(dimensions, ensure_ascii=True, separators=(",", ":")))
    return args


def inner(format: str = "pdf", font_source: str = "linux", runtime_id: str | None = None,
          dimensions: dict[str, Any] | None = None) -> None:
    if dimensions is not None:
        dimensions = dimension_request(dimensions, format, runtime_id)
    if format not in {"pdf", "fods", "reference"} or font_source not in {"linux", "windows"}:
        raise ValueError("Unsupported output format")
    worker = worker_identity(Path(__file__)) if runtime_id is not None else None
    if runtime_id is not None:
        if re.fullmatch(r"[0-9a-f]{64}", runtime_id) is None:
            raise ValueError("Invalid runtime identity")
        mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
        for mount_path in RUNTIME_PATHS:
            matches = [entry for entry in mounts if entry[4] == mount_path]
            if (len(matches) != 1 or "ro" not in matches[0][5].split(",")
                    or matches[0][matches[0].index("-") + 1] != "squashfs"):
                raise RuntimeError("Runtime did not use the read-only image")
    # Probe the actual namespace that will also contain Office and PDF parsing.
    interfaces = sorted(name for _, name in socket.if_nameindex())
    if interfaces != ["lo"] or Path("/mnt/c").exists() or Path("/root").exists():
        raise RuntimeError("Isolation boundary was not established")
    status = Path("/proc/self/status").read_text()
    if "NoNewPrivs:\t1" not in status or "CapEff:\t0000000000000000" not in status:
        raise RuntimeError("Unexpected process privileges")
    limits = {
        key: Path("/limits", key).read_text().strip()
        for key in ("memory.max", "memory.swap.max", "pids.max", "cpu.max")
    }
    if (
        limits["memory.max"] != "1073741824"
        or limits["memory.swap.max"] != "0"
        or limits["pids.max"] != "64"
        or limits["cpu.max"] != "100000 100000"
    ):
        raise RuntimeError("Resource limits were not enforced")
    # Explicitly reject external connectivity before touching the workbook.
    network_denied = False
    with socket.socket() as probe:
        probe.settimeout(0.2)
        try:
            probe.connect(("192.0.2.1", 9))  # Documentation-only address; no route is allowed.
        except OSError:
            network_denied = True
    if not network_denied:
        raise RuntimeError("Network access was not denied")
    core_guard = install_core_guard()
    unit_controls = await_unit_admission() if runtime_id is not None else None
    if font_source == "windows":
        mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
        font_mounts = [
            mount for mount in mounts if len(mount) > 5 and mount[4] == "/reference-fonts"
        ]
        if len(font_mounts) != 1 or "ro" not in font_mounts[0][5].split(","):
            raise RuntimeError("Reference fonts are not read-only")
        font_config = Path("/tmp/reference-fonts.conf")
        font_config.write_text(
            '<?xml version="1.0"?><fontconfig><dir>/reference-fonts</dir>'
            "<cachedir>/tmp/font-cache</cachedir></fontconfig>",
            encoding="utf-8",
        )
        os.environ["FONTCONFIG_FILE"] = str(font_config)
    # Only the explicitly pinned runtime requests this new evidence contract.
    # Existing live-runtime renders and saved reports are not silently upgraded.
    font_directory = (font_directory_identity(Path("/reference-fonts"))
                      if runtime_id is not None and font_source == "windows" else None)
    font_evidence = resolved_fonts()
    profile = Path("/tmp/profile/user")
    profile.mkdir(parents=True)
    (profile / "registrymodifications.xcu").write_text(
        '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
        '<prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop>'
        "</item></oor:items>",
        encoding="utf-8",
    )
    observation = import_observed_fods(dimensions) if dimensions is not None else None
    result = (subprocess.CompletedProcess([], 0, b"", b"") if observation is not None
              else subprocess.run(
        [
            "/usr/bin/libreoffice",
            "-env:UserInstallation=file:///tmp/profile",
            "--headless",
            "--nologo",
            "--nodefault",
            "--norestore",
            "--convert-to",
            "fods" if format == "fods" else "pdf:calc_pdf_Export",
            "--outdir",
            "/output",
            "/page.xlsx",
        ],
        capture_output=True,
        timeout=45,
        check=True,
    ))
    pdf = Path("/output/page.fods" if format == "fods" else "/output/page.pdf")
    if not pdf.is_file() or not 0 < pdf.stat().st_size <= MAX_FILE:
        raise RuntimeError(
            "Missing or oversized PDF: "
            + (result.stdout + result.stderr)[:2048].decode(errors="replace")
        )
    metadata = (
        subprocess.run(
            ["/usr/bin/pdfinfo", "-box", "-f", "1", "-l", "50", str(pdf)],
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.decode()
        if format != "fods"
        else ""
    )
    count_match = re.search(r"^Pages:\s+(\d+)$", metadata, re.MULTILINE)
    sizes = [
        [float(width), float(height)]
        for width, height in re.findall(
            r"^Page\s+\d+ size:\s+([\d.]+) x ([\d.]+) pts", metadata, re.MULTILINE
        )
    ]
    page_count = int(count_match[1]) if count_match else 0
    if format != "fods" and (not 1 <= page_count <= 50 or len(sizes) != page_count):
        raise RuntimeError("Invalid PDF page geometry")
    report = {
        "policy": POLICY,
        "status": "PASS",
        "format": format,
        "fontSource": font_source,
        "referenceFontMountReadOnly": font_source == "windows",
        "resolvedFonts": font_evidence,
        "fontRequestPolicy": FONT_REQUEST_POLICY,
        "uid": os.getuid(),
        "networkInterfaces": interfaces,
        "networkDenied": network_denied,
        "hostFilesystemHidden": True,
        "noNewPrivileges": True,
        "resourceLimits": limits,
        "coreDumpGuard": core_guard,
        "sourceSha256": hashlib.sha256(Path("/page.xlsx").read_bytes()).hexdigest(),
        format + "Sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "pageCount": page_count,
        "sizes": sizes,
        "pdfInfo": metadata,
        "officeOutput": result.stdout.decode(errors="replace"),
    }
    if observation is not None:
        report["dimensionObservation"] = dict(
            observation, sourceSha256=report["sourceSha256"], fodsSha256=report["fodsSha256"])
    if runtime_id is not None:
        report["runtimeImage"] = {
            "policy": RUNTIME_POLICY, "sha256": runtime_id,
            "mountsReadOnly": True, "filesystem": "squashfs",
            "kernel": os.uname().release,
        }
        report["unitControls"] = unit_controls
    if format == "reference":
        from PIL import Image

        image_paths = []
        image_sizes = []
        total = pdf.stat().st_size
        for number, (width, height) in enumerate(sizes, 1):
            expected = (math.ceil(width * 150 / 72), math.ceil(height * 150 / 72))
            if expected[0] * expected[1] > 20_000_000:
                raise RuntimeError("Reference raster pixel limit exceeded")
            path = Path(f"/output/page-{number}.png")
            subprocess.run(
                [
                    "/usr/bin/pdftoppm",
                    "-f",
                    str(number),
                    "-l",
                    str(number),
                    "-r",
                    "150",
                    "-singlefile",
                    "-png",
                    str(pdf),
                    str(path.with_suffix("")),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
                timeout=15,
            )
            if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= MAX_FILE:
                raise RuntimeError("Invalid raster output")
            total += path.stat().st_size
            if total > MAX_FILE - 64 * 1024:
                raise RuntimeError("Reference bundle size limit exceeded")
            with Image.open(path) as image:
                if image.format != "PNG" or image.size != expected:
                    raise RuntimeError("Reference raster dimensions mismatch")
                image.verify()
            image_paths.append(path)
            image_sizes.append(list(expected))
        bundle = Path("/output/reference.zip")
        with ZipFile(bundle, "w", compression=ZIP_STORED) as archive:
            archive.write(pdf, "page.pdf")
            for path in image_paths:
                archive.write(path, path.name)
        report.update(
            pdfSha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
            referenceSha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
            imageSha256=[hashlib.sha256(path.read_bytes()).hexdigest() for path in image_paths],
            imageSizes=image_sizes,
            dpi=150,
            rasterizer="poppler-pdftoppm",
        )
    if font_directory is not None:
        verify_font_directory(Path("/reference-fonts"), font_directory)
        report["fontDirectoryIdentity"] = font_directory
    if worker is not None:
        if worker_identity(Path(__file__)) != worker:
            raise RuntimeError("Renderer worker changed during rendering")
        report["workerIdentity"] = worker
    Path("/output/renderer-report.json").write_text(json.dumps(report), encoding="utf-8")


def declared_font_requests(root: ET.Element) -> list[tuple[str, str, str]]:
    """Preserve named font faces; family-only deduplication drops bold/italic."""
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    fonts = root.findall(namespace + "fonts/" + namespace + "font")
    if not fonts or len(fonts) > 4096:
        raise RuntimeError("Invalid font declarations")

    def enabled(font: ET.Element, tag: str) -> bool:
        nodes = font.findall(namespace + tag)
        if len(nodes) > 1:
            raise RuntimeError("Ambiguous font style")
        if not nodes:
            return False
        value = nodes[0].get("val", "1")
        if value not in {"0", "1", "true", "false"}:
            raise RuntimeError("Invalid font style")
        return value in {"1", "true"}

    requests = set()
    for font in fonts:
        names = font.findall(namespace + "name")
        if len(names) != 1:
            raise RuntimeError("Ambiguous font name")
        family = names[0].get("val", "")
        if (not family.strip() or len(family) > 256
                or any(ord(c) < 32 or ord(c) == 127 for c in family)):
            raise RuntimeError("Invalid font name")
        requests.add((family, "bold" if enabled(font, "b") else "regular",
                      "italic" if enabled(font, "i") else "roman"))
    if len(requests) > 128:
        raise RuntimeError("Font resolution budget exceeded")
    return sorted(requests)


def font_pattern(family: str, weight: str, slant: str) -> str:
    # Fontconfig has its own syntax even when argv is passed without a shell.
    literal = "".join("\\" + char if char in "\\-:," else char for char in family)
    return f"{literal}:weight={weight}:slant={slant}"


def resolved_fonts() -> list[dict[str, str]]:
    with ZipFile("/page.xlsx") as archive:
        info = archive.getinfo("xl/styles.xml")
        if info.file_size > 16 * 1024 * 1024:
            raise RuntimeError("Oversized font declarations")
        raw = archive.read(info)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise RuntimeError("Unsafe font declarations")
    requests = declared_font_requests(ET.fromstring(raw))
    records = []
    for family, weight, slant in requests:
        pattern = font_pattern(family, weight, slant)
        match = (
            subprocess.run(
                ["/usr/bin/fc-match",
                 "--format=%{file}\t%{index}\t%{family}\t%{style}\t%{weight}\t%{slant}",
                 "--", pattern],
                capture_output=True,
                check=True,
                timeout=5,
            )
            .stdout.decode()
            .split("\t")
        )
        if len(match) != 6:
            raise RuntimeError("Font resolution failed")
        path = Path(match[0]).resolve(strict=True)
        if (
            not str(path).startswith(("/usr/share/fonts/", "/reference-fonts/"))
            or not 0 < path.stat().st_size <= MAX_FILE
        ):
            raise RuntimeError("Unexpected font source")
        records.append(
            {
                "requestedFamily": family,
                "requestedWeight": weight,
                "requestedSlant": slant,
                "requestPattern": pattern,
                "resolvedFamily": match[2],
                "resolvedStyle": match[3],
                "resolvedWeight": match[4],
                "resolvedSlant": match[5],
                "collectionIndex": match[1],
                "file": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return records


def broker(directory: Path, format: str = "pdf", font_source: str = "linux",
           runtime_id: str | None = None, dimensions: dict[str, Any] | None = None) -> None:
    if dimensions is not None:
        dimensions = dimension_request(dimensions, format, runtime_id)
    if format not in {"pdf", "fods", "reference"} or font_source not in {"linux", "windows"}:
        raise ValueError("Unsupported output format")
    broker_guard = install_core_guard(expected_uid=0)
    bootstrap = bootstrap_python_identity() if runtime_id is not None else None
    directory = directory.resolve(strict=True)
    source = directory / "page.xlsx"
    if source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= MAX_FILE:
        raise ValueError("Invalid input")
    unit = "llmocr-render-" + uuid4().hex
    print(json.dumps({"unit": unit}), flush=True)
    with pinned_runtime(runtime_id) as runtime_root, \
            tempfile.TemporaryDirectory(prefix="llmocr-render-") as temp:
        root = Path(temp)
        if runtime_id is not None:
            # Copy the invocation snapshot into this broker's private Linux
            # directory, then bind only that read-only file into the child.
            worker_source = Path(__file__)
            expected_worker = worker_identity(worker_source)
            shutil.copyfile(worker_source, root / "worker.py")
            (root / "worker.py").chmod(0o444)
            if worker_identity(root / "worker.py") != expected_worker:
                raise RuntimeError("Renderer worker changed before isolation")
            (root / "admission").mkdir(mode=0o755)
        shutil.copyfile(source, root / "page.xlsx")
        (root / "page.xlsx").chmod(0o444)
        (root / "output").mkdir(mode=0o777)
        (root / "output").chmod(0o777)
        # systemd starts the unit outside the broker's mount namespace. Join
        # this live broker's private mounts only; cgroup membership is retained.
        namespace = (["/usr/bin/nsenter", f"--mount=/proc/{os.getpid()}/ns/mnt", "--"]
                     if runtime_root is not None else [])
        command = [
            "/usr/bin/systemd-run",
            "--quiet",
            "--wait",
            "--pipe",
            "--collect",
            "--unit=" + unit,
            "--property=MemoryMax=1073741824",
            "--property=MemorySwapMax=0",
            "--property=TasksMax=64",
            "--property=CPUQuota=100%",
            "--property=RuntimeMaxSec=60",
            "--property=LimitFSIZE=67108864",
            "--property=LimitCORE=1",
            "--property=KillMode=control-group",
            "--property=TimeoutStopSec=2",
            *namespace,
            *sandbox_command(root, unit, format, font_source, runtime_root, runtime_id, dimensions),
        ]
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            last_heartbeat = time.monotonic()
            admission_deadline = last_heartbeat + 5
            admitted = runtime_id is None
            try:
                while process.poll() is None:
                    ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                    if ready:
                        if not os.read(sys.stdin.fileno(), 4096):
                            raise RuntimeError("Renderer owner disconnected")
                        last_heartbeat = time.monotonic()
                    if time.monotonic() - last_heartbeat > 5:
                        raise RuntimeError("Renderer owner lease expired")
                    if not admitted:
                        if time.monotonic() >= admission_deadline:
                            raise RuntimeError("Renderer unit admission timed out")
                        controls = read_unit_controls(unit)
                        if controls is not None:
                            publish_unit_admission(root, controls)
                            admitted = True
                if process.returncode:
                    log.seek(0)
                    raise RuntimeError(log.read(4096).decode(errors="replace"))
                if bootstrap is not None and bootstrap_python_identity() != bootstrap:
                    raise RuntimeError("Bootstrap Python changed during rendering")
                for name, limit in (
                    ("reference.zip" if format == "reference" else "page." + format, MAX_FILE),
                    ("renderer-report.json", 1024 * 1024),
                ):
                    path = root / "output" / name
                    if (
                        path.is_symlink()
                        or not path.is_file()
                        or not 0 < path.stat().st_size <= limit
                    ):
                        raise RuntimeError("Invalid renderer output")
                    if name == "renderer-report.json":
                        report = json.loads(path.read_text(encoding="utf-8"))
                        report["brokerCoreDumpGuard"] = broker_guard
                        if bootstrap is not None:
                            validate_bootstrap_python(bootstrap)
                            report["bootstrapPythonIdentity"] = bootstrap
                        (directory / name).write_text(json.dumps(report), encoding="utf-8")
                    else:
                        shutil.copyfile(path, directory / name)
            finally:
                try:
                    subprocess.run(
                        ["/usr/bin/systemctl", "stop", unit],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                    )
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)


def inspect_environment(runtime_id: str) -> dict[str, Any]:
    """No-document identity inspection; no Office, PDF or OCR invocation."""
    guard = install_core_guard(expected_uid=0)
    worker = worker_identity(Path(__file__))
    bootstrap = bootstrap_python_identity()
    with pinned_runtime(runtime_id):
        fonts = font_directory_identity(Path("/mnt/c/Windows/Fonts"))
        verify_font_directory(Path("/mnt/c/Windows/Fonts"), fonts)
        if bootstrap_python_identity() != bootstrap or worker_identity(Path(__file__)) != worker:
            raise RuntimeError("Environment changed during inspection")
    return {
        "policy": "wsl-current-environment-identity-v1",
        "runtimeImage": {"policy": RUNTIME_POLICY, "sha256": runtime_id,
                         "mountsReadOnly": True, "filesystem": "squashfs",
                         "kernel": os.uname().release},
        "fontDirectoryIdentity": fonts, "bootstrapPythonIdentity": bootstrap,
        "workerIdentity": worker, "brokerCoreDumpGuard": guard,
    }


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--inspect-environment":
        print(json.dumps(inspect_environment(sys.argv[2])))
    elif len(sys.argv) == 6:
        if len(sys.argv[5]) > 4096:
            raise SystemExit("Dimension request limit exceeded")
        requested = dimension_request(json.loads(sys.argv[5]), sys.argv[2], sys.argv[4])
        if sys.argv[1] == "--inner":
            inner(sys.argv[2], sys.argv[3], sys.argv[4], requested)
        else:
            broker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], requested)
    elif len(sys.argv) == 5 and sys.argv[1] == "--inner":
        inner(sys.argv[2], sys.argv[3], sys.argv[4])
    elif len(sys.argv) == 5:
        broker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4])
    elif len(sys.argv) == 4 and sys.argv[1] == "--inner":
        inner(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4:
        broker(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 3:
        broker(Path(sys.argv[1]), sys.argv[2])
    elif len(sys.argv) == 2:
        broker(Path(sys.argv[1]))
    else:
        raise SystemExit("Expected a renderer directory")
