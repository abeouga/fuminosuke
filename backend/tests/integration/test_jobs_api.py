import io
import time

from fastapi.testclient import TestClient
from PIL import Image


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 240), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_template_upload_is_queued_and_processed(client: TestClient) -> None:
    response = client.post(
        "/api/v1/template-jobs",
        data={"name": "テスト帳票", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("ignored-original-name.png", _png(), "image/png"))],
        headers={"Idempotency-Key": "template-test-1"},
    )

    assert response.status_code == 202
    job_id = response.json()["jobId"]
    detail: dict[str, object] = {}
    for _ in range(500):
        detail = client.get(f"/api/v1/jobs/{job_id}").json()
        if detail["status"] == "REVIEW_REQUIRED":
            break
        time.sleep(0.01)
    assert detail["status"] == "REVIEW_REQUIRED"

    manifest = client.app.state.storage.read_bytes(f"uploads/{job_id}/manifest.json")
    assert b"ignored-original-name" not in manifest


def test_idempotency_key_returns_existing_job(client: TestClient) -> None:
    request = {
        "data": {"name": "同一帳票", "mode": "REFERENCE_TEMPLATE"},
        "files": [("files", ("one.png", _png(), "image/png"))],
        "headers": {"Idempotency-Key": "same-request"},
    }
    first = client.post("/api/v1/template-jobs", **request)
    second = client.post("/api/v1/template-jobs", **request)

    assert first.status_code == second.status_code == 202
    assert first.json()["jobId"] == second.json()["jobId"]


def test_rejects_non_image_upload(client: TestClient) -> None:
    response = client.post(
        "/api/v1/template-jobs",
        data={"name": "不正", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("payload.txt", b"not an image", "text/plain"))],
    )

    assert response.status_code == 422
    assert response.json()["code"] == "UNSUPPORTED_IMAGE_FORMAT"
    assert "traceback" not in response.text.lower()
