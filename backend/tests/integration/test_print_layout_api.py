from __future__ import annotations

import copy
import hashlib
from io import BytesIO

import pytest
import yaml
from PIL import Image
from sqlalchemy import select

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.domain.print_layout import print_error
from app.domain.template_config import FieldMappingConfig, parse_template_config
from app.services.spreadsheet_template_import_service import SpreadsheetTemplateImportService
from tests.unit.test_print_layout import workbook_bytes


@pytest.fixture
def print_template(client, monkeypatch):
    def render(self, content, **kwargs):
        _, _, _, image = self._parse_workbook(content)
        return b"stub PDF (not used for page rendering)", image, 1

    monkeypatch.setattr(SpreadsheetTemplateImportService, "_render_workbook_preview", render)
    content = workbook_bytes()
    response = client.post(
        "/api/v1/spreadsheet-template-jobs",
        data={"name": "改ページ検証"},
        files={
            "file": (
                "source.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["jobId"]
    with client.app.state.database.session() as session:
        job = session.get(ProcessingJob, job_id)
        version = session.get(TemplateVersion, job.template_version_id)
        config = parse_template_config(
            client.app.state.storage.read_bytes(version.config_relative_path).decode("utf-8")
        )
        cell = next(c for c in version.layout_json["cells"] if c["id"] == "xlsx_C32")
        config = config.model_copy(
            update={
                "field_mappings": [
                    FieldMappingConfig(
                        mapping_id="layout_xlsx_C32",
                        target_path="document.unifiedEarTag",
                        label="識別番号",
                        **{k: cell[k] for k in ("x", "y", "width", "height")},
                    )
                ]
            }
        )
        client.app.state.storage.write_text(
            version.config_relative_path,
            yaml.safe_dump(config.model_dump(mode="json", by_alias=True), allow_unicode=True),
        )
        return version.id, job_id, content, copy.deepcopy(version.layout_json)


def body(state, breaks=None):
    return {"baseRevision": state["revision"], "breakAfterRows": [31] if breaks is None else breaks}


def test_save_raster_snapshot_and_legacy_data_preserved(client, print_template):
    version_id, job_id, content, legacy_layout = print_template
    url = f"/api/v1/template-versions/{version_id}/print-layout"
    state = client.get(url).json()
    assert state["source"]["unsupported"] == []
    assert state["saved"] is None
    preview = client.post(url + "/preview", json=body(state))
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["pages"]) == 2
    # Existing OCR jobs are not reconstructed from the draft or its new metadata.
    historical = {"templateSnapshot": {"old": True}, "fieldMappings": [{"mappingId": "old"}]}
    with client.app.state.database.session() as session:
        old_job = ProcessingJob(
            job_type="EXTRACT_DOCUMENT",
            mode="SPREADSHEET_TEMPLATE",
            status="REVIEW_REQUIRED",
            template_version_id=version_id,
            request_hash="b" * 64,
            config_snapshot_json=historical,
        )
        session.add(old_job)
        session.commit()
        old_id = old_job.id
    saved = client.put(url, json=body(state))
    assert saved.status_code == 200, saved.text
    saved_state = saved.json()
    assert saved_state["revision"] != state["revision"]
    plan = saved_state["saved"]
    assert plan["ocrReady"] is False
    assert plan["sourceSha256"] == hashlib.sha256(content).hexdigest()
    bound = [f for p in plan["pages"] for f in p["fields"] if f["bindings"]]
    assert len(bound) == 1
    assert bound[0]["templatePage"] == 2
    assert bound[0]["sourceRange"] == "C32:C32"
    assert bound[0]["bindings"] == [
        {
            "mappingId": "layout_xlsx_C32",
            "sourceRegionId": "layout_xlsx_C32",
            "targetPath": "document.unifiedEarTag",
            "mappingType": "text",
        }
    ]
    for page in plan["pages"]:
        image = client.get(url + f"/pages/{page['page']}/image")
        assert image.status_code == 200
        assert hashlib.sha256(image.content).hexdigest() == page["referenceImage"]["sha256"]
        with Image.open(BytesIO(image.content)) as decoded:
            assert decoded.size == (page["width"], page["height"])
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        assert version.layout_json == legacy_layout
        assert session.get(ProcessingJob, job_id).config_snapshot_json["printLayout"] == plan
        assert session.get(ProcessingJob, old_id).config_snapshot_json == historical
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
    second = client.put(url, json=body(saved_state, [30]))
    assert second.status_code == 200
    assert second.json()["saved"]["pages"][1]["startRow"] == 31
    assert plan["pages"][1]["startRow"] == 32
    cleared = client.delete(url, params={"base_revision": second.json()["revision"]})
    assert cleared.status_code == 200
    assert cleared.json()["saved"] is None
    with client.app.state.database.session() as session:
        assert session.get(TemplateVersion, version_id).layout_json == legacy_layout
        assert "printLayout" not in session.get(ProcessingJob, job_id).config_snapshot_json
        assert session.get(ProcessingJob, old_id).config_snapshot_json == historical


def test_conflict_invalid_and_published_mutation_rejected(client, print_template):
    version_id, _, _, _ = print_template
    url = f"/api/v1/template-versions/{version_id}/print-layout"
    state = client.get(url).json()
    bad = client.put(url, json=body(state, [31, 31]))
    assert bad.status_code == 422
    assert client.get(url).json()["saved"] is None
    saved = client.put(url, json=body(state)).json()
    assert client.put(url, json=body(state)).status_code == 409
    assert client.delete(url, params={"base_revision": state["revision"]}).status_code == 409
    assert client.get(url).json()["saved"] == saved["saved"]
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        version.status = "ACTIVE"
        session.commit()
    published = client.put(url, json=body(saved))
    assert published.status_code == 409
    assert published.json()["code"] == "TEMPLATE_VERSION_IMMUTABLE"


def test_render_failure_does_not_commit_partial_plan(client, print_template, monkeypatch):
    version_id, job_id, _, _ = print_template
    url = f"/api/v1/template-versions/{version_id}/print-layout"
    state = client.get(url).json()
    from app.services import print_layout_service

    calls = 0
    real = print_layout_service.render_page

    def fail_second(page):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise print_error("描画失敗", status=503)
        return real(page)

    monkeypatch.setattr(print_layout_service, "render_page", fail_second)
    result = client.put(url, json=body(state))
    assert result.status_code == 503
    assert client.get(url).json()["saved"] is None
    with client.app.state.database.session() as session:
        assert "printLayout" not in session.get(ProcessingJob, job_id).config_snapshot_json
        assert not any(
            a.artifact_name.startswith("print-reference-")
            for a in session.scalars(select(JobArtifact).where(JobArtifact.job_id == job_id))
        )


def test_manual_geometry_never_guessed_and_stale_image_rejected(client, print_template):
    version_id, _, _, _ = print_template
    url = f"/api/v1/template-versions/{version_id}/print-layout"
    state = client.get(url).json()
    assert client.put(url, json=body(state)).status_code == 200
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        layout = copy.deepcopy(version.layout_json)
        layout["cells"][0]["x"] += 1
        version.layout_json = layout
        session.commit()
    updated = client.get(url).json()
    assert updated["source"]["unsupported"]
    assert client.get(url + "/pages/1/image").json()["code"] == "PRINT_LAYOUT_STALE"
    assert client.put(url, json=body(updated)).status_code == 422


def test_active_paged_template_cannot_silently_run_page_one(client, print_template):
    version_id, _, _, _ = print_template
    url = f"/api/v1/template-versions/{version_id}/print-layout"
    state = client.get(url).json()
    assert client.put(url, json=body(state)).status_code == 200
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        version.status = "ACTIVE"
        session.commit()
    out = BytesIO()
    Image.new("RGB", (1240, 1754), "white").save(out, format="PNG")
    response = client.post(
        "/api/v1/ocr-jobs",
        data={"templateVersionId": version_id},
        files={"file": ("page.png", out.getvalue(), "image/png")},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "PRINT_LAYOUT_OCR_NOT_AVAILABLE"


def test_fork_copies_page_artifacts_and_snapshot_without_changing_v1(client, print_template):
    from app.db.models import Template
    from app.services.template_version_service import TemplateVersionService

    version_id, _, _, _ = print_template
    url = f"/api/v1/template-versions/{version_id}/print-layout"
    state = client.get(url).json()
    saved = client.put(url, json=body(state)).json()["saved"]
    database, storage = client.app.state.database, client.app.state.storage
    # Exercise cloning defensively for imported/future persisted page metadata.
    # Current public activation intentionally refuses these plans.
    with database.session() as session:
        version = session.get(TemplateVersion, version_id)
        version.status = "ACTIVE"
        session.get(Template, version.template_id).active_version_id = version_id
        session.commit()
    fork = TemplateVersionService(database, storage).edit(version_id)
    cloned = client.get(f"/api/v1/template-versions/{fork.id}/print-layout").json()
    assert cloned["source"]["unsupported"] == []
    for original, page in zip(saved["pages"], cloned["saved"]["pages"], strict=True):
        assert original["range"] == page["range"]
        assert original["transform"] == page["transform"]
        assert original["referenceImage"]["relativePath"] != page["referenceImage"]["relativePath"]
        assert original["referenceImage"]["artifactId"] != page["referenceImage"]["artifactId"]
        assert original["referenceImage"]["sha256"] == page["referenceImage"]["sha256"]
        assert original["fields"][0]["cellId"] != page["fields"][0]["cellId"]
    with database.session() as session:
        assert session.get(TemplateVersion, version_id).quality_json["printLayout"] == saved
        assert (
            session.get(ProcessingJob, fork.source_job_id).config_snapshot_json["printLayout"]
            == cloned["saved"]
        )
    assert (
        client.get(f"/api/v1/template-versions/{fork.id}/print-layout/pages/2/image").status_code
        == 200
    )
    assert client.get(f"/api/v1/template-versions/{fork.id}/print-pdf").status_code == 409
    html = client.get(f"/api/v1/template-versions/{fork.id}/print").text
    assert "Page 2" in html and "/print-layout/pages/2/image" in html
