from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.models import JobArtifact
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.domain.ocr_models import ReadingStatus
from app.domain.template_config import (
    DocumentIdentityConfig,
    FieldMappingConfig,
    MarkOptionConfig,
    TemplateConfig,
    build_blank_template_config,
)
from app.extraction.region_cropper import RegionCrops
from app.ocr.base import OcrProvider, OcrProviderRequest, OcrProviderResponse
from app.repositories.job_repository import JobRepository
from app.services.extraction_service import ExtractionOutput
from app.services.ocr_request_preparation_service import OcrRequestPreparer
from app.services.ocr_workflow_service import OcrWorkflowService
from app.storage.local_storage import LocalStorageService


class _RequestCaptured(Exception):
    def __init__(self, request: OcrProviderRequest) -> None:
        super().__init__("request captured before provider response processing")
        self.request = request


class _CaptureProvider:
    async def extract(self, request: OcrProviderRequest) -> OcrProviderResponse:
        raise _RequestCaptured(request)


class _CapturingWorkflow(OcrWorkflowService):
    def _create_provider(self, provider_name: str) -> OcrProvider:
        assert provider_name == "openai"
        return _CaptureProvider()


def _mapping(
    mapping_id: str,
    target_path: str,
    label: str,
    seed: int,
    *,
    printed_label_text: str = "",
    mapping_type: str = "text",
    mark_recognition_mode: str = "automatic",
) -> FieldMappingConfig:
    options = (
        [
            MarkOptionConfig(
                id=f"{mapping_id}-yes",
                label="Yes",
                value="yes",
                x=0,
                y=0,
                width=15,
                height=20,
            ),
            MarkOptionConfig(
                id=f"{mapping_id}-no",
                label="No",
                value="no",
                x=15,
                y=0,
                width=15,
                height=20,
            ),
        ]
        if mapping_type == "mark"
        else []
    )
    return FieldMappingConfig(
        mapping_id=mapping_id,
        target_path=target_path,
        label=label,
        x=10 + seed,
        y=20 + seed,
        width=30,
        height=20,
        padding=0,
        printed_label_text=printed_label_text,
        mapping_type=mapping_type,  # type: ignore[arg-type]
        mark_recognition_mode=mark_recognition_mode,  # type: ignore[arg-type]
        mark_selection_mode="single",
        mark_options=options,
    )


def _template_config() -> TemplateConfig:
    mappings = [
        _mapping(
            "identity",
            "document.unifiedEarTag",
            "Identity",
            1,
            printed_label_text="個体識別番号",
        ),
        _mapping("sire-0", "saleCalves.0.sireName", "Sire 0", 2),
        _mapping("price-0", "saleCalves.0.priceYen", "Price 0", 3),
        _mapping("notes-0", "saleCalves.0.notes", "Notes 0", 4),
        _mapping("price-1", "saleCalves.1.priceYen", "Price 1", 5),
        _mapping("price-2", "saleCalves.2.priceYen", "Price 2", 6),
        _mapping("calving-parity", "calvings.0.parity", "Parity", 7),
        _mapping("calving-date", "calvings.0.calvingDate", "Calving date", 8),
        _mapping(
            "mark-local",
            "customFields.localMark",
            "Local mark",
            9,
            mapping_type="mark",
        ),
        _mapping(
            "mark-manual",
            "customFields.manualMark",
            "Manual mark",
            10,
            mapping_type="mark",
            mark_recognition_mode="manual",
        ),
        _mapping(
            "mark-openai",
            "customFields.openAiMark",
            "OpenAI mark",
            11,
            mapping_type="mark",
        ),
    ]
    base = build_blank_template_config(
        template_id="phase5a-template",
        template_version=7,
        name="Phase 5A request contract",
        canonical_width=200,
        canonical_height=200,
    )
    raw = base.model_dump(mode="json", by_alias=True)
    raw["fieldMappings"] = [
        mapping.model_dump(mode="json", by_alias=True) for mapping in mappings
    ]
    raw["documentIdentityFields"] = [
        DocumentIdentityConfig(
            mapping_id="identity",
            target_path="document.unifiedEarTag",
            label="Identity",
            value_type="digits",
            exact_length=10,
        ).model_dump(mode="json", by_alias=True)
    ]
    return TemplateConfig.model_validate(raw)


