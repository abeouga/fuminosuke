import json
import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from tests.synthetic.image_factory import encode_png, ledger_image


def _wait_for(client: TestClient, job_id: str, target: str) -> dict[str, object]:
    detail: dict[str, object] = {}
    for _ in range(800):
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        detail = response.json()
        if detail["status"] in {target, "FAILED"}:
            break
        time.sleep(0.01)
    assert detail["status"] == target, detail
    return detail


def test_failed_boundary_can_be_confirmed_and_reprocessed(client: TestClient) -> None:
    source = np.full((400, 1200, 3), 72, dtype=np.uint8)
    create = client.post(
        "/api/v1/template-jobs",
        data={
            "name": "横長書類",
            "mode": "REFERENCE_TEMPLATE",
            "fieldNames": json.dumps(["宛名", "金額"], ensure_ascii=False),
        },
        files=[("files", ("wide.png", encode_png(source), "image/png"))],
    )
    assert create.status_code == 202
    job_id = create.json()["jobId"]
    failed = _wait_for(client, job_id, "FAILED")
    assert failed["error"]["code"] == "DOCUMENT_BOUNDARY_NOT_FOUND"

    boundary_image = client.get(f"/api/v1/template-jobs/{job_id}/boundary-image")
    assert boundary_image.status_code == 200
    assert boundary_image.headers["content-type"] == "image/png"

    update = client.put(
        f"/api/v1/template-jobs/{job_id}/boundary",
        json={
            "corners": [
                {"x": 0.03, "y": 0.03},
                {"x": 0.97, "y": 0.03},
                {"x": 0.97, "y": 0.97},
                {"x": 0.03, "y": 0.97},
            ]
        },
    )
    assert update.status_code == 200
    assert update.json()["status"] == "QUEUED"
    assert update.json()["attemptNumber"] == 2

    _wait_for(client, job_id, "REVIEW_REQUIRED")
    templates = client.get("/api/v1/templates").json()
    template_id = next(
        template["id"] for template in templates if template["name"] == "横長書類"
    )
    version = client.get(f"/api/v1/templates/{template_id}").json()["versions"][0]
    assert version["canonicalWidth"] == 1754
    assert version["canonicalHeight"] == 1240
    assert version["quality"]["manualBoundaryUsed"] is True
    layout = client.get(f"/api/v1/template-versions/{version['id']}/layout").json()
    assert [item["label"] for item in layout["labelPool"]] == ["宛名", "金額"]
    mapping_config = client.get(
        f"/api/v1/template-versions/{version['id']}/field-mappings"
    ).json()
    assert [target["label"] for target in mapping_config["targets"]] == ["宛名", "金額"]
    assert not any(target["label"] == "個体識別番号" for target in mapping_config["targets"])
    redetected = client.post(f"/api/v1/template-versions/{version['id']}/layout/detect")
    assert redetected.status_code == 200
    assert [item["label"] for item in redetected.json()["labelPool"]] == ["宛名", "金額"]


