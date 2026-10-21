"""Shared provider wire configuration, independent of editing transports."""

from __future__ import annotations

import copy
from typing import Any

from websockets.asyncio.client import connect

from app.core.config import Settings
from app.editing.errors import reject

UPSTREAM_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
SESSION_CONFIG: dict[str, Any] = {
    "type": "session.update",
    "session": {
        "type": "transcription",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "transcription": {
                    "model": "gpt-live-transcribe",
                    "languages": ["ja"],
                    "delay": "low",
                },
                "turn_detection": None,
            }
        },
    },
}


def transcription_config(
    keywords: list[str], preceding: str = "", question: str = ""
) -> dict[str, Any]:
    config = copy.deepcopy(SESSION_CONFIG)
    transcription = config["session"]["audio"]["input"]["transcription"]
    transcription["prompt"] = (
        "日本語で帳票の欄を編集する音声。欄名と変更指示を聞き取る。"
        "数字を一桁ずつ読んだ値は先頭のゼロを含めて文字起こしする。"
    )
    if question.strip():
        # A short answer (三行目) is easily misheard without the question it answers.
        transcription["prompt"] += "この音声は質問「" + question.strip()[:200] + "」への答え。"
    if preceding.strip():
        # A pause splits one sentence into pieces; the next piece continues this text.
        transcription["prompt"] += "この音声は次の発言の続き。" + preceding.strip()[-200:]
    if keywords:
        transcription["keywords"] = keywords
    return config


def upstream_connection(settings: Settings) -> Any:
    key = settings.ocr.openai_api_key
    if settings.voice.provider != "openai" or not key:
        raise reject("VOICE_NOT_CONFIGURED", "音声APIが設定されていません。", 503)
    # No client-provided destination, proxy, redirect, reconnect or SDK retry.
    return connect(
        UPSTREAM_URL,
        additional_headers={"Authorization": f"Bearer {key.get_secret_value()}"},
        proxy=None,
        open_timeout=10,
        close_timeout=3,
        max_size=262144,
        max_queue=16,
    )
