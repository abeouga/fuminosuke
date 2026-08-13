from fastapi.testclient import TestClient


def test_sire_name_pool_can_be_managed(client: TestClient) -> None:
    empty = client.get("/api/v1/sire-names")
    assert empty.status_code == 200
    assert empty.json() == {
        "items": [],
        "count": 0,
        "allowedSuffixes": ["ET"],
    }

    created = client.post("/api/v1/sire-names", json={"name": " 勝忠平 "})
    assert created.status_code == 201
    assert created.json()["name"] == "勝忠平"

    duplicate = client.post("/api/v1/sire-names", json={"name": "勝 忠 平"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "SIRE_NAME_ALREADY_EXISTS"

    listed = client.get("/api/v1/sire-names")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["items"][0]["name"] == "勝忠平"

    deleted = client.delete(f"/api/v1/sire-names/{created.json()['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/v1/sire-names").json()["items"] == []


def test_sire_name_pool_rejects_blank_names(client: TestClient) -> None:
    response = client.post("/api/v1/sire-names", json={"name": "　 "})

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_SIRE_NAME"