def _crop(mapping: FieldMappingConfig, seed: int, *, ink: bool) -> RegionCrops:
    aligned = np.full((20, 30, 3), 10 + seed * 17, dtype=np.uint8)
    handwriting = np.full((20, 30, 3), 255, dtype=np.uint8)
    handwriting[0, 0] = 240 + seed
    if ink:
        handwriting[7:9, 10:12] = 0
    gray = np.full((20, 30), 255, dtype=np.uint8)
    return RegionCrops(
        config=mapping.as_region(),
        bbox=(mapping.x, mapping.y, mapping.width, mapping.height),
        aligned_color=aligned,
        clean_reference=np.full_like(aligned, 255),
        handwriting_only=handwriting,
        enhanced_gray=gray,
        ink_difference=np.zeros_like(gray),
    )


def _extraction(job_id: str) -> ExtractionOutput:
    config = _template_config()
    crops = {
        mapping.mapping_id: _crop(
            mapping,
            seed,
            ink=mapping.mapping_id != "price-2",
        )
        for seed, mapping in enumerate(config.field_mappings, start=1)
    }
    aligned = np.full((200, 200, 3), 137, dtype=np.uint8)
    aligned[0, 0] = (1, 2, 3)
    return ExtractionOutput(
        job_id=job_id,
        attempt_number=1,
        template_version_id="phase5a-template-version",
        aligned_rgb=aligned,
        template_rgb=np.full_like(aligned, 255),
        crops=crops,
        marks=[
            {
                "mappingId": "mark-local",
                "optionValue": "yes",
                "status": "detected",
            },
            {
                "mappingId": "mark-local",
                "optionValue": "no",
                "status": "not_detected",
            },
        ],
        quality={},
        unknown_overlap_regions=[],
        artifacts={},
        config=config,
    )


def _snapshot(*, send_full_document: bool) -> dict[str, object]:
    return {
        "ocrEnabled": True,
        "ocrProvider": "openai",
        "fieldMappingHash": "phase5a-frozen-mapping-hash",
        "sendFullDocumentToOpenAi": send_full_document,
        "sireNamePool": ["Sire A", "Sire B"],
        "sireNameAllowedSuffixes": ["ET"],
        "saleNoteSexSymbolPool": ["♂", "♀", "invalid"],
        "templateCandidatePools": [
            {
                "targetPattern": "document.unifiedEarTag",
                "candidates": ["0123456789", "9876543210"],
                "allowedSuffixes": ["X"],
            }
        ],
        "recordStructure": {
            "sections": [{"id": "calving", "collectionPath": "calvings"}],
            "groups": [
                {
                    "id": "calving-1",
                    "sectionId": "calving",
                    "order": 1,
                    "recordKeyField": "parity",
                    "recordKeyValue": "1",
                }
            ],
        },
    }


def _canonical_request(request: OcrProviderRequest) -> dict[str, object]:
    return {
        "templateId": request.template_id,
        "templateVersion": request.template_version,
        "fullDocumentSha256": (
            hashlib.sha256(request.full_document_png).hexdigest()
            if request.full_document_png is not None
            else None
        ),
        "shouldCancelConfigured": request.should_cancel is not None,
        "regions": [
            {
                "regionId": region.region_id,
                "sourceRegionId": region.source_region_id,
                "targetPath": region.target_path,
                "boundingBox": region.bounding_box,
                "imageSha256": hashlib.sha256(region.image_png).hexdigest(),
                "handwritingSha256": (
                    hashlib.sha256(region.handwriting_png).hexdigest()
                    if region.handwriting_png is not None
                    else None
                ),
                "candidates": region.candidates,
                "allowedSuffixes": region.allowed_suffixes,
                "candidateType": region.candidate_type,
                "transcriptionFormat": region.transcription_format,
                "printedLabelExcluded": region.printed_label_excluded,
                "printedLabelText": region.printed_label_text,
                "isDocumentIdentity": region.is_document_identity,
                "identityValueType": region.identity_value_type,
                "identityExactLength": region.identity_exact_length,
            }
            for region in request.regions
        ],
    }


