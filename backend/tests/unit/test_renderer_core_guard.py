import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import renderer_glyph_worker as glyph
from app.services import renderer_wsl as bridge
from app.services import renderer_wsl_worker as worker
from app.services.renderer_wsl_worker import validate_core_guard
from tests.core_guard_fixture import core_guard_evidence


def test_valid_current_protocol_is_not_a_production_certificate():
    evidence = core_guard_evidence()
    validate_core_guard(evidence)
    assert "qualified" not in evidence


@pytest.mark.parametrize("value", [None, False, [], "PASS", {"qualified": True}])
def test_missing_or_boolean_evidence_is_rejected(value):
    with pytest.raises(RuntimeError, match="Core dump guard evidence"):
        validate_core_guard(value)


@pytest.mark.parametrize("key,value", [
    ("policy", "old"), ("architecture", "aarch64"), ("coreLimits", [0, 1]),
    ("coreLimits", [1, -1]), ("coreLimits", [True, 1]), ("coreLimits", [1.0, 1]),
    ("coreLimits", None), ("noNewPrivileges", 1), ("seccompMode", 1),
    ("seccompMode", 2.0), ("filterBytes", 0), ("filterBytes", 192.0),
    ("deniedMutationProbes", 3), ("deniedMutationProbes", 4.0),
    ("filterSha256", "b" * 64), ("librarySha256", "bad"), ("librarySha256", None),
])
def test_unverified_guard_cannot_be_accepted(key, value):
    evidence = core_guard_evidence()
    evidence[key] = value
    with pytest.raises(RuntimeError, match="Core dump guard evidence"):
        validate_core_guard(evidence)


def test_glyph_guard_failure_precedes_any_document_read(monkeypatch):
    monkeypatch.setattr(glyph, "_environment", lambda runtime: {})

    def failed_guard():
        raise RuntimeError("guard failed before parsing")

    monkeypatch.setattr(glyph, "install_core_guard", failed_guard)
    monkeypatch.setattr(glyph, "_read", lambda *args: pytest.fail("Input read before guard"))
    with pytest.raises(RuntimeError, match="guard failed before parsing"):
        glyph.inner("a" * 64)


def test_office_guard_failure_precedes_any_font_or_workbook_parse(monkeypatch):
    text = {"memory.max": "1073741824", "memory.swap.max": "0", "pids.max": "64",
            "cpu.max": "100000 100000", "status": "NoNewPrivs:\t1\nCapEff:\t0000000000000000"}
    monkeypatch.setattr(worker.Path, "read_text", lambda path: text[path.name])
    monkeypatch.setattr(worker.Path, "exists", lambda path: False)
    monkeypatch.setattr(worker.socket, "if_nameindex", lambda: [(1, "lo")])

    def no_route(*args):
        raise OSError("no route")

    socket = SimpleNamespace(settimeout=lambda t: None, connect=no_route)
    monkeypatch.setattr(worker.socket, "socket", lambda: nullcontext(socket))

    def failed_guard():
        raise RuntimeError("guard failed before parsing")

    monkeypatch.setattr(worker, "install_core_guard", failed_guard)
    monkeypatch.setattr(worker, "resolved_fonts", lambda: pytest.fail("Font parse before guard"))
    with pytest.raises(RuntimeError, match="guard failed before parsing"):
        worker.inner()


@pytest.mark.parametrize("change", ["missing", "filter", "limits", "boolean"])
@pytest.mark.parametrize("field", ["coreDumpGuard", "brokerCoreDumpGuard"])
def test_bridge_rejects_missing_or_wrong_guard_report(tmp_path, monkeypatch, change, field):
    workbook, pdf = b"synthetic not parsed", b"%PDF-synthetic"
    guard = core_guard_evidence()
    if change == "filter":
        guard["filterSha256"] = "f" * 64
    elif change == "limits":
        guard["coreLimits"] = [0, 1]
    report = {
        "status": "PASS", "format": "pdf", "fontSource": "linux",
        "policy": worker.POLICY, "fontRequestPolicy": worker.FONT_REQUEST_POLICY,
        "sourceSha256": hashlib.sha256(workbook).hexdigest(),
        "pdfSha256": hashlib.sha256(pdf).hexdigest(),
        "coreDumpGuard": core_guard_evidence(),
        "brokerCoreDumpGuard": core_guard_evidence(),
    }
    report[field] = True if change == "boolean" else guard
    if change == "missing":
        report.pop(field)
    (tmp_path / "page.pdf").write_bytes(pdf)
    (tmp_path / "renderer-report.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(bridge, "render_directory", lambda: nullcontext(tmp_path))
    process = SimpleNamespace(returncode=0, stdin=None, poll=lambda: 0, wait=lambda **kw: 0)
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda *a, **kw: process)
    with pytest.raises(RuntimeError, match="Core dump guard evidence"):
        bridge.render_wsl_pdf(workbook)


@pytest.mark.parametrize("module", [worker, glyph])
def test_broker_guard_failure_precedes_input_resolution(monkeypatch, module):
    def failed_guard(*, expected_uid):
        assert expected_uid == 0
        raise RuntimeError("broker guard failed")

    monkeypatch.setattr(module, "install_core_guard", failed_guard)
    monkeypatch.setattr(Path, "resolve", lambda *a, **kw: pytest.fail("Input used before guard"))
    with pytest.raises(RuntimeError, match="broker guard failed"):
        module.broker(Path("unused-input"), runtime_id="a" * 64)
