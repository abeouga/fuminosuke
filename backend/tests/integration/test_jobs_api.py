import io
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.v1.routes import templates as template_routes
from app.core.config import Settings
from app.db.base import Base
from app.db.models import ProcessingJob, Template
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode
from app.main import create_app
from app.repositories.job_repository import JobRepository


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 240), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_template_upload_is_queued_and_processed(client: TestClient) -> None:
    response = client.post(
        "/api/v1/template-jobs",
        data={"name": "テスト帳票", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("ignored-original-name.png", _png(), "image/png"))],
        headers={"Idempotency-Key": "template-test-1"},
    )

    assert response.status_code == 202
    job_id = response.json()["jobId"]
    detail: dict[str, object] = {}
    for _ in range(500):
        detail = client.get(f"/api/v1/jobs/{job_id}").json()
        if detail["status"] == "REVIEW_REQUIRED":
            break
        time.sleep(0.01)
    assert detail["status"] == "REVIEW_REQUIRED"

    template = client.get("/api/v1/templates").json()[0]
    assert template["documentType"] == "generic-document"
    version = client.get(f"/api/v1/templates/{template['id']}").json()["versions"][0]
    with client.app.state.database.session() as session:
        from app.db.models import TemplateVersion
        from app.domain.template_config import parse_template_config

        saved = session.get(TemplateVersion, version["id"])
        assert saved is not None
        config = parse_template_config(
            client.app.state.storage.read_bytes(saved.config_relative_path).decode("utf-8")
        )
    assert config.regions == []
    assert config.field_mappings == []
    assert config.marks == []

    listed = client.get("/api/v1/jobs", params={"limit": 10})
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == job_id

    manifest = client.app.state.storage.read_bytes(f"uploads/{job_id}/manifest.json")
    assert b"ignored-original-name" not in manifest

    protected_delete = client.delete(f"/api/v1/jobs/{job_id}")
    assert protected_delete.status_code == 409
    assert protected_delete.json()["code"] == "JOB_IN_USE"

    reprocessed = client.post(f"/api/v1/template-versions/{version['id']}/reprocess")
    assert reprocessed.status_code == 202
    for _ in range(500):
        detail = client.get(f"/api/v1/jobs/{job_id}").json()
        if detail["status"] in {"REVIEW_REQUIRED", "FAILED"}:
            break
        time.sleep(0.01)
    assert detail["status"] == "REVIEW_REQUIRED"
    versions = client.get(f"/api/v1/templates/{template['id']}").json()["versions"]
    assert len(versions) == 2
    with client.app.state.database.session() as session:
        regenerated = session.get(TemplateVersion, versions[0]["id"])
        assert regenerated is not None
        new_config = parse_template_config(
            client.app.state.storage.read_bytes(regenerated.config_relative_path).decode("utf-8")
        )
    assert new_config.regions == []
    assert new_config.marks == []


def test_attention_only_lists_old_review_jobs_beyond_the_recent_limit(
    client: TestClient,
) -> None:
    old_created_at = datetime(2025, 1, 1, tzinfo=UTC)
    review_jobs = [
        ProcessingJob(
            id="old-ocr-review",
            job_type=JobType.EXTRACT_DOCUMENT.value,
            status=JobStatus.REVIEW_REQUIRED.value,
            request_hash="ocr-review",
            config_snapshot_json={},
            created_at=old_created_at,
        ),
        ProcessingJob(
            id="old-template-review",
            job_type=JobType.PREPARE_TEMPLATE.value,
            status=JobStatus.REVIEW_REQUIRED.value,
            request_hash="template-review",
            config_snapshot_json={},
            created_at=old_created_at - timedelta(seconds=1),
        ),
    ]
    recent_confirmed = [
        ProcessingJob(
            id=f"recent-confirmed-{index:03d}",
            job_type=JobType.EXTRACT_DOCUMENT.value,
            status=JobStatus.CONFIRMED.value,
            request_hash=f"confirmed-{index}",
            config_snapshot_json={},
            created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
        )
        for index in range(101)
    ]
    with client.app.state.database.session() as session:
        session.add_all([*review_jobs, *recent_confirmed])
        session.commit()

    recent_response = client.get("/api/v1/jobs", params={"limit": 100})
    assert recent_response.status_code == 200
    recent_ids = {item["id"] for item in recent_response.json()}
    assert "old-ocr-review" not in recent_ids
    assert "old-template-review" not in recent_ids

    attention_response = client.get(
        "/api/v1/jobs",
        params={"limit": 1000, "attentionOnly": True},
    )
    assert attention_response.status_code == 200
    attention = attention_response.json()
    assert {item["id"] for item in attention} == {
        "old-ocr-review",
        "old-template-review",
    }
    assert {item["jobType"] for item in attention} == {
        JobType.EXTRACT_DOCUMENT.value,
        JobType.PREPARE_TEMPLATE.value,
    }