EXPECTED_REGIONS = [
    {
        "regionId": "identity",
        "sourceRegionId": "identity",
        "targetPath": "document.unifiedEarTag",
        "boundingBox": (11, 21, 30, 20),
        "imageSha256": "733191f334c08480071f9ee8a2aec902c6b4b456b542a68c8fabb01dcb564fb4",
        "handwritingSha256": None,
        "candidates": ("0123456789", "9876543210"),
        "allowedSuffixes": ("X",),
        "candidateType": "managed_text",
        "transcriptionFormat": None,
        "printedLabelExcluded": True,
        "printedLabelText": "個体識別番号",
        "isDocumentIdentity": True,
        "identityValueType": "digits",
        "identityExactLength": 10,
    },
    {
        "regionId": "sire-0",
        "sourceRegionId": "sire-0",
        "targetPath": "saleCalves.0.sireName",
        "boundingBox": (12, 22, 30, 20),
        "imageSha256": "8653bb5a01e1a2c675cc2f10128ba8fbfedcf1eef399367b6b0fff3658ab5511",
        "handwritingSha256": "587d23e26c343653c8596b766b282bc600ba32ff4c108466c5618331ef5edcf4",
        "candidates": ("Sire A", "Sire B"),
        "allowedSuffixes": ("ET",),
        "candidateType": "sire_name",
        "transcriptionFormat": None,
        "printedLabelExcluded": False,
        "printedLabelText": "",
        "isDocumentIdentity": False,
        "identityValueType": None,
        "identityExactLength": None,
    },
    {
        "regionId": "price-0",
        "sourceRegionId": "price-0",
        "targetPath": "saleCalves.0.priceYen",
        "boundingBox": (13, 23, 30, 20),
        "imageSha256": "1022a318026538c44c74c5bae7a89034760cdbc31258dc765a998aab256288fb",
        "handwritingSha256": "38ea6caac245dbd464898e7325d90fb1d7f849730b8d26881b410e3155879654",
        "candidates": (),
        "allowedSuffixes": (),
        "candidateType": None,
        "transcriptionFormat": None,
        "printedLabelExcluded": False,
        "printedLabelText": "",
        "isDocumentIdentity": False,
        "identityValueType": None,
        "identityExactLength": None,
    },
    {
        "regionId": "notes-0",
        "sourceRegionId": "notes-0",
        "targetPath": "saleCalves.0.notes",
        "boundingBox": (14, 24, 30, 20),
        "imageSha256": "4b41f9c37093dae36364bf9a6046e29a3790f8fa7a1c458acdaf00b8643040a0",
        "handwritingSha256": "0c637455b0cf340ffe08a7b7e34b2f5897fe7f5ace2f0464908c198170d38311",
        "candidates": ("♂", "♀"),
        "allowedSuffixes": (),
        "candidateType": "sale_calf_note_prefix",
        "transcriptionFormat": None,
        "printedLabelExcluded": False,
        "printedLabelText": "",
        "isDocumentIdentity": False,
        "identityValueType": None,
        "identityExactLength": None,
    },
    {
        "regionId": "price-1",
        "sourceRegionId": "price-1",
        "targetPath": "saleCalves.1.priceYen",
        "boundingBox": (15, 25, 30, 20),
        "imageSha256": "d2f27ef6f0979f6a29dfa2803f8cc1862a3c79dec74266612c8873f82a26510c",
        "handwritingSha256": "f788c47c3578af61f117428f77fd885c17697e0140326a84aed3306c422d5419",
        "candidates": (),
        "allowedSuffixes": (),
        "candidateType": None,
        "transcriptionFormat": None,
        "printedLabelExcluded": False,
        "printedLabelText": "",
        "isDocumentIdentity": False,
        "identityValueType": None,
        "identityExactLength": None,
    },
    {
        "regionId": "calving-date",
        "sourceRegionId": "calving-date",
        "targetPath": "calvings.0.calvingDate",
        "boundingBox": (18, 28, 30, 20),
        "imageSha256": "8e09f18b538ceaf16b178272d2e3658ad3f5436c793ed85d1059485174803604",
        "handwritingSha256": "c9312e7663e97a4a286dbf08b502df488aed73589a4c50a4dc70f2e6cb0f0f86",
        "candidates": (),
        "allowedSuffixes": (),
        "candidateType": None,
        "transcriptionFormat": "japanese_era_date_numbers",
        "printedLabelExcluded": False,
        "printedLabelText": "",
        "isDocumentIdentity": False,
        "identityValueType": None,
        "identityExactLength": None,
    },
    {
        "regionId": "mark-openai",
        "sourceRegionId": "mark-openai",
        "targetPath": "customFields.openAiMark",
        "boundingBox": (21, 31, 30, 20),
        "imageSha256": "3434801b65506c461b5f3c128d13b7142f835a356f0824af537b4f77dc4d7adf",
        "handwritingSha256": "ba837f892ee155fa1a847b558cd1c07a4c1b98b5b18e84098c483a446fdce94c",
        "candidates": ("Yes", "No"),
        "allowedSuffixes": (),
        "candidateType": "mark_choice",
        "transcriptionFormat": None,
        "printedLabelExcluded": False,
        "printedLabelText": "",
        "isDocumentIdentity": False,
        "identityValueType": None,
        "identityExactLength": None,
    },
]


