import hashlib
import json
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import renderer_wsl, renderer_wsl_worker
from tests.bootstrap_python_fixture import bootstrap_python_evidence
from tests.core_guard_fixture import core_guard_evidence
from tests.wsl_execution_fixture import wsl_execution_evidence

RUNTIME = "a" * 64
FONT_CHANGES = {
    "fonts_policy": ("policy", "old"),
    "fonts_sha": ("sha256", "bad"),
    "fonts_sha_type": ("sha256", 0),
    "fonts_count_zero": ("fileCount", 0),
    "fonts_count_high": ("fileCount", 4097),
    "fonts_count_bool": ("fileCount", True),
    "fonts_bytes_negative": ("totalBytes", -1),
    "fonts_bytes_high": ("totalBytes", 1024 * 1024 * 1024 + 1),
    "fonts_bytes_bool": ("totalBytes", False),
}


def test_runtime_image_is_rehashed_through_the_held_descriptor(tmp_path):
    path = tmp_path / "image"
    path.write_bytes(b"one")
    identity = hashlib.sha256(b"one").hexdigest()
    with path.open("rb") as stream:
        renderer_wsl_worker.verify_runtime_descriptor(stream.fileno(), identity)
        renderer_wsl_worker.verify_runtime_descriptor(stream.fileno(), identity)
        timestamps = path.stat()
        path.write_bytes(b"two")
        os.utime(path, ns=(timestamps.st_atime_ns, timestamps.st_mtime_ns))
        with pytest.raises(RuntimeError, match="identity changed"):
            renderer_wsl_worker.verify_runtime_descriptor(stream.fileno(), identity)
@pytest.mark.parametrize("identity", [
    "", "../image", "A" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n",
])
def test_runtime_selector_rejects_paths_and_noncanonical_identifiers(identity):
    with pytest.raises(ValueError, match="runtime identity"):
        renderer_wsl.render_wsl_pdf(b"fixture", runtime_id=identity)
    with pytest.raises(ValueError, match="runtime identity"), \
            renderer_wsl_worker.pinned_runtime(identity):
        pytest.fail("Invalid identity entered the runtime mount")


def test_unselected_runtime_keeps_existing_execution_without_mounting():
    with renderer_wsl_worker.pinned_runtime(None) as root:
        assert root is None


def test_frozen_command_replaces_all_runtime_roots_but_not_windows_fonts(tmp_path):
    root = tmp_path / "image"
    command = renderer_wsl_worker.sandbox_command(
        tmp_path, "fixture", "reference", "windows", root, RUNTIME,
    )
    bindings = {command[i + 2]: command[i + 1] for i, value in enumerate(command)
                if value == "--ro-bind"}
    for destination in renderer_wsl_worker.RUNTIME_PATHS:
        assert bindings[destination] == str(root / destination.lstrip("/"))
    assert bindings["/reference-fonts"] == "/mnt/c/Windows/Fonts"
    assert command[-4:] == ["--inner", "reference", "windows", RUNTIME]
    with pytest.raises(ValueError, match="provided together"):
        renderer_wsl_worker.sandbox_command(tmp_path, "fixture", runtime_id=RUNTIME)


@pytest.mark.parametrize("change", [
    None, "missing", "sha256", "policy", "filesystem", "mountsReadOnly",
    "fonts_missing", *FONT_CHANGES,
])
def test_bridge_requires_selected_readonly_runtime_identity(tmp_path, monkeypatch, change):
    workbook, pdf = b"fixture XLSX", b"%PDF-fixture"
    runtime = {"policy": "wsl-squashfs-runtime-v1", "sha256": RUNTIME,
               "filesystem": "squashfs", "mountsReadOnly": True}
    font_change = change is not None and change.startswith("fonts_")
    if change not in {None, "missing"} and not font_change:
        runtime[change] = "changed"
    report = {
        **wsl_execution_evidence(),
        "status": "PASS", "format": "pdf", "fontSource": "windows",
        "policy": "wsl-bwrap-cgroup-v1", "fontRequestPolicy": "wsl-fontconfig-face-v2",
       "sourceSha256": hashlib.sha256(workbook).hexdigest(),
        "coreDumpGuard": core_guard_evidence(),
        "brokerCoreDumpGuard": core_guard_evidence(),
        "bootstrapPythonIdentity": bootstrap_python_evidence(),
        "pdfSha256": hashlib.sha256(pdf).hexdigest(),
        "workerIdentity": {"policy": "wsl-worker-source-v1", "sha256": hashlib.sha256(
            Path(renderer_wsl_worker.__file__).read_bytes()).hexdigest()},
        "fontDirectoryIdentity": {"policy": "wsl-font-directory-v1", "fileCount": 1,
                                  "totalBytes": 7, "sha256": "b" * 64},
    }
    if change != "missing":
        report["runtimeImage"] = runtime
    if change == "fonts_missing":
        report.pop("fontDirectoryIdentity")
    elif change in FONT_CHANGES:
        field, value = FONT_CHANGES[change]
        report["fontDirectoryIdentity"][field] = value
    (tmp_path / "page.pdf").write_bytes(pdf)
    (tmp_path / "renderer-report.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(renderer_wsl, "render_directory", lambda: nullcontext(tmp_path))
    commands = []

    def start(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdin=None, poll=lambda: 0, wait=lambda **kwargs: 0)

    monkeypatch.setattr(renderer_wsl.subprocess, "Popen", start)
    if change is None:
        assert renderer_wsl.render_wsl_pdf(
            workbook, font_source="windows", runtime_id=RUNTIME,
        ) == (pdf, report)
    else:
        message = "font directory identity mismatch" if font_change else "runtime identity mismatch"
        with pytest.raises(RuntimeError, match=message):
            renderer_wsl.render_wsl_pdf(workbook, font_source="windows", runtime_id=RUNTIME)
    command = commands[0]
    start = command.index("--exec") + 1
    assert command[start:start + 4] == ["/usr/bin/unshare", "--mount", "--propagation", "private"]
    assert command[-1] == RUNTIME