def test_cancel_is_rejected_after_template_version_persistence(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/template-jobs",
        data={"name": "保存境界テスト", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("source.png", _png(), "image/png"))],
    )
    assert response.status_code == 202
    job_id = response.json()["jobId"]
    for _ in range(500):
        detail = client.get(f"/api/v1/jobs/{job_id}").json()
        if detail["status"] == JobStatus.REVIEW_REQUIRED.value:
            break
        time.sleep(0.01)
    assert detail["status"] == JobStatus.REVIEW_REQUIRED.value
    with client.app.state.database.session() as session:
        JobRepository(session).update(
            job_id,
            status=JobStatus.PROCESSING,
            cancel_requested=False,
        )

    cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert cancelled.status_code == 409
    assert cancelled.json()["code"] == "TEMPLATE_PERSISTENCE_COMPLETE"
    with client.app.state.database.session() as session:
        persisted = JobRepository(session).get(job_id)
        assert persisted is not None
        assert persisted.cancel_requested is False


def test_reprocess_can_be_cancelled_before_a_new_version_is_persisted(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/template-jobs",
        data={"name": "再生成取消テスト", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("source.png", _png(), "image/png"))],
    )
    assert response.status_code == 202
    job_id = response.json()["jobId"]
    for _ in range(500):
        detail = client.get(f"/api/v1/jobs/{job_id}").json()
        if detail["status"] == JobStatus.REVIEW_REQUIRED.value:
            break
        time.sleep(0.01)
    assert detail["status"] == JobStatus.REVIEW_REQUIRED.value
    template_summary = next(
        item
        for item in client.get("/api/v1/templates").json()
        if item["name"] == "再生成取消テスト"
    )
    template = client.get(
        f"/api/v1/templates/{template_summary['id']}"
    ).json()
    version_id = template["versions"][0]["id"]
    with client.app.state.database.session() as session:
        repository = JobRepository(session)
        job = repository.get(job_id)
        assert job is not None
        snapshot = dict(job.config_snapshot_json)
        snapshot["reprocessTemplateVersionId"] = version_id
        repository.update(
            job_id,
            status=JobStatus.PROCESSING,
            cancel_requested=False,
            config_snapshot_json=snapshot,
        )

    cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert cancelled.status_code == 200
    with client.app.state.database.session() as session:
        persisted = JobRepository(session).get(job_id)
        assert persisted is not None
        assert persisted.cancel_requested is True
        assert persisted.status == JobStatus.PROCESSING


def test_idempotency_key_returns_existing_job(client: TestClient) -> None:
    request = {
        "data": {"name": "同一帳票", "mode": "REFERENCE_TEMPLATE"},
        "files": [("files", ("one.png", _png(), "image/png"))],
        "headers": {"Idempotency-Key": "same-request"},
    }
    first = client.post("/api/v1/template-jobs", **request)
    second = client.post("/api/v1/template-jobs", **request)

    assert first.status_code == second.status_code == 202
    assert first.json()["jobId"] == second.json()["jobId"]


def test_rejects_non_image_upload(client: TestClient) -> None:
    response = client.post(
        "/api/v1/template-jobs",
        data={"name": "不正", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("payload.txt", b"not an image", "text/plain"))],
    )

    assert response.status_code == 422
    assert response.json()["code"] == "UNSUPPORTED_IMAGE_FORMAT"
    assert "traceback" not in response.text.lower()


