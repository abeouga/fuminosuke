from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from io import BytesIO

import pytest
import yaml
from PIL import Image
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.domain.job_pages import PagedSubmission, template_page_id
from app.domain.ocr_factory import blank_ocr_document
from app.domain.ocr_models import BoundingBox, FieldReading
from app.domain.template_config import FieldMappingConfig, parse_template_config
from app.jobs.runner import JobRunner
from app.ocr.base import OcrProviderResponse
from app.ocr.deterministic_mapper import assign_mapped_reading
from app.services.extraction_service import ImageExtractionService
from app.services.job_page_service import JobPageService
from app.services.job_service import JobService, UploadPayload
from app.services.ocr_workflow_service import OcrWorkflowService
from app.services.paged_ocr_execution import PagedOcrExecution
from tests.integration.test_print_layout_api import print_template  # noqa: F401


@pytest.fixture
def pages_case(client, print_template):  # noqa: F811 - imported pytest fixture
    version_id = print_template[0]
    state = client.app.state
    with state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        config = parse_template_config(
            state.storage.read_bytes(version.config_relative_path).decode()
        )
        cell = next(c for c in version.layout_json["cells"] if c["id"] == "xlsx_C2")
        config.field_mappings.append(
            FieldMappingConfig(
                mapping_id="layout_xlsx_C2",
                target_path="document.cattleName",
                label="Name",
                **{k: cell[k] for k in ("x", "y", "width", "height")},
            )
        )
        state.storage.write_text(
            version.config_relative_path,
            yaml.safe_dump(config.model_dump(mode="json", by_alias=True)),
        )
    url = f"/api/v1/template-versions/{version_id}/print-layout"
    before = client.get(url).json()
    response = client.put(url, json={"baseRevision": before["revision"], "breakAfterRows": [31]})
    assert response.status_code == 200, response.text
    with state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        version.status = "ACTIVE"
        version.quality_json = {**version.quality_json, "layoutVerified": True}
        session.commit()
        plan = copy.deepcopy(version.quality_json["printLayout"])
        revision = JobPageService(state.database, state.storage, state.settings).revision(version)
    images = [state.storage.read_bytes(p["referenceImage"]["relativePath"]) for p in plan["pages"]]
    bindings = [
        dict(
            templatePageId=template_page_id(version_id, p),
            inputId=f"input-{i}",
            inputRevision=1,
            inputSha256=hashlib.sha256(images[i]).hexdigest(),
        )
        for i, p in enumerate(plan["pages"])
    ]
    manifest = dict(
        templateVersionId=version_id,
        templateRevision=revision,
        bindings=bindings,
        uploadIds=["input-0", "input-1"],
    )
    return JobPageService(state.database, state.storage, state.settings), manifest, images


def accept(case, monkeypatch, **changes):
    service, manifest, images = case
    # Synthetic execution fixture only; formal admission is tested separately.
    monkeypatch.setattr(service, "_require_qualified_reference", lambda session, version: None)
    body = {**copy.deepcopy(manifest), **changes}
    return service.accept(
        PagedSubmission.model_validate(body), [UploadPayload(b, "image/png") for b in images]
    )


def test_unqualified_reference_is_not_activated(client, pages_case):
    service, manifest, images = pages_case
    with pytest.raises(AppError, match="位置精度") as caught:
        service.accept(
            PagedSubmission.model_validate(manifest),
            [UploadPayload(b, "image/png") for b in images],
        )
    assert caught.value.code == "PRINT_REFERENCE_NOT_VALIDATED"
    response = client.post(
        "/api/v1/ocr-page-jobs",
        data={"manifest": json.dumps(manifest)},
        files=[("files", (f"{i}.png", b, "image/png")) for i, b in enumerate(images)],
    )
    assert response.status_code == 409
    assert response.json()["code"] == "PRINT_REFERENCE_NOT_VALIDATED"


