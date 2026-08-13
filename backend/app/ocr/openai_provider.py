from __future__ import annotations

import base64
import json
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
from app.ocr.base import OcrProviderRequest, OcrProviderResponse
from app.ocr.deterministic_mapper import build_mapped_document
from app.ocr.prompt_loader import load_transcription_prompt
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
            # Retry attempts are counted and bounded below. SDK-internal
            # retries would be invisible to apiCallCount and cancellation.
            max_retries=0,
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
        last_error: AppError | None = None
        call_count = 0
        retryable_codes = {
            "OCR_RATE_LIMITED",
            "OCR_TIMEOUT",
            "OCR_PROVIDER_ERROR",
        }
        for model in models:
            for _ in range(self._settings.max_retries + 1):
                if call_count >= self._settings.max_calls_per_job:
                    break
                self._raise_if_cancelled(request)
                call_count += 1
                try:
                    return await self._call(request, model, call_count)
                except AppError as exc:
                    last_error = exc
                    if exc.code not in retryable_codes:
                        raise
            if call_count >= self._settings.max_calls_per_job:
                break
        if last_error is not None:
            raise last_error
        raise AppError(
            status=503,
            code="MODEL_NOT_CONFIGURED",
            title="OpenAI OCR is not configured",
            detail="OCRモデルが設定されていません。",
        )

    @staticmethod
    def _raise_if_cancelled(request: OcrProviderRequest) -> None:
        if request.should_cancel is None or not request.should_cancel():
            return
        raise AppError(
            status=409,
            code="JOB_CANCELLED",
            title="OCR job was cancelled",
            detail="読み取りは取り消されました。",
        )

    async def _call(
        self, request: OcrProviderRequest, model: str, call_count: int
    ) -> OcrProviderResponse:
        content = build_mapped_input_content(
            request,
            image_detail=self._settings.image_detail,
        )
        started = time.perf_counter()
        response_input = cast(ResponseInputParam, [{"role": "user", "content": content}])
        try:
            response = await self._client.responses.parse(
                model=model,
                instructions=load_transcription_prompt(),
                input=response_input,
                text_format=TranscriptionBatch,
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
        if not isinstance(parsed, TranscriptionBatch):
            raise self._provider_error(
                502, "OCR_SCHEMA_INVALID", "OCR転記結果の形式が一致しません。"
            )
        try:
            document = build_mapped_document(request, parsed)
        except (ValidationError, ValueError) as exc:
            logger.warning(
                "OpenAI mapped OCR result could not be assembled: %s",
                type(exc).__name__,
            )
            raise self._provider_error(
                502,
                "OCR_MAPPING_INVALID",
                "OCR転記結果をテンプレートのJSON項目へ割り当てられませんでした。",
            ) from exc
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


def build_mapped_input_content(
    request: OcrProviderRequest,
    *,
    image_detail: str,
) -> list[dict[str, Any]]:
    if not request.regions or any(not region.target_path for region in request.regions):
        raise AppError(
            status=409,
            code="TEMPLATE_FIELD_MAPPING_REQUIRED",
            title="Template field mappings are required",
            detail=(
                "OpenAI読み取りには意味づけ済みの手書きセルが必要です。"
                "ラベルや帳票全体からの推測読み取りは行いません。"
            ),
        )
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                "各画像はテンプレートで確定済みの手書き記入セルです。"
                "項目の意味を推測せず、mappingIdごとに画像内の文字だけを転記してください。"
                "候補情報が付いたセルだけは、画像と十分に一致する候補を"
                "matchedCandidateとして返せます。"
            ),
        }
    ]
    for region in request.regions:
        content.append(
            {
                "type": "input_text",
                "text": f"mappingId: {region.region_id}",
            }
        )
        content.append(
            {
                "type": "input_text",
                "text": "view: mapped_input_cell",
            }
        )
        if region.printed_label_text:
            content.append(
                {
                    "type": "input_text",
                    "text": (
                        "fixedPrintedTextToIgnore: "
                        f"{json.dumps(region.printed_label_text, ensure_ascii=False)}\n"
                        "Ignore only that fixed printed label. "
                        "Transcribe the handwritten entry in this image."
                    ),
                }
            )
        if region.candidate_type == "sale_calf_note_prefix":
            content.append(
                {
                    "type": "input_text",
                    "text": (
                        "candidateType: sale_calf_note_prefix\n"
                        "allowedFirstSymbols: "
                        f"{json.dumps(region.candidates, ensure_ascii=False)}\n"
                        "The first transcribed character must be exactly one of "
                        "allowedFirstSymbols. Return that symbol as matchedCandidate. "
                        "Preserve any clearly visible handwritten suffix after it. "
                        "Do not substitute kana or katakana for the sex symbol."
                    ),
                }
            )
        elif region.candidates:
            content.append(
                {
                    "type": "input_text",
                    "text": (
                        "candidateType: sire_name\n"
                        f"candidateSireNames: {json.dumps(region.candidates, ensure_ascii=False)}\n"
                        "allowedSuffixes: "
                        f"{json.dumps(region.allowed_suffixes, ensure_ascii=False)}"
                    ),
                }
            )
        if region.transcription_format == "japanese_era_date_numbers":
            content.append(
                {
                    "type": "input_text",
                    "text": (
                        "transcriptionFormat: japanese_era_date_numbers\n"
                        "Return raw using ASCII digits only, as exactly three groups "
                        "separated by one space: ERA_YEAR MONTH DAY. "
                        "Do not include era names, 年/月/日, printed labels, "
                        "or any other characters."
                    ),
                }
            )
        encoded = base64.b64encode(region.image_png).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{encoded}",
                "detail": image_detail,
            }
        )
        if region.handwriting_png is not None:
            handwriting_encoded = base64.b64encode(region.handwriting_png).decode("ascii")
            content.append(
                {
                    "type": "input_text",
                    "text": "view: mapped_input_cell_handwriting_hint",
                }
            )
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{handwriting_encoded}",
                    "detail": image_detail,
                }
            )
    return content


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
