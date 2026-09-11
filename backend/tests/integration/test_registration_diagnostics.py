import asyncio
import hashlib
import json
from collections.abc import Callable

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.models import JobArtifact, JobInput, ProcessingJob
from app.db.session import Database
from app.domain.template_config import FieldMappingConfig, load_template_config
from app.jobs.runner import JobRunner
from app.services.extraction_service import ImageExtractionService
from app.storage.local_storage import LocalStorageService
from tests.synthetic.image_factory import encode_png, ledger_image


@pytest.mark.parametrize("attempt", [1, 2])
def test_failed_registration_persists_snapshot_diagnostics_before_cropping(
    settings_factory: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch, attempt: int,
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    job_id = "registration-diagnostic-job"
    reference = cv2.resize(ledger_image(), (1240, 1754))
    source = np.full_like(reference, 255)
    reference_bytes, input_bytes = encode_png(reference), encode_png(source)
    reference_path = storage.write_bytes(
        f"uploads/{job_id}/template_snapshot/clean_template.png", reference_bytes,
    )
    input_path = storage.write_bytes(f"uploads/{job_id}/original.png", input_bytes)
    config = load_template_config().model_copy(update={
        "canonical_width": 1240, "canonical_height": 1754,
        "regions": [], "marks": [], "field_mappings": [FieldMappingConfig(
            mapping_id="field-identity", target_path="document.cattleName", label="name",
            x=180, y=160, width=240, height=180, padding=0, enabled=True,
        )],
    })
    config_json = config.model_dump(mode="json", by_alias=True)
    config_sha = hashlib.sha256(json.dumps(
        config_json, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    reference_sha, input_sha = (
        hashlib.sha256(content).hexdigest() for content in (reference_bytes, input_bytes)
    )
    snapshot = {
        "templateVersionId": "test-version", "ocrEnabled": True, "ocrProvider": "openai",
        "fieldMappings": config_json["fieldMappings"],
        "templateSnapshot": {
            "schemaVersion": 1, "templateId": config.template_id,
            "templateVersionId": "test-version", "templateVersionNumber": config.template_version,
            "sourceArtifactId": "test-source", "sourceArtifactSha256": reference_sha,
            "cleanTemplateSha256": reference_sha, "cleanTemplatePath": reference_path,
            "cleanTemplateMediaType": "image/png", "config": config_json,
            "configSha256": config_sha,
        },
    }
    with database.session() as session:
        session.add(ProcessingJob(
            id=job_id, job_type="EXTRACT_DOCUMENT", status="QUEUED", request_hash="test",
            config_snapshot_json=snapshot, attempt_number=attempt,
        ))
        session.flush()
        session.add(JobInput(
            job_id=job_id, input_index=0, media_type="image/png", format="png",
            width=1240, height=1754, file_bytes=len(input_bytes), sha256=input_sha,
            relative_path=input_path,
        ))
        session.commit()
    service = ImageExtractionService(settings=settings, database=database, storage=storage)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Registration failure must not continue to handwriting extraction")

    monkeypatch.setattr(service._isolator, "isolate", forbidden)
    runner = JobRunner(
        database=database, settings=settings, storage=storage, handler=service.process,
        worker_count=1,
    )
    try:
        asyncio.run(runner._execute(job_id))
        with database.session() as session:
            job = session.get(ProcessingJob, job_id)
            assert job is not None
            assert (job.status, job.progress_percent, job.error_code) == (
                "FAILED", 28, "LOW_REGISTRATION_CONFIDENCE",
            )
            assert job.config_snapshot_json == snapshot
            artifacts = session.scalars(select(JobArtifact)).all()
            assert {a.artifact_name for a in artifacts} == {
                "registrationNormalized", "registrationReference",
                "registrationRejectedDebug", "registrationFailure",
            }
            assert {a.attempt_number for a in artifacts} == {attempt}
            diagnostic = next(a for a in artifacts if a.artifact_name == "registrationFailure")
            payload = json.loads(storage.read_bytes(diagnostic.relative_path))
            assert payload["inputSha256"] == input_sha
            assert payload["templateSnapshotSha256"] == reference_sha
            assert payload["templateConfigSnapshotSha256"] == config_sha
            assert payload["diagnostics"]["rejectionReason"] == "NO_DESCRIPTORS"
            assert "api_key" not in json.dumps(payload)
        assert storage.read_bytes(reference_path) == reference_bytes
        assert storage.read_bytes(input_path) == input_bytes
    finally:
        database.dispose()
