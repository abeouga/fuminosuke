from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.domain.ocr_models import OcrDocument


@dataclass(frozen=True, slots=True)
class OcrRegionInput:
    region_id: str
    image_png: bytes
    handwriting_png: bytes | None = None
    target_path: str | None = None
    source_region_id: str | None = None
    bounding_box: tuple[int, int, int, int] | None = None
    candidates: tuple[str, ...] = ()
    allowed_suffixes: tuple[str, ...] = ()
    candidate_type: str | None = None
    transcription_format: str | None = None
    printed_label_excluded: bool = False
    printed_label_text: str = ""


@dataclass(frozen=True, slots=True)
class OcrProviderRequest:
    regions: list[OcrRegionInput]
    template_id: str = "cow-breeding-ledger-v1"
    template_version: str = "1"
    should_cancel: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class OcrProviderResponse:
    document: OcrDocument
    provider: str
    model_id: str | None
    prompt_version: str | None
    api_call_count: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: Decimal | None = None
    request_id: str | None = None
    duration_ms: float | None = None


class OcrProvider(Protocol):
    async def extract(self, request: OcrProviderRequest) -> OcrProviderResponse: ...
