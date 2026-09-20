"""Offline SDK boundary: accepted instructions survive batching and retries."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

from app.ocr import openai_provider as module
from app.ocr.base import OcrProviderRequest, OcrRegionInput
from app.ocr.transcription_models import CompactTranscriptionBatch, CompactTranscriptionItem
from tests.unit.test_openai_call_budget import _expected_association_token, _png_bytes, _settings


def test_accepted_prompt_preserves_sdk_payload_when_runtime_prompt_changes(monkeypatch):
    image = _png_bytes(160)
    sent = []

    async def parse(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(
            output_parsed=CompactTranscriptionBatch(items=[CompactTranscriptionItem(
                i="c0", a=_expected_association_token("c0", image), v="fixture", s="r", c=None,
            )]),
            incomplete_details=None, usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            id="offline", status="completed",
        )

    provider = object.__new__(module.OpenAIOcrProvider)
    provider._settings = _settings(max_calls=2, max_retries=0)
    provider._client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    legacy = OcrProviderRequest(regions=[OcrRegionInput(
        region_id="arbitrary-id", target_path="customFields.arbitrary", image_png=image,
    )])
    monkeypatch.setattr(module, "load_transcription_prompt", lambda: "accepted prompt")
    before = asyncio.run(provider._call(legacy, "primary", 1))
    pinned = replace(legacy, transcription_prompt="accepted prompt")

    def unavailable():
        raise FileNotFoundError("Current prompt is no longer installed")

    monkeypatch.setattr(module, "load_transcription_prompt", unavailable)
    after = asyncio.run(provider._call(pinned, "primary", 1))
    assert sent[0] == sent[1]
    assert before.batch == after.batch


def test_batch_and_retry_subset_preserve_accepted_prompt():
    provider = object.__new__(module.OpenAIOcrProvider)
    request = OcrProviderRequest(
        regions=[OcrRegionInput(
            region_id=str(i), image_png=b"fixture", is_document_identity=i == 0,
        ) for i in range(62)],
        transcription_prompt="accepted prompt",
    )
    batches = provider._batched_requests(request)
    assert len(batches) == 4
    assert all(batch.transcription_prompt == "accepted prompt" for batch in batches)
    retry = provider._request_with_regions(batches[0], batches[0].regions[:1])
    assert retry.transcription_prompt == "accepted prompt"
