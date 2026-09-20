"""Requires an image built by scripts/build_wsl_renderer_runtime.py in test WSL."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from app.services.renderer_wsl import linux_path, render_wsl_pdf, render_wsl_reference
from tests.unit.test_preserving_print_workbook import advanced


@pytest.fixture(scope="module")
def installed_runtime():
    result = subprocess.run([
        "wsl.exe", "-d", "Ubuntu-22.04", "-u", "root", "--exec", "/usr/bin/python3", "-I", "-c",
        "import json,pathlib,re;"
        "p=pathlib.Path('/opt/llmocr-renderer-runtimes');"
        "ids=sorted(f.stem for f in p.glob('*.squashfs') if re.fullmatch('[0-9a-f]{64}',f.stem));"
        "assert ids, 'Build the dedicated WSL runtime image before integration tests';"
        "print(json.dumps(ids))",
    ], capture_output=True, check=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    identities = json.loads(result.stdout)
    missing = next(f"{number:064x}" for number in range(len(identities) + 1)
                   if f"{number:064x}" not in identities)
    return identities[0], missing


def test_real_frozen_reference_matches_live_runtime_and_keeps_isolation(installed_runtime):
    from app.services.print_workbook import workbook_copy

    source, plan = advanced(prefixed=True)
    copied = workbook_copy(source, plan)
    _, original_images, original = render_wsl_reference(copied)
    pdf, frozen_images, report = render_wsl_reference(copied, runtime_id=installed_runtime[0])
    assert pdf.startswith(b"%PDF-")
    assert frozen_images == original_images
    assert report["pageCount"] == original["pageCount"] == 2
    assert report["imageSha256"] == original["imageSha256"]
    assert report["sourceSha256"] == original["sourceSha256"]
    assert report["resolvedFonts"] == original["resolvedFonts"]
    assert report["runtimeImage"]["sha256"] == installed_runtime[0]
    assert report["runtimeImage"]["filesystem"] == "squashfs"
    assert report["runtimeImage"]["mountsReadOnly"] is True
    assert "runtimeImage" not in original
    assert "workerIdentity" not in original
    assert "unitControls" not in original
    from tests.wsl_execution_fixture import unit_control_evidence

    assert report["unitControls"] == unit_control_evidence()
    worker_source = Path(__file__).parents[2] / "app/services/renderer_wsl_worker.py"
    assert report["workerIdentity"] == {
        "policy": "wsl-worker-source-v1",
        "sha256": hashlib.sha256(worker_source.read_bytes()).hexdigest(),
    }
    assert "fontDirectoryIdentity" not in original
    fonts = report["fontDirectoryIdentity"]
    assert fonts["policy"] == "wsl-font-directory-v1"
    assert 0 < fonts["fileCount"] <= 4096
    assert 0 < fonts["totalBytes"] <= 1024 * 1024 * 1024
    # Independent Windows-side full-directory read, not the resolver candidates.
    from app.services.renderer_wsl_worker import font_directory_identity

    assert fonts == font_directory_identity(Path("C:/Windows/Fonts"))
    assert report["uid"] == 65534
    assert report["networkDenied"] is True and report["networkInterfaces"] == ["lo"]
    assert report["referenceFontMountReadOnly"] is True
    assert report["noNewPrivileges"] is True
    from app.services.renderer_wsl_worker import validate_core_guard

    validate_core_guard(report["coreDumpGuard"])
    validate_core_guard(report["brokerCoreDumpGuard"])
    assert report["coreDumpGuard"] == original["coreDumpGuard"]
    assert report["brokerCoreDumpGuard"] == original["brokerCoreDumpGuard"]
    assert report["resourceLimits"] == {
        "memory.max": "1073741824", "memory.swap.max": "0",
        "pids.max": "64", "cpu.max": "100000 100000",
    }


def test_missing_image_cannot_fall_back_to_live_runtime(installed_runtime):
    with pytest.raises(RuntimeError, match="FileNotFoundError"):
        render_wsl_pdf(b"fixture input must not be rendered", runtime_id=installed_runtime[1])


def test_mismatched_actual_unit_timeout_stops_before_workbook_parse(
    tmp_path, monkeypatch, installed_runtime,
):
    from app.services import renderer_wsl as bridge

    worker = Path(__file__).parents[2] / "app/services/renderer_wsl_worker.py"
    source = worker.read_bytes()
    marker = b'"--property=RuntimeMaxSec=60"'
    assert source.count(marker) == 1
    private_worker = tmp_path / "renderer_wsl_worker.py"
    private_worker.write_bytes(source.replace(marker, b'"--property=RuntimeMaxSec=61"'))
    monkeypatch.setattr(bridge, "__file__", str(tmp_path / "renderer_wsl.py"))
    # Invalid ZIP intentionally proves the admission error precedes XLSX/font parsing.
    with pytest.raises(RuntimeError, match="Invalid effective unit controls"):
        bridge.render_wsl_pdf(
            b"must never reach ZipFile or Office", runtime_id=installed_runtime[0],
        )
    assert worker.read_bytes() == source


def test_real_changed_worker_cannot_claim_selected_invocation_identity(
    tmp_path, monkeypatch, installed_runtime,
):
    from app.services import renderer_wsl as bridge
    from app.services.print_workbook import workbook_copy

    source, plan = advanced(prefixed=True)
    worker = Path(__file__).parents[2] / "app/services/renderer_wsl_worker.py"
    before = worker.read_bytes()
    substitute = tmp_path / "substitute.py"
    substitute.write_bytes(before + b"\n# Independent identity fault injection.\n")
    original_path = bridge.linux_path

    def route(path):
        return original_path(substitute if path.name == "worker.py" else path)

    monkeypatch.setattr(bridge, "linux_path", route)
    with pytest.raises(RuntimeError, match="Renderer worker identity mismatch"):
        render_wsl_pdf(workbook_copy(source, plan), runtime_id=installed_runtime[0])
    assert worker.read_bytes() == before


def test_lost_owner_stops_frozen_runtime_unit(tmp_path, installed_runtime):
    from app.services.print_workbook import workbook_copy

    source, plan = advanced(prefixed=True)
    (tmp_path / "page.xlsx").write_bytes(workbook_copy(source, plan))
    worker = Path(__file__).parents[2] / "app/services/renderer_wsl_worker.py"
    result = subprocess.run([
        "wsl.exe", "-d", "Ubuntu-22.04", "-u", "root", "--exec",
        "/usr/bin/unshare", "--mount", "--propagation", "private",
        "/usr/bin/python3", "-I", "-S", "-B", linux_path(worker), linux_path(tmp_path),
        "pdf", "windows", installed_runtime[0],
    ], input=b"", capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode != 0
    assert b"Renderer owner disconnected" in result.stderr
    assert not (tmp_path / "page.pdf").exists()
    unit = json.loads(result.stdout.splitlines()[0])["unit"]
    state = subprocess.run([
        "wsl.exe", "-d", "Ubuntu-22.04", "--exec", "/usr/bin/systemctl", "is-active", unit,
    ], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    assert state.returncode != 0
    assert state.stdout.strip() in {b"inactive", b"failed", b"unknown"}
