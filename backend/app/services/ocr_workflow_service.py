from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from app.core.config import Settings
from app.db.session import Database
from app.domain.ocr_factory import blank_ocr_document
from app.image_processing.artifacts import ArtifactWriter
from app.ocr.base import OcrProvider, OcrProviderResponse
from app.ocr.disabled_provider import DisabledOcrProvider
from app.ocr.mock_provider import MockOcrProvider
from app.ocr.openai_provider import OpenAIOcrProvider
from app.ocr.text_presence import PaddleTextPresenceGate
from app.repositories.job_repository import JobRepository
from app.repositories.ocr_result_repository import OcrResultRepository
from app.services.document_reconciliation_service import DocumentReconciliationService
from app.services.extraction_service import ExtractionOutput
from app.services.ocr_request_preparation_service import OcrRequestPreparer
from app.services.ocr_response_finalization_service import OcrResponseFinalizer
from app.services.paged_crop_correspondence import reject, verify_before_provider
from app.services.paged_ocr_budget import PagedCallBudget
from app.services.paged_transcription_resources import accepted_transcription_prompt
from app.storage.local_storage import LocalStorageService
from app.validation.business_validators import DeterministicValidator

logger = logging.getLogger(__name__)


class OcrWorkflowService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        storage: LocalStorageService,
    ) -> None:
        self._settings = settings
        self._database = database
        self._storage = storage
        self._writer = ArtifactWriter(database, storage)
        self._validator = DeterministicValidator()
        self._presence_gate = PaddleTextPresenceGate()
        self._documents = DocumentReconciliationService(self._validator)
        self._request_preparer = OcrRequestPreparer()
        self._response_finalizer = OcrResponseFinalizer(self._validator)

    def process(
        self, extraction: ExtractionOutput, *, reserved_calls_for_other_pages: int = 0
    ) -> dict[str, Any] | None:
        crop_correspondence = None
        with self._database.session() as session:
            job = JobRepository(session).get(extraction.job_id)
            if job is None:
                raise ValueError("OCR job not found")
            if "jobPages" in job.config_snapshot_json:
                from app.services.job_page_service import JobPageService

                crop_correspondence = verify_before_provider(
                    extraction, session=session, job=job,
                    pages_service=JobPageService(self._database, self._storage, self._settings),
                    settings=self._settings, storage=self._storage,
                )
            elif extraction.page_snapshot is not None:
                reject("PAGE_SNAPSHOT_UNEXPECTED")
            job_snapshot = dict(extraction.page_snapshot or job.config_snapshot_json)
            ocr_enabled = bool(job_snapshot.get("ocrEnabled", True))
            budget_snapshot_sha = str(job.config_snapshot_json.get("jobPagesSha256", ""))
            transcription_prompt = (
                accepted_transcription_prompt(job.config_snapshot_json)
                if extraction.page_snapshot is not None
                and ocr_enabled
                and job_snapshot.get("ocrProvider", self._settings.ocr.provider) == "openai"
                else None
            )
        writer = (
            ArtifactWriter(
                self._database, self._storage, page_id=extraction.page_snapshot["pageId"],
            ) if extraction.page_snapshot else self._writer
        )
        provider_name = (
            str(job_snapshot.get("ocrProvider", self._settings.ocr.provider))
            if ocr_enabled
            else "disabled"
        )
        if crop_correspondence is not None:
            writer.json(
                job_id=extraction.job_id, attempt_number=extraction.attempt_number,
                name="cropCorrespondence", file_name="crop_correspondence.json",
                value=crop_correspondence, replace=True,
            )
        provider = self._create_provider(provider_name)
        prepared = self._request_preparer.prepare(
            extraction=extraction,
            job_snapshot=job_snapshot,
            provider_name=provider_name,
            should_cancel=lambda: self._cancel_requested(extraction.job_id),
        )
        request = prepared.request
        if extraction.page_snapshot is not None and provider_name == "openai":
            budget = PagedCallBudget(
                self._database, extraction.job_id, extraction.page_snapshot["pageId"],
                extraction.attempt_number, budget_snapshot_sha,
                protected_calls=reserved_calls_for_other_pages,
            )
            budget.remaining()  # Reject missing/changed acceptance before any provider call.
            request = replace(
                request, call_budget=budget, transcription_prompt=transcription_prompt,
            )
        all_regions = request.regions
        mapped_cells_payload = prepared.mapped_cells_payload
        mark_mappings = prepared.mark_mappings
        if provider_name == "openai":
            self._progress(extraction.job_id, "PREPARING_MAPPED_CELLS", 72)
            if self._settings.ocr.paddle_presence_audit_enabled and self._presence_gate.available:
                presence_by_id = {
                    region.region_id: self._presence_gate.inspect(
                        extraction.crops[
                            region.source_region_id or region.region_id
                        ].handwriting_only
                    )
                    for region in all_regions
                }
                writer.json(
                    job_id=extraction.job_id,
                    attempt_number=extraction.attempt_number,
                    name="paddleTextPresence",
                    file_name="paddle_text_presence.json",
                    value={
                        "engine": "paddleocr",
                        "gatingEnabled": False,
                        "classifiedInputCount": len(all_regions),
                        "detectedTextCount": sum(item.detected for item in presence_by_id.values()),
                        "openAiCropCount": len(all_regions),
                        "regions": [
                            presence_by_id[region.region_id].to_dict(region_id=region.region_id)
                            for region in all_regions
                        ],
                    },
                )
            writer.json(
                job_id=extraction.job_id,
                attempt_number=extraction.attempt_number,
                name="mappedCells",
                file_name="mapped_cells.json",
                value=mapped_cells_payload,
            )
        if self._cancel_requested(extraction.job_id):
            return None
        self._progress(extraction.job_id, "CALLING_OCR", 78)
        response = (
            OcrProviderResponse(
                document=blank_ocr_document(
                    provider="local-mark-detector",
                    source_regions=[mapping.mapping_id for mapping in mark_mappings],
                ),
                provider="local-mark-detector",
                model_id=None,
                prompt_version=None,
                api_call_count=0,
            )
            if provider_name == "openai" and not all_regions
            else asyncio.run(provider.extract(request))
        )
        if response.provider == "openai":
            total_tokens = (
                response.input_tokens + response.output_tokens
                if response.input_tokens is not None and response.output_tokens is not None
                else None
            )
            with self._database.session() as session:
                JobRepository(session).add_event(
                    job_id=extraction.job_id,
                    level="INFO",
                    event_code="OPENAI_API_USAGE",
                    stage="CALLING_OCR",
                    metadata={
                        "attemptNumber": extraction.attempt_number,
                        "modelId": response.model_id,
                        "requestId": response.request_id,
                        "apiCallCount": response.api_call_count,
                        "inputTokens": response.input_tokens,
                        "outputTokens": response.output_tokens,
                        "totalTokens": total_tokens,
                        "estimatedCost": (
                            str(response.estimated_cost)
                            if response.estimated_cost is not None
                            else None
                        ),
                        "durationMs": response.duration_ms,
                    },
                )
        self._progress(extraction.job_id, "VALIDATING_RESULT", 90)
        finalized = self._response_finalizer.finalize(
            prepared=prepared,
            provider_response=response,
            extraction=extraction,
            job_snapshot=job_snapshot,
            provider_name=provider_name,
        )
        response = finalized.response
        report = finalized.validation_report
        response_document = response.document
        mapped_cells_payload = finalized.mapped_cells_payload
        openai_mapping_diagnostics = finalized.diagnostics_payload
        configured_paths = finalized.configured_paths
        if mapped_cells_payload is not None:
            writer.json(
                job_id=extraction.job_id,
                attempt_number=extraction.attempt_number,
                name="mappedCells",
                file_name="mapped_cells.json",
                value=mapped_cells_payload,
                replace=True,
            )
        if openai_mapping_diagnostics is not None:
            writer.json(
                job_id=extraction.job_id,
                attempt_number=extraction.attempt_number,
                name="openAiMappingDiagnostics",
                file_name="openai_mapping_diagnostics.json",
                value=openai_mapping_diagnostics,
            )
        if extraction.page_snapshot is not None:
            return {
                "result": response_document.model_dump(mode="json", by_alias=True),
                "validation": report.to_dict(), "quality": extraction.quality,
                "mappedCells": mapped_cells_payload, "diagnostics": openai_mapping_diagnostics,
                "provider": response.provider, "modelId": response.model_id,
                "apiCallCount": response.api_call_count,
            }
        with self._database.session() as session:
            stored = OcrResultRepository(session).save_provider_result(
                job_id=extraction.job_id,
                response=response,
                report=report,
            )
            current_job = JobRepository(session).get(extraction.job_id)
            explicit_action = (
                current_job.config_snapshot_json.get("documentAction")
                if current_job is not None
                else None
            )
            if current_job is not None and explicit_action in {
                "ADD_PAGE",
                "RESCAN_PAGE",
                "NEW_DOCUMENT",
            }:
                stored = self._documents.resolve(
                    session,
                    job=current_job,
                    stored=stored,
                    action=explicit_action,
                    target_document_id=current_job.config_snapshot_json.get("targetDocumentId"),
                    target_job_id=None,
                    target_page_id=current_job.config_snapshot_json.get("targetPageId"),
                    configured_paths=configured_paths,
                )
                session.commit()
            revision = stored.revision
        # A spreadsheet file belongs to a confirmed ledger document. Draft OCR
        # results remain in the database so an abandoned review cannot create
        # an orphan file in Google Drive.
        self._progress(extraction.job_id, "WRITING_ARTIFACTS", 96)
        writer.json(
            job_id=extraction.job_id,
            attempt_number=extraction.attempt_number,
            name="result",
            file_name="result.json",
            value={
                "jobId": extraction.job_id,
                "revision": revision,
                "result": response_document.model_dump(mode="json", by_alias=True),
                "validation": report.to_dict(),
                "quality": extraction.quality,
            },
        )
        logger.info(
            "OCR provider call completed",
            extra={
                "jobId": extraction.job_id,
                "provider": response.provider,
                "modelId": response.model_id,
                "requestId": response.request_id,
                "inputTokens": response.input_tokens,
                "outputTokens": response.output_tokens,
                "durationMs": response.duration_ms,
            },
        )

        return None

    def _create_provider(self, provider_name: str) -> OcrProvider:
        if provider_name == "disabled":
            return DisabledOcrProvider()
        if provider_name == "mock":
            return MockOcrProvider()
        if provider_name == "openai":
            return OpenAIOcrProvider(self._settings.ocr)
        raise ValueError(f"Unsupported OCR provider: {provider_name}")

    def _progress(self, job_id: str, stage: str, percent: int) -> None:
        with self._database.session() as session:
            JobRepository(session).update(job_id, progress_stage=stage, progress_percent=percent)

    def _cancel_requested(self, job_id: str) -> bool:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            return job is None or job.cancel_requested
