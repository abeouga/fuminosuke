import asyncio
import hashlib
import io
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import SecretStr

from app.core.config import OcrSettings
from app.core.errors import AppError
from app.domain.ocr_models import ReadingStatus
from app.ocr.base import OcrProviderRequest, OcrRegionInput
from app.ocr.openai_provider import (
    OpenAIOcrProvider,
    _deduplicate_requested_items,
    _expand_compact_item,
    _TranscriptionResponse,
)
from app.ocr.transcription_models import (
    CompactTranscriptionBatch,
    CompactTranscriptionItem,
    TranscriptionBatch,
    TranscriptionItem,
)


def test_retry_and_fallback_calls_share_one_exact_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=4, max_retries=1))
    calls: list[tuple[str, int]] = []

    async def fake_call(
        request: OcrProviderRequest,
        model: str,
        call_count: int,
    ) -> _TranscriptionResponse:
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
    ) -> _TranscriptionResponse:
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
    ) -> _TranscriptionResponse:
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


def test_large_request_is_batched_and_combined_without_missing_mappings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=3, max_retries=0))
    calls: list[list[str]] = []
    regions = [
        OcrRegionInput(
            region_id=f"cell_{index:04d}",
            image_png=b"crop",
            target_path=f"customFields.field_{index:04d}",
        )
        for index in range(61)
    ]

    async def fake_call(
        request: OcrProviderRequest,
        model: str,
        call_count: int,
    ) -> _TranscriptionResponse:
        calls.append([region.region_id for region in request.regions])
        return _response(
            model,
            call_count,
            items=[
                TranscriptionItem(
                    mapping_id=region.region_id,
                    raw=region.region_id,
                    status=ReadingStatus.READABLE,
                )
                for region in request.regions
            ],
        )

    monkeypatch.setattr(provider, "_call", fake_call)
    try:
        response = asyncio.run(provider.extract(OcrProviderRequest(regions=regions)))
    finally:
        asyncio.run(provider._client.close())

    assert [len(batch) for batch in calls] == [30, 30, 1]
    assert response.api_call_count == 3
    assert len(response.document.custom_fields) == 61
    assert response.document.custom_fields["field_0060"].raw == "cell_0060"


def test_document_identity_region_is_isolated_from_regular_batches() -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=4, max_retries=0))
    regions = [
        OcrRegionInput(
            region_id=f"cell_{index:04d}",
            image_png=b"crop",
            target_path=f"customFields.field_{index:04d}",
        )
        for index in range(31)
    ]
    regions.append(
        OcrRegionInput(
            region_id="identity_cell",
            image_png=b"identity",
            target_path="document.unifiedEarTag",
            is_document_identity=True,
            identity_value_type="digits",
            identity_exact_length=10,
        )
    )

    try:
        batches = provider._batched_requests(OcrProviderRequest(regions=regions))
    finally:
        asyncio.run(provider._client.close())

    assert [len(batch.regions) for batch in batches] == [30, 1, 1]
    assert batches[-1].regions[0].region_id == "identity_cell"


def test_independent_batches_use_bounded_parallelism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=3, max_retries=0))
    active = 0
    max_active = 0
    regions = [
        OcrRegionInput(
            region_id=f"cell_{index:04d}",
            image_png=b"crop",
            target_path=f"customFields.field_{index:04d}",
        )
        for index in range(61)
    ]

    async def fake_call(
        request: OcrProviderRequest,
        model: str,
        call_count: int,
    ) -> _TranscriptionResponse:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _response(
            model,
            call_count,
            items=[
                TranscriptionItem(
                    mapping_id=region.region_id,
                    raw="1",
                    status=ReadingStatus.READABLE,
                )
                for region in request.regions
            ],
        )

    monkeypatch.setattr(provider, "_call", fake_call)
    try:
        response = asyncio.run(provider.extract(OcrProviderRequest(regions=regions)))
    finally:
        asyncio.run(provider._client.close())

    assert max_active == 2
    assert response.api_call_count == 3


def test_uncertain_cells_are_retried_alone_at_high_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(max_calls=3, max_retries=0).model_copy(
        update={
            "initial_image_detail": "low",
            "retry_uncertain_high_detail": True,
        }
    )
    provider = OpenAIOcrProvider(settings)
    regions = [
        OcrRegionInput(
            region_id="cell_a",
            image_png=b"a",
            target_path="customFields.a",
        ),
        OcrRegionInput(
            region_id="cell_b",
            image_png=b"b",
            target_path="customFields.b",
        ),
    ]
    high_detail_calls: list[tuple[list[str], str]] = []

    async def fake_initial(
        request: OcrProviderRequest,
        model: str,
        call_count: int,
    ) -> _TranscriptionResponse:
        return _response(
            model,
            call_count,
            items=[
                TranscriptionItem(
                    mapping_id=request.regions[0].region_id,
                    raw="12?",
                    status=ReadingStatus.UNCERTAIN,
                ),
                TranscriptionItem(
                    mapping_id=request.regions[1].region_id,
                    raw="34",
                    status=ReadingStatus.READABLE,
                ),
            ],
        )

    async def fake_high_detail(
        request: OcrProviderRequest,
        model: str,
        call_count: int,
        *,
        image_detail: str,
    ) -> _TranscriptionResponse:
        high_detail_calls.append(([item.region_id for item in request.regions], image_detail))
        return _response(
            model,
            call_count,
            items=[
                TranscriptionItem(
                    mapping_id=request.regions[0].region_id,
                    raw="123",
                    status=ReadingStatus.READABLE,
                )
            ],
        )

    monkeypatch.setattr(provider, "_call", fake_initial)
    monkeypatch.setattr(provider, "_call_detail", fake_high_detail)
    try:
        response = asyncio.run(provider.extract(OcrProviderRequest(regions=regions)))
    finally:
        asyncio.run(provider._client.close())

    assert high_detail_calls == [(["cell_a"], "high")]
    assert response.api_call_count == 2
    assert response.document.custom_fields["a"].raw == "123"
    assert response.document.custom_fields["b"].raw == "34"


