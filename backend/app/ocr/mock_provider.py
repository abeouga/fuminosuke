from __future__ import annotations

import asyncio

from app.core.errors import AppError
from app.domain.ocr_models import ReadingStatus
from app.ocr.base import OcrProviderRequest, OcrProviderResponse
from app.ocr.deterministic_mapper import build_mapped_document
from app.ocr.transcription_models import TranscriptionBatch, TranscriptionItem


class MockOcrProvider:
    def __init__(self, *, delay_seconds: float = 0, error_code: str | None = None) -> None:
        self._delay_seconds = delay_seconds
        self._error_code = error_code

    async def extract(self, request: OcrProviderRequest) -> OcrProviderResponse:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if self._error_code:
            raise AppError(
                status=502,
                code=self._error_code,
                title="Mock OCR provider error",
                detail="Mock OCR Providerが指定されたエラーを返しました。",
            )
        document = build_mapped_document(
            request,
            TranscriptionBatch(
                items=[
                    TranscriptionItem(
                        mapping_id=item.region_id,
                        raw=None,
                        status=ReadingStatus.BLANK,
                        alternatives=[],
                        review_reason=None,
                        matched_candidate=None,
                        is_ditto=False,
                    )
                    for item in request.regions
                ]
            ),
        )
        return OcrProviderResponse(
            document=document,
            provider="mock",
            model_id="mock-v1",
            prompt_version="generic-document-v2",
            api_call_count=0,
        )
