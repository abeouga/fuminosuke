import time

from fastapi.testclient import TestClient

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
    version_id = detail["versions"][0]["id"]
    approved = client.post(f"/api/v1/template-versions/{version_id}/approve")
    assert approved.status_code == 200
    return version_id


def test_review_correct_confirm_and_export(client: TestClient) -> None:
    version_id = _active_template(client)
    image = encode_png(ledger_image(handwriting_seed=5))
    created = client.post(
        "/api/v1/ocr-jobs",
        data={"templateVersionId": version_id, "ocrEnabled": "true"},
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
    result = first["result"]
    result["document"]["cattleName"]["raw"] = "修正花子"
    result["document"]["cattleName"]["normalized"] = "修正花子"
    correction = {
        "baseRevision": 1,
        "document": result["document"],
        "saleCalves": result["saleCalves"],
        "pedigree": result["pedigree"],
        "calvings": result["calvings"],
        "reviewItems": result["reviewItems"],
    }
    saved = client.put(f"/api/v1/ocr-jobs/{job_id}/corrections", json=correction)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    assert saved.json()["result"]["document"]["cattleName"]["raw"] == "修正花子"

    stale = client.put(f"/api/v1/ocr-jobs/{job_id}/corrections", json=correction)
    assert stale.status_code == 409
    assert stale.json()["code"] == "REVISION_CONFLICT"

    confirmed = client.post(f"/api/v1/ocr-jobs/{job_id}/confirm")
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

    duplicate = client.post(f"/api/v1/ocr-jobs/{job_id}/confirm")
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "INVALID_STATE_TRANSITION"
