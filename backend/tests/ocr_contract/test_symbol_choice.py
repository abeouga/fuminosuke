"""Offline acceptance checks for a symbol written inside one physical field."""

import base64

import pytest

from app.domain.ocr_models import ReadingStatus
from app.domain.template_config import MarkOptionConfig, TemplateConfig
from app.domain.template_definition import TemplateMarkSet
from app.extraction.mark_detector import MarkDetector
from app.layout_analysis.models import LayoutDefinition, LayoutMarkSet
from app.ocr.base import OcrProviderResponse
from app.ocr.deterministic_mapper import build_mapped_document
from app.ocr.openai_provider import build_mapped_input_content
from app.ocr.transcription_models import TranscriptionBatch, TranscriptionItem
from app.services.layout_service import LayoutService
from app.services.ocr_request_preparation_service import OcrRequestPreparer
from app.services.ocr_response_finalization_service import OcrResponseFinalizer
from app.services.template_definition_service import TemplateDefinitionService
from app.validation.business_validators import DeterministicValidator
from tests.ocr_contract.test_phase5a_request_preparation_contract import (
    _crop,
    _extraction,
    _mapping,
)

SYMBOLS = ("○", "◎", "×", "△")  # noqa: RUF001 - actual allowed handwritten symbols


@pytest.mark.parametrize(
    "raw,status,expected",
    [
        *[(symbol, "readable", "readable") for symbol in SYMBOLS],
        (None, "blank", "blank"),
        ("○", "uncertain", "uncertain"),
        ("□", "readable", "uncertain"),
        ("○・△", "readable", "uncertain"),
    ],
)
def test_symbol_crop_candidates_and_result_keep_physical_identity(raw, status, expected):
    extraction = _extraction("symbol-offline")
    mapping = _mapping(
        "symbol",
        "customFields.symbol",
        "判定",
        1,
        mapping_type="mark",
        mark_recognition_mode="symbol",
    )
    mapping.mark_options = [
        MarkOptionConfig(id=f"s{i}", label=s, value=s, x=0, y=0, width=30, height=20)
        for i, s in enumerate(SYMBOLS)
    ]
    extraction.config = extraction.config.model_copy(
        update={
            "field_mappings": [mapping],
            "document_identity": None,
            "document_identity_fields": [],
        }
    )
    # The saved configuration, including the recognition mode, must round-trip.
    extraction.config = TemplateConfig.model_validate(extraction.config.model_dump(by_alias=True))
    crop = _crop(mapping, 1, ink=raw is not None)
    extraction.crops = {"symbol": crop}
    extraction.marks = []
    assert MarkDetector(extraction.config).detect_all(crop.aligned_color, crop.aligned_color) == []
    prepared = OcrRequestPreparer().prepare(
        extraction=extraction, job_snapshot={}, provider_name="openai", should_cancel=None
    )
    assert prepared.openai_mark_ids == {"symbol"}
    (region,) = prepared.request.regions
    assert region.candidates == SYMBOLS
    assert region.bounding_box == crop.bbox
    content = build_mapped_input_content(prepared.request, image_detail="high")
    text = "\n".join(item["text"] for item in content if item["type"] == "input_text")
    assert "candidateType:symbol_choice" in text
    assert "do_not_force_a_choice" in text
    images = [
        base64.b64decode(item["image_url"].split(",")[1])
        for item in content
        if item["type"] == "input_image"
    ]
    assert images == [region.image_png]
    document = build_mapped_document(
        prepared.request,
        TranscriptionBatch(items=[TranscriptionItem(mapping_id="symbol", raw=raw, status=status)]),
    )
    finalized = OcrResponseFinalizer(DeterministicValidator()).finalize(
        prepared=prepared,
        extraction=extraction,
        job_snapshot={},
        provider_name="openai",
        provider_response=OcrProviderResponse(
            document=document,
            provider="openai",
            model_id="offline",
            prompt_version=None,
            api_call_count=0,
        ),
    )
    reading = finalized.response.document.custom_fields["symbol"]
    assert reading.status == ReadingStatus(expected)
    assert reading.normalized == (raw if expected == "readable" else None)
    assert reading.source_region_id == "symbol"
    assert reading.bounding_box is not None
    assert tuple(reading.bounding_box.model_dump().values()) == crop.bbox


def test_symbol_metadata_survives_layout_mapping_and_generic_definition():
    payload = {
        "id": "choice",
        "targetPath": "customFields.symbol",
        "recognitionMode": "symbol",
        "selectionMode": "single",
        "options": [
            {
                "id": "circle",
                "label": "○",
                "value": "circle",
                "x": 0,
                "y": 0,
                "width": 30,
                "height": 20,
            }
        ],
    }
    layout = LayoutDefinition.model_validate(
        {
            "canonicalWidth": 100,
            "canonicalHeight": 100,
            "detector": "test",
            "ocrAvailable": False,
            "cells": [
                {
                    "id": "physical",
                    "role": "mark",
                    "x": 10,
                    "y": 20,
                    "width": 30,
                    "height": 20,
                    "confidence": 1,
                    "markSets": [payload],
                }
            ],
            "relations": [],
            "warnings": [],
        }
    )
    (mapping,) = LayoutService._as_field_mappings(layout)
    assert mapping.mapping_id == "layout_physical_choice"
    assert mapping.target_path == "customFields.symbol"
    assert mapping.mark_recognition_mode == "symbol"
    assert (mapping.x, mapping.y, mapping.width, mapping.height) == (10, 20, 30, 20)
    (cell,) = TemplateDefinitionService._cells_from_layout(layout)
    assert cell.mark_sets[0].model_dump(by_alias=True) == payload


@pytest.mark.parametrize("model", [LayoutMarkSet, TemplateMarkSet])
def test_symbol_mode_rejects_multiple_selection(model):
    with pytest.raises(ValueError, match="single selection"):
        model.model_validate(
            {
                "id": "choice",
                "targetPath": "customFields.symbol",
                "recognitionMode": "symbol",
                "selectionMode": "multiple",
                "options": [
                    {
                        "id": "circle",
                        "label": "○",
                        "value": "circle",
                        "x": 0,
                        "y": 0,
                        "width": 30,
                        "height": 20,
                    }
                ],
            }
        )
