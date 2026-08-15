import hashlib
import threading
import time
from collections.abc import Callable
from io import BytesIO

import numpy as np
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.core.config import Settings
from app.db.base import Base
from app.db.models import JobArtifact, TemplateVersion
from app.db.session import Database
from app.domain.ocr_models import ReadingStatus
from app.main import create_app
from app.ocr.base import OcrProviderRequest, OcrProviderResponse
from app.ocr.deterministic_mapper import build_mapped_document
from app.ocr.openai_provider import OpenAIOcrProvider
from app.ocr.transcription_models import TranscriptionBatch, TranscriptionItem
from tests.synthetic.image_factory import encode_png, ledger_image


def _wait_for(client: TestClient, job_id: str, target: str) -> None:
    last: dict[str, object] = {}
    for _ in range(800):
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {target, "FAILED"}:
            break
        time.sleep(0.01)
    assert last["status"] == target, last


def _active_template(client: TestClient) -> str:
    image = encode_png(ledger_image())
    created = client.post(
        "/api/v1/template-jobs",
        data={"name": "OCR API統合テスト", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("blank.png", image, "image/png"))],
    )
    job_id = created.json()["jobId"]
    _wait_for(client, job_id, "REVIEW_REQUIRED")
    template = client.get("/api/v1/templates").json()[0]
    detail = client.get(f"/api/v1/templates/{template['id']}").json()
    version = detail["versions"][0]
    version_id = version["id"]
    saved_layout = client.put(
        f"/api/v1/template-versions/{version_id}/layout",
        json={
            "canonicalWidth": version["canonicalWidth"],
            "canonicalHeight": version["canonicalHeight"],
            "detector": "opencv+paddleocr",
            "ocrAvailable": False,
            "cells": [
                {
                    "id": "label",
                    "role": "label",
                    "x": 20,
                    "y": 20,
                    "width": 80,
                    "height": 40,
                    "confidence": 1,
                    "text": "牛名",
                    "textConfidence": None,
                    "source": "manual",
                },
                {
                    "id": "handwriting",
                    "role": "input",
                    "x": 120,
                    "y": 20,
                    "width": 240,
                    "height": 80,
                    "confidence": 1,
                    "text": "",
                    "textConfidence": None,
                    "source": "manual",
                },
            ],
            "relations": [
                {
                    "id": "handwriting_horizontal",
                    "inputCellId": "handwriting",
                    "labelCellIds": ["label"],
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": "document.cattleName",
                }
            ],
            "allowCrossAxisLabels": False,
            "warnings": [],
        },
    )
    assert saved_layout.status_code == 200
    verified = client.post(
        f"/api/v1/template-versions/{version_id}/layout/verify"
    )
    assert verified.status_code == 200
    approved = client.post(f"/api/v1/template-versions/{version_id}/approve")
    assert approved.status_code == 200
    return version_id


def test_existing_active_boundary_fallback_template_cannot_start_ocr(
    client: TestClient,
) -> None:
    version_id = _active_template(client)
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        assert version is not None
        version.quality_json = {
            **version.quality_json,
            "fallbackUsed": True,
            "warnings": [
                *version.quality_json.get("warnings", []),
                "FULL_IMAGE_BOUNDARY_FALLBACK",
            ],
        }
        session.commit()

    response = client.post(
        "/api/v1/ocr-jobs",
        data={
            "templateVersionId": version_id,
            "motherIndividualId": "1234567890",
            "ocrEnabled": "true",
        },
        files=[
            (
                "file",
                ("filled.png", encode_png(ledger_image(handwriting_seed=4)), "image/png"),
            )
        ],
    )

    assert response.status_code == 409
    assert response.json()["code"] == "TEMPLATE_DOCUMENT_BOUNDARY_NOT_VERIFIED"


def test_ocr_can_start_without_manually_entered_individual_id(
    client: TestClient,
) -> None:
    version_id = _active_template(client)
    created = client.post(
        "/api/v1/ocr-jobs",
        data={
            "templateVersionId": version_id,
            "ocrEnabled": "true",
        },
        files=[
            (
                "file",
                ("filled.png", encode_png(ledger_image(handwriting_seed=12)), "image/png"),
            )
        ],
    )

    assert created.status_code == 202
    job_id = created.json()["jobId"]
    _wait_for(client, job_id, "REVIEW_REQUIRED")
    job = client.app.state.job_service.get(job_id)
    assert "motherIndividualId" not in job.config_snapshot_json

    result = client.get(f"/api/v1/ocr-jobs/{job_id}/result")
    assert result.status_code == 200
    body = result.json()
    assert body["individualId"] is None
    assert (
        body["result"]["document"]["unifiedEarTag"]["sourceRegionId"]
        != "manual_mother_individual_id"
    )


