"""Real WSL glyph protocol, using only a synthetic reference workbook."""

import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from app.services.renderer_glyph_bridge import validate_wsl_pdf_glyphs
from app.services.renderer_wsl import linux_path, render_wsl_reference
from tests.unit.test_preserving_print_workbook import advanced


@pytest.fixture(scope="module")
def glyph_runtime():
    backend = Path(__file__).parents[2]
    result = subprocess.run(
        [
            "wsl.exe",
            "-d",
            "Ubuntu-22.04",
            "-u",
            "root",
            "--exec",
            "/usr/bin/python3",
            "-I",
            "-c",
            "import json,pathlib,re;"
            "p=pathlib.Path('/opt/llmocr-renderer-runtimes');"
            "print(json.dumps(sorted(f.stem for f in p.glob('*.squashfs') "
            "if re.fullmatch('[0-9a-f]{64}',f.stem))))",
        ],
        capture_output=True,
        check=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    identities = json.loads(result.stdout)
    assert 0 < len(identities) <= 16, "Build the dedicated glyph runtime image first"
    for identity in identities:
        probe = subprocess.run(
            [
                "wsl.exe",
                "-d",
                "Ubuntu-22.04",
                "-u",
                "root",
                "--cd",
                linux_path(backend),
                "--exec",
                "/usr/bin/unshare",
                "--mount",
                "--propagation",
                "private",
                "/usr/bin/python3",
                "-m",
                "scripts.probe_wsl_glyph_runtime",
                identity,
            ],
            capture_output=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if probe.returncode == 0:
            evidence = json.loads(probe.stdout)
            assert evidence["runtimeId"] == identity
            assert evidence["productionQualified"] is False
            return identity
    pytest.fail("No installed image passed the pinned glyph dependency probe")


@pytest.fixture(scope="module")
def reference(glyph_runtime):
    from app.services.print_workbook import workbook_copy

    source, plan = advanced(prefixed=True)
    pdf, _, report = render_wsl_reference(workbook_copy(source, plan), runtime_id=glyph_runtime)
    assert report["pageCount"] == 2
    return pdf, report


def test_real_fixed_helper_retains_blank_reference_page(reference):
    from app.services.renderer_glyph_bridge import code_bundle

    pdf, reference_report = reference
    report = validate_wsl_pdf_glyphs(pdf, reference_report)
    assert report["status"] == "PASS"
    assert report["codeBundleSha256"] == hashlib.sha256(code_bundle()).hexdigest()
    assert report["runtimeImage"] == reference_report["runtimeImage"]
    assert report["fontDirectoryIdentity"] == reference_report["fontDirectoryIdentity"]
    assert report["uid"] == 65534
    assert report["networkDenied"] is True and report["networkInterfaces"] == ["lo"]
    assert report["productionQualified"] is False
    from app.services.renderer_wsl_worker import validate_core_guard

    validate_core_guard(report["coreDumpGuard"])
    validate_core_guard(report["brokerCoreDumpGuard"])
    assert report["coreDumpGuard"] == reference_report["coreDumpGuard"]
    assert report["brokerCoreDumpGuard"] == reference_report["brokerCoreDumpGuard"]
    glyphs = report["glyphs"]
    assert glyphs["pageCount"] == 2
    assert [page["renderedPage"] for page in glyphs["pages"]] == [1, 2]
    # The existing advanced fixture has only B2="氏名"; page 2 contains borders.
    assert [page["textInstructionGlyphs"] for page in glyphs["pages"]] == [2, 0]
    assert report["allMatchedFacesDeclared"] is False
    assert glyphs["textInstructionGlyphs"] == sum(
        p["textInstructionGlyphs"] for p in glyphs["pages"]
    )
    assert glyphs["fonts"] and glyphs["visibilityAndLayoutVerified"] is False


@pytest.mark.parametrize(
    "key,message",
    [
        ("fontDirectoryIdentity", "font directory changed"),
        ("workerIdentity", "worker changed"),
    ],
)
def test_changed_reference_environment_is_rejected_before_pdf_parse(reference, key, message):
    _, report = reference
    altered = deepcopy(report)
    altered[key]["sha256"] = "0" * 64
    not_pdf = b"This intentionally is not PDF syntax."
    altered["pdfSha256"] = hashlib.sha256(not_pdf).hexdigest()
    with pytest.raises(RuntimeError, match=message):
        validate_wsl_pdf_glyphs(not_pdf, altered)


def test_changed_source_face_bytes_are_rejected(reference):
    pdf, report = reference
    altered = deepcopy(report)
    altered["resolvedFonts"][0]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="Source font bytes changed"):
        validate_wsl_pdf_glyphs(pdf, altered)


def test_real_fixed_helper_verifies_text_on_both_pages(glyph_runtime):
    from io import BytesIO

    from openpyxl import load_workbook

    from app.domain.print_layout import page_plan
    from app.services.print_layout_source import read_source
    from app.services.print_workbook import workbook_copy
    from tests.unit.test_print_layout import request, workbook_bytes

    # A separate fixture extends coverage; do not change the shared blank-page fixture.
    book = load_workbook(BytesIO(workbook_bytes(rows=30)))
    book.active["B16"] = "後頁ABC"
    output = BytesIO()
    book.save(output)
    source = output.getvalue()
    plan = page_plan(read_source(source, "帳票", {"cells": []}), request(break_after_rows=[15]))
    pdf, _, reference_report = render_wsl_reference(
        workbook_copy(source, plan), runtime_id=glyph_runtime
    )
    result = validate_wsl_pdf_glyphs(pdf, reference_report)
    assert [page["textInstructionGlyphs"] for page in result["glyphs"]["pages"]] == [2, 5]
    assert result["glyphs"]["textInstructionGlyphs"] == 7
    assert len(result["sourceFaces"]) >= 2  # Latin requested face plus CJK fallback.
    assert result["allMatchedFacesDeclared"] is False


def test_changed_code_bundle_cannot_claim_original_invocation_identity(
    reference, tmp_path, monkeypatch
):
    import io
    from zipfile import ZipFile

    from app.services import renderer_glyph_bridge as bridge

    original = bridge.code_bundle()
    with ZipFile(io.BytesIO(original)) as archive:
        entries = [
            (
                name,
                archive.read(name)
                + (b"\n# Identity fault injection.\n" if name == "__main__.py" else b""),
            )
            for name in archive.namelist()
        ]
    alternate = tmp_path / "substitute.pyz"
    alternate.write_bytes(bridge._bundle(entries))
    resolve = bridge.linux_path
    monkeypatch.setattr(
        bridge,
        "linux_path",
        lambda path: resolve(
            alternate if path.name == "glyph.pyz" else path,
        ),
    )
    with pytest.raises(RuntimeError, match="identity or dependency mismatch"):
        validate_wsl_pdf_glyphs(*reference)
    assert bridge.code_bundle() == original


def test_missing_runtime_cannot_fall_back_to_host_dependencies(reference):
    pdf, report = reference
    changed = deepcopy(report)
    changed["runtimeImage"]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="FileNotFoundError"):
        validate_wsl_pdf_glyphs(pdf, changed)


