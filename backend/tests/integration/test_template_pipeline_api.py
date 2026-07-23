import time

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
    assert any(target["path"] == "document.cattleName" for target in mapping_config["targets"])
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
            ]
        },
    )
    assert save_mapping.status_code == 200
    assert save_mapping.json()["mappings"][0]["targetPath"] == "document.cattleName"

    approve = client.post(f"/api/v1/template-versions/{version_id}/approve")
    assert approve.status_code == 200
    assert approve.json()["status"] == "ACTIVE"
    active_mapping = client.put(
        f"/api/v1/template-versions/{version_id}/field-mappings",
        json={"mappings": save_mapping.json()["mappings"]},
    )
    assert active_mapping.status_code == 200

    filled = encode_png(ledger_image(handwriting_seed=3))
    extract = client.post(
        "/api/v1/ocr-jobs",
        data={"templateVersionId": version_id, "ocrEnabled": "false"},
        files=[("file", ("filled.png", filled, "image/png"))],
    )
    assert extract.status_code == 202
    extract_job_id = extract.json()["jobId"]
    _wait_for(client, extract_job_id, "REVIEW_REQUIRED")

    extraction_artifacts = client.get(f"/api/v1/jobs/{extract_job_id}/artifacts").json()["items"]
    names = {item["name"] for item in extraction_artifacts}
    assert {"alignedSource", "handwritingMask", "fieldResults"} <= names


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
