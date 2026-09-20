"""Accepted prompt bytes and response-model schema for the paged OCR route only.

This does not qualify SDK binaries, native dependencies, or the renderer runtime.
"""

from __future__ import annotations

import hashlib
from typing import Any, Never

from app.domain.print_layout import print_error
from app.ocr.prompt_loader import load_transcription_prompt
from app.ocr.transcription_models import CompactTranscriptionBatch
from app.services.print_layout_service import digest

ADAPTER = "paged-transcription-resources-v1"


def _fail(detail: str, code: str = "PAGE_TRANSCRIPTION_RESOURCES_INVALID") -> Never:
    raise print_error(detail, code, 409)


def capture_transcription_resources() -> dict[str, Any]:
    try:
        prompt = load_transcription_prompt()
    except (OSError, UnicodeError) as exc:
        _fail(f"受付用OCR指示文を読み込めません: {type(exc).__name__}")
    if not prompt.strip():
        _fail("受付用OCR指示文が空です。")
    schema = CompactTranscriptionBatch.model_json_schema()
    return {
        "adapter": ADAPTER,
        "prompt": {"text": prompt, "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()},
        "responseModelSchema": {"json": schema, "sha256": digest(schema)},
    }


def accepted_transcription_prompt(job_snapshot: dict[str, Any]) -> str:
    """Validate accepted content without re-reading the mutable prompt file."""
    bundle = job_snapshot.get("jobPages")
    if not isinstance(bundle, dict) or digest(bundle) != job_snapshot.get("jobPagesSha256"):
        _fail("受付ページsnapshotの整合性を確認できません。")
    resources = bundle.get("transcriptionResources")
    if not isinstance(resources, dict) or resources.get("adapter") != ADAPTER:
        _fail(
            "受付時のOCR指示文・応答schemaが保存されていません。新しい読み取りが必要です。",
            "PAGE_TRANSCRIPTION_RESOURCES_REQUIRED",
        )
    if set(resources) != {"adapter", "prompt", "responseModelSchema"}:
        _fail("未対応のOCRリソースsnapshotです。")
    prompt = resources.get("prompt")
    schema = resources.get("responseModelSchema")
    if (
        not isinstance(prompt, dict) or set(prompt) != {"text", "sha256"}
        or not isinstance(prompt.get("text"), str) or not prompt["text"].strip()
        or not isinstance(schema, dict) or set(schema) != {"json", "sha256"}
        or not isinstance(schema.get("json"), dict)
    ):
        _fail("受付時のOCR指示文・応答schemaが不正です。")
    text: str = prompt["text"]
    try:
        prompt_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeError:
        _fail("受付時のOCR指示文が不正です。")
    if prompt_sha != prompt["sha256"] or digest(schema["json"]) != schema["sha256"]:
        _fail("受付時のOCR指示文・応答schemaのSHAが一致しません。")
    if schema["json"] != CompactTranscriptionBatch.model_json_schema():
        _fail(
            "受付時と実行環境のOCR応答schemaが異なります。",
            "PAGE_TRANSCRIPTION_SCHEMA_CHANGED",
        )
    return text
