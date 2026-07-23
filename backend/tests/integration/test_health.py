from fastapi.testclient import TestClient


def test_health_and_docs(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "jobWorker": "running",
        "ocrProvider": "mock",
    }
    assert client.get("/docs").status_code == 200
    assert "app.db" not in response.text.lower()
    assert ":\\" not in response.text