@pytest.mark.parametrize("kind", ["missing", "duplicate", "unknown", "stale", "input_stale"])
def test_invalid_acceptance(pages_case, monkeypatch, kind):
    service, manifest, images = pages_case
    body = copy.deepcopy(manifest)
    if kind == "missing":
        body["bindings"].pop()
    elif kind == "duplicate":
        body["bindings"][1]["inputId"] = body["bindings"][0]["inputId"]
    elif kind == "unknown":
        body["bindings"][1]["templatePageId"] = "unknown"
    elif kind == "stale":
        body["templateRevision"] = "0" * 64
    else:
        body["bindings"][1]["inputSha256"] = "0" * 64
    with pytest.raises(AppError):
        accept((service, body, images), monkeypatch)


def test_reversed_uploads_and_immutable_snapshots(pages_case, monkeypatch):
    service, manifest, images = pages_case
    manifest["uploadIds"].reverse()
    job = accept((service, manifest, list(reversed(images))), monkeypatch)
    snapshot = copy.deepcopy(job.config_snapshot_json)
    with service.database.session() as session:
        pages = service.validate(session, job)
        assert [p["clientInputId"] for p in pages] == ["input-0", "input-1"]
        assert [p["fieldMappings"][0]["mappingId"] for p in pages] == [
            "layout_xlsx_C2",
            "layout_xlsx_C32",
        ]
        assert all(p["fieldMappings"][0]["y"] < 250 for p in pages)
        version = session.get(TemplateVersion, manifest["templateVersionId"])
        version.quality_json = {}
        service.storage.write_text(version.config_relative_path, "changed")
        session.commit()
        assert service.validate(session, job) == pages
        assert session.get(ProcessingJob, job.id).config_snapshot_json == snapshot


def test_image_sha_change_prevents_all_execution(pages_case, monkeypatch):
    service, _, _ = pages_case
    job = accept(pages_case, monkeypatch)
    page = job.config_snapshot_json["jobPages"]["pages"][1]
    service.storage.write_bytes(page["templateSnapshot"]["cleanTemplatePath"], b"changed")
    with service.database.session() as session, pytest.raises(AppError) as caught:
        service.validate(session, job)
    assert caught.value.code == "JOB_PAGE_IMAGE_CHANGED"


def execution(service):
    extraction = ImageExtractionService(
        settings=service.settings, database=service.database, storage=service.storage
    )
    workflow = OcrWorkflowService(
        settings=service.settings, database=service.database, storage=service.storage
    )
    class PageFixtureProvider:
        async def extract(self, request):
            # The generic mock-result.json is a cattle-ledger document with unrelated
            # values and no crop provenance. Use request-bound readings for page tests.
            document = blank_ocr_document(provider="mock", source_regions=[])
            for region in request.regions:
                assert region.target_path and region.bounding_box
                x, y, width, height = region.bounding_box
                assign_mapped_reading(document, region.target_path, FieldReading(
                    raw=None, normalized=None, status="blank",
                    source_region_id=region.source_region_id or region.region_id,
                    bounding_box=BoundingBox(x=x, y=y, width=width, height=height),
                ))
            return OcrProviderResponse(
                document=document, provider="mock", model_id="page-fixture",
                prompt_version="fixture", api_call_count=0,
            )
    workflow._create_provider = lambda name: PageFixtureProvider()
    return PagedOcrExecution(service, extraction, workflow)


