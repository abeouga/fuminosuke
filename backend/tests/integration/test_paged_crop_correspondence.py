"""Pre-provider checks use accepted page ownership, not extraction assertions."""
from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob
from app.services.paged_crop_correspondence import verify_before_provider
from tests.integration.test_job_pages import accept, execution, pages_case  # noqa: F401
from tests.integration.test_print_layout_api import print_template  # noqa: F401


def test_changed_crop_never_constructs_provider(pages_case, monkeypatch):  # noqa: F811
    service, _, _ = pages_case
    job = accept(pages_case, monkeypatch)
    executor = execution(service)
    page = job.config_snapshot_json["jobPages"]["pages"][0]
    extracted = executor.extraction.process(job.id, page_id=page["pageId"])
    region_id, crop = next(iter(extracted.crops.items()))
    changed = crop.aligned_color.copy()
    changed[0, 0] ^= 255
    extracted.crops[region_id] = replace(crop, aligned_color=changed)
    calls = []

    def forbidden_provider(name):
        calls.append(name)
        raise AssertionError("Unsafe crop reached provider construction")

    monkeypatch.setattr(executor.workflow, "_create_provider", forbidden_provider)
    with pytest.raises(AppError) as caught:
        executor.workflow.process(extracted)
    assert caught.value.code == "PAGED_CROP_CORRESPONDENCE_INVALID"
    assert calls == []


def test_accepted_snapshot_and_owned_artifacts_are_required(pages_case, monkeypatch):  # noqa: F811
    service, _, _ = pages_case
    job = accept(pages_case, monkeypatch)
    executor = execution(service)
    page = job.config_snapshot_json["jobPages"]["pages"][0]
    extracted = executor.extraction.process(job.id, page_id=page["pageId"])
    calls = []

    def forbidden_provider(name):
        calls.append(name)
        raise AssertionError("Unsafe page reached provider construction")

    monkeypatch.setattr(executor.workflow, "_create_provider", forbidden_provider)
    for changed in (
        replace(extracted, page_snapshot=None),
        replace(extracted, attempt_number=extracted.attempt_number + 1),
        replace(extracted, template_version_id="other-version"),
        replace(extracted, page_snapshot={**copy.deepcopy(page), "inputId": "other-input"}),
    ):
        with pytest.raises(AppError) as caught:
            executor.workflow.process(changed)
        assert caught.value.code == "PAGED_CROP_CORRESPONDENCE_INVALID"
    with service.database.session() as session:
        row = session.get(ProcessingJob, job.id)
        report = verify_before_provider(
            extracted, session=session, job=row, pages_service=service,
            settings=service.settings, storage=service.storage,
        )
        assert report["status"] == "VERIFIED"
        assert report["productionQualified"] is False
        artifact = session.scalar(select(JobArtifact).where(
            JobArtifact.job_id == job.id,
            JobArtifact.artifact_name == f"page.{page['pageId']}.alignedSource",
        ))
        path, sha = artifact.relative_path, artifact.sha256
        content = service.storage.read_bytes(path)
    service.storage.write_bytes(path, b"changed")
    with pytest.raises(AppError) as caught:
        executor.workflow.process(extracted)
    assert caught.value.code == "PAGED_CROP_CORRESPONDENCE_INVALID"
    assert caught.value.metadata["reason"] == "PAGE_ARTIFACT_CHANGED"
    service.storage.write_bytes(path, content)
    with service.database.session() as session:
        artifact = session.scalar(select(JobArtifact).where(
            JobArtifact.job_id == job.id,
            JobArtifact.artifact_name == f"page.{page['pageId']}.alignedSource",
        ))
        artifact.relative_path = f"uploads/{job.id}/other-page.png"
        service.storage.write_bytes(artifact.relative_path, content)
        assert artifact.sha256 == sha  # Same bytes do not override artifact ownership.
        session.commit()
    with pytest.raises(AppError) as caught:
        executor.workflow.process(extracted)
    assert caught.value.code == "PAGED_CROP_CORRESPONDENCE_INVALID"
    assert caught.value.metadata["reason"] == "PAGE_ARTIFACT_UNAVAILABLE"
    assert calls == []


def test_page_failure_keeps_reason_and_retry_preserves_success(pages_case, monkeypatch):  # noqa: F811
    service, _, _ = pages_case
    job = accept(pages_case, monkeypatch)
    executor = execution(service)
    pages = job.config_snapshot_json["jobPages"]["pages"]
    failed_page = pages[0]["pageId"]
    process = executor.extraction.process

    def changed_crop(job_id, *, page_id):
        result = process(job_id, page_id=page_id)
        if page_id == failed_page:
            region_id, crop = next(iter(result.crops.items()))
            result.crops[region_id] = replace(crop, bbox=(0, 0, *crop.bbox[2:]))
        return result

    monkeypatch.setattr(executor.extraction, "process", changed_crop)
    with pytest.raises(AppError) as caught:
        executor.execute(job.id)
    assert caught.value.code == "MULTI_PAGE_PARTIAL_FAILURE"
    with service.database.session() as session:
        artifacts = list(session.scalars(select(JobArtifact).where(
            JobArtifact.job_id == job.id, JobArtifact.artifact_name.like("pageResult.%"),
        )))
        results = {a.artifact_name: json.loads(service.storage.read_bytes(a.relative_path))
                   for a in artifacts}
        failed = results[f"pageResult.{failed_page}"]
        assert failed["status"] == "FAILED"
        assert failed["errorCode"] == "PAGED_CROP_CORRESPONDENCE_INVALID"
        assert failed["safetyFailure"]["reason"] == "CROP_GEOMETRY_CHANGED"
        assert failed["safetyFailure"]["pageId"] == failed_page
        successful = results[f"pageResult.{pages[1]['pageId']}"]
        assert successful["status"] == "SUCCESS"
        successful_sha = next(
            a.sha256 for a in artifacts if a.artifact_name.endswith(pages[1]["pageId"])
        )
        row = session.get(ProcessingJob, job.id)
        row.attempt_number += 1
        session.commit()

    retried = []

    def retry(job_id, *, page_id):
        retried.append(page_id)
        return process(job_id, page_id=page_id)

    monkeypatch.setattr(executor.extraction, "process", retry)
    executor.execute(job.id)
    assert retried == [failed_page]
    with service.database.session() as session:
        success_artifacts = list(session.scalars(select(JobArtifact).where(
            JobArtifact.job_id == job.id,
            JobArtifact.artifact_name == f"pageResult.{pages[1]['pageId']}",
        )))
        assert len(success_artifacts) == 1 and success_artifacts[0].sha256 == successful_sha