def test_compact_wire_item_expands_to_internal_review_model() -> None:
    item = _expand_compact_item(
        CompactTranscriptionItem(i="c0", a="000000", v="123", s="u", c=None),
        "layout_cell_0123",
    )

    assert item.mapping_id == "layout_cell_0123"
    assert item.raw == "123"
    assert item.status is ReadingStatus.UNCERTAIN
    assert item.review_reason == "MODEL_UNCERTAIN"


def test_openai_call_uses_short_ids_compact_schema_and_configured_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=2, max_retries=0))
    captured: dict[str, object] = {}
    image_png = _png_bytes(40)

    async def fake_parse(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            output_parsed=CompactTranscriptionBatch(
                items=[
                    CompactTranscriptionItem(
                        i="unexpected",
                        a=_expected_association_token(
                            "c0", image_png
                        ),
                        v="123",
                        s="r",
                        c=None,
                    )
                ]
            ),
            incomplete_details=None,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            id="response-id",
            status="completed",
        )

    monkeypatch.setattr(provider._client.responses, "parse", fake_parse)
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="layout_cell_very_long_identifier",
                image_png=image_png,
                target_path="customFields.value",
            )
        ]
    )
    try:
        response = asyncio.run(provider._call(request, "primary", 1))
    finally:
        asyncio.run(provider._client.close())

    input_text = str(captured["input"])
    assert captured["text_format"] is CompactTranscriptionBatch
    assert "mappingId:c0" in input_text
    assert "layout_cell_very_long_identifier" not in input_text
    assert "'detail': 'high'" in input_text
    assert response.batch.items[0].mapping_id == "layout_cell_very_long_identifier"


def test_openai_call_preserves_missing_mapping_as_uncertain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=2, max_retries=0))
    missing_image = _png_bytes(80)
    returned_image = _png_bytes(120)

    async def fake_parse(**kwargs: object) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            output_parsed=CompactTranscriptionBatch(
                items=[
                    CompactTranscriptionItem(
                        i="c1",
                        a=_expected_association_token("c1", returned_image),
                        v="456",
                        s="r",
                        c=None,
                    )
                ]
            ),
            incomplete_details=None,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            id="response-id",
            status="completed",
        )

    monkeypatch.setattr(provider._client.responses, "parse", fake_parse)
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="missing_cell",
                image_png=missing_image,
                target_path="customFields.missing",
            ),
            OcrRegionInput(
                region_id="returned_cell",
                image_png=returned_image,
                target_path="customFields.returned",
            ),
        ]
    )
    try:
        response = asyncio.run(provider._call(request, "primary", 1))
    finally:
        asyncio.run(provider._client.close())

    by_id = {item.mapping_id: item for item in response.batch.items}
    assert by_id["returned_cell"].raw == "456"
    assert by_id["missing_cell"].status is ReadingStatus.UNCERTAIN
    assert by_id["missing_cell"].review_reason == "OCR_MAPPING_MISSING"