def test_all_pages_use_existing_pipeline_and_result_boundary(client, pages_case, monkeypatch):
    service, _, _ = pages_case
    job = accept(pages_case, monkeypatch)
    executor = execution(service)
    executor.execute(job.id)
    response = client.get(f"/api/v1/ocr-jobs/{job.id}/page-results")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed"
    assert len(result["pages"]) == 2
    assert all(p["status"] == "SUCCESS" for p in result["pages"])
    assert all(p["extraction"]["apiCallCount"] == 0 for p in result["pages"])
    with service.database.session() as session:
        crops = list(
            session.scalars(
                select(JobArtifact).where(
                    JobArtifact.job_id == job.id,
                    JobArtifact.artifact_name.like("page.%.fieldResults"),
                )
            )
        )
        assert len(crops) == 2
        for artifact in crops:
            data = json.loads(service.storage.read_bytes(artifact.relative_path))
            assert len(data["regions"]) == 1
            page = next(
                p
                for p in job.config_snapshot_json["jobPages"]["pages"]
                if p["pageId"] in artifact.artifact_name
            )
            mapping = page["fieldMappings"][0]
            rect = {k: mapping[k] for k in ("x", "y", "width", "height")}
            padding = mapping["padding"]
            assert data["regions"][mapping["mappingId"]]["bbox"] == [
                rect["x"] - padding,
                rect["y"] - padding,
                rect["width"] + 2 * padding,
                rect["height"] + 2 * padding,
            ]
            with Image.open(
                BytesIO(service.storage.read_bytes(page["templateSnapshot"]["cleanTemplatePath"]))
            ) as reference:
                # All four field edges lie on rasterized source-cell borders: 0 px.
                x, y, w, h = (rect[k] for k in ("x", "y", "width", "height"))
                for point in (
                    (x, y + h // 2),
                    (x + w, y + h // 2),
                    (x + w // 2, y),
                    (x + w // 2, y + h),
                ):
                    assert reference.getpixel(point) == (34, 34, 34)
    assert (
        client.get(f"/api/v1/ocr-jobs/{job.id}/result").json()["code"]
        == "MULTI_PAGE_REVIEW_NOT_AVAILABLE"
    )
    assert (
        client.get(f"/api/v1/ocr-jobs/{job.id}/export").json()["code"]
        == "MULTI_PAGE_OUTPUT_NOT_AVAILABLE"
    )
    assert (
        client.post(f"/api/v1/ocr-jobs/{job.id}/confirm", json={"baseRevision": 1}).json()["code"]
        == "MULTI_PAGE_REVIEW_NOT_AVAILABLE"
    )


def test_page_failure_and_retry_keep_success(pages_case, monkeypatch):
    service, _, _ = pages_case
    job = accept(pages_case, monkeypatch)
    executor = execution(service)
    runner = JobRunner(
        database=service.database,
        storage=service.storage,
        settings=service.settings,
        handler=executor.execute,
        worker_count=1,
    )
    jobs = JobService(
        database=service.database, storage=service.storage, settings=service.settings, runner=runner
    )
    accepted_snapshot = copy.deepcopy(job.config_snapshot_json)
    original = executor.workflow.process
    calls = []

    def fail_second(extraction):
        number = extraction.page_snapshot["pageNumber"]
        calls.append(number)
        if number == 2:
            raise RuntimeError("offline failure")
        return original(extraction)

    monkeypatch.setattr(executor.workflow, "process", fail_second)
    asyncio.run(runner._execute(job.id))
    assert jobs.get(job.id).status == "FAILED"
    assert jobs.get(job.id).error_code == "MULTI_PAGE_PARTIAL_FAILURE"
    assert calls == [1, 2]
    calls.clear()

    def succeed(extraction):
        calls.append(extraction.page_snapshot["pageNumber"])
        return original(extraction)

    monkeypatch.setattr(executor.workflow, "process", succeed)
    with service.database.session() as session:
        version = session.get(TemplateVersion, job.template_version_id)
        version.quality_json = {}
        service.storage.write_text(version.config_relative_path, "new version data")
        session.commit()
    asyncio.run(jobs.retry(job.id))
    asyncio.run(runner._execute(job.id))
    assert calls == [2]
    assert jobs.get(job.id).status == "REVIEW_REQUIRED"
    assert jobs.get(job.id).config_snapshot_json == accepted_snapshot
