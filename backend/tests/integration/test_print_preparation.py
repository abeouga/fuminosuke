from __future__ import annotations

import copy
import hashlib
import math
from io import BytesIO

import pytest
from openpyxl import load_workbook
from PIL import Image

from app.db.models import ProcessingJob, TemplateVersion
from app.services import print_preparation_service as preparation
from app.services.print_layout_service import PrintLayoutService
from app.services.print_layout_source import read_source
from app.services.wsl_workbook_geometry import MODEL
from tests.integration import test_template_print_files as existing
from tests.integration.test_print_layout_api import print_template  # noqa: F401

saved_template = existing.saved_template
REAL_SOURCE = PrintLayoutService._source


@pytest.fixture
def prepared_fixture(client, saved_template, monkeypatch):
    version, job, content, url, state = saved_template
    monkeypatch.setattr(PrintLayoutService, "_source", REAL_SOURCE)
    monkeypatch.setattr(client.app.state.settings.app, "print_pdf_renderer", "wsl")
    source = read_source(content, "帳票", {"cells": []})
    geometry = {
        "model": MODEL,
        "unit": "pt",
        "hiddenRows": [],
        "hiddenColumns": [],
        "columns": [10.0]
        + [
            next(c["width"] for c in source["cells"] if c["startCol"] == column == c["endCol"])
            for column in range(2, 5)
        ],
        "rows": [15.0]
        + [
            b["y"] - a["y"]
            for a, b in zip(source["rowBoundaries"], source["rowBoundaries"][1:], strict=False)
        ],
    }
    fonts = [{"requestedFamily": "Fixture", "resolvedFamily": "Fixture"}]
    monkeypatch.setattr(
        preparation,
        "import_wsl_geometry",
        lambda *a, **k: (
            b"fixture FODS; real parser/renderer tested separately",
            {"resolvedFonts": fonts},
        ),
    )
    monkeypatch.setattr(preparation, "dimensions", lambda *a: copy.deepcopy(geometry))
    calls = []

    def render(data, **kwargs):
        book = load_workbook(BytesIO(data))
        count = len(book.active.row_breaks.brk) + 1
        width, height = math.ceil(210 * 150 / 25.4), math.ceil(297 * 150 / 25.4)
        output = BytesIO()
        Image.new("RGB", (width, height), "white").save(output, format="PNG")
        images = [output.getvalue()] * count
        pdf = b"%PDF-fixture; native PDF generation tested by WSL integration"
        calls.append(data)
        return (
            pdf,
            images,
            {
                "resolvedFonts": fonts,
                "pageCount": count,
                "sizes": [[210 * 72 / 25.4, 297 * 72 / 25.4]] * count,
                "imageSizes": [[width, height]] * count,
            },
        )

    monkeypatch.setattr(preparation, "render_wsl_reference", render)
    return version, job, content, url, state, calls, render


def test_prepare_preserves_canonical_meaning_and_serves_immutable_files(
    client, prepared_fixture, monkeypatch
):
    version, job, source, url, state, calls, _ = prepared_fixture
    with client.app.state.database.session() as session:
        original = copy.deepcopy(session.get(TemplateVersion, version).layout_json)
    response = client.post(url + "/prepare", params={"base_revision": state["revision"]})
    assert response.status_code == 200, response.text
    next_state = response.json()
    plan = next_state["saved"]
    assert plan["geometryModelVersion"] == MODEL
    assert [p["range"] for p in plan["pages"]] == [p["range"] for p in state["saved"]["pages"]]
    assert plan["commonScale"] * 100 == round(plan["commonScale"] * 100)
    assert next_state["source"]["geometryModelVersion"] == MODEL
    assert plan["sourceSha256"] == hashlib.sha256(source).hexdigest()
    bindings = [b for page in plan["pages"] for field in page["fields"] for b in field["bindings"]]
    assert bindings == [
        {
            "mappingId": "layout_xlsx_C32",
            "sourceRegionId": "layout_xlsx_C32",
            "targetPath": "document.unifiedEarTag",
            "mappingType": "text",
        }
    ]
    with client.app.state.database.session() as session:
        record = session.get(TemplateVersion, version)
        assert record.layout_json == original and record.status == "DRAFT"
        assert session.get(ProcessingJob, job).config_snapshot_json["printLayout"] == plan
    # Downloads must use the prepared bytes even when no converter can run.
    monkeypatch.setattr(
        preparation, "render_wsl_reference", lambda *a, **k: pytest.fail("No render on download")
    )
    for format in ("xlsx", "pdf"):
        file = client.get(
            url + "/files/" + format, params={"base_revision": next_state["revision"]}
        )
        assert file.status_code == 200, file.text
        assert hashlib.sha256(file.content).hexdigest() == plan["printFiles"][format]["sha256"]
    assert (
        client.get(url + "/files/xlsx", params={"base_revision": next_state["revision"]}).content
        == calls[0]
    )
    assert client.get(url).json()["revision"] == next_state["revision"]
    assert (
        client.post(f"/api/v1/template-versions/{version}/approve").json()["code"]
        == "PRINT_LAYOUT_OCR_NOT_AVAILABLE"
    )


