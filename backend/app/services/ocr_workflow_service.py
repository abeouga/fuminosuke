from __future__ import annotations

import asyncio
import logging

from app.core.config import Settings
from app.db.session import Database
from app.image_processing.artifacts import ArtifactWriter, encode_png
from app.ocr.base import OcrProvider, OcrProviderRequest, OcrRegionInput
from app.ocr.disabled_provider import DisabledOcrProvider
from app.ocr.mock_provider import MockOcrProvider
from app.ocr.openai_provider import OpenAIOcrProvider
from app.repositories.job_repository import JobRepository
from app.repositories.ocr_result_repository import OcrResultRepository
from app.services.extraction_service import ExtractionOutput
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
        self._writer = ArtifactWriter(database, storage)
        self._validator = DeterministicValidator()

    def process(self, extraction: ExtractionOutput) -> None:
        with self._database.session() as session:
            job = JobRepository(session).get(extraction.job_id)
            if job is None:
                raise ValueError("OCR job not found")
            ocr_enabled = bool(job.config_snapshot_json.get("ocrEnabled", True))
        provider_name = self._settings.ocr.provider if ocr_enabled else "disabled"
        provider = self._create_provider(provider_name)
        request = OcrProviderRequest(
            regions=[
                OcrRegionInput(
                    region_id=region.region_id,
                    image_png=encode_png(extraction.crops[region.region_id].aligned_color),
                    handwriting_png=encode_png(extraction.crops[region.region_id].handwriting_only),
                    target_path=(
                        region.expected_fields[0] if len(region.expected_fields) == 1 else None
                    ),
                    label=next(
                        (
                            mapping.label
                            for mapping in extraction.config.field_mappings
                            if mapping.mapping_id == region.region_id
                        ),
                        None,
                    ),
                    bounding_box=extraction.crops[region.region_id].bbox,
                )
                for region in extraction.config.ocr_regions()
                if region.region_id in extraction.crops
            ],
            template_id=extraction.config.template_id,
            template_version=str(extraction.config.template_version),
        )
        self._progress(extraction.job_id, "CALLING_OCR", 78)
        response = asyncio.run(provider.extract(request))
        response.document.processing_metadata.model_id = response.model_id
        response.document.processing_metadata.prompt_version = response.prompt_version
        self._progress(extraction.job_id, "VALIDATING_RESULT", 90)
        report = self._validator.validate(response.document, image_quality=extraction.quality)
        response_document = self._validator.merge_review_items(response.document, report)
        response = response.__class__(
            document=response_document,
            provider=response.provider,
            model_id=response.model_id,
            prompt_version=response.prompt_version,
            api_call_count=response.api_call_count,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost=response.estimated_cost,
            request_id=response.request_id,
            duration_ms=response.duration_ms,
        )
        with self._database.session() as session:
            stored = OcrResultRepository(session).save_provider_result(
                job_id=extraction.job_id,
                response=response,
                report=report,
            )
            revision = stored.revision
        self._progress(extraction.job_id, "WRITING_ARTIFACTS", 96)
        self._writer.json(
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
