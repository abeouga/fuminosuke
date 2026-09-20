"""Real WSL evidence. No OCR calls, user data or qualification flags are used."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import JobArtifact
from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services.print_layout_source import read_source
from app.services.print_pdf import render_wsl_print_pdf
from app.services.print_workbook import workbook_copy
from app.services.renderer_wsl import import_wsl_geometry, linux_path, render_wsl_pdf
from tests.integration import test_template_print_files as print_files
from tests.integration.test_print_layout_api import print_template  # noqa: F401
from tests.unit.test_print_layout import workbook_bytes

saved_template = print_files.saved_template


@pytest.mark.parametrize("breaks", [[], [16], [10, 20]])
def test_real_isolated_wsl_pdf_matches_saved_pages(breaks):
    original = workbook_bytes(rows=33, manual=9)
    plan = page_plan(
        read_source(original, "帳票", {"cells": []}),
        PrintLayoutRequest(base_revision="0" * 64, break_after_rows=breaks),
    )
    printable = workbook_copy(original, plan)
    pdf, report = render_wsl_pdf(printable)
    assert pdf.startswith(b"%PDF-")
    assert report["pageCount"] == len(breaks) + 1
    assert len(report["sizes"]) == len(breaks) + 1
    assert report["uid"] == 65534
    assert report["networkInterfaces"] == ["lo"]
    assert report["networkDenied"] is True
    assert report["hostFilesystemHidden"] is True
    assert report["noNewPrivileges"] is True
    assert report["resourceLimits"] == {
        "memory.max": "1073741824",
        "memory.swap.max": "0",
        "pids.max": "64",
        "cpu.max": "100000 100000",
    }
    assert report["sourceSha256"] == hashlib.sha256(printable).hexdigest()
    assert report["pdfSha256"] == hashlib.sha256(pdf).hexdigest()
    assert all(
        abs(width - 210 * 72 / 25.4) < 0.1 and abs(height - 297 * 72 / 25.4) < 0.1
        for width, height in report["sizes"]
    )


@pytest.mark.parametrize(
    "change,code", [("pages", "PAGE_COUNT_MISMATCH"), ("paper", "PAGE_GEOMETRY_MISMATCH")]
)
def test_real_wsl_pdf_does_not_accept_wrong_saved_geometry(change, code):
    original = workbook_bytes(rows=33, manual=9)
    plan = page_plan(
        read_source(original, "帳票", {"cells": []}),
        PrintLayoutRequest(base_revision="0" * 64, break_after_rows=[16]),
    )
    printable = workbook_copy(original, plan)
    if change == "pages":
        plan["pages"] = plan["pages"][:1]
    else:
        plan["paperSize"] = "A3"
    with pytest.raises(AppError) as error:
        render_wsl_print_pdf(printable, plan, "Ubuntu-22.04")
    assert error.value.code == code


def test_wsl_pdf_download_uses_configured_renderer_and_keeps_source(
    client, saved_template, monkeypatch
):
    _, job_id, original, url, state = saved_template
    monkeypatch.setattr(client.app.state.settings.app, "print_pdf_renderer", "wsl")
    response = client.get(url + "/files/pdf", params={"base_revision": state["revision"]})
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF-")
    assert response.headers["x-renderer-qualified"] == "false"
    assert client.get(url).json()["revision"] == state["revision"]
    with client.app.state.database.session() as session:
        artifact = session.scalar(select(JobArtifact).where(
            JobArtifact.job_id == job_id, JobArtifact.artifact_name == "sourceWorkbook"))
        assert client.app.state.storage.read_bytes(artifact.relative_path) == original


def test_lost_owner_stops_only_its_wsl_service(tmp_path):
    (tmp_path / "page.xlsx").write_bytes(workbook_bytes(rows=33, manual=9))
    worker = Path(__file__).parents[2] / "app/services/renderer_wsl_worker.py"
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
            linux_path(worker),
            linux_path(tmp_path),
        ],
        input=b"",
        capture_output=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert b"Renderer owner disconnected" in result.stderr
    assert not (tmp_path / "page.pdf").exists()
    unit = json.loads(result.stdout.splitlines()[0])["unit"]
    state = subprocess.run(
        ["wsl.exe", "-d", "Ubuntu-22.04", "--exec", "/usr/bin/systemctl", "is-active", unit],
        capture_output=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert state.returncode != 0
    assert state.stdout.strip() in {b"inactive", b"failed", b"unknown"}


def test_real_preserving_copy_with_hidden_management_sheet_and_geometry():
    from app.services.wsl_workbook_geometry import MODEL, dimensions
    from tests.unit.test_preserving_print_workbook import advanced

    source, plan = advanced(prefixed=True)
    copied = workbook_copy(source, plan)
    pdf, report = render_wsl_pdf(copied)
    assert pdf.startswith(b"%PDF-")
    assert report["pageCount"] == 2
    fods, evidence = import_wsl_geometry(copied)
    assert evidence["format"] == "fods"
    assert evidence["sourceSha256"] == hashlib.sha256(copied).hexdigest()
    assert evidence["fodsSha256"] == hashlib.sha256(fods).hexdigest()
    assert evidence["networkDenied"] is True
    measured = dimensions(fods, "帳票", 30, 4)
    assert measured["model"] == MODEL
    assert len(measured["columns"]) == 4 and len(measured["rows"]) == 30
    assert measured["hiddenRows"] == [8]
    assert measured["rows"][7] == 0
    assert all(height > 0 for index, height in enumerate(measured["rows"]) if index != 7)


def test_real_windows_fonts_are_read_only_and_resolved_by_hash():
    from tests.unit.test_preserving_print_workbook import advanced

    source, plan = advanced()
    copied = workbook_copy(source, plan)
    pdf, report = render_wsl_pdf(copied, font_source="windows")
    assert report["pageCount"] == 2 and pdf.startswith(b"%PDF-")
    assert report["referenceFontMountReadOnly"] is True
    assert report["hostFilesystemHidden"] is True
    assert report["fontSource"] == "windows"
    assert report["resolvedFonts"]
    for font in report["resolvedFonts"]:
        assert font["file"].startswith("/reference-fonts/")
        path = Path("C:/Windows/Fonts") / Path(font["file"]).name
        assert font["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_real_windows_font_evidence_preserves_regular_bold_italic_and_bold_italic():
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    sheet = book.active
    faces = ((False, False), (True, False), (False, True), (True, True))
    for row, (bold, italic) in enumerate(faces, 1):
        sheet.cell(row, 1, "Font fixture ABC 123").font = Font(name="Calibri", b=bold, i=italic)
    sheet.print_area = "A1:A4"
    buffer = BytesIO()
    book.save(buffer)
    original = buffer.getvalue()
    pdf, report = render_wsl_pdf(original, font_source="windows")
    assert pdf.startswith(b"%PDF-") and report["pageCount"] == 1
    assert report["fontRequestPolicy"] == "wsl-fontconfig-face-v2"
    assert report["sourceSha256"] == hashlib.sha256(original).hexdigest()
    fonts = report["resolvedFonts"]
    assert len(fonts) == 4
    assert {(f["requestedWeight"], f["requestedSlant"]) for f in fonts} == {
        ("regular", "roman"), ("bold", "roman"), ("regular", "italic"), ("bold", "italic"),
    }
    assert len({(f["file"], f["collectionIndex"]) for f in fonts}) == 4
    for font in fonts:
        assert font["requestedFamily"] == "Calibri"
        assert "Calibri" in font["resolvedFamily"].split(",")
        assert font["resolvedWeight"] == ("200" if font["requestedWeight"] == "bold" else "80")
        assert font["resolvedSlant"] == ("100" if font["requestedSlant"] == "italic" else "0")
        assert font["resolvedStyle"]
        assert font["requestPattern"] == (
            f"Calibri:weight={font['requestedWeight']}:slant={font['requestedSlant']}"
        )
        path = Path("C:/Windows/Fonts") / Path(font["file"]).name
        assert font["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
