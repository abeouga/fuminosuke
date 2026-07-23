from __future__ import annotations

import base64
import logging
import time
from decimal import Decimal
from typing import Any, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from openai.types.responses import ResponseInputParam
from pydantic import ValidationError

from app.core.config import OcrSettings
from app.core.errors import AppError
from app.domain.ocr_models import OcrDocument
from app.ocr.base import OcrProviderRequest, OcrProviderResponse
from app.ocr.deterministic_mapper import build_mapped_document
from app.ocr.prompt_loader import load_ocr_prompt, load_transcription_prompt
from app.ocr.transcription_models import TranscriptionBatch

logger = logging.getLogger(__name__)


class OpenAIOcrProvider:
    def __init__(self, settings: OcrSettings) -> None:
        self._settings = settings
        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        if not key.strip() or not (settings.primary_model or "").strip():
            raise AppError(
                status=503,
                code="MODEL_NOT_CONFIGURED",
                title="OpenAI OCR is not configured",
                detail="バックエンドのOpenAI APIキーとモデルを設定してください。",
            )
        self._client = AsyncOpenAI(
            api_key=key,
            timeout=float(settings.timeout_seconds),
            max_retries=settings.max_retries,
        )

    async def extract(self, request: OcrProviderRequest) -> OcrProviderResponse:
        models: list[str] = []
        if self._settings.primary_model:
            models.append(self._settings.primary_model)
        if (
            self._settings.fallback_model
            and self._settings.fallback_model != self._settings.primary_model
        ):
            models.append(self._settings.fallback_model)
        if len(models) > self._settings.max_calls_per_job:
            models = models[: self._settings.max_calls_per_job]
        last_error: AppError | None = None
        for call_count, model in enumerate(models, start=1):
            try:
                return await self._call(request, model, call_count)
            except AppError as exc:
                last_error = exc
                if exc.code not in {
                    "OCR_RATE_LIMITED",
                    "OCR_TIMEOUT",
                    "OCR_PROVIDER_ERROR",
                }:
                    raise
        if last_error is not None:
            raise last_error
        raise AppError(
            status=503,
            code="MODEL_NOT_CONFIGURED",
            title="OpenAI OCR is not configured",
            detail="OCRモデルが設定されていません。",
        )

    async def _call(
        self, request: OcrProviderRequest, model: str, call_count: int
    ) -> OcrProviderResponse:
        mapped_mode = bool(request.regions) and all(
            region.target_path for region in request.regions
        )
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "以下はGUIで項目が確定済みの記入欄画像です。意味を推測せず、"
                    "各mappingIdについて見える手書き文字だけを転記してください。"
                    if mapped_mode
                    else (
                        "以下はregion単位の画像です。各画像の直前のregionIdを"
                        "sourceRegionIdとして転記してください。"
                    )
                ),
            }
        ]
        for region in request.regions:
            descriptor = f"regionId: {region.region_id}"
            if mapped_mode:
                descriptor = (
                    f"mappingId: {region.region_id}\n"
                    f"targetPath: {region.target_path}\n"
                    f"label: {region.label or region.target_path}"
                )
            content.append({"type": "input_text", "text": descriptor})
            content.append(
                {
                    "type": "input_text",
                    "text": "view: original (帳票レイアウトと筆跡の確認用)",
                }
            )
            encoded = base64.b64encode(region.image_png).decode("ascii")
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{encoded}",
                    "detail": self._settings.image_detail,
                }
            )
            if region.handwriting_png is not None:
                handwriting_encoded = base64.b64encode(region.handwriting_png).decode("ascii")
                content.append(
                    {
                        "type": "input_text",
                        "text": (
                            "view: handwriting_hint (印刷除去の補助画像。薄い筆跡が欠けることが"
                            "あるため、最終判断はoriginalと照合する)"
                        ),
                    }
                )
                content.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{handwriting_encoded}",
                        "detail": self._settings.image_detail,
                    }
                )
        started = time.perf_counter()
        response_input = cast(ResponseInputParam, [{"role": "user", "content": content}])
        try:
            response = await self._client.responses.parse(
                model=model,
                instructions=(load_transcription_prompt() if mapped_mode else load_ocr_prompt()),
                input=response_input,
                text_format=TranscriptionBatch if mapped_mode else OcrDocument,
                max_output_tokens=self._settings.max_output_tokens,
                reasoning={"effort": self._settings.reasoning_effort},
                store=False,
            )
        except RateLimitError as exc:
            raise self._provider_error(
                429, "OCR_RATE_LIMITED", "OCR APIのレート上限に達しました。"
            ) from exc
        except APITimeoutError as exc:
            raise self._provider_error(
                504, "OCR_TIMEOUT", "OCR APIがタイムアウトしました。"
            ) from exc
        except APIConnectionError as exc:
            raise self._provider_error(
                502, "OCR_PROVIDER_ERROR", "OCR APIへ接続できません。"
            ) from exc
        except APIStatusError as exc:
            raise self._provider_error(
                502, "OCR_PROVIDER_ERROR", "OCR APIがエラーを返しました。"
            ) from exc
        except ValidationError as exc:
            errors = [
                {
                    "type": item["type"],
                    "location": [str(part) for part in item["loc"]],
                    "message": item["msg"],
                }
                for item in exc.errors(include_input=False)
            ]
            logger.warning("OpenAI OCR schema validation failed: %s", errors)
            raise self._provider_error(
                502, "OCR_SCHEMA_INVALID", "OCR応答がSchemaに一致しません。"
            ) from exc
        except (ValueError, TypeError) as exc:
            logger.warning("OpenAI OCR structured parsing failed: %s", type(exc).__name__)
            raise self._provider_error(
                502, "OCR_SCHEMA_INVALID", "OCR応答がSchemaに一致しません。"
            ) from exc
        parsed = response.output_parsed
        if parsed is None:
            reason = response.incomplete_details.reason if response.incomplete_details else None
            usage = response.usage
            logger.warning(
                "OpenAI OCR returned no parsed document",
                extra={
                    "requestId": response.id,
                    "responseStatus": response.status,
                    "incompleteReason": reason,
                    "inputTokens": usage.input_tokens if usage else None,
                    "outputTokens": usage.output_tokens if usage else None,
                },
            )
            detail = (
                "OCR応答が最大出力トークン数に達しました。設定を増やして再試行してください。"
                if reason == "max_output_tokens"
                else "OCR応答を構造化できません。"
            )
            raise self._provider_error(502, "OCR_SCHEMA_INVALID", detail)
        if mapped_mode:
            if not isinstance(parsed, TranscriptionBatch):
                raise self._provider_error(
                    502, "OCR_SCHEMA_INVALID", "OCR転記結果の形式が一致しません。"
                )
            document = build_mapped_document(request, parsed)
        else:
            if not isinstance(parsed, OcrDocument):
                raise self._provider_error(
                    502, "OCR_SCHEMA_INVALID", "OCR結果の形式が一致しません。"
                )
            document = parsed
        usage = response.usage
        input_tokens = usage.input_tokens if usage else None
        output_tokens = usage.output_tokens if usage else None
        return OcrProviderResponse(
            document=document,
            provider="openai",
            model_id=model,
            prompt_version=self._settings.prompt_version,
            api_call_count=call_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=_estimate_cost(model, input_tokens, output_tokens),
            request_id=response.id,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    @staticmethod
    def _provider_error(status: int, code: str, detail: str) -> AppError:
        return AppError(status=status, code=code, title="OCR provider failed", detail=detail)


def _estimate_cost(
    model: str, input_tokens: int | None, output_tokens: int | None
) -> Decimal | None:
    if input_tokens is None or output_tokens is None:
        return None
    if model == "gpt-5-mini" or model.startswith("gpt-5-mini-"):
        per_million_input = Decimal("0.25")
        per_million_output = Decimal("2.00")
    else:
        return None
    total = (
        Decimal(input_tokens) * per_million_input + Decimal(output_tokens) * per_million_output
    ) / Decimal(1_000_000)
    return total.quantize(Decimal("0.000001"))
