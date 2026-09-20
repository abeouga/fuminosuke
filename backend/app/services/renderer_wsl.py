"""Windows bridge to the fixed-protocol, network-isolated WSL print renderer.

Rendering success is not an OCR reference qualification certificate.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal
from zipfile import ZipFile

from app.domain.print_layout import print_error
from app.services.renderer_temp import render_directory
from app.services.renderer_wsl_worker import validate_bootstrap_python, validate_core_guard
from app.services.wsl_execution_evidence import validate_execution


def linux_path(path: Path) -> str:
    resolved = path.resolve(strict=True)
    if not resolved.drive or len(resolved.drive) != 2 or resolved.drive[1] != ":":
        raise ValueError("Only local Windows drive paths are supported")
    return "/mnt/" + resolved.drive[0].lower() + "/" + "/".join(resolved.parts[1:])


def render_wsl_pdf(
    workbook: bytes,
    *,
    distribution: str = "Ubuntu-22.04",
    font_source: Literal["linux", "windows"] = "linux",
    runtime_id: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    return _render(workbook, distribution=distribution, format="pdf", font_source=font_source,
                   runtime_id=runtime_id)


def import_wsl_geometry(
    workbook: bytes,
    *,
    distribution: str = "Ubuntu-22.04",
    font_source: Literal["linux", "windows"] = "linux",
    runtime_id: str | None = None,
    dimension_request: dict[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Import into Office's FODS geometry inside the same isolated worker."""
    return _render(workbook, distribution=distribution, format="fods", font_source=font_source,
                   runtime_id=runtime_id, dimension_request=dimension_request)


def render_wsl_reference(
    workbook: bytes,
    *,
    distribution: str = "Ubuntu-22.04",
    runtime_id: str | None = None,
) -> tuple[bytes, list[bytes], dict[str, Any]]:
    """One Office PDF, with page images rasterized from that exact PDF in isolation."""
    payload, report = _render(
        workbook, distribution=distribution, format="reference", font_source="windows",
        runtime_id=runtime_id,
    )
    count = report.get("pageCount")
    if not isinstance(count, int) or not 1 <= count <= 50:
        raise RuntimeError("Invalid reference page count")
    with ZipFile(io.BytesIO(payload)) as archive:
        expected = ["page.pdf", *[f"page-{number}.png" for number in range(1, count + 1)]]
        if (
            archive.namelist() != expected
            or sum(e.file_size for e in archive.infolist()) > 64 * 1024 * 1024
        ):
            raise RuntimeError("Invalid reference bundle")
        pdf = archive.read("page.pdf")
        images = [archive.read(name) for name in expected[1:]]
    if (
        hashlib.sha256(pdf).hexdigest() != report["pdfSha256"]
        or [hashlib.sha256(data).hexdigest() for data in images] != report["imageSha256"]
    ):
        raise RuntimeError("Reference page identity mismatch")
    return pdf, images, report


