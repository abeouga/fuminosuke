from __future__ import annotations

from app.db.session import Database
from app.repositories.job_repository import JobRepository
from app.services.extraction_service import ImageExtractionService
from app.services.ocr_workflow_service import OcrWorkflowService


class DocumentOcrExecutionService:
    """Executes the document OCR pipeline for an already accepted job."""

    def __init__(
        self,
        *,
        database: Database,
        extraction_service: ImageExtractionService,
        ocr_workflow_service: OcrWorkflowService,
    ) -> None:
        self._database = database
        self._extraction_service = extraction_service
        self._ocr_workflow_service = ocr_workflow_service

    def execute(self, job_id: str) -> None:
        extraction = self._extraction_service.process(job_id)
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            if job is None or job.cancel_requested:
                return
        self._ocr_workflow_service.process(extraction)
