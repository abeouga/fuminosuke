"""Finite-choice model calls for question answering (GPT-6 Luna / Decisions).

One bounded HTTP request per call through the shared ApiBudget; no model loop, no free
text. Tests substitute a scripted chooser, so no paid request is needed for boundaries.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Protocol

import httpx

from app.ai.api_budget import ApiBudget
from app.core.config import Settings
from app.editing.errors import reject

MODEL = "gpt-6-luna"
TIMEOUT_SECONDS = 10.0
PURPOSE = "qa.decisions"


class DecisionChooser(Protocol):
    def check_available(self) -> None: ...

    async def choose(self, context: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return Decisions answers: [{name, type: choice|refusal, choice?, confidence?}]."""
        ...


class OpenAIDecisions:
    def __init__(self, settings: Settings, budget: ApiBudget, purpose: str = PURPOSE) -> None:
        self.settings, self.budget, self.purpose = settings, budget, purpose

    @property
    def configured(self) -> bool:
        return bool(self.settings.ocr.openai_api_key) and self.settings.voice.provider == "openai"

    def check_available(self) -> None:
        if not self.configured:
            raise reject("QA_NOT_CONFIGURED", "質問応答用AIの接続設定がありません。", 503)
        if self.budget.status()["remaining"] < 1:
            raise reject("AI_API_BUDGET_EXHAUSTED", "外部AI APIの許可枠に達しました。", 429)

    async def choose(self, context: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.check_available()
        key = self.settings.ocr.openai_api_key
        assert key is not None
        body = {
            "model": MODEL,
            "input": context,
            "questions": [{"type": "choice", **question} for question in questions],
        }
        try:
            # ApiBudget reserves before the actual send and never refunds failures.
            async with asyncio.timeout(TIMEOUT_SECONDS):
                async with self.budget.client(self.purpose) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/decisions",
                        headers={"Authorization": "Bearer " + key.get_secret_value()},
                        json=body,
                        timeout=TIMEOUT_SECONDS,
                    )
                    if not response.is_success:
                        raise reject(
                            "QA_PROVIDER_FAILED", "質問応答用AIへの要求が拒否されました。", 503
                        )
                    value = response.json()
        except (TimeoutError, httpx.TimeoutException) as error:
            raise reject("QA_TIMEOUT", "質問の解析が時間内に完了しませんでした。", 504) from error
        except httpx.HTTPError as error:
            raise reject(
                "QA_PROVIDER_FAILED", "質問応答用AIに接続できませんでした。", 503
            ) from error
        except (ValueError, json.JSONDecodeError) as error:
            raise reject("QA_RESPONSE_INVALID", "質問の解析形式が不正です。", 422) from error
        if (
            not isinstance(value, dict)
            or value.get("model") != MODEL
            or not isinstance(value.get("answers"), list)
        ):
            raise reject("QA_RESPONSE_INVALID", "候補選択の応答が不正です。", 422)
        return [answer for answer in value["answers"] if isinstance(answer, dict)]


def decode_answer(
    answers: list[dict[str, Any]], name: str, allowed: set[str], threshold: float
) -> str | None:
    """The chosen value when it is an allowed choice above ``threshold``; otherwise None."""
    decoded = decode_choice(answers, name, allowed)
    return decoded[0] if decoded is not None and decoded[1] >= threshold else None


def decode_choice(
    answers: list[dict[str, Any]], name: str, allowed: set[str]
) -> tuple[str, float] | None:
    """The chosen value and its confidence; None for a refusal or a missing answer."""
    answer = next((a for a in answers if a.get("name") == name), None)
    if answer is None or answer.get("type") != "choice":
        return None
    candidate, confidence = answer.get("choice"), answer.get("confidence")
    if (
        not isinstance(candidate, str)
        or candidate not in allowed
        or isinstance(confidence, bool)
        or not isinstance(confidence, float | int)
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise reject("QA_RESPONSE_INVALID", "候補選択の候補又は確率が不正です。", 422)
    return candidate, float(confidence)