EXPECTED_MAPPED_CELL_ORDER = [
    "identity",
    "sire-0",
    "price-0",
    "notes-0",
    "price-1",
    "price-2",
    "calving-parity",
    "calving-date",
    "mark-openai",
    "mark-local",
    "mark-manual",
]


EXPECTED_SENT_MAPPING_IDS = {
    "identity",
    "sire-0",
    "price-0",
    "notes-0",
    "price-1",
    "calving-date",
    "mark-openai",
}


EXPECTED_MAPPED_CELL_SHA = {
    "identity": (
        "733191f334c08480071f9ee8a2aec902c6b4b456b542a68c8fabb01dcb564fb4",
        None,
    ),
    "sire-0": (
        "8653bb5a01e1a2c675cc2f10128ba8fbfedcf1eef399367b6b0fff3658ab5511",
        "587d23e26c343653c8596b766b282bc600ba32ff4c108466c5618331ef5edcf4",
    ),
    "price-0": (
        "1022a318026538c44c74c5bae7a89034760cdbc31258dc765a998aab256288fb",
        "38ea6caac245dbd464898e7325d90fb1d7f849730b8d26881b410e3155879654",
    ),
    "notes-0": (
        "4b41f9c37093dae36364bf9a6046e29a3790f8fa7a1c458acdaf00b8643040a0",
        "0c637455b0cf340ffe08a7b7e34b2f5897fe7f5ace2f0464908c198170d38311",
    ),
    "price-1": (
        "d2f27ef6f0979f6a29dfa2803f8cc1862a3c79dec74266612c8873f82a26510c",
        "f788c47c3578af61f117428f77fd885c17697e0140326a84aed3306c422d5419",
    ),
    "price-2": (
        "8a08fd19ea578a6a10aee713bd7845538ecda17f7976627d5fd8b820775556b3",
        "63dd2e2e5e161850a00582434b1ec7b4d4dab7a3c94d2a91b28e195b45529a08",
    ),
    "calving-parity": (
        "45f27ee1f2d3972e9b76d079f6d703bfce4ce6e07324ec26cb734f94af929d9b",
        "d1d45c5f1849a15c44d1a5f44dc11d2de1e20e134286c5bc4ac66213f2aed486",
    ),
    "calving-date": (
        "8e09f18b538ceaf16b178272d2e3658ad3f5436c793ed85d1059485174803604",
        "c9312e7663e97a4a286dbf08b502df488aed73589a4c50a4dc70f2e6cb0f0f86",
    ),
    "mark-openai": (
        "3434801b65506c461b5f3c128d13b7142f835a356f0824af537b4f77dc4d7adf",
        "ba837f892ee155fa1a847b558cd1c07a4c1b98b5b18e84098c483a446fdce94c",
    ),
    "mark-local": (
        "2f364f15646f1c231bbe8b9636a69420b5a234279c283d1645cf8d9e16f4e4e7",
        "04d8063a061496b7784ee8e74f6178dfd6a8cf3a421f53ae02e50954d36cea94",
    ),
    "mark-manual": (
        "55c217790f1a6542545adf75a50e9bad62e70e4450a50bd169fd4d3666dd4d96",
        "5d6843dd6c38b970b28b5c30fab3ef8dd4e44c5cf4b487ebbe907ae591393b1f",
    ),
}


