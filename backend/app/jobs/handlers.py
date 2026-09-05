from __future__ import annotations

from typing import Protocol

from app.db.session import Database
from app.domain.enums import JobType
from app.repositories.job_repository import JobRepository
from app.services.document_ocr_execution_service import DocumentOcrExecutionService
from app.services.template_service import TemplatePreparationService


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
        database: Database,
        template_service: TemplatePreparationService,
        document_ocr_execution: DocumentOcrExecutionService,
    ) -> None:
        self._database = database
        self._template_service = template_service
        self._document_ocr_execution = document_ocr_execution

    def __call__(self, job_id: str) -> None:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            if job is None:
                raise ValueError("Job not found")
            if job.cancel_requested:
                return
            job_type = JobType(job.job_type)
        if job_type is JobType.PREPARE_TEMPLATE:
            self._template_service.process(job_id)
            return
        self._document_ocr_execution.execute(job_id)
