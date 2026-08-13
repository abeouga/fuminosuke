from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import ConfirmedResult, OcrResult, ProcessingJob
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.services.ocr_result_service import OcrResultService
from app.storage.local_storage import LocalStorageService


def test_confirm_api_requires_base_revision(client: TestClient) -> None:
    response = client.post("/api/v1/ocr-jobs/missing-job/confirm")

    assert response.status_code == 422


def test_confirm_rejects_a_revision_the_user_did_not_view(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    try:
        with database.session() as session:
            job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                request_hash="c" * 64,
                config_snapshot_json={},
            )
            session.add(job)
            session.flush()
            session.add(
                OcrResult(
                    job_id=job.id,
                    schema_version="1.0",
                    model_id=None,
                    prompt_version=None,
                    provider="mock",
                    raw_candidate_json={},
                    current_corrected_json={},
                    validation_json={},
                    revision=2,
                    api_call_count=0,
                    input_tokens=None,
                    output_tokens=None,
                    estimated_cost=None,
                )
            )
            session.commit()
            job_id = job.id

        with pytest.raises(AppError) as caught:
            OcrResultService(database, storage).confirm(
                job_id,
                base_revision=1,
            )

        assert caught.value.code == "REVISION_CONFLICT"
        with database.session() as session:
            job = session.get(ProcessingJob, job_id)
            assert job is not None
            assert job.status == JobStatus.REVIEW_REQUIRED.value
            assert (
                session.query(ConfirmedResult)
                .filter(ConfirmedResult.job_id == job_id)
                .one_or_none()
                is None
            )
    finally:
        database.dispose()
