from __future__ import annotations

from typing import Protocol

from app.core.config import Settings
from app.db.session import Database
from app.domain.enums import JobType
from app.repositories.job_repository import JobRepository
from app.services.extraction_service import ImageExtractionService
from app.services.ocr_workflow_service import OcrWorkflowService
from app.services.template_service import TemplatePreparationService
from app.storage.local_storage import LocalStorageService


class JobHandler(Protocol):
    def __call__(self, job_id: str) -> None: ...


class PendingPipelineHandler:
    """Temporary dispatcher replaced by the image pipelines in Phase 3 and later."""

    def __call__(self, job_id: str) -> None:
        del job_id


class PipelineJobHandler:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        storage: LocalStorageService,
    ) -> None:
        self._database = database
        self._template_service = TemplatePreparationService(
            settings=settings,
            database=database,
            storage=storage,
        )
        self._extraction_service = ImageExtractionService(
            settings=settings,
            database=database,
            storage=storage,
        )
        self._ocr_workflow = OcrWorkflowService(
            settings=settings,
            database=database,
            storage=storage,
        )

    def __call__(self, job_id: str) -> None:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            if job is None:
                raise ValueError("Job not found")
            job_type = JobType(job.job_type)
        if job_type is JobType.PREPARE_TEMPLATE:
            self._template_service.process(job_id)
            return
        output = self._extraction_service.process(job_id)
        self._ocr_workflow.process(output)
