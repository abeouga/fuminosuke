from collections.abc import Callable

from app.core.config import Settings
from app.db.base import Base
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.repositories.job_repository import JobRepository


def test_job_crud(settings_factory: Callable[..., Settings]) -> None:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            repository = JobRepository(session)
            created = repository.create(
                job_type=JobType.PREPARE_TEMPLATE,
                request_hash="a" * 64,
                config_snapshot={"mode": "REFERENCE_TEMPLATE"},
            )
            assert repository.get(created.id) is not None
            assert [item.id for item in repository.list()] == [created.id]

            updated = repository.update(
                created.id,
                status=JobStatus.QUEUED,
                progress_stage="VALIDATING_INPUT",
                progress_percent=10,
            )
            assert updated is not None
            assert updated.status == JobStatus.QUEUED.value
            assert updated.progress_percent == 10

            assert repository.delete(created.id) is True
            assert repository.get(created.id) is None
            assert repository.delete(created.id) is False
    finally:
        database.dispose()