EXPECTED_TARGET_PATHS = {
    "identity": "document.unifiedEarTag",
    "sire-0": "saleCalves.0.sireName",
    "price-0": "saleCalves.0.priceYen",
    "notes-0": "saleCalves.0.notes",
    "price-1": "saleCalves.1.priceYen",
    "price-2": "saleCalves.2.priceYen",
    "calving-parity": "calvings.0.parity",
    "calving-date": "calvings.0.calvingDate",
    "mark-openai": "customFields.openAiMark",
    "mark-local": "customFields.localMark",
    "mark-manual": "customFields.manualMark",
}


def test_request_preparer_matches_the_frozen_workflow_contract() -> None:
    extraction = _extraction("phase5a-direct-preparer")

    prepared = OcrRequestPreparer().prepare(
        extraction=extraction,
        job_snapshot=_snapshot(send_full_document=True),
        provider_name="openai",
        should_cancel=lambda: False,
    )

    assert _canonical_request(prepared.request) == {
        "templateId": "phase5a-template",
        "templateVersion": "7",
        "fullDocumentSha256": (
            "864b015db5f89f8369727d66e91e683e7a002c7f21b9e6991984246b90b6175f"
        ),
        "shouldCancelConfigured": True,
        "regions": EXPECTED_REGIONS,
    }
    assert prepared.mapped_cells_payload is not None
    cells = prepared.mapped_cells_payload["cells"]
    assert isinstance(cells, list)
    assert [cell["mappingId"] for cell in cells] == EXPECTED_MAPPED_CELL_ORDER
    assert {
        cell["mappingId"] for cell in cells if cell["sentToOpenAi"] is True
    } == EXPECTED_SENT_MAPPING_IDS
    assert [region.region_id for region in prepared.fixed_record_regions] == [
        "calving-parity"
    ]
    assert prepared.openai_mark_ids == {"mark-openai"}
    assert prepared.fixed_calving_keys == {"calvings.0.parity": "1"}
    assert prepared.mark_state_by_id["mark-local"] == (
        ReadingStatus.READABLE,
        "Yes",
        None,
    )