@pytest.mark.parametrize("change", ["revision", "config", "source", "render"])
def test_conflicts_or_render_failure_do_not_publish_candidate(
    client, prepared_fixture, monkeypatch, change
):
    version, _, _, url, state, _, render = prepared_fixture
    old = copy.deepcopy(state["saved"])

    def fail_or_change(data, **kwargs):
        if change == "render":
            raise RuntimeError("Renderer failed")
        with client.app.state.database.session() as session:
            record = session.get(TemplateVersion, version)
            if change == "revision":
                record.quality_json = {**record.quality_json, "layoutInputRevision": 90}
                session.commit()
            elif change == "config":
                client.app.state.storage.write_bytes(
                    record.config_relative_path, b"changed test config"
                )
            else:
                from sqlalchemy import select

                from app.db.models import JobArtifact

                artifact = session.scalar(
                    select(JobArtifact).where(
                        JobArtifact.job_id == record.source_job_id,
                        JobArtifact.artifact_name == "sourceWorkbook",
                    )
                )
                client.app.state.storage.write_bytes(
                    artifact.relative_path, b"changed test workbook"
                )
        return render(data, **kwargs)

    monkeypatch.setattr(preparation, "render_wsl_reference", fail_or_change)
    response = client.post(url + "/prepare", params={"base_revision": state["revision"]})
    assert response.status_code == 409, response.text
    with client.app.state.database.session() as session:
        assert session.get(TemplateVersion, version).quality_json["printLayout"] == old


def test_stale_revision_never_starts_conversion(client, prepared_fixture, monkeypatch):
    _, _, _, url, _, _, _ = prepared_fixture
    monkeypatch.setattr(
        preparation, "import_wsl_geometry", lambda *a, **k: pytest.fail("Must not start")
    )
    response = client.post(url + "/prepare", params={"base_revision": "0" * 64})
    assert response.status_code == 409


def test_changed_page_settings_require_new_print_files(client, prepared_fixture):
    _, _, _, url, state, _, _ = prepared_fixture
    ready = client.post(url + "/prepare", params={"base_revision": state["revision"]}).json()
    response = client.put(url, json={"baseRevision": ready["revision"], "breakAfterRows": [20]})
    assert response.status_code == 200, response.text
    changed = response.json()
    assert "printFiles" not in changed["saved"]
    file = client.get(url + "/files/pdf", params={"base_revision": changed["revision"]})
    assert file.status_code == 409 and file.json()["code"] == "PRINT_FILES_NOT_PREPARED"


def test_authoring_preserves_prepared_files_without_office_and_tamper_is_rejected(
    client,
    prepared_fixture,
    monkeypatch,
):
    version, _, _, url, state, _, _ = prepared_fixture
    ready = client.post(url + "/prepare", params={"base_revision": state["revision"]}).json()
    layout = client.get(f"/api/v1/template-versions/{version}/layout").json()
    layout["relations"] = [
        {
            "id": "meaning",
            "inputCellId": "xlsx_C32",
            "labelCellIds": [],
            "axis": "horizontal",
            "order": 1,
            "targetPath": "document.unifiedEarTag",
        }
    ]
    from tests.integration.test_page_virtual_headings import heading

    layout["cells"].append(heading(ready["saved"]["pages"][1], "prepared_virtual"))
    monkeypatch.setattr(
        preparation, "import_wsl_geometry", lambda *a, **k: pytest.fail("No Office on meaning save")
    )
    response = client.put(
        url + "/authoring", params={"base_revision": ready["revision"]}, json=layout
    )
    assert response.status_code == 200, response.text
    changed = response.json()["state"]
    assert changed["saved"]["printFiles"] == ready["saved"]["printFiles"]
    assert [p["referenceImage"] for p in changed["saved"]["pages"]] == [
        p["referenceImage"] for p in ready["saved"]["pages"]
    ]
    file = client.get(url + "/files/pdf", params={"base_revision": changed["revision"]})
    assert file.status_code == 200
    ref = changed["saved"]["printFiles"]["pdf"]
    client.app.state.storage.write_bytes(ref["relativePath"], b"corrupted fixture artifact")
    file = client.get(url + "/files/pdf", params={"base_revision": changed["revision"]})
    assert file.status_code == 409 and file.json()["code"] == "PRINT_FILE_CHANGED"


def test_real_wsl_preparation_saves_exact_pdf_and_raster_pages(client, saved_template, monkeypatch):
    version, _, source, url, state = saved_template
    monkeypatch.setattr(PrintLayoutService, "_source", REAL_SOURCE)
    monkeypatch.setattr(client.app.state.settings.app, "print_pdf_renderer", "wsl")
    response = client.post(url + "/prepare", params={"base_revision": state["revision"]})
    assert response.status_code == 200, response.text
    ready = response.json()
    assert ready["saved"]["sourceSha256"] == hashlib.sha256(source).hexdigest()
    report = client.app.state.storage.read_bytes(
        ready["saved"]["printFiles"]["report"]["relativePath"]
    )
    import json

    report = json.loads(report)
    assert report["format"] == "reference" and report["networkDenied"] is True
    assert report["dpi"] == 150 and report["referenceFontMountReadOnly"] is True
    pdf = client.get(url + "/files/pdf", params={"base_revision": ready["revision"]})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert hashlib.sha256(pdf.content).hexdigest() == report["pdfSha256"]
    assert len(ready["saved"]["pages"]) == report["pageCount"] == 2
    for index, page in enumerate(ready["saved"]["pages"]):
        image = client.get(url + f"/pages/{page['page']}/image")
        assert image.status_code == 200
        assert hashlib.sha256(image.content).hexdigest() == report["imageSha256"][index]
        with Image.open(BytesIO(image.content)) as loaded:
            assert (
                list(loaded.size) == report["imageSizes"][index] == [page["width"], page["height"]]
            )
    assert client.get(url).json()["revision"] == ready["revision"]
