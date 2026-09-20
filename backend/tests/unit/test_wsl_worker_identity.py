import hashlib
import json
import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.services import renderer_wsl as bridge
from app.services import renderer_wsl_worker as worker
from tests.bootstrap_python_fixture import bootstrap_python_evidence
from tests.core_guard_fixture import core_guard_evidence
from tests.wsl_execution_fixture import wsl_execution_evidence


def test_worker_identity_reads_contents_even_when_metadata_is_unchanged(tmp_path):
    source = tmp_path / "worker.py"
    source.write_bytes(b"# one\n")
    stamp = source.stat()
    before = worker.worker_identity(source)
    assert before == {"policy": "wsl-worker-source-v1",
                      "sha256": hashlib.sha256(b"# one\n").hexdigest()}
    source.write_bytes(b"# two\n")
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert worker.worker_identity(source) != before


@pytest.mark.parametrize("size", [0, 1024 * 1024 + 1])
def test_worker_identity_has_bounded_nonempty_input(tmp_path, size):
    source = tmp_path / "worker.py"
    source.write_bytes(b"x" * size)
    with pytest.raises(RuntimeError, match="worker size"):
        worker.worker_identity(source)


def test_pinned_child_binds_the_private_copy_not_repository_source(tmp_path):
    command = worker.sandbox_command(
        tmp_path, "fixture", "pdf", "linux", tmp_path / "runtime", "a" * 64,
    )
    bindings = {command[i + 2]: command[i + 1] for i, value in enumerate(command)
                if value == "--ro-bind"}
    assert bindings["/worker.py"] == str(tmp_path / "worker.py")


@pytest.mark.parametrize("change", [None, "missing", "policy", "sha256", "type",
    "execution:uid", "execution:networkInterfaces", "execution:networkDenied",
    "execution:hostFilesystemHidden", "execution:noNewPrivileges",
    "execution:referenceFontMountReadOnly", "execution:resourceLimits"])
def test_bridge_requires_exact_invocation_worker_and_snapshots_before_start(
    tmp_path, monkeypatch, change,
):
    source_dir, output = tmp_path / "source", tmp_path / "output"
    source_dir.mkdir()
    output.mkdir()
    source = source_dir / "renderer_wsl_worker.py"
    source.write_bytes(b"# version one\n")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(bridge, "__file__", str(source_dir / "renderer_wsl.py"))
    monkeypatch.setattr(bridge, "render_directory", lambda: nullcontext(output))
    workbook, pdf = b"fixture", b"%PDF-fixture"
    evidence = {"policy": "wsl-worker-source-v1", "sha256": source_sha}
    if change in {"policy", "sha256"}:
        evidence[change] = "changed"
    report = {
        **wsl_execution_evidence(windows_fonts=False),
        "status": "PASS", "format": "pdf", "fontSource": "linux",
        "policy": "wsl-bwrap-cgroup-v1", "fontRequestPolicy": "wsl-fontconfig-face-v2",
       "sourceSha256": hashlib.sha256(workbook).hexdigest(),
        "coreDumpGuard": core_guard_evidence(),
        "brokerCoreDumpGuard": core_guard_evidence(),
        "bootstrapPythonIdentity": bootstrap_python_evidence(),
        "pdfSha256": hashlib.sha256(pdf).hexdigest(),
        "runtimeImage": {"policy": "wsl-squashfs-runtime-v1", "sha256": "a" * 64,
                         "filesystem": "squashfs", "mountsReadOnly": True},
    }
    if change != "missing":
        report["workerIdentity"] = None if change == "type" else evidence
    if change is not None and change.startswith("execution:"):
        report.pop(change.split(":", 1)[1])
    commands = []

    def start(command, **kwargs):
        commands.append(command)
        # A repository deployment after launch must not alter this invocation.
        source.write_bytes(b"# version two\n")
        (output / "page.pdf").write_bytes(pdf)
        (output / "renderer-report.json").write_text(json.dumps(report), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdin=None, poll=lambda: 0, wait=lambda **kw: 0)

    monkeypatch.setattr(bridge.subprocess, "Popen", start)
    if change is None:
        assert bridge.render_wsl_pdf(workbook, runtime_id="a" * 64) == (pdf, report)
        assert bridge.linux_path(output / "worker.py") in commands[0]
        assert (output / "worker.py").read_bytes() == b"# version one\n"
    else:
        message = ("execution isolation evidence mismatch" if change.startswith("execution:")
                   else "worker identity mismatch")
        with pytest.raises(RuntimeError, match=message):
            bridge.render_wsl_pdf(workbook, runtime_id="a" * 64)
