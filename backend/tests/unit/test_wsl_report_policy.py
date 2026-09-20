import hashlib
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.services import renderer_wsl
from app.services.renderer_environment import environment_report
from tests.core_guard_fixture import core_guard_evidence


@pytest.mark.parametrize("policy", [None, "family-only-v1", "wsl-fontconfig-face-v2"])
def test_bridge_requires_current_font_request_records(tmp_path, monkeypatch, policy):
    workbook, pdf = b"fixture XLSX", b"%PDF-fixture"
    report = {
        "status": "PASS", "format": "pdf", "fontSource": "windows",
        "policy": "wsl-bwrap-cgroup-v1", "fontRequestPolicy": policy,
       "sourceSha256": hashlib.sha256(workbook).hexdigest(),
        "coreDumpGuard": core_guard_evidence(),
        "brokerCoreDumpGuard": core_guard_evidence(),
        "pdfSha256": hashlib.sha256(pdf).hexdigest(),
    }
    (tmp_path / "page.pdf").write_bytes(pdf)
    (tmp_path / "renderer-report.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(renderer_wsl, "render_directory", lambda: nullcontext(tmp_path))
    process = SimpleNamespace(returncode=0, stdin=None, poll=lambda: 0, wait=lambda **kwargs: 0)
    monkeypatch.setattr(renderer_wsl.subprocess, "Popen", lambda *args, **kwargs: process)
    if policy == "wsl-fontconfig-face-v2":
        assert renderer_wsl.render_wsl_pdf(workbook, font_source="windows") == (pdf, report)
    else:
        with pytest.raises(RuntimeError, match="identity mismatch"):
            renderer_wsl.render_wsl_pdf(workbook, font_source="windows")


def test_environment_report_does_not_claim_the_removed_metadata_hash_cache():
    report = environment_report({"fonts": {"codes": []}})
    assert report["runtimeDependencies"]["hashCache"] == "none-content-rehashed-v1"
    assert report["runtimeDependencies"]["completeTrace"] is False
    assert report["environmentQualified"] is False
