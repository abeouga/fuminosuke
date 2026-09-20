"""Zipapp entrypoint for a fixed, leased and network-isolated glyph helper.

The root broker imports only standard-library code. PDF/font libraries are
imported by the nonprivileged inner process after namespace/resource checks.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
import re
import select
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4
from zipfile import ZIP_STORED, ZipFile

from app.services.renderer_wsl_worker import (
    RUNTIME_PATHS,
    RUNTIME_POLICY,
    await_unit_admission,
    bootstrap_python_identity,
    font_directory_identity,
    install_core_guard,
    pinned_runtime,
    publish_unit_admission,
    read_unit_controls,
    sandbox_command,
    validate_bootstrap_python,
    verify_font_directory,
)

POLICY = "wsl-glyph-helper-v1"
MAX_INPUT = 64 * 1024 * 1024
MAX_REPORT = 1024 * 1024
MAX_CODE = 8 * 1024 * 1024
PYTHON = "/usr/local/lib/llmocr-glyph-v1/bin/python"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _read(path: Path, limit: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise RuntimeError("Invalid glyph helper file")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if not 0 < len(content) <= limit:
        raise RuntimeError("Glyph helper file size changed")
    return content


def glyph_sandbox_command(root: Path, unit: str, runtime_root: Path, runtime_id: str) -> list[str]:
    command = sandbox_command(root, unit, "glyph", "windows", runtime_root, runtime_id)
    if command[-7:] != [
        "/usr/bin/python3",
        "-I",
        "/worker.py",
        "--inner",
        "glyph",
        "windows",
        runtime_id,
    ]:
        raise RuntimeError("Unexpected renderer sandbox protocol")
    # Reuse the isolation bindings, but the payload and interpreter are a
    # different fixed protocol. This does not run Office or accept commands.
    command = command[:-7]
    command[command.index("/page.xlsx")] = "/glyph-input.zip"
    command[command.index("/worker.py")] = "/glyph-code.pyz"
    return [*command, PYTHON, "-I", "/glyph-code.pyz", "--inner", runtime_id]


def _environment(runtime_id: str) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", runtime_id) is None:
        raise ValueError("Pinned glyph runtime is required")
    mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
    for path in (*RUNTIME_PATHS, "/reference-fonts", "/glyph-code.pyz", "/glyph-input.zip"):
        matches = [entry for entry in mounts if entry[4] == path]
        if len(matches) != 1 or "ro" not in matches[0][5].split(","):
            raise RuntimeError("Glyph input/code/runtime mounts are not read-only")
        if path in RUNTIME_PATHS and matches[0][matches[0].index("-") + 1] != "squashfs":
            raise RuntimeError("Glyph runtime did not use the pinned image")
    interfaces = sorted(name for _, name in socket.if_nameindex())
    if (
        interfaces != ["lo"]
        or Path("/mnt/c").exists()
        or Path("/root").exists()
        or os.getuid() != 65534
        or os.getgid() != 65534
    ):
        raise RuntimeError("Glyph isolation boundary was not established")
    status = Path("/proc/self/status").read_text()
    if "NoNewPrivs:\t1" not in status or "CapEff:\t0000000000000000" not in status:
        raise RuntimeError("Unexpected glyph helper privileges")
    limits = {
        key: Path("/limits", key).read_text().strip()
        for key in ("memory.max", "memory.swap.max", "pids.max", "cpu.max")
    }
    if limits != {
        "memory.max": "1073741824",
        "memory.swap.max": "0",
        "pids.max": "64",
        "cpu.max": "100000 100000",
    }:
        raise RuntimeError("Glyph resource limits were not enforced")
    denied = False
    with socket.socket() as probe:
        probe.settimeout(0.2)
        try:
            probe.connect(("192.0.2.1", 9))
        except OSError:
            denied = True
    if not denied:
        raise RuntimeError("Glyph helper network was not denied")
    return {
        "uid": os.getuid(),
        "networkInterfaces": interfaces,
        "networkDenied": denied,
        "hostFilesystemHidden": True,
        "noNewPrivileges": True,
        "resourceLimits": limits,
        "runtimeImage": {
            "policy": RUNTIME_POLICY,
            "sha256": runtime_id,
            "filesystem": "squashfs",
            "mountsReadOnly": True,
            "kernel": os.uname().release,
        },
    }


def _input(payload: bytes) -> tuple[bytes, dict[str, Any]]:
    with ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if (
            [e.filename for e in entries] != ["reference.pdf", "manifest.json"]
            or any(e.compress_type != ZIP_STORED or e.flag_bits & 1 for e in entries)
            or not 0 < entries[0].file_size <= MAX_INPUT - MAX_REPORT
            or not 0 < entries[1].file_size <= MAX_REPORT
        ):
            raise RuntimeError("Invalid glyph input archive")
        pdf, raw_manifest = archive.read("reference.pdf"), archive.read("manifest.json")
    manifest = json.loads(raw_manifest)
    expected = {
        "policy",
        "pdfSha256",
        "pageCount",
        "runtimeImage",
        "fontDirectoryIdentity",
        "resolvedFonts",
        "referenceWorkerIdentity",
        "referenceReportSha256",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != expected
        or manifest["policy"] != POLICY
        or raw_manifest != _canonical(manifest)
        or manifest["pdfSha256"] != hashlib.sha256(pdf).hexdigest()
        or type(manifest["pageCount"]) is not int
        or not 1 <= manifest["pageCount"] <= 50
    ):
        raise RuntimeError("Invalid glyph input manifest")
    return pdf, manifest


def _source_faces(records: Any) -> list[Any]:
    from app.services.renderer_glyph_fonts import SourceFace

    if not isinstance(records, list) or not 1 <= len(records) <= 64:
        raise RuntimeError("Invalid source font record count")
    faces = []
    cache: dict[tuple[str, str], bytes] = {}
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("Invalid source font record")
        name, index, sha = record.get("file"), record.get("collectionIndex"), record.get("sha256")
        if (
            not isinstance(name, str)
            or PurePosixPath(name).parent != PurePosixPath("/reference-fonts")
            or PurePosixPath(name).name in {"", ".", ".."}
            or "\\" in name
            or "\x00" in name
            or not isinstance(index, str)
            or re.fullmatch(r"0|[1-9][0-9]{0,2}", index) is None
            or int(index) >= 256
            or not isinstance(sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha) is None
        ):
            raise RuntimeError("Invalid source font path, face or identity")
        key = (name, sha)
        if key not in cache:
            data = _read(Path(name), MAX_INPUT)
            if hashlib.sha256(data).hexdigest() != sha:
                raise RuntimeError("Source font bytes changed")
            cache[key] = data
            if sum(len(value) for value in cache.values()) > 256 * 1024 * 1024:
                raise RuntimeError("Source font input budget exceeded")
        faces.append(SourceFace(data=cache[key], sha256=sha, collection_index=int(index)))
    return faces


def inner(runtime_id: str) -> None:
    environment = _environment(runtime_id)
    core_guard = install_core_guard()
    environment["unitControls"] = await_unit_admission()
    code = _read(Path("/glyph-code.pyz"), MAX_CODE)
    payload = _read(Path("/glyph-input.zip"), MAX_INPUT)
    pdf, manifest = _input(payload)
    if manifest["runtimeImage"] != environment["runtimeImage"]:
        raise RuntimeError("Reference runtime changed before glyph validation")
    with ZipFile(io.BytesIO(code)) as archive:
        worker = archive.read("app/services/renderer_wsl_worker.py")
    if manifest["referenceWorkerIdentity"] != {
        "policy": "wsl-worker-source-v1",
        "sha256": hashlib.sha256(worker).hexdigest(),
    }:
        raise RuntimeError("Reference worker changed before glyph validation")
    fonts = font_directory_identity(Path("/reference-fonts"))
    if fonts != manifest["fontDirectoryIdentity"]:
        raise RuntimeError("Reference font directory changed before glyph validation")
    dependencies = {
        name: importlib.metadata.version(name)
        for name in ("fonttools", "pypdf", "typing_extensions")
    }
    if dependencies != {"fonttools": "4.65.0", "pypdf": "6.10.0", "typing_extensions": "4.15.0"}:
        raise RuntimeError("Pinned glyph dependencies do not match")
    from app.services.renderer_glyph_font_sources import find_source_faces
    from app.services.renderer_glyph_pdf import validate_pdf_glyphs

    declared_ids = {
        (face.sha256, face.collection_index) for face in _source_faces(manifest["resolvedFonts"])
    }
    source_records: list[dict[str, Any]] = []

    def resolve(names: set[str]) -> list[Any]:
        candidates = find_source_faces(Path("/reference-fonts"), names)
        source_records.extend(
            {
                "file": candidate.file,
                "sha256": candidate.source.sha256,
                "collectionIndex": str(candidate.source.collection_index),
                "sourcePostScriptName": candidate.postscript_name,
            }
            for candidate in candidates
        )
        return [candidate.source for candidate in candidates]

    glyphs = validate_pdf_glyphs(
        pdf,
        manifest["pdfSha256"],
        (),
        source_resolver=resolve,
    )
    if glyphs["pageCount"] != manifest["pageCount"]:
        raise RuntimeError("Reference page count changed")
    verify_font_directory(Path("/reference-fonts"), fonts)
    if (
        _read(Path("/glyph-code.pyz"), MAX_CODE) != code
        or _read(Path("/glyph-input.zip"), MAX_INPUT) != payload
    ):
        raise RuntimeError("Glyph code or input changed during validation")
    report = {
        "policy": POLICY,
        "status": "PASS",
        **environment,
        "coreDumpGuard": core_guard,
        "fontDirectoryIdentity": fonts,
        "codeBundleSha256": hashlib.sha256(code).hexdigest(),
        "inputSha256": hashlib.sha256(payload).hexdigest(),
        "manifestSha256": hashlib.sha256(_canonical(manifest)).hexdigest(),
        "dependencies": dependencies,
        "glyphs": glyphs,
        "sourceFaceBasis": "pinned-font-directory-and-embedded-glyph-match-v1",
        "sourceFaces": source_records,
        "allMatchedFacesDeclared": all(
            (record["sha256"], int(record["collectionIndex"])) in declared_ids
            for record in source_records
        ),
        "productionQualified": False,
    }
    output = _canonical(report)
    if len(output) > MAX_REPORT:
        raise RuntimeError("Glyph report is oversized")
    Path("/output/glyph-report.json").write_bytes(output)


def broker(directory: Path, runtime_id: str) -> None:
    broker_guard = install_core_guard(expected_uid=0)
    bootstrap = bootstrap_python_identity(Path(sys.argv[0]))
    directory = directory.resolve(strict=True)
    payload = _read(directory / "input.zip", MAX_INPUT)
    code = _read(Path(sys.argv[0]), MAX_CODE)
    unit = "llmocr-glyph-" + uuid4().hex
    print(json.dumps({"unit": unit}), flush=True)
    with (
        pinned_runtime(runtime_id) as runtime_root,
        tempfile.TemporaryDirectory(
            prefix="llmocr-glyph-",
        ) as owned,
    ):
        if runtime_root is None:
            raise RuntimeError("A pinned glyph runtime image is required")
        root = Path(owned)
        (root / "admission").mkdir(mode=0o755)
        (root / "page.xlsx").write_bytes(payload)
        (root / "worker.py").write_bytes(code)
        (root / "page.xlsx").chmod(0o444)
        (root / "worker.py").chmod(0o444)
        (root / "output").mkdir(mode=0o777)
        (root / "output").chmod(0o777)
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
            "/usr/bin/nsenter",
            f"--mount=/proc/{os.getpid()}/ns/mnt",
            "--",
            *glyph_sandbox_command(root, unit, runtime_root, runtime_id),
        ]
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            last_heartbeat = time.monotonic()
            admission_deadline = last_heartbeat + 5
            admitted = False
            try:
                while process.poll() is None:
                    ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                    if ready:
                        if not os.read(sys.stdin.fileno(), 4096):
                            raise RuntimeError("Glyph owner disconnected")
                        last_heartbeat = time.monotonic()
                    if time.monotonic() - last_heartbeat > 5:
                        raise RuntimeError("Glyph owner lease expired")
                    if not admitted:
                        if time.monotonic() >= admission_deadline:
                            raise RuntimeError("Glyph unit admission timed out")
                        controls = read_unit_controls(unit)
                        if controls is not None:
                            publish_unit_admission(root, controls)
                            admitted = True
                if process.returncode:
                    log.seek(0)
                    raise RuntimeError(log.read(4096).decode(errors="replace"))
                if bootstrap_python_identity(Path(sys.argv[0])) != bootstrap:
                    raise RuntimeError("Glyph bootstrap Python changed during validation")
                report = _read(root / "output/glyph-report.json", MAX_REPORT)
                parsed = json.loads(report)
                parsed["brokerCoreDumpGuard"] = broker_guard
                validate_bootstrap_python(bootstrap)
                parsed["bootstrapPythonIdentity"] = bootstrap
                report = _canonical(parsed)
                if len(report) > MAX_REPORT:
                    raise RuntimeError("Glyph broker report is oversized")
                (directory / "glyph-report.json").write_bytes(report)
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


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--inner":
        inner(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "--broker":
        broker(Path(sys.argv[2]), sys.argv[3])
    else:
        raise SystemExit("Expected fixed glyph broker or inner protocol")
