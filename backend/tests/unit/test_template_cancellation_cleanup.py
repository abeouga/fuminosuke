from collections.abc import Callable

from app.core.config import Settings
from app.db.base import Base
from app.db.models import ProcessingJob, Template, TemplateVersion
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode, TemplateVersionStatus
from app.services.template_service import TemplatePreparationService
from app.storage.local_storage import LocalStorageService


def test_cancelled_persisted_template_version_is_compensated(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    service = TemplatePreparationService(
        settings=settings,
        database=database,
        storage=storage,
    )
    try:
        with database.session() as session:
            job = ProcessingJob(
                job_type=JobType.PREPARE_TEMPLATE.value,
                status=JobStatus.PROCESSING.value,
                request_hash="a" * 64,
                cancel_requested=True,
                config_snapshot_json={},
            )
            template = Template(
                name="cancelled-template",
                document_type="generic-document",
            )
            session.add_all([job, template])
            session.flush()
            version = TemplateVersion(
                template_id=template.id,
                version_number=1,
                mode=TemplateMode.REFERENCE_TEMPLATE.value,
                status=TemplateVersionStatus.DRAFT.value,
                canonical_width=32,
                canonical_height=24,
                source_job_id=job.id,
                clean_template_artifact_id=None,
                mask_artifact_id=None,
                config_relative_path=(
                    f"templates/{template.id}/versions/1/template.yaml"
                ),
                quality_json={"layoutVerified": False},
                layout_json={},
            )
            session.add(version)
            session.flush()
            job.config_snapshot_json = {
                "templateId": template.id,
                "templateVersionId": version.id,
            }
            session.commit()
            job_id = job.id
            template_id = template.id
            version_id = version.id

        storage.write_text(
            f"templates/{template_id}/versions/1/template.yaml",
            "name: cancelled",
        )

        service._discard_cancelled_version(job_id, version_id)

        with database.session() as session:
            assert session.get(TemplateVersion, version_id) is None
            assert session.get(Template, template_id) is None
            persisted_job = session.get(ProcessingJob, job_id)
            assert persisted_job is not None
            assert "templateId" not in persisted_job.config_snapshot_json
            assert "templateVersionId" not in persisted_job.config_snapshot_json
        assert not storage.exists(f"templates/{template_id}")
    finally:
        database.dispose()


def test_failed_reprocess_cleanup_keeps_the_source_draft(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    service = TemplatePreparationService(
        settings=settings,
        database=database,
        storage=storage,
    )
    try:
        with database.session() as session:
            job = ProcessingJob(
                job_type=JobType.PREPARE_TEMPLATE.value,
                status=JobStatus.PROCESSING.value,
                request_hash="b" * 64,
                config_snapshot_json={},
            )
            template = Template(
                name="reprocess-source",
                document_type="generic-document",
            )
            session.add_all([job, template])
            session.flush()
            source = TemplateVersion(
                template_id=template.id,
                version_number=1,
                mode=TemplateMode.REFERENCE_TEMPLATE.value,
                status=TemplateVersionStatus.DRAFT.value,
                canonical_width=32,
                canonical_height=24,
                source_job_id=job.id,
                clean_template_artifact_id=None,
                mask_artifact_id=None,
                config_relative_path="templates/source/template.yaml",
                quality_json={},
                layout_json={},
            )
            session.add(source)
            session.flush()
            job.config_snapshot_json = {
                "reprocessTemplateVersionId": source.id,
            }
            session.commit()
            source_id = source.id
            job_id = job.id

        service._discard_job_version(job_id)

        with database.session() as session:
            assert session.get(TemplateVersion, source_id) is not None
    finally:
        database.dispose()
