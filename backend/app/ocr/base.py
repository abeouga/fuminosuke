from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
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
    is_document_identity: bool = False
    identity_value_type: str | None = None
    identity_exact_length: int | None = None
    # Template reading settings of this field. The instruction is sent
    # with the image; keywords are never sent and are applied to the reading by the
    # server only (keyword_mode "reference_text" or "forced_text").
    reading_instruction: str | None = None
    keywords: tuple[str, ...] = ()
    keyword_mode: str | None = None


@dataclass(frozen=True, slots=True)
class OcrProviderRequest:
    regions: list[OcrRegionInput]
    full_document_png: bytes | None = None
    template_id: str = "generic-document-v1"
    template_version: str = "1"
    should_cancel: Callable[[], bool] | None = None
    call_budget: OcrCallBudget | None = None
    transcription_prompt: str | None = None


class OcrCallBudget(Protocol):
    """Orchestration-owned durable budget; OCR calculation has no DB dependency."""

    def remaining(self) -> int: ...

    def reserve(self) -> int | None:
        """Commit a non-refundable reservation; return its document-wide ordinal."""
        ...


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
    diagnostics: tuple[dict[str, object], ...] = field(default_factory=tuple)


class OcrProvider(Protocol):
    async def extract(self, request: OcrProviderRequest) -> OcrProviderResponse: ...