def test_lost_owner_stops_only_its_glyph_unit(reference, glyph_runtime, tmp_path):
    from app.services import renderer_glyph_bridge as bridge

    pdf, report = reference
    manifest = bridge.input_manifest(pdf, report)
    (tmp_path / "input.zip").write_bytes(
        bridge._bundle(
            [
                ("reference.pdf", pdf),
                ("manifest.json", bridge.canonical_bytes(manifest)),
            ]
        )
    )
    code = tmp_path / "glyph.pyz"
    code.write_bytes(bridge.code_bundle())
    result = subprocess.run(
        [
            "wsl.exe",
            "-d",
            "Ubuntu-22.04",
            "-u",
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
            linux_path(code),
            "--broker",
            linux_path(tmp_path),
            glyph_runtime,
        ],
        input=b"",
        capture_output=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert b"Glyph owner disconnected" in result.stderr
    assert not (tmp_path / "glyph-report.json").exists()
    unit = json.loads(result.stdout.splitlines()[0])["unit"]
    assert unit.startswith("llmocr-glyph-")
    state = subprocess.run(
        [
            "wsl.exe",
            "-d",
            "Ubuntu-22.04",
            "--exec",
            "/usr/bin/systemctl",
            "is-active",
            unit,
        ],
        capture_output=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert state.returncode != 0
    assert state.stdout.strip() in {b"inactive", b"failed", b"unknown"}
