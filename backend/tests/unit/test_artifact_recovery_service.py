from __future__ import annotations

import hashlib
from collections.abc import Callable

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import JobArtifact, JobInput, ProcessingJob
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.services.artifact_recovery_service import ArtifactRecoveryService
from app.storage.local_storage import LocalStorageService


def test_missing_artifacts_are_rebuilt_from_portable_source_blobs(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    source = b"portable source image"
    template = b"portable template image"
    job_id = "portable-artifact-job"
    artifact_path = f"artifacts/{job_id}/attempts/1/aligned_source.png"
    try:
        with database.session() as session:
            session.add(
                ProcessingJob(
                    id=job_id,
                    job_type=JobType.EXTRACT_DOCUMENT.value,
                    status=JobStatus.CONFIRMED.value,
                    request_hash="r" * 64,
                    image_sha256=hashlib.sha256(source).hexdigest(),
                    progress_stage="CONFIRMED",
                    progress_percent=100,
                    config_snapshot_json={
                        "templateSnapshot": {
                            "cleanTemplateSha256": hashlib.sha256(template).hexdigest(),
                            "cleanTemplatePath": (
                                f"uploads/{job_id}/template_snapshot/clean_template.png"
                            ),
                        }
                    },
                    template_snapshot_bytes=template,
                )
            )
            session.add(
                JobInput(
                    job_id=job_id,
                    input_index=0,
                    media_type="image/jpeg",
                    format="jpeg",
                    width=100,
                    height=200,
                    file_bytes=len(source),
                    sha256=hashlib.sha256(source).hexdigest(),
                    relative_path=f"uploads/{job_id}/original.jpg",
                    content_bytes=source,
                )
            )
            session.add(
                JobArtifact(
                    job_id=job_id,
                    attempt_number=1,
                    artifact_name="alignedSource",
                    media_type="image/png",
                    relative_path=artifact_path,
                    sha256=hashlib.sha256(b"rebuilt").hexdigest(),
                )
            )
            session.commit()

        service = ArtifactRecoveryService(
            settings=settings,
            database=database,
            storage=storage,
        )

        def rebuild(_job_id: str) -> object:
            storage.write_bytes(artifact_path, b"rebuilt")
            return object()

        monkeypatch.setattr(service._extraction, "process", rebuild)
        service.ensure_artifact(job_id, "alignedSource")

        assert storage.read_bytes(f"uploads/{job_id}/original.jpg") == source
        assert (
            storage.read_bytes(f"uploads/{job_id}/template_snapshot/clean_template.png")
            == template
        )
        assert storage.read_bytes(artifact_path) == b"rebuilt"
        with database.session() as session:
            stored_job = session.get(ProcessingJob, job_id)
            assert stored_job is not None
            assert stored_job.progress_stage == "CONFIRMED"
            assert stored_job.progress_percent == 100
    finally:
        database.dispose()


def test_recovery_rejects_a_different_source_image(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    source = b"expected source"
    job_id = "source-mismatch-job"
    try:
        with database.session() as session:
            session.add(
                ProcessingJob(
                    id=job_id,
                    job_type=JobType.EXTRACT_DOCUMENT.value,
                    status=JobStatus.CONFIRMED.value,
                    request_hash="m" * 64,
                    image_sha256=hashlib.sha256(source).hexdigest(),
                    config_snapshot_json={},
                )
            )
            session.add(
                JobInput(
                    job_id=job_id,
                    input_index=0,
                    media_type="image/jpeg",
                    format="jpeg",
                    width=100,
                    height=200,
                    file_bytes=len(source),
                    sha256=hashlib.sha256(source).hexdigest(),
                    relative_path=f"uploads/{job_id}/original.jpg",
                )
            )
            session.commit()

        service = ArtifactRecoveryService(
            settings=settings,
            database=database,
            storage=storage,
        )
        with pytest.raises(AppError) as raised:
            service.recover(job_id, source_bytes=b"different source")
        assert raised.value.code == "OCR_SOURCE_IMAGE_MISMATCH"
    finally:
        database.dispose()