@pytest.mark.parametrize(
    ("send_full_document", "expected_full_document_sha"),
    [
        (False, None),
        (True, "864b015db5f89f8369727d66e91e683e7a002c7f21b9e6991984246b90b6175f"),
    ],
)
def test_existing_workflow_request_preparation_is_canonical(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
    send_full_document: bool,
    expected_full_document_sha: str | None,
) -> None:
    settings = settings_factory(
        data_dir=tmp_path,
        ocr_provider="openai",
        openai_api_key="phase5a-test-only",
    )
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    try:
        with database.session() as session:
            job = JobRepository(session).create(
                job_type=JobType.EXTRACT_DOCUMENT,
                request_hash="5" * 64,
                config_snapshot=_snapshot(send_full_document=send_full_document),
                status=JobStatus.QUEUED,
            )
        workflow = _CapturingWorkflow(
            settings=settings,
            database=database,
            storage=storage,
        )

        with pytest.raises(_RequestCaptured) as captured:
            workflow.process(_extraction(job.id))

        canonical = _canonical_request(captured.value.request)
        assert canonical == {
            "templateId": "phase5a-template",
            "templateVersion": "7",
            "fullDocumentSha256": expected_full_document_sha,
            "shouldCancelConfigured": True,
            "regions": EXPECTED_REGIONS,
        }

        with database.session() as session:
            artifact = session.scalar(
                select(JobArtifact).where(
                    JobArtifact.job_id == job.id,
                    JobArtifact.attempt_number == 1,
                    JobArtifact.artifact_name == "mappedCells",
                )
            )
            assert artifact is not None
        mapped_cells = json.loads(storage.read_bytes(artifact.relative_path))
        cells = mapped_cells["cells"]
        assert [cell["mappingId"] for cell in cells] == EXPECTED_MAPPED_CELL_ORDER
        assert {
            cell["mappingId"] for cell in cells if cell["sentToOpenAi"] is True
        } == EXPECTED_SENT_MAPPING_IDS
        assert {
            cell["mappingId"]: cell["localResolution"]
            for cell in cells
            if cell["sentToOpenAi"] is False
        } == {
            "price-2": "trailing_blank_row",
            "calving-parity": "fixed_record_key",
            "mark-local": "mark_detector",
            "mark-manual": "manual_choice_required",
        }
        assert [cell["boundingBox"] for cell in cells] == [
            {"x": 11 + index, "y": 21 + index, "width": 30, "height": 20}
            for index in [0, 1, 2, 3, 4, 5, 6, 7, 10, 8, 9]
        ]
        assert mapped_cells | {"cells": []} == {
            "source": "TEMPLATE_VERSION_SNAPSHOT",
            "fieldMappingHash": "phase5a-frozen-mapping-hash",
            "mappedCellCount": 11,
            "openAiCropCount": 7,
            "openAiImageCount": 13,
            "locallyResolvedCellCount": 4,
            "labelCellsSentToOpenAi": False,
            "printedLabelExclusionRuleCount": 1,
            "wholeDocumentSentToOpenAi": False,
            "targetPathsSentToOpenAi": False,
            "paddleGatingEnabled": False,
            "trailingBlankRowGatingEnabled": True,
            "trailingBlankRows": [{"collection": "saleCalves", "rowIndex": 2}],
            "cells": [],
        }
        assert cells[0]["alignedCellSha256"] == EXPECTED_REGIONS[0]["imageSha256"]
        assert cells[0]["handwritingHintSha256"] is None
        assert cells[0]["alignedCellArtifact"] == "fieldCrop.identity.handwritingOnly"
        by_id = {cell["mappingId"]: cell for cell in cells}
        assert set(by_id) == set(EXPECTED_MAPPED_CELL_SHA)
        for mapping_id, cell in by_id.items():
            aligned_sha, handwriting_sha = EXPECTED_MAPPED_CELL_SHA[mapping_id]
            assert cell["openAiMappingId"] == mapping_id
            assert cell["targetPath"] == EXPECTED_TARGET_PATHS[mapping_id]
            assert cell["alignedCellSha256"] == aligned_sha
            assert cell["handwritingHintSha256"] == handwriting_sha
        for region in EXPECTED_REGIONS:
            cell = by_id[region["sourceRegionId"]]
            assert cell["openAiMappingId"] == region["regionId"]
            assert cell["targetPath"] == region["targetPath"]
            assert cell["alignedCellSha256"] == region["imageSha256"]
            assert cell["handwritingHintSha256"] == region["handwritingSha256"]
    finally:
        database.dispose()
