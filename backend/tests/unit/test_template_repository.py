from collections.abc import Callable

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import JobArtifact, ProcessingJob, Template, TemplateVersion
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode, TemplateVersionStatus
from app.repositories.template_repository import TemplateRepository


def test_full_image_boundary_fallback_cannot_be_approved(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            job = ProcessingJob(
                job_type=JobType.PREPARE_TEMPLATE.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                request_hash="a" * 64,
                config_snapshot_json={},
            )
            template = Template(
                name="boundary-fallback-template",
                document_type="generic-document",
            )
            session.add_all([job, template])
            session.flush()
            artifact = JobArtifact(
                job_id=job.id,
                attempt_number=1,
                artifact_name="cleanTemplate",
                media_type="image/png",
                relative_path="artifacts/template/clean_template.png",
                sha256="b" * 64,
                width=1240,
                height=1754,
            )
            session.add(artifact)
            session.flush()
            version = TemplateVersion(
                template_id=template.id,
                version_number=1,
                mode=TemplateMode.SINGLE_IMAGE_EXPERIMENTAL.value,
                status=TemplateVersionStatus.DRAFT.value,
                canonical_width=1240,
                canonical_height=1754,
                source_job_id=job.id,
                clean_template_artifact_id=artifact.id,
                mask_artifact_id=None,
                config_relative_path="templates/template/versions/1/template.yaml",
                quality_json={
                    "fallbackUsed": True,
                    "warnings": ["FULL_IMAGE_BOUNDARY_FALLBACK"],
                    "layoutFinalized": True,
                    "layoutVerified": True,
                },
                layout_json={"relations": [{"id": "relation"}]},
            )
            session.add(version)
            session.commit()
            version_id = version.id

        with database.session() as session, pytest.raises(AppError) as caught:
            TemplateRepository(session).approve(version_id)

        assert caught.value.code == "TEMPLATE_DOCUMENT_BOUNDARY_NOT_VERIFIED"
    finally:
        database.dispose()