def test_openai_call_records_request_raw_and_expanded_mapping_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=2, max_retries=0))
    image_png = _png_bytes(160)

    async def fake_parse(**kwargs: object) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            output_parsed=CompactTranscriptionBatch(
                items=[
                    CompactTranscriptionItem(
                        i="c0",
                        a=_expected_association_token("c0", image_png),
                        v="関東",
                        s="r",
                        c=None,
                    )
                ]
            ),
            incomplete_details=None,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            id="response-diagnostic",
            status="completed",
        )

    monkeypatch.setattr(provider._client.responses, "parse", fake_parse)
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="layout_xlsx_S76",
                image_png=image_png,
                target_path="customFields.region",
                source_region_id="xlsx_S76",
            )
        ]
    )
    try:
        response = asyncio.run(provider._call(request, "primary", 2))
    finally:
        asyncio.run(provider._client.close())

    manifest = response.diagnostic["requestManifest"]
    assert len(manifest) == 1
    assert manifest[0]["sequence"] == 0
    assert manifest[0]["compactId"] == "c0"
    assert manifest[0]["originalMappingId"] == "layout_xlsx_S76"
    assert manifest[0]["targetPath"] == "customFields.region"
    assert manifest[0]["sourceRegionId"] == "xlsx_S76"
    assert manifest[0]["associationToken"] == _expected_association_token("c0", image_png)
    assert manifest[0]["sourceImageSha256"] == hashlib.sha256(image_png).hexdigest()
    assert manifest[0]["preparedImageSha256"] == manifest[0]["requestImageSha256"]
    assert manifest[0]["imageMatchesPreparedRequest"] is True
    assert manifest[0]["contentTextIndex"] == 1
    assert manifest[0]["contentImageIndex"] == 2
    assert manifest[0]["imageDetail"] == "high"
    assert response.diagnostic["rawItems"] == [
        {
            "responseIndex": 0,
            "compactId": "c0",
            "associationToken": _expected_association_token("c0", image_png),
            "expectedAssociationToken": _expected_association_token("c0", image_png),
            "associationMatches": True,
            "value": "関東",
            "status": "r",
        }
    ]
    assert response.diagnostic["expandedItems"] == [
        {
            "expandedIndex": 0,
            "compactId": "c0",
            "originalMappingId": "layout_xlsx_S76",
            "value": "関東",
            "status": "readable",
            "reviewReason": None,
        }
    ]
    assert response.diagnostic["batchNumber"] == 2
    assert response.diagnostic["requestId"] == "response-diagnostic"


def test_openai_call_rejects_value_whose_visual_association_token_is_shifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=2, max_retries=0))
    first_image = _png_bytes(180)
    second_image = _png_bytes(220)
    first_token = _expected_association_token("c0", first_image)

    async def fake_parse(**kwargs: object) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            output_parsed=CompactTranscriptionBatch(
                items=[
                    CompactTranscriptionItem(
                        i="c0", a=first_token, v=None, s="u", c=None
                    ),
                    CompactTranscriptionItem(
                        i="c1", a=first_token, v="関東", s="r", c=None
                    ),
                ]
            ),
            incomplete_details=None,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            id="response-shifted",
            status="completed",
        )

    monkeypatch.setattr(provider._client.responses, "parse", fake_parse)
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="first",
                image_png=first_image,
                target_path="customFields.first",
            ),
            OcrRegionInput(
                region_id="second",
                image_png=second_image,
                target_path="customFields.second",
            ),
        ]
    )
    try:
        response = asyncio.run(provider._call(request, "primary", 1))
    finally:
        asyncio.run(provider._client.close())

    by_id = {item.mapping_id: item for item in response.batch.items}
    assert by_id["first"].status is ReadingStatus.UNCERTAIN
    assert by_id["second"].raw is None
    assert by_id["second"].status is ReadingStatus.UNCERTAIN
    assert by_id["second"].review_reason == "OCR_ASSOCIATION_MISMATCH"
    assert response.diagnostic["associationMismatchCompactIds"] == ["c1"]


def test_batch_count_must_fit_call_budget() -> None:
    provider = OpenAIOcrProvider(_settings(max_calls=2, max_retries=0))
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id=f"cell_{index:04d}",
                image_png=b"crop",
                target_path=f"customFields.field_{index:04d}",
            )
            for index in range(61)
        ]
    )
    try:
        with pytest.raises(AppError) as captured:
            asyncio.run(provider.extract(request))
    finally:
        asyncio.run(provider._client.close())

    assert captured.value.code == "OCR_CALL_BUDGET_TOO_SMALL"


def test_duplicate_mapping_results_are_collapsed_and_conflicts_require_review() -> None:
    identical = TranscriptionItem(
        mapping_id="cell_0001",
        raw="123",
        status=ReadingStatus.READABLE,
    )
    collapsed = _deduplicate_requested_items(
        [
            identical,
            identical.model_copy(),
            TranscriptionItem(
                mapping_id="cell_0002",
                raw="456",
                status=ReadingStatus.READABLE,
            ),
            TranscriptionItem(
                mapping_id="cell_0002",
                raw="459",
                status=ReadingStatus.READABLE,
            ),
        ]
    )

    assert len(collapsed) == 2
    assert collapsed[0].status is ReadingStatus.READABLE
    assert collapsed[1].status is ReadingStatus.UNCERTAIN
    assert collapsed[1].raw == "456"
    assert collapsed[1].alternatives == ["459"]
    assert collapsed[1].review_reason == "DUPLICATE_TRANSCRIPTION_CONFLICT"


def _response(
    model: str,
    call_count: int,
    *,
    items: list[TranscriptionItem] | None = None,
) -> _TranscriptionResponse:
    del call_count
    return _TranscriptionResponse(
        batch=TranscriptionBatch(items=items or []),
        model_id=model,
        input_tokens=10,
        output_tokens=20,
        estimated_cost=Decimal("0.0001"),
        request_id="response-id",
        duration_ms=1.0,
    )


def _expected_association_token(compact_id: str, image_png: bytes) -> str:
    digest_prefix = hashlib.sha256(compact_id.encode("ascii") + image_png).hexdigest()[:12]
    return f"{int(digest_prefix, 16) % 1_000_000:06d}"


def _png_bytes(gray: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (40, 20), (gray, gray, gray)).save(output, format="PNG")
    return output.getvalue()
