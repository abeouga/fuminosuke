import asyncio

import pytest
from pydantic import SecretStr

from app.core.config import OcrSettings
from app.core.errors import AppError
from app.domain.ocr_factory import blank_ocr_document
from app.ocr.base import OcrProviderRequest, OcrProviderResponse
from app.ocr.openai_provider import OpenAIOcrProvider


def test_retry_and_fallback_calls_share_one_exact_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=4, max_retries=1))
    calls: list[tuple[str, int]] = []

    async def fake_call(
        request: OcrProviderRequest,
        model: str,
        call_count: int,
    ) -> OcrProviderResponse:
        del request
        calls.append((model, call_count))
        if model == "primary":
            raise provider._provider_error(504, "OCR_TIMEOUT", "timeout")
        return _response(model, call_count)

    monkeypatch.setattr(provider, "_call", fake_call)
    try:
        response = asyncio.run(provider.extract(OcrProviderRequest(regions=[])))
    finally:
        asyncio.run(provider._client.close())

    assert calls == [("primary", 1), ("primary", 2), ("fallback", 3)]
    assert response.api_call_count == 3


def test_sdk_retries_are_disabled_and_total_budget_is_never_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=1, max_retries=5))
    calls: list[int] = []

    async def always_fails(
        request: OcrProviderRequest,
        model: str,
        call_count: int,
    ) -> OcrProviderResponse:
        del request, model
        calls.append(call_count)
        raise provider._provider_error(429, "OCR_RATE_LIMITED", "limited")

    monkeypatch.setattr(provider, "_call", always_fails)
    try:
        with pytest.raises(AppError) as captured:
            asyncio.run(provider.extract(OcrProviderRequest(regions=[])))
    finally:
        asyncio.run(provider._client.close())

    assert provider._client.max_retries == 0
    assert captured.value.code == "OCR_RATE_LIMITED"
    assert calls == [1]


def test_cancellation_is_checked_between_explicit_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=4, max_retries=3))
    calls: list[int] = []

    async def first_attempt_fails(
        request: OcrProviderRequest,
        model: str,
        call_count: int,
    ) -> OcrProviderResponse:
        del request, model
        calls.append(call_count)
        raise provider._provider_error(504, "OCR_TIMEOUT", "timeout")

    monkeypatch.setattr(provider, "_call", first_attempt_fails)
    request = OcrProviderRequest(
        regions=[],
        should_cancel=lambda: len(calls) >= 1,
    )
    try:
        with pytest.raises(AppError) as captured:
            asyncio.run(provider.extract(request))
    finally:
        asyncio.run(provider._client.close())

    assert captured.value.code == "JOB_CANCELLED"
    assert calls == [1]


def _settings(*, max_calls: int, max_retries: int) -> OcrSettings:
    return OcrSettings(
        provider="openai",
        openai_api_key=SecretStr("test-key"),
        primary_model="primary",
        fallback_model="fallback",
        image_detail="high",
        reasoning_effort="low",
        max_calls_per_job=max_calls,
        max_output_tokens=100,
        timeout_seconds=5,
        max_retries=max_retries,
        prompt_version="test",
        schema_version="1.0",
    )


def _response(model: str, call_count: int) -> OcrProviderResponse:
    return OcrProviderResponse(
        document=blank_ocr_document(provider="openai", source_regions=[]),
        provider="openai",
        model_id=model,
        prompt_version="test",
        api_call_count=call_count,
    )