def test_rejects_removed_multi_scan_mode_before_reading_upload(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_called = False

    async def unexpected_read(*_args: object, **_kwargs: object) -> None:
        nonlocal read_called
        read_called = True
        raise AssertionError("file content must not be read before count validation")

    monkeypatch.setattr(template_routes, "read_upload", unexpected_read)
    response = client.post(
        "/api/v1/template-jobs",
        data={"name": "廃止モード", "mode": "MULTI_SCAN_CONSENSUS"},
        files=[("files", ("scan.png", _png(), "image/png"))],
    )

    assert response.status_code == 422
    assert read_called is False


def test_failed_empty_template_does_not_block_recovery(
    client: TestClient,
) -> None:
    with client.app.state.database.session() as session:
        orphan = Template(
            name="復旧可能テンプレート",
            document_type="generic-document",
        )
        session.add(orphan)
        session.commit()
        orphan_id = orphan.id
    client.app.state.storage.write_text(
        f"templates/{orphan_id}/partial.txt",
        "incomplete",
    )

    response = client.post(
        "/api/v1/template-jobs",
        data={"name": "復旧可能テンプレート", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("replacement.png", _png(), "image/png"))],
    )

    assert response.status_code == 202
    with client.app.state.database.session() as session:
        assert session.get(Template, orphan_id) is None
    assert not client.app.state.storage.exists(f"templates/{orphan_id}")


def test_normal_failed_job_can_still_be_retried(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueued: list[str] = []

    async def record_enqueue(job_id: str) -> None:
        enqueued.append(job_id)

    monkeypatch.setattr(client.app.state.job_runner, "enqueue", record_enqueue)
    with client.app.state.database.session() as session:
        repository = JobRepository(session)
        failed = repository.create(
            job_type=JobType.PREPARE_TEMPLATE,
            request_hash="e" * 64,
            config_snapshot={
                "name": "通常失敗の再試行",
                "mode": TemplateMode.REFERENCE_TEMPLATE.value,
            },
            status=JobStatus.FAILED,
            mode=TemplateMode.REFERENCE_TEMPLATE,
        )
        repository.update(
            failed.id,
            error_code="PROCESSING_FAILED",
            error_message_safe="処理に失敗しました。再試行できます。",
        )
        failed_id = failed.id

    response = client.post(f"/api/v1/jobs/{failed_id}/retry")

    assert response.status_code == 200
    assert response.json()["status"] == JobStatus.QUEUED.value
    assert response.json()["attemptNumber"] == 2
    assert response.json()["error"] is None
    assert enqueued == [failed_id]


def test_ocr_job_without_an_accepted_template_snapshot_cannot_be_retried(
    client: TestClient,
) -> None:
    with client.app.state.database.session() as session:
        repository = JobRepository(session)
        failed = repository.create(
            job_type=JobType.EXTRACT_DOCUMENT,
            request_hash="f" * 64,
            config_snapshot={
                "templateVersionId": "legacy-version",
                "motherIndividualId": "1234567890",
                "ocrEnabled": True,
            },
            status=JobStatus.FAILED,
        )
        repository.update(
            failed.id,
            error_code="PROCESSING_FAILED",
            error_message_safe="処理に失敗しました。",
        )
        failed_id = failed.id

    response = client.post(f"/api/v1/jobs/{failed_id}/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "OCR_TEMPLATE_SNAPSHOT_INVALID"
    assert "安全に再試行できません" in response.json()["detail"]
    persisted = client.get(f"/api/v1/jobs/{failed_id}").json()
    assert persisted["status"] == "FAILED"
    assert persisted["attemptNumber"] == 1


def test_startup_releases_name_reserved_by_incomplete_created_job(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        incomplete = JobRepository(session).create(
            job_type=JobType.PREPARE_TEMPLATE,
            request_hash="d" * 64,
            config_snapshot={
                "name": "停止から再登録",
                "mode": TemplateMode.REFERENCE_TEMPLATE.value,
            },
            mode=TemplateMode.REFERENCE_TEMPLATE,
        )
        incomplete_id = incomplete.id
    database.dispose()

    with TestClient(create_app(settings)) as test_client:
        recovered = test_client.get(f"/api/v1/jobs/{incomplete_id}")
        retry = test_client.post(f"/api/v1/jobs/{incomplete_id}/retry")
        replacement = test_client.post(
            "/api/v1/template-jobs",
            data={"name": "停止から再登録", "mode": "REFERENCE_TEMPLATE"},
            files=[("files", ("replacement.png", _png(), "image/png"))],
        )

        assert recovered.status_code == 200
        assert recovered.json()["status"] == "FAILED"
        assert recovered.json()["error"]["code"] == "INCOMPLETE_JOB_INPUTS"
        assert retry.status_code == 409
        assert retry.json()["code"] == "INCOMPLETE_JOB_INPUTS"
        assert "元画像が保存されていません" in retry.json()["detail"]
        still_failed = test_client.get(f"/api/v1/jobs/{incomplete_id}").json()
        assert still_failed["status"] == "FAILED"
        assert still_failed["attemptNumber"] == 1
        assert replacement.status_code == 202
