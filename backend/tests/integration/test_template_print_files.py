from __future__ import annotations

import copy
from io import BytesIO

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select

from app.db.models import JobArtifact, ProcessingJob, Template, TemplateVersion
from app.domain.print_layout import PAPER_MM, print_error
from app.services import office_reference_renderer as office
from app.services import template_print_service
from app.services.print_layout_service import PrintLayoutService
from app.services.print_layout_source import read_source
from app.services.template_version_service import TemplateVersionService
from tests.integration import test_print_layout_api as existing
from tests.integration.test_print_layout_api import print_template  # noqa: F401
from tests.unit.test_print_layout import workbook_bytes


@pytest.fixture
def saved_template(client, monkeypatch, request):
    content = workbook_bytes(rows=33, manual=9)
    monkeypatch.setattr(existing, "workbook_bytes", lambda: content)
    version_id, job_id, _, _ = request.getfixturevalue("print_template")
    # Fix source geometry in this API fixture. Office is tested separately; GUI
    # planning, persistence, rendering and version cloning still use real code.
    monkeypatch.setattr(
        PrintLayoutService,
        "_source",
        lambda self, session, version: read_source(content, "帳票", version.layout_json),
    )
    url = f"/api/v1/template-versions/{version_id}/print-layout"
    state = client.get(url).json()
    response = client.put(url, json={"baseRevision": state["revision"], "breakAfterRows": [16]})
    assert response.status_code == 200, response.text
    return version_id, job_id, content, url, response.json()


def download(client, url, state, format="xlsx"):
    return client.get(url + f"/files/{format}", params={"base_revision": state["revision"]})


@pytest.mark.parametrize("breaks", [[], [16], [10, 20]])
def test_api_saved_boundaries_source_and_snapshot_preservation(client, saved_template, breaks):
    version_id, job_id, content, url, state = saved_template
    state = client.put(
        url, json={"baseRevision": state["revision"], "breakAfterRows": breaks}
    ).json()
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        quality, layout = copy.deepcopy(version.quality_json), copy.deepcopy(version.layout_json)
        snapshot = copy.deepcopy(session.get(ProcessingJob, job_id).config_snapshot_json)
        old = ProcessingJob(
            job_type="EXTRACT_DOCUMENT",
            mode="SPREADSHEET_TEMPLATE",
            status="REVIEW_REQUIRED",
            template_version_id=version_id,
            request_hash="c" * 64,
            config_snapshot_json={"historical": "immutable"},
        )
        session.add(old)
        session.commit()
        old_id = old.id
    response = download(client, url, state)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-print-layout-revision"] == state["revision"]
    assert response.headers["x-renderer-qualified"] == "false"
    assert "attachment" in response.headers["content-disposition"]
    assert [b.id for b in load_workbook(BytesIO(response.content)).active.row_breaks.brk] == breaks
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        assert version.quality_json == quality and version.layout_json == layout
        assert session.get(ProcessingJob, job_id).config_snapshot_json == snapshot
        assert session.get(ProcessingJob, old_id).config_snapshot_json == {
            "historical": "immutable"
        }
        artifact = session.scalars(
            select(JobArtifact).where(
                JobArtifact.job_id == job_id, JobArtifact.artifact_name == "sourceWorkbook"
            )
        ).first()
        assert client.app.state.storage.read_bytes(artifact.relative_path) == content
    assert (
        client.post(f"/api/v1/template-versions/{version_id}/approve").json()["code"]
        == "PRINT_LAYOUT_OCR_NOT_AVAILABLE"
    )


def test_versions_export_their_own_saved_ranges(client, saved_template):
    version_id, _, _, url, state = saved_template
    first = download(client, url, state).content
    database, storage = client.app.state.database, client.app.state.storage
    # Public activation remains blocked. Exercise historical/future persisted
    # metadata using the same defensive setup as existing version fork tests.
    with database.session() as session:
        version = session.get(TemplateVersion, version_id)
        version.status = "ACTIVE"
        session.get(Template, version.template_id).active_version_id = version_id
        session.commit()
    fork = TemplateVersionService(database, storage).edit(version_id)
    new_url = f"/api/v1/template-versions/{fork.id}/print-layout"
    second_state = client.get(new_url).json()
    second_state = client.put(
        new_url, json={"baseRevision": second_state["revision"], "breakAfterRows": [20]}
    ).json()
    second = download(client, new_url, second_state)
    assert second.status_code == 200, second.text
    assert [b.id for b in load_workbook(BytesIO(second.content)).active.row_breaks.brk] == [20]
    assert download(client, url, state).content == first
    assert client.get(url).json()["saved"] == state["saved"]


