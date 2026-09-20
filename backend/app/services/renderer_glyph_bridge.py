"""Fixed Windows-to-WSL glyph-validation protocol; no PDF/font parsing here."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any, TypeGuard
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from app.services.renderer_temp import render_directory
from app.services.renderer_wsl import linux_path
from app.services.renderer_wsl_worker import (
    validate_bootstrap_python,
    validate_core_guard,
    validate_unit_controls,
)

MAX_INPUT = 64 * 1024 * 1024
MAX_REPORT = 1024 * 1024
POLICY = "wsl-glyph-helper-v1"
CODE_MODULES = (
    "renderer_wsl_worker",
    "renderer_glyph_fonts",
    "renderer_glyph_pdf",
    "renderer_glyph_worker",
    "renderer_glyph_font_sources",
)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def input_manifest(pdf: bytes, reference: dict[str, Any]) -> dict[str, Any]:
    """Copy the exact reference conditions into a bounded, immutable input."""
    runtime, fonts = reference.get("runtimeImage"), reference.get("fontDirectoryIdentity")
    worker = reference.get("workerIdentity")
    records, count = reference.get("resolvedFonts"), reference.get("pageCount")
    if (
        not isinstance(pdf, bytes)
        or not 0 < len(pdf) <= MAX_INPUT - MAX_REPORT
        or reference.get("policy") != "wsl-bwrap-cgroup-v1"
        or reference.get("status") != "PASS"
        or reference.get("format") != "reference"
        or reference.get("fontSource") != "windows"
        or reference.get("fontRequestPolicy") != "wsl-fontconfig-face-v2"
        or reference.get("pdfSha256") != hashlib.sha256(pdf).hexdigest()
        or not _sha(reference.get("sourceSha256"))
        or type(count) is not int
        or not 1 <= count <= 50
    ):
        raise ValueError("Invalid reference PDF identity or policy")
    if (
        not isinstance(runtime, dict)
        or not _sha(runtime.get("sha256"))
        or runtime.get("policy") != "wsl-squashfs-runtime-v1"
        or runtime.get("mountsReadOnly") is not True
        or runtime.get("filesystem") != "squashfs"
        or not isinstance(runtime.get("kernel"), str)
        or not runtime["kernel"]
    ):
        raise ValueError("Pinned reference runtime is required")
    if (
        not isinstance(fonts, dict)
        or not _sha(fonts.get("sha256"))
        or fonts.get("policy") != "wsl-font-directory-v1"
        or type(fonts.get("fileCount")) is not int
        or not 1 <= fonts["fileCount"] <= 4096
        or type(fonts.get("totalBytes")) is not int
        or not 0 < fonts["totalBytes"] <= 1024**3
    ):
        raise ValueError("Pinned reference font directory is required")
    if (
        not isinstance(worker, dict)
        or worker.get("policy") != "wsl-worker-source-v1"
        or not _sha(worker.get("sha256"))
    ):
        raise ValueError("Pinned reference worker is required")
    if not isinstance(records, list) or not 1 <= len(records) <= 64:
        raise ValueError("Invalid resolved source font records")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Invalid source face record")
        path, index = record.get("file"), record.get("collectionIndex")
        if (
            not isinstance(path, str)
            or PurePosixPath(path).parent != PurePosixPath("/reference-fonts")
            or PurePosixPath(path).name in {"", ".", ".."}
            or "\\" in path
            or "\x00" in path
            or not isinstance(index, str)
            or re.fullmatch(r"0|[1-9][0-9]{0,2}", index) is None
            or int(index) >= 256
            or not _sha(record.get("sha256"))
        ):
            raise ValueError("Invalid source font path, face or identity")
    manifest = {
        "policy": POLICY,
        "pdfSha256": reference["pdfSha256"],
        "pageCount": count,
        "runtimeImage": runtime,
        "fontDirectoryIdentity": fonts,
        "resolvedFonts": records,
        "referenceWorkerIdentity": worker,
        "referenceReportSha256": hashlib.sha256(canonical_bytes(reference)).hexdigest(),
    }
    encoded = canonical_bytes(manifest)
    if len(encoded) > MAX_REPORT:
        raise ValueError("Reference manifest is oversized")
    # No mutable caller-owned nested dictionaries escape this boundary.
    return dict(json.loads(encoded))


def _bundle(entries: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name, data in entries:
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.external_attr = 0o100444 << 16
            archive.writestr(info, data)
    return output.getvalue()


def code_bundle() -> bytes:
    entries = [
        ("__main__.py", b"from app.services.renderer_glyph_worker import main\nmain()\n"),
        ("app/__init__.py", b""),
        ("app/services/__init__.py", b""),
    ]
    for module in CODE_MODULES:
        path = Path(__file__).with_name(module + ".py")
        with path.open("rb") as stream:
            source = stream.read(MAX_REPORT + 1)
        if not 0 < len(source) <= MAX_REPORT:
            raise ValueError("Invalid glyph helper code size")
        entries.append(("app/services/" + module + ".py", source))
    return _bundle(entries)


def validate_result(
    report: dict[str, Any],
    manifest: dict[str, Any],
    code_sha: str,
    payload_sha: str,
) -> None:
    if (
        report.get("policy") != POLICY
        or report.get("status") != "PASS"
        or report.get("codeBundleSha256") != code_sha
        or report.get("inputSha256") != payload_sha
        or report.get("manifestSha256") != hashlib.sha256(canonical_bytes(manifest)).hexdigest()
        or report.get("runtimeImage") != manifest["runtimeImage"]
        or report.get("fontDirectoryIdentity") != manifest["fontDirectoryIdentity"]
        or report.get("dependencies")
        != {
            "fonttools": "4.65.0",
            "pypdf": "6.10.0",
            "typing_extensions": "4.15.0",
        }
        or report.get("productionQualified") is not False
    ):
        raise RuntimeError("Glyph helper identity or dependency mismatch")
    if (
        type(report.get("uid")) is not int
        or report["uid"] != 65534
        or report.get("networkInterfaces") != ["lo"]
        or report.get("networkDenied") is not True
        or report.get("noNewPrivileges") is not True
        or report.get("hostFilesystemHidden") is not True
        or report.get("resourceLimits")
        != {
            "memory.max": "1073741824",
            "memory.swap.max": "0",
            "pids.max": "64",
            "cpu.max": "100000 100000",
        }
    ):
        raise RuntimeError("Glyph helper isolation evidence mismatch")
    glyphs = report.get("glyphs")
    if (
        not isinstance(glyphs, dict)
        or glyphs.get("policy") != "wsl-pdf-used-glyphs-v1"
        or glyphs.get("pdfSha256") != manifest["pdfSha256"]
        or type(glyphs.get("pageCount")) is not int
        or glyphs["pageCount"] != manifest["pageCount"]
        or glyphs.get("productionQualified") is not False
        or glyphs.get("visibilityAndLayoutVerified") is not False
        or not isinstance(glyphs.get("pages"), list)
        or [p.get("renderedPage") if isinstance(p, dict) else None for p in glyphs["pages"]]
        != list(range(1, manifest["pageCount"] + 1))
    ):
        raise RuntimeError("Glyph helper page evidence mismatch")
    _validate_glyph_details(glyphs, _validate_source_records(report, manifest))
    validate_core_guard(report.get("coreDumpGuard"))
    validate_core_guard(report.get("brokerCoreDumpGuard"))
    validate_bootstrap_python(report.get("bootstrapPythonIdentity"))
    validate_unit_controls(report.get("unitControls"))


def _integer(value: Any, lower: int, upper: int) -> TypeGuard[int]:
    return type(value) is int and lower <= value <= upper


def _validate_source_records(
    report: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[tuple[str, int], set[str]]:
    records = report.get("sourceFaces")
    if (
        report.get("sourceFaceBasis") != "pinned-font-directory-and-embedded-glyph-match-v1"
        or not isinstance(records, list)
        or not 1 <= len(records) <= 64
    ):
        raise RuntimeError("Invalid source face discovery evidence")
    result: dict[tuple[str, int], set[str]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("Invalid matched source face")
        path, index = record.get("file"), record.get("collectionIndex")
        name, sha = record.get("sourcePostScriptName"), record.get("sha256")
        if (
            not isinstance(path, str)
            or PurePosixPath(path).parent != PurePosixPath("/reference-fonts")
            or PurePosixPath(path).name in {"", ".", ".."}
            or "\\" in path
            or "\x00" in path
            or not isinstance(index, str)
            or re.fullmatch(r"0|[1-9][0-9]{0,2}", index) is None
            or int(index) >= 256
            or not _sha(sha)
            or not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,127}", name) is None
        ):
            raise RuntimeError("Invalid matched source face path or identity")
        identity = (sha, int(index))
        if identity in result and name in result[identity]:
            raise RuntimeError("Duplicate matched source face identity")
        result.setdefault(identity, set()).add(name)
    declared = {
        (item["sha256"], int(item["collectionIndex"])) for item in manifest["resolvedFonts"]
    }
    if report.get("allMatchedFacesDeclared") is not (set(result) <= declared):
        raise RuntimeError("Source face declaration coverage changed")
    return result


def _validate_glyph_details(
    glyphs: dict[str, Any],
    source_names: dict[tuple[str, int], set[str]],
) -> None:
    total, distinct = glyphs.get("textInstructionGlyphs"), glyphs.get("distinctFontCodes")
    if not _integer(total, 1, 2_000_000) or not _integer(distinct, 1, min(32768, total)):
        raise RuntimeError("Invalid glyph helper document counts")
    for ordinal, page in enumerate(glyphs["pages"], 1):
        count, unique = page.get("textInstructionGlyphs"), page.get("distinctFontCodes")
        if (
            type(page.get("renderedPage")) is not int
            or page["renderedPage"] != ordinal
            or not _integer(count, 0, total)
            or not _integer(unique, 0, min(count, distinct))
            or not _integer(page.get("contentOperations"), 1, 500_000)
            or not _sha(page.get("instructionUnicodeSha256"))
            or not _sha(page.get("contentSha256"))
        ):
            raise RuntimeError("Invalid glyph helper page counts or identity")
    if (
        sum(page["textInstructionGlyphs"] for page in glyphs["pages"]) != total
        or sum(page["contentOperations"] for page in glyphs["pages"]) > 500_000
    ):
        raise RuntimeError("Glyph helper document totals do not match pages")
    fonts = glyphs.get("fonts")
    if not isinstance(fonts, list) or not 1 <= len(fonts) <= 128:
        raise RuntimeError("Invalid glyph helper font evidence")
    source_ids = set(source_names)
    used_sources = set()
    objects: set[tuple[int, int]] = set()
    for font in fonts:
        if not isinstance(font, dict):
            raise RuntimeError("Invalid glyph helper font record")
        identity = font.get("fontObject")
        if (
            not isinstance(identity, list)
            or len(identity) != 2
            or not _integer(identity[0], 1, 2**31 - 1)
            or not _integer(identity[1], 0, 65535)
            or tuple(identity) in objects
            or font.get("policy") != "wsl-truetype-glyph-codes-v1"
            or font.get("productionQualified") is not False
            or not _integer(font.get("sourceCollectionIndex"), 0, 255)
            or (font.get("sourceSha256"), font["sourceCollectionIndex"]) not in source_ids
            or not isinstance(font.get("sourcePostScriptName"), str)
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,127}", font["sourcePostScriptName"]) is None
            or not _integer(font.get("usedFontCodes"), 1, 256)
            or any(
                not _sha(font.get(key))
                for key in (
                    "embeddedSha256",
                    "toUnicodeSha256",
                    "verifiedCodesSha256",
                )
            )
        ):
            raise RuntimeError("Invalid glyph helper font identity or counts")
        source_identity = (font["sourceSha256"], font["sourceCollectionIndex"])
        if font["sourcePostScriptName"] not in source_names[source_identity]:
            raise RuntimeError("Matched source name differs from verified font")
        used_sources.add((*source_identity, font["sourcePostScriptName"]))
        objects.add((identity[0], identity[1]))
    expected_sources = {
        (*identity, name) for identity, names in source_names.items() for name in names
    }
    if sum(font["usedFontCodes"] for font in fonts) != distinct or used_sources != expected_sources:
        raise RuntimeError("Glyph helper font totals do not match document")


def validate_saved_result(
    pdf: bytes, reference: dict[str, Any], report: dict[str, Any],
) -> None:
    """Recheck persisted evidence against current owned inputs/code, without parsing PDF."""
    manifest = input_manifest(pdf, reference)
    payload = _bundle([("reference.pdf", pdf), ("manifest.json", canonical_bytes(manifest))])
    if len(payload) > MAX_INPUT:
        raise ValueError("Glyph input bundle is oversized")
    validate_result(report, manifest, hashlib.sha256(code_bundle()).hexdigest(),
                    hashlib.sha256(payload).hexdigest())


def validate_wsl_pdf_glyphs(
    pdf: bytes,
    reference: dict[str, Any],
    *,
    distribution: str = "Ubuntu-22.04",
) -> dict[str, Any]:
    manifest = input_manifest(pdf, reference)
    payload = _bundle([("reference.pdf", pdf), ("manifest.json", canonical_bytes(manifest))])
    if len(payload) > MAX_INPUT:
        raise ValueError("Glyph input bundle is oversized")
    code = code_bundle()
    with render_directory() as directory, tempfile.TemporaryFile() as log:
        (directory / "input.zip").write_bytes(payload)
        code_path = directory / "glyph.pyz"
        code_path.write_bytes(code)
        command = [
            "wsl.exe",
            "--distribution",
            distribution,
            "--user",
            "root",
            "--exec",
            "/usr/bin/unshare",
            "--mount",
            "--propagation",
            "private",
            "/usr/bin/python3",
            "-I",
            "-S",
            "-B",
            linux_path(code_path),
            "--broker",
            linux_path(directory),
            manifest["runtimeImage"]["sha256"],
        ]
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
                    raise RuntimeError("Glyph helper timed out")
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
                raise RuntimeError(log.read(4096).decode("utf-8", errors="replace"))
            path = directory / "glyph-report.json"
            if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_REPORT:
                raise RuntimeError("Invalid glyph helper output file")
            report = json.loads(path.read_bytes())
            if not isinstance(report, dict):
                raise RuntimeError("Invalid glyph helper output")
            validate_result(
                report,
                manifest,
                hashlib.sha256(code).hexdigest(),
                hashlib.sha256(payload).hexdigest(),
            )
            return report
        finally:
            if process.stdin:
                with suppress(OSError):
                    process.stdin.close()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