def test_review_correct_confirm_and_export(client: TestClient) -> None:
    version_id = _active_template(client)
    image = encode_png(ledger_image(handwriting_seed=5))
    created = client.post(
        "/api/v1/ocr-jobs",
        data={
            "templateVersionId": version_id,
            "motherIndividualId": "123-4567-890",
            "ocrEnabled": "true",
        },
        files=[("file", ("filled.png", image, "image/png"))],
    )
    assert created.status_code == 202
    job_id = created.json()["jobId"]
    _wait_for(client, job_id, "REVIEW_REQUIRED")

    response = client.get(f"/api/v1/ocr-jobs/{job_id}/result")
    assert response.status_code == 200
    first = response.json()
    assert first["provider"] == "mock"
    assert first["revision"] == 1
    assert first["individualId"] == "1234567890"
    result = first["result"]
    assert result["document"]["unifiedEarTag"]["raw"] == "1234567890"
    assert (
        result["document"]["unifiedEarTag"]["sourceRegionId"]
        == "manual_mother_individual_id"
    )
    assert result["templateId"] != "cow-breeding-ledger-v1"
    assert result["templateVersion"] == "1"
    result["document"]["cattleName"]["raw"] = "修正花子"
    result["document"]["cattleName"]["normalized"] = "修正花子"
    correction = {
        "baseRevision": 1,
        "document": result["document"],
        "saleCalves": [
            {
                "parity": {
                    "raw": "3",
                    "normalized": "3",
                    "status": "readable",
                    "alternatives": [],
                    "reviewReason": None,
                    "sourceRegionId": "record:sale-3",
                    "boundingBox": None,
                },
                "ageDays": {
                    "raw": None,
                    "normalized": None,
                    "status": "blank",
                    "alternatives": [],
                    "reviewReason": None,
                    "sourceRegionId": "unmapped",
                    "boundingBox": None,
                },
                "weightKg": {
                    "raw": "320",
                    "normalized": "320",
                    "status": "readable",
                    "alternatives": [],
                    "reviewReason": None,
                    "sourceRegionId": "weight-3",
                    "boundingBox": None,
                },
                "priceYen": {
                    "raw": "850000",
                    "normalized": "850000",
                    "status": "readable",
                    "alternatives": [],
                    "reviewReason": None,
                    "sourceRegionId": "price-3",
                    "boundingBox": None,
                },
                "sireName": {
                    "raw": "福之姫",
                    "normalized": "福之姫",
                    "status": "readable",
                    "alternatives": [],
                    "reviewReason": None,
                    "sourceRegionId": "sire-3",
                    "boundingBox": None,
                },
                "saleDate": {
                    "raw": None,
                    "normalized": None,
                    "status": "blank",
                    "alternatives": [],
                    "reviewReason": None,
                    "sourceRegionId": "unmapped",
                    "boundingBox": None,
                },
                "notes": {
                    "raw": None,
                    "normalized": None,
                    "status": "blank",
                    "alternatives": [],
                    "reviewReason": None,
                    "sourceRegionId": "unmapped",
                    "boundingBox": None,
                },
            }
        ],
        "pedigree": result["pedigree"],
        "calvings": result["calvings"],
        "customFields": {
            "treatmentHistory": {
                "raw": "なし",
                "normalized": "なし",
                "status": "readable",
                "alternatives": [],
                "reviewReason": None,
                "sourceRegionId": "custom_treatment",
                "boundingBox": None,
            }
        },
        "reviewItems": result["reviewItems"],
    }
    saved = client.put(f"/api/v1/ocr-jobs/{job_id}/corrections", json=correction)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    assert saved.json()["result"]["document"]["cattleName"]["raw"] == "修正花子"
    assert saved.json()["result"]["customFields"]["treatmentHistory"]["raw"] == "なし"

    stale = client.put(f"/api/v1/ocr-jobs/{job_id}/corrections", json=correction)
    assert stale.status_code == 409
    assert stale.json()["code"] == "REVISION_CONFLICT"

    missing_revision = client.post(f"/api/v1/ocr-jobs/{job_id}/confirm")
    assert missing_revision.status_code == 422

    stale_confirm = client.post(
        f"/api/v1/ocr-jobs/{job_id}/confirm",
        json={"baseRevision": 1},
    )
    assert stale_confirm.status_code == 409
    assert stale_confirm.json()["code"] == "REVISION_CONFLICT"
    still_pending = client.get(f"/api/v1/ocr-jobs/{job_id}/result")
    assert still_pending.status_code == 200
    assert still_pending.json()["revision"] == 2
    assert still_pending.json()["confirmed"] is False

    confirmed = client.post(
        f"/api/v1/ocr-jobs/{job_id}/confirm",
        json={"baseRevision": 2},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmed"] is True
    assert confirmed.json()["individualId"] == "1234567890"

    searched = client.get(
        "/api/v1/individual-records", params={"individualId": "123-4567-890"}
    )
    assert searched.status_code == 200
    search_body = searched.json()
    assert search_body["individualId"] == "1234567890"
    assert search_body["count"] == 1
    assert search_body["records"][0]["sourceJobId"] == job_id
    assert search_body["records"][0]["data"]["document"]["cattleName"]["raw"] == (
        "修正花子"
    )
    assert (
        search_body["records"][0]["data"]["customFields"]["treatmentHistory"]["raw"]
        == "なし"
    )

    catalog = client.get("/api/v1/individual-records/catalog")
    assert catalog.status_code == 200
    catalog_body = catalog.json()
    assert catalog_body["count"] == 1
    assert catalog_body["items"][0]["individualId"] == "1234567890"
    assert catalog_body["items"][0]["cattleName"] == "修正花子"
    assert catalog_body["items"][0]["recordCount"] == 1

    sire_search = client.get(
        "/api/v1/sale-calf-records", params={"sireName": "福之姫"}
    )
    assert sire_search.status_code == 200
    sire_body = sire_search.json()
    assert sire_body["count"] == 1
    assert sire_body["matches"][0]["individualId"] == "1234567890"
    assert sire_body["matches"][0]["recordKey"] == "3"
    assert sire_body["matches"][0]["data"]["priceYen"]["normalized"] == "850000"

    missing = client.get(
        "/api/v1/individual-records", params={"individualId": "9999999999"}
    )
    assert missing.status_code == 200
    assert missing.json()["records"] == []

    invalid = client.get(
        "/api/v1/individual-records", params={"individualId": "123"}
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "INVALID_INDIVIDUAL_ID"
    assert client.get(f"/api/v1/ocr-jobs/{job_id}/export?format=json").status_code == 200
    csv_response = client.get(f"/api/v1/ocr-jobs/{job_id}/export?format=csv")
    assert csv_response.status_code == 200
    assert "document.cattleName.normalized" in csv_response.text
    assert "修正花子" in csv_response.text

    excel_table = client.get(f"/api/v1/ocr-jobs/{job_id}/excel-table")
    assert excel_table.status_code == 200
    table_body = excel_table.json()
    assert table_body["sheetName"] == "読み取り結果"
    assert table_body["fileName"].startswith("繁殖台帳_1234567890_修正花子_")
    displays = [
        cell["display"]
        for row in table_body["rows"]
        for cell in row["cells"]
    ]
    assert "売却子牛一覧" in displays
    assert "福之姫" in displays
    assert "なし" in displays

    xlsx_response = client.get(f"/api/v1/ocr-jobs/{job_id}/export?format=xlsx")
    assert xlsx_response.status_code == 200
    assert xlsx_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "filename*=utf-8''" in xlsx_response.headers["content-disposition"].lower()
    workbook = load_workbook(BytesIO(xlsx_response.content), data_only=False)
    sheet = workbook["読み取り結果"]
    values = [cell.value for row in sheet.iter_rows() for cell in row]
    assert "修正花子" in values
    assert "福之姫" in values
    assert 850000 in values

    duplicate = client.post(
        f"/api/v1/ocr-jobs/{job_id}/confirm",
        json={"baseRevision": confirmed.json()["revision"]},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "INVALID_STATE_TRANSITION"


def test_openai_uses_only_snapshotted_mapped_input_cells(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_factory(
        ocr_provider="openai",
        openai_api_key="test-key",
        openai_primary_model="gpt-5-mini",
    )
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    database.dispose()
    captured: dict[str, OcrProviderRequest] = {}

    async def fake_extract(
        _provider: OpenAIOcrProvider,
        request: OcrProviderRequest,
    ) -> OcrProviderResponse:
        captured["request"] = request
        batch = TranscriptionBatch(
            items=[
                TranscriptionItem(
                    mapping_id=region.region_id,
                    raw="こよみ",
                    status=ReadingStatus.READABLE,
                )
                for region in reversed(request.regions)
            ]
        )
        return OcrProviderResponse(
            document=build_mapped_document(request, batch),
            provider="openai",
            model_id="gpt-5-mini",
            prompt_version="test",
            api_call_count=1,
        )

    monkeypatch.setattr(OpenAIOcrProvider, "extract", fake_extract)
    with TestClient(create_app(settings)) as openai_client:
        for name in ("勝忠平", "光晴重", "重波平", "百合茂"):
            assert openai_client.post(
                "/api/v1/sire-names",
                json={"name": name},
            ).status_code == 201
        version_id = _active_template(openai_client)
        mappings = openai_client.get(
            f"/api/v1/template-versions/{version_id}/field-mappings"
        ).json()["mappings"]
        assert len(mappings) == 1
        mapping_id = mappings[0]["mappingId"]

        image = encode_png(ledger_image(handwriting_seed=7))
        created = openai_client.post(
            "/api/v1/ocr-jobs",
            data={
                "templateVersionId": version_id,
                "motherIndividualId": "1234567890",
                "ocrEnabled": "true",
            },
            files=[("file", ("filled.png", image, "image/png"))],
        )
        assert created.status_code == 202
        job_id = created.json()["jobId"]
        _wait_for(openai_client, job_id, "REVIEW_REQUIRED")

        request = captured["request"]
        assert len(request.regions) == 1
        assert request.regions[0].region_id == mapping_id
        assert request.regions[0].source_region_id == mapping_id
        assert request.regions[0].target_path == "document.cattleName"
        assert request.regions[0].bounding_box == (120, 20, 240, 80)
        assert request.regions[0].handwriting_png is not None

        job = openai_client.app.state.job_service.get(job_id)
        snapshot = job.config_snapshot_json
        assert snapshot["fieldMappingCount"] == 1
        assert snapshot["motherIndividualId"] == "1234567890"
        assert snapshot["fieldMappings"][0]["mappingId"] == mapping_id
        assert snapshot["fieldMappings"][0]["padding"] == 0
        assert len(snapshot["fieldMappingHash"]) == 64
        assert snapshot["sireNamePool"] == ["勝忠平", "光晴重", "重波平", "百合茂"]
        assert snapshot["sireNameAllowedSuffixes"] == ["ET"]
        assert len(snapshot["sireNamePoolHash"]) == 64
        assert snapshot["saleNoteSexSymbolPool"] == ["♂", "♀"]

        mapped_cells = openai_client.get(
            f"/api/v1/jobs/{job_id}/artifacts/mappedCells"
        ).json()
        assert mapped_cells["mappedCellCount"] == 1
        assert mapped_cells["openAiCropCount"] == 1
        assert mapped_cells["openAiImageCount"] == 2
        assert mapped_cells["locallyResolvedCellCount"] == 0
        assert mapped_cells["trailingBlankRowGatingEnabled"] is True
        assert mapped_cells["trailingBlankRows"] == []
        assert mapped_cells["labelCellsSentToOpenAi"] is False
        assert mapped_cells["wholeDocumentSentToOpenAi"] is False
        assert mapped_cells["targetPathsSentToOpenAi"] is False
        assert mapped_cells["paddleGatingEnabled"] is False
        assert mapped_cells["cells"][0]["mappingId"] == mapping_id
        assert mapped_cells["cells"][0]["openAiMappingId"] == mapping_id
        assert mapped_cells["cells"][0]["targetPath"] == "document.cattleName"
        assert mapped_cells["cells"][0]["response"]["raw"] == "こよみ"

        result = openai_client.get(f"/api/v1/ocr-jobs/{job_id}/result").json()
        assert result["fieldPreviews"] == [
            {
                "targetPath": "document.cattleName",
                "mappingId": mapping_id,
                "artifactUrl": (
                    f"/api/v1/jobs/{job_id}/artifacts/"
                    f"fieldCrop.{mapping_id}.alignedColor"
                ),
                    "boundingBox": {
                        "x": 120,
                        "y": 20,
                        "width": 240,
                        "height": 80,
                    },
                    "recordGroupId": None,
                    "fieldKey": None,
                    "mappingType": "text",
                    "markOptions": [],
                }
            ]


def test_openai_rejects_active_template_without_field_mappings(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory(
        ocr_provider="openai",
        openai_api_key="test-key",
        openai_primary_model="gpt-5-mini",
    )
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    database.dispose()
    with TestClient(create_app(settings)) as openai_client:
        version_id = _active_template(openai_client)
        cleared = openai_client.put(
            f"/api/v1/template-versions/{version_id}/field-mappings",
            json={"mappings": []},
        )
        assert cleared.status_code == 200

        image = encode_png(ledger_image(handwriting_seed=8))
        created = openai_client.post(
            "/api/v1/ocr-jobs",
            data={
                "templateVersionId": version_id,
                "motherIndividualId": "1234567890",
                "ocrEnabled": "true",
            },
            files=[("file", ("filled.png", image, "image/png"))],
        )

        assert created.status_code == 409
        assert created.json()["code"] == "TEMPLATE_FIELD_MAPPING_REQUIRED"


def test_existing_template_with_sale_calf_rows_over_ten_requires_repair(
    client: TestClient,
) -> None:
    version_id = _active_template(client)
    with client.app.state.database.session() as session:
        version = session.get(TemplateVersion, version_id)
        assert version is not None
        config_path = version.config_relative_path
    content = client.app.state.storage.read_bytes(config_path).decode("utf-8")
    assert "document.cattleName" in content
    client.app.state.storage.write_text(
        config_path,
        content.replace(
            "document.cattleName",
            "saleCalves.10.priceYen",
        ),
    )

    image = encode_png(ledger_image(handwriting_seed=9))
    created = client.post(
        "/api/v1/ocr-jobs",
        data={
            "templateVersionId": version_id,
            "motherIndividualId": "1234567890",
            "ocrEnabled": "true",
        },
        files=[("file", ("filled.png", image, "image/png"))],
    )

    assert created.status_code == 409
    assert created.json()["code"] == "TEMPLATE_FIELD_MAPPING_OUT_OF_RANGE"
    assert "saleCalves.10.priceYen" in created.json()["detail"]


def test_ocr_uses_the_template_snapshot_captured_at_acceptance(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_id = _active_template(client)
    extraction_service = client.app.state.job_runner._handler._extraction_service
    original_process = extraction_service.process
    processing_started = threading.Event()
    allow_processing = threading.Event()

    def delayed_process(job_id: str) -> object:
        processing_started.set()
        if not allow_processing.wait(timeout=5):
            raise RuntimeError("Timed out waiting to mutate the live template")
        return original_process(job_id)

    monkeypatch.setattr(extraction_service, "process", delayed_process)
    image = encode_png(ledger_image(handwriting_seed=10))
    created = client.post(
        "/api/v1/ocr-jobs",
        data={
            "templateVersionId": version_id,
            "motherIndividualId": "1234567890",
            "ocrEnabled": "true",
        },
        files=[("file", ("filled.png", image, "image/png"))],
    )
    assert created.status_code == 202
    job_id = created.json()["jobId"]
    assert processing_started.wait(timeout=5)

    job = client.app.state.job_service.get(job_id)
    snapshot = job.config_snapshot_json["templateSnapshot"]
    assert snapshot["sourceArtifactId"]
    assert len(snapshot["cleanTemplateSha256"]) == 64
    assert len(snapshot["configSha256"]) == 64
    assert snapshot["cleanTemplatePath"].startswith(
        f"uploads/{job_id}/template_snapshot/"
    )
    assert isinstance(snapshot["config"], dict)

    try:
        with client.app.state.database.session() as session:
            version = session.get(TemplateVersion, version_id)
            assert version is not None
            artifact = session.get(JobArtifact, version.clean_template_artifact_id)
            assert artifact is not None
            assert artifact.width is not None
            assert artifact.height is not None
            live_template_path = artifact.relative_path
            live_config_path = version.config_relative_path
        changed_template = np.zeros(
            (artifact.height, artifact.width, 3),
            dtype=np.uint8,
        )
        client.app.state.storage.write_bytes(
            live_template_path,
            encode_png(changed_template),
        )
        client.app.state.storage.write_text(
            live_config_path,
            "not: [valid",
        )
    finally:
        allow_processing.set()

    _wait_for(client, job_id, "REVIEW_REQUIRED")
    copied_template = client.get(
        f"/api/v1/jobs/{job_id}/artifacts/cleanTemplate"
    )
    assert copied_template.status_code == 200
    assert hashlib.sha256(copied_template.content).hexdigest() == (
        snapshot["cleanTemplateSha256"]
    )