@pytest.mark.parametrize("breaks", [[], [16], [10, 20]])
def test_pdf_converts_exact_downloadable_workbook_and_validates_pages(
    client,
    saved_template,
    monkeypatch,
    breaks,
):
    _, _, _, url, state = saved_template
    state = client.put(
        url, json={"baseRevision": state["revision"], "breakAfterRows": breaks}
    ).json()
    xlsx = download(client, url, state).content
    calls = []
    monkeypatch.setattr(office, "renderer_identity", lambda: {"executable": "fixture"})

    def convert(directory, identity, *, sandboxed):
        assert sandboxed is True
        assert (directory / "page.xlsx").read_bytes() == xlsx
        calls.append(identity)
        writer = PdfWriter()
        for _ in range(len(breaks) + 1):
            writer.add_blank_page(width=210 * 72 / 25.4, height=297 * 72 / 25.4)
        writer.write(directory / "page.pdf")
        return directory / "page.pdf", {}

    monkeypatch.setattr(office, "convert_office_file", convert)
    result = download(client, url, state, "pdf")
    assert result.status_code == 200, result.text
    assert result.headers["content-type"] == "application/pdf"
    assert len(PdfReader(BytesIO(result.content)).pages) == len(breaks) + 1
    assert len(calls) == 1  # Success exercises the real bounded PDF helper, not Office.


@pytest.mark.parametrize("failure", ["count", "size", "malformed", "renderer"])
def test_pdf_failures_do_not_return_partial_or_legacy_pdf(
    client, saved_template, monkeypatch, failure
):
    _, _, _, url, state = saved_template
    monkeypatch.setattr(office, "renderer_identity", lambda: {"executable": "fixture"})
    calls = []

    def convert(directory, identity, *, sandboxed):
        assert sandboxed is True
        calls.append(sandboxed)
        if failure == "renderer":
            raise print_error("Office render timed out.", "RENDER_FAILED")
        if failure == "malformed":
            (directory / "page.pdf").write_bytes(b"invalid")
        else:
            writer = PdfWriter()
            for _ in range(1 if failure == "count" else 2):
                width, height = PAPER_MM["Letter" if failure == "size" else "A4"]
                writer.add_blank_page(width=width * 72 / 25.4, height=height * 72 / 25.4)
            writer.write(directory / "page.pdf")
        return directory / "page.pdf", {}

    monkeypatch.setattr(office, "convert_office_file", convert)
    result = download(client, url, state, "pdf")
    assert result.status_code in {409, 422}, result.text
    assert (
        result.json()["code"]
        == {
            "count": "PAGE_COUNT_MISMATCH",
            "size": "PAGE_GEOMETRY_MISMATCH",
            "malformed": "RENDER_FAILED",
            "renderer": "RENDER_FAILED",
        }[failure]
    )
    assert len(calls) == 1
    assert download(client, url, state).status_code == 200  # failure does not lock future requests


@pytest.mark.parametrize(
    "failure", ["revision", "stale", "source", "missing", "unsaved", "invalid"]
)
def test_export_rejects_invalid_or_stale_inputs(client, saved_template, failure):
    version_id, job_id, _, url, state = saved_template
    if failure == "revision":
        state["revision"] = "0" * 64
    else:
        with client.app.state.database.session() as session:
            version = session.get(TemplateVersion, version_id)
            if failure == "stale":
                version.layout_json = {**version.layout_json, "changed": True}
            elif failure in {"unsaved", "invalid"}:
                quality = copy.deepcopy(version.quality_json)
                if failure == "unsaved":
                    quality.pop("printLayout")
                else:
                    quality["printLayout"]["pages"][1]["startRow"] += 1
                version.quality_json = quality
            else:
                artifact = session.scalars(
                    select(JobArtifact).where(
                        JobArtifact.job_id == job_id, JobArtifact.artifact_name == "sourceWorkbook"
                    )
                ).first()
                if failure == "source":
                    client.app.state.storage.write_bytes(artifact.relative_path, b"changed")
                else:
                    artifact.relative_path = "missing.xlsx"
            session.commit()
            state["revision"] = PrintLayoutService.revision(version)
    result = download(client, url, state)
    assert result.status_code in {409, 422}, result.text
    assert (
        result.json()["code"]
        == {
            "revision": "PRINT_LAYOUT_CONFLICT",
            "stale": "PRINT_LAYOUT_STALE",
            "source": "PRINT_LAYOUT_SOURCE_CHANGED",
            "missing": "PRINT_LAYOUT_SOURCE_MISSING",
            "unsaved": "PRINT_LAYOUT_NOT_SAVED",
            "invalid": "PRINT_LAYOUT_INVALID",
        }[failure]
    )


def test_pdf_rechecks_revision_after_conversion(client, saved_template, monkeypatch):
    version_id, _, _, url, state = saved_template

    def changed_during_render(workbook, plan):
        with client.app.state.database.session() as session:
            version = session.get(TemplateVersion, version_id)
            version.quality_json = {**version.quality_json, "layoutInputRevision": 42}
            session.commit()
        return b"must not be downloaded"

    monkeypatch.setattr(template_print_service, "render_print_pdf", changed_during_render)
    result = download(client, url, state, "pdf")
    assert result.status_code == 409
    assert result.json()["code"] == "PRINT_LAYOUT_CONFLICT"


def test_missing_version_and_invalid_format(client):
    url = "/api/v1/template-versions/missing/print-layout/files/"
    assert client.get(url + "xlsx", params={"base_revision": "0" * 64}).status_code == 404
    assert client.get(url + "exe", params={"base_revision": "0" * 64}).status_code == 422
    assert client.get(url + "xlsx").status_code == 422