def _render(
    workbook: bytes,
    *,
    distribution: str,
    format: Literal["pdf", "fods", "reference"],
    font_source: Literal["linux", "windows"],
    runtime_id: str | None = None,
    dimension_request: dict[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    if dimension_request is not None:
        from app.services.renderer_wsl_worker import dimension_request as validate_request

        dimension_request = validate_request(dimension_request, format, runtime_id)
    if runtime_id is not None and re.fullmatch(r"[0-9a-f]{64}", runtime_id) is None:
        raise ValueError("Invalid runtime identity")
    if not 0 < len(workbook) <= 64 * 1024 * 1024:
        raise print_error("印刷用XLSXのサイズが上限を超えています。", "RENDER_FAILED")
    with render_directory() as directory, tempfile.TemporaryFile() as log:
        (directory / "page.xlsx").write_bytes(workbook)
        worker_path = Path(__file__).with_name("renderer_wsl_worker.py")
        worker_sha = None
        if runtime_id is not None:
            with worker_path.open("rb") as worker_stream:
                worker_source = worker_stream.read(1024 * 1024 + 1)
            if not 0 < len(worker_source) <= 1024 * 1024:
                raise RuntimeError("Invalid renderer worker size")
            worker_sha = hashlib.sha256(worker_source).hexdigest()
            worker_path = directory / "worker.py"
            worker_path.write_bytes(worker_source)
        command = [
            "wsl.exe",
            "--distribution",
            distribution,
            "--user",
            "root",
            "--exec",
            "/usr/bin/python3",
            "-I",
            # The pinned root bootstrap needs only stdlib, not host site hooks.
            *(["-S", "-B"] if runtime_id is not None else []),
            linux_path(worker_path),
            linux_path(directory),
            format,
            font_source,
        ]
        if runtime_id is not None:
            executable = command.index("--exec") + 1
            command[executable:executable] = [
                "/usr/bin/unshare", "--mount", "--propagation", "private",
            ]
            command.append(runtime_id)
        if dimension_request is not None:
            command.append(json.dumps(dimension_request, ensure_ascii=True, separators=(",", ":")))
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        started = time.monotonic()
        try:
            while process.poll() is None:
                if time.monotonic() - started > 80:
                    raise print_error("隔離PDF変換がタイムアウトしました。", "RENDER_TIMEOUT", 409)
                try:
                    assert process.stdin is not None
                    process.stdin.write(b".\n")
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    break
                time.sleep(0.2)
            process.wait(timeout=10)
            if process.returncode:
                log.seek(0)
                # Fixed worker diagnostics, never the workbook's data, are available to callers.
                raise RuntimeError(log.read(4096).decode("utf-8", errors="replace"))
            report_path = directory / "renderer-report.json"
            pdf_path = directory / ("reference.zip" if format == "reference" else "page." + format)
            if (
                report_path.is_symlink()
                or pdf_path.is_symlink()
                or report_path.stat().st_size > 1024 * 1024
                or not 0 < pdf_path.stat().st_size <= 64 * 1024 * 1024
            ):
                raise RuntimeError("Invalid renderer outputs")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            pdf = pdf_path.read_bytes()
            if (
                report.get("status") != "PASS"
                or report.get("format") != format
                or report.get("fontSource") != font_source
                or report.get("policy") != "wsl-bwrap-cgroup-v1"
                or report.get("fontRequestPolicy") != "wsl-fontconfig-face-v2"
                or report.get("sourceSha256") != hashlib.sha256(workbook).hexdigest()
                or report.get(format + "Sha256") != hashlib.sha256(pdf).hexdigest()
            ):
                raise RuntimeError("Renderer output identity mismatch")
            if runtime_id is not None and (
                not isinstance(report.get("runtimeImage"), dict)
                or report["runtimeImage"].get("sha256") != runtime_id
                or report["runtimeImage"].get("policy") != "wsl-squashfs-runtime-v1"
                or report["runtimeImage"].get("filesystem") != "squashfs"
                or report["runtimeImage"].get("mountsReadOnly") is not True
            ):
                raise RuntimeError("Renderer runtime identity mismatch")
            if runtime_id is not None and font_source == "windows":
                fonts = report.get("fontDirectoryIdentity")
                if (not isinstance(fonts, dict)
                        or fonts.get("policy") != "wsl-font-directory-v1"
                        or not isinstance(fonts.get("sha256"), str)
                        or re.fullmatch(r"[0-9a-f]{64}", fonts["sha256"]) is None
                        or type(fonts.get("fileCount")) is not int
                        or not 1 <= fonts["fileCount"] <= 4096
                        or type(fonts.get("totalBytes")) is not int
                        or not 0 <= fonts["totalBytes"] <= 1024 * 1024 * 1024):
                    raise RuntimeError("Renderer font directory identity mismatch")
            if runtime_id is not None and (
                not isinstance(report.get("workerIdentity"), dict)
                or report["workerIdentity"].get("policy") != "wsl-worker-source-v1"
                or report["workerIdentity"].get("sha256") != worker_sha
            ):
                raise RuntimeError("Renderer worker identity mismatch")
            if dimension_request is not None:
                observation = report.get("dimensionObservation")
                if (not isinstance(observation, dict)
                        or observation.get("policy") != "wsl-uno-dimension-v1"
                        or observation.get("request") != dimension_request
                        or observation.get("sourceSha256") != report["sourceSha256"]
                        or observation.get("fodsSha256") != report["fodsSha256"]):
                    raise RuntimeError("Renderer dimension observation identity mismatch")
                if (validate_request(observation["request"], format, runtime_id)
                        != dimension_request):
                    raise RuntimeError("Renderer dimension request mismatch")
                for axis in ("columns", "rows"):
                    values = observation.get(axis)
                    if (not isinstance(values, list) or len(values) != dimension_request[axis]
                            or any(not isinstance(v, list) or len(v) != 3
                                   or type(v[0]) is not int or not 0 <= v[0] <= 2**31 - 1
                                   or type(v[1]) is not int or not 0 <= v[1] <= 2**31 - 1
                                   or type(v[2]) is not bool for v in values)):
                        raise RuntimeError("Invalid renderer dimension observations")
            validate_core_guard(report.get("coreDumpGuard"))
            validate_core_guard(report.get("brokerCoreDumpGuard"))
            if runtime_id is not None:
                validate_bootstrap_python(report.get("bootstrapPythonIdentity"))
                try:
                    validate_execution(report)
                except ValueError as exc:
                    raise RuntimeError("Renderer execution isolation evidence mismatch") from exc
            return pdf, report
        except subprocess.TimeoutExpired as exc:
            raise print_error(
                "隔離PDF変換の終了を確認できませんでした。", "RENDER_TIMEOUT", 409
            ) from exc
        finally:
            if process.stdin:
                with suppress(OSError):
                    process.stdin.close()  # EOF revokes the lease and stops only this unit.
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