def test_reference_approve_then_extract(client: TestClient) -> None:
    blank = encode_png(ledger_image())
    create = client.post(
        "/api/v1/template-jobs",
        data={"name": "合成牛繁殖台帳", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("blank.png", blank, "image/png"))],
    )
    assert create.status_code == 202
    template_job_id = create.json()["jobId"]
    _wait_for(client, template_job_id, "REVIEW_REQUIRED")

    templates = client.get("/api/v1/templates").json()
    assert templates
    detail = client.get(f"/api/v1/templates/{templates[0]['id']}").json()
    version_id = detail["versions"][0]["id"]
    premature_approval = client.post(f"/api/v1/template-versions/{version_id}/approve")
    assert premature_approval.status_code == 409
    artifacts = client.get(f"/api/v1/jobs/{template_job_id}/artifacts").json()["items"]
    assert {item["name"] for item in artifacts} >= {
        "cleanTemplate",
        "quality",
        "result",
    }

    mapping_response = client.get(f"/api/v1/template-versions/{version_id}/field-mappings")
    assert mapping_response.status_code == 200
    mapping_config = mapping_response.json()
    assert mapping_config["mappings"] == []
    assert mapping_config["validForOcr"] is False
    assert mapping_config["warnings"]
    assert mapping_config["targets"] == []

    custom_mapping = client.put(
        f"/api/v1/template-versions/{version_id}/field-mappings",
        json={
            "mappings": [
                {
                    "mappingId": "custom_treatment",
                    "targetPath": "customFields.treatmentHistory",
                    "label": "治療歴",
                    "x": 100,
                    "y": 120,
                    "width": 320,
                    "height": 90,
                    "padding": 4,
                    "enabled": True,
                    "modelDetail": "high",
                }
            ],
            "documentIdentity": {
                "mappingId": "custom_treatment",
                "targetPath": "customFields.treatmentHistory",
                "label": "治療歴",
                "valueType": "text",
                "exactLength": None,
            },
        },
    )
    assert custom_mapping.status_code == 200
    assert custom_mapping.json()["validForOcr"] is True
    assert custom_mapping.json()["warnings"] == []
    assert any(
        target["path"] == "customFields.treatmentHistory"
        for target in custom_mapping.json()["targets"]
    )

    compound_mapping = client.put(
        f"/api/v1/template-versions/{version_id}/field-mappings",
        json={
            "mappings": [
                custom_mapping.json()["mappings"][0],
                {
                    "mappingId": "cattle_name",
                    "targetPath": "document.cattleName",
                    "label": "牛名",
                    "x": 450,
                    "y": 120,
                    "width": 220,
                    "height": 90,
                    "padding": 4,
                    "enabled": True,
                    "modelDetail": "high",
                },
            ],
            "documentIdentity": {
                **custom_mapping.json()["documentIdentity"],
                "matchRole": "primary",
                "displayRole": "subtitle",
            },
            "documentIdentityFields": [
                {
                    **custom_mapping.json()["documentIdentity"],
                    "matchRole": "primary",
                    "displayRole": "subtitle",
                },
                {
                    "mappingId": "cattle_name",
                    "targetPath": "document.cattleName",
                    "label": "牛名",
                    "valueType": "text",
                    "exactLength": None,
                    "matchRole": "confirmation",
                    "displayRole": "title",
                },
            ],
        },
    )
    assert compound_mapping.status_code == 200
    assert [
        field["targetPath"] for field in compound_mapping.json()["documentIdentityFields"]
    ] == ["customFields.treatmentHistory", "document.cattleName"]
    assert compound_mapping.json()["documentIdentity"]["matchRole"] == "primary"

    combined_confirmation_mapping = client.put(
        f"/api/v1/template-versions/{version_id}/field-mappings",
        json={
            "mappings": compound_mapping.json()["mappings"],
            "documentIdentity": None,
            "documentIdentityFields": [
                {
                    **compound_mapping.json()["documentIdentityFields"][0],
                    "matchRole": "confirmation",
                },
                {
                    **compound_mapping.json()["documentIdentityFields"][1],
                    "matchRole": "confirmation",
                },
            ],
        },
    )
    assert combined_confirmation_mapping.status_code == 200
    assert combined_confirmation_mapping.json()["validForOcr"] is True
    assert combined_confirmation_mapping.json()["documentIdentity"] is None
    assert [
        field["matchRole"]
        for field in combined_confirmation_mapping.json()["documentIdentityFields"]
    ] == ["confirmation", "confirmation"]

    detected_layout = client.post(
        f"/api/v1/template-versions/{version_id}/layout/detect"
    )
    assert detected_layout.status_code == 200
    assert detected_layout.json()["detector"] == "opencv"
    assert detected_layout.json()["ocrAvailable"] is False
    original_width = detail["versions"][0]["canonicalWidth"]
    original_height = detail["versions"][0]["canonicalHeight"]
    draft_layout = client.put(
        f"/api/v1/template-versions/{version_id}/layout",
        json={
            "canonicalWidth": original_width,
            "canonicalHeight": original_height,
            "detector": "opencv+paddleocr",
            "ocrAvailable": False,
            "cells": [
                {
                    "id": "draft_label",
                    "role": "label",
                    "x": 20,
                    "y": 20,
                    "width": min(160, original_width - 20),
                    "height": min(50, original_height - 20),
                    "confidence": 0.9,
                    "text": "名号",
                    "textConfidence": 0.8,
                    "source": "detected",
                },
                {
                    "id": "draft_input",
                    "role": "input",
                    "x": min(200, original_width - 121),
                    "y": 20,
                    "width": 120,
                    "height": min(50, original_height - 20),
                    "confidence": 0.9,
                    "text": "",
                    "textConfidence": None,
                    "source": "detected",
                },
            ],
            "relations": [],
            "allowCrossAxisLabels": False,
            "warnings": [],
        },
    )
    assert draft_layout.status_code == 200
    assert draft_layout.json()["relations"] == []
    persisted_draft = client.get(f"/api/v1/template-versions/{version_id}/layout")
    assert persisted_draft.status_code == 200
    assert [cell["role"] for cell in persisted_draft.json()["cells"]] == [
        "label",
        "input",
    ]
    draft_mappings = client.get(
        f"/api/v1/template-versions/{version_id}/field-mappings"
    ).json()
    assert draft_mappings["mappings"] == []
    assert draft_mappings["validForOcr"] is False

    ungrouped_layout = {
        **draft_layout.json(),
        "relations": [
            {
                "id": "draft_sale_price",
                "inputCellId": "draft_input",
                "labelCellIds": ["draft_label"],
                "axis": "horizontal",
                "order": 1,
                "targetPath": "saleCalves.0.priceYen",
            }
        ],
        "recordSections": [],
        "recordGroups": [],
        "recordStructureVerified": False,
    }
    ungrouped_save = client.put(
        f"/api/v1/template-versions/{version_id}/layout",
        json=ungrouped_layout,
    )
    assert ungrouped_save.status_code == 200
    assert ungrouped_save.json()["relations"][0]["targetPath"] == (
        "saleCalves.0.priceYen"
    )
    ungrouped_mappings = client.get(
        f"/api/v1/template-versions/{version_id}/field-mappings"
    ).json()
    assert ungrouped_mappings["mappings"] == []
    assert ungrouped_mappings["validForOcr"] is False

    completed_mapping_with_unassigned_cell = {
        **draft_layout.json(),
        "cells": [
            *draft_layout.json()["cells"],
            {
                "id": "unassigned_history",
                "role": "input",
                "x": min(330, original_width - 81),
                "y": 20,
                "width": 80,
                "height": min(50, original_height - 20),
                "confidence": 0.9,
                "text": "1",
                "textConfidence": 0.8,
                "source": "detected",
            },
        ],
        "relations": [
            {
                "id": "draft_name",
                "inputCellId": "draft_input",
                "labelCellIds": ["draft_label"],
                "axis": "horizontal",
                "order": 1,
                "targetPath": "document.cattleName",
            }
        ],
    }
    mixed_draft_save = client.put(
        f"/api/v1/template-versions/{version_id}/layout",
        json=completed_mapping_with_unassigned_cell,
    )
    assert mixed_draft_save.status_code == 200
    mixed_draft_mappings = client.get(
        f"/api/v1/template-versions/{version_id}/field-mappings"
    ).json()
    assert mixed_draft_mappings["mappings"] == []
    assert mixed_draft_mappings["validForOcr"] is False

    draft_template = client.get(f"/api/v1/templates/{templates[0]['id']}").json()
    assert draft_template["versions"][0]["quality"]["layoutDraftSaved"] is True
    assert draft_template["versions"][0]["quality"]["layoutFinalized"] is False

    saved_layout = client.put(
        f"/api/v1/template-versions/{version_id}/layout",
        json={
            "canonicalWidth": original_width,
            "canonicalHeight": original_height,
            "detector": "opencv+paddleocr",
            "ocrAvailable": False,
            "cells": [
                {
                    "id": "cell_label",
                    "role": "label",
                    "x": 20,
                    "y": 20,
                    "width": min(160, original_width - 20),
                    "height": min(50, original_height - 20),
                    "confidence": 0.9,
                    "text": "名号",
                    "textConfidence": 0.8,
                    "source": "detected",
                },
                {
                    "id": "cell_input",
                    "role": "input",
                    "x": min(200, original_width - 121),
                    "y": 20,
                    "width": 120,
                    "height": min(50, original_height - 20),
                    "confidence": 0.9,
                    "text": "",
                    "textConfidence": None,
                    "source": "detected",
                },
            ],
            "relations": [
                {
                    "id": "cell_input_horizontal",
                    "inputCellId": "cell_input",
                    "labelCellIds": ["cell_label"],
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
    assert saved_layout.json()["relations"][0]["targetPath"] == "document.cattleName"
    persisted_layout = client.get(f"/api/v1/template-versions/{version_id}/layout")
    assert persisted_layout.status_code == 200
    assert persisted_layout.json()["cells"][0]["text"] == "名号"

    save_mapping = client.put(
        f"/api/v1/template-versions/{version_id}/field-mappings",
        json={
            "mappings": [
                {
                    "mappingId": "field_document_cattleName",
                    "targetPath": "document.cattleName",
                    "label": "名号",
                    "x": 100,
                    "y": 120,
                    "width": 320,
                    "height": 90,
                    "padding": 4,
                    "enabled": True,
                    "modelDetail": "high",
                }
            ],
            "documentIdentity": {
                "mappingId": "field_document_cattleName",
                "targetPath": "document.cattleName",
                "label": "名号",
                "valueType": "text",
                "exactLength": None,
            },
        },
    )
    assert save_mapping.status_code == 200
    assert save_mapping.json()["mappings"][0]["targetPath"] == "document.cattleName"

    verification = client.post(
        f"/api/v1/template-versions/{version_id}/layout/verify"
    )
    assert verification.status_code == 200
    approve = client.post(f"/api/v1/template-versions/{version_id}/approve")
    assert approve.status_code == 200
    assert approve.json()["status"] == "ACTIVE"
    active_mapping = client.put(
        f"/api/v1/template-versions/{version_id}/field-mappings",
        json={
            "mappings": save_mapping.json()["mappings"],
            "documentIdentity": save_mapping.json()["documentIdentity"],
        },
    )
    assert active_mapping.status_code == 200

    filled = encode_png(ledger_image(handwriting_seed=3))
    invalidated = client.get(f"/api/v1/templates/{templates[0]['id']}").json()
    assert invalidated["versions"][0]["quality"]["layoutVerified"] is False
    blocked = client.post(
        "/api/v1/ocr-jobs",
        data={
            "templateVersionId": version_id,
            "motherIndividualId": "1234567890",
            "ocrEnabled": "false",
        },
        files=[("file", ("filled.png", filled, "image/png"))],
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "TEMPLATE_LAYOUT_NOT_VERIFIED"

    reverified = client.post(
        f"/api/v1/template-versions/{version_id}/layout/verify"
    )
    assert reverified.status_code == 200

    edited_mask = np.zeros((original_height, original_width), dtype=np.uint8)
    edited_mask[30:60, 206:215] = 255
    encoded_ok, encoded_mask = cv2.imencode(".png", edited_mask)
    assert encoded_ok
    saved_mask = client.put(
        f"/api/v1/template-versions/{version_id}/handwriting-mask",
        files={"file": ("edited-mask.png", encoded_mask.tobytes(), "image/png")},
    )
    assert saved_mask.status_code == 200
    assert saved_mask.json()["artifactName"] == "handwritingMaskEdited"

    edited_state = client.get(f"/api/v1/templates/{templates[0]['id']}").json()
    assert edited_state["versions"][0]["status"] == "ACTIVE"
    assert edited_state["versions"][0]["quality"]["layoutVerified"] is False
    assert edited_state["versions"][0]["quality"]["editedMaskApplied"] is False

    redetected = client.post(
        f"/api/v1/template-versions/{version_id}/layout/detect"
    )
    assert redetected.status_code == 200
    assert redetected.json()["detector"] == "opencv"
    assert redetected.json()["ocrAvailable"] is False
    edited_reverified = client.post(
        f"/api/v1/template-versions/{version_id}/layout/verify"
    )
    assert edited_reverified.status_code == 200

    verified_state = client.get(f"/api/v1/templates/{templates[0]['id']}").json()
    verified_quality = verified_state["versions"][0]["quality"]
    assert verified_state["versions"][0]["status"] == "ACTIVE"
    assert verified_quality["layoutVerified"] is True
    assert verified_quality["layoutVerificationDetector"] == "opencv"
    assert verified_quality["handwritingMaskSource"] == "USER_EDITED"
    assert verified_quality["editedMaskApplied"] is True
    assert (
        verified_quality["editedMaskAppliedSha256"]
        == verified_quality["editedMaskSha256"]
    )

    effective_mask_response = client.get(
        f"/api/v1/jobs/{template_job_id}/artifacts/handwritingMask"
    )
    edited_mask_response = client.get(
        f"/api/v1/jobs/{template_job_id}/artifacts/handwritingMaskEdited"
    )
    assert effective_mask_response.status_code == 200
    assert edited_mask_response.status_code == 200
    effective_mask = cv2.imdecode(
        np.frombuffer(effective_mask_response.content, dtype=np.uint8),
        cv2.IMREAD_GRAYSCALE,
    )
    persisted_edited_mask = cv2.imdecode(
        np.frombuffer(edited_mask_response.content, dtype=np.uint8),
        cv2.IMREAD_GRAYSCALE,
    )
    assert np.array_equal(effective_mask, edited_mask)
    assert np.array_equal(persisted_edited_mask, edited_mask)

    source_normalized_response = client.get(
        f"/api/v1/jobs/{template_job_id}/artifacts/normalized"
    )
    source_clean_response = client.get(
        f"/api/v1/jobs/{template_job_id}/artifacts/cleanTemplate"
    )
    source_normalized = cv2.imdecode(
        np.frombuffer(source_normalized_response.content, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    source_clean = cv2.imdecode(
        np.frombuffer(source_clean_response.content, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert source_normalized is not None
    assert source_clean is not None
    assert np.any(source_normalized[edited_mask > 0] != source_clean[edited_mask > 0])

    extract = client.post(
        "/api/v1/ocr-jobs",
        data={
            "templateVersionId": version_id,
            "motherIndividualId": "1234567890",
            "ocrEnabled": "false",
        },
        files=[("file", ("filled.png", filled, "image/png"))],
    )
    assert extract.status_code == 202
    extract_job_id = extract.json()["jobId"]
    _wait_for(client, extract_job_id, "REVIEW_REQUIRED")

    extraction_artifacts = client.get(f"/api/v1/jobs/{extract_job_id}/artifacts").json()["items"]
    names = {item["name"] for item in extraction_artifacts}
    assert {"alignedSource", "handwritingMask", "fieldResults"} <= names
    assert all("?v=" in item["url"] for item in extraction_artifacts)
    extraction_clean_response = client.get(
        f"/api/v1/jobs/{extract_job_id}/artifacts/cleanTemplate"
    )
    extraction_clean = cv2.imdecode(
        np.frombuffer(extraction_clean_response.content, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert np.array_equal(extraction_clean, source_clean)


def test_template_name_is_reserved_immediately(client: TestClient) -> None:
    blank = encode_png(ledger_image())
    first = client.post(
        "/api/v1/template-jobs",
        data={"name": "重複禁止テンプレート", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("blank.png", blank, "image/png"))],
    )
    assert first.status_code == 202

    duplicate = client.post(
        "/api/v1/template-jobs",
        data={"name": " 重複禁止テンプレート ", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("blank.png", blank, "image/png"))],
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "TEMPLATE_NAME_CONFLICT"
    _wait_for(client, first.json()["jobId"], "REVIEW_REQUIRED")


def test_edited_mask_reprocess_creates_new_version(client: TestClient) -> None:
    filled = encode_png(ledger_image(handwriting_seed=7))
    create = client.post(
        "/api/v1/template-jobs",
        data={"name": "編集マスク", "mode": "SINGLE_IMAGE_EXPERIMENTAL"},
        files=[("files", ("filled.png", filled, "image/png"))],
    )
    assert create.status_code == 202
    job_id = create.json()["jobId"]
    _wait_for(client, job_id, "REVIEW_REQUIRED")
    template = client.get("/api/v1/templates").json()[0]
    detail = client.get(f"/api/v1/templates/{template['id']}").json()
    original = detail["versions"][0]
    initial_layout = client.get(
        f"/api/v1/template-versions/{original['id']}/layout"
    )
    assert initial_layout.status_code == 200
    assert initial_layout.json()["cells"]
    assert original["quality"]["layoutPreDetected"] is True
    layout = client.put(
        f"/api/v1/template-versions/{original['id']}/layout",
        json={
            "canonicalWidth": original["canonicalWidth"],
            "canonicalHeight": original["canonicalHeight"],
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
                    "text": "備考",
                    "textConfidence": None,
                    "source": "manual",
                },
                {
                    "id": "handwriting",
                    "role": "input",
                    "x": 100,
                    "y": 80,
                    "width": original["canonicalWidth"] - 120,
                    "height": original["canonicalHeight"] - 100,
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
    assert layout.status_code == 200
    verification = client.post(
        f"/api/v1/template-versions/{original['id']}/layout/verify"
    )
    assert verification.status_code == 200
    mask = client.get(f"/api/v1/jobs/{job_id}/artifacts/handwritingMask")
    assert mask.status_code == 200
    candidates = client.get(f"/api/v1/jobs/{job_id}/artifacts/handwritingCandidates")
    assert candidates.status_code == 200
    candidate_payload = candidates.json()
    assert candidate_payload["coordinateSystem"]["unit"] == "normalized"
    assert candidate_payload["candidates"]
    assert all(
        0 <= item["x"] <= 1 and 0 < item["width"] <= 1 for item in candidate_payload["candidates"]
    )

    saved = client.put(
        f"/api/v1/template-versions/{original['id']}/handwriting-mask",
        files={"file": ("mask.png", mask.content, "image/png")},
    )
    assert saved.status_code == 200
    assert saved.json()["artifactName"] == "handwritingMaskEdited"
    replaced = client.put(
        f"/api/v1/template-versions/{original['id']}/handwriting-mask",
        files={"file": ("mask-again.png", mask.content, "image/png")},
    )
    assert replaced.status_code == 200
    assert replaced.json()["artifactName"] == "handwritingMaskEdited"
    artifact_names = [
        item["name"]
        for item in client.get(f"/api/v1/jobs/{job_id}/artifacts").json()["items"]
    ]
    assert artifact_names.count("handwritingMaskEdited") == 1
    mask_invalidated = client.get(f"/api/v1/templates/{template['id']}").json()
    assert mask_invalidated["versions"][0]["quality"]["layoutVerified"] is False
    reprocess = client.post(f"/api/v1/template-versions/{original['id']}/reprocess")
    assert reprocess.status_code == 202
    assert reprocess.json()["jobId"] == job_id
    _wait_for(client, job_id, "REVIEW_REQUIRED")

    updated = client.get(f"/api/v1/templates/{template['id']}").json()
    assert len(updated["versions"]) == 2
    newest, archived = updated["versions"]
    assert newest["versionNumber"] == 2
    assert newest["status"] == "DRAFT"
    assert newest["quality"]["maskSource"] == "USER_EDITED"
    assert archived["status"] == "ARCHIVED"
    names = {
        item["name"] for item in client.get(f"/api/v1/jobs/{job_id}/artifacts").json()["items"]
    }
    assert {"cleanPreview", "handwritingOnly", "debugOverlay"} <= names
