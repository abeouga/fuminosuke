from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.errors import AppError
from app.domain.ocr_models import OcrDocument
from app.ocr.base import OcrProviderRequest, OcrProviderResponse


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
        fixture_path = (
            Path(__file__).resolve().parents[2]
            / "resources"
            / "templates"
            / "cow-breeding-ledger-v1"
            / "mock-result.json"
        )
        document = OcrDocument.model_validate_json(fixture_path.read_text(encoding="utf-8"))
        document.processing_metadata.source_regions = [item.region_id for item in request.regions]
        return OcrProviderResponse(
            document=document,
            provider="mock",
            model_id="mock-v1",
            prompt_version="cow-ledger-v1",
            api_call_count=0,
        )
