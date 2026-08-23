import time

from fastapi.testclient import TestClient

from app.services.template_candidate_service import TemplateCandidateService
from tests.synthetic.image_factory import encode_png, ledger_image


def _wait_for_review(client: TestClient, job_id: str) -> None:
    for _ in range(800):
        detail = client.get(f"/api/v1/jobs/{job_id}").json()
        if detail["status"] in {"REVIEW_REQUIRED", "FAILED"}:
            assert detail["status"] == "REVIEW_REQUIRED", detail
            return
        time.sleep(0.01)
    raise AssertionError("Template preparation did not finish")


def test_candidate_settings_bind_a_pool_to_all_repeated_rows(client: TestClient) -> None:
    created = client.post(
        "/api/v1/template-jobs",
        data={"name": "候補設定用帳票", "mode": "REFERENCE_TEMPLATE"},
        files=[("files", ("blank.png", encode_png(ledger_image()), "image/png"))],
    )
    assert created.status_code == 202
    _wait_for_review(client, created.json()["jobId"])
    template_id = client.get("/api/v1/templates").json()[0]["id"]
    detail = client.get(f"/api/v1/templates/{template_id}").json()
    version_id = detail["versions"][0]["id"]

    mapping = client.put(
        f"/api/v1/template-versions/{version_id}/field-mappings",
        json={
            "mappings": [
                {
                    "mappingId": "sire_1",
                    "targetPath": "saleCalves.0.sireName",
                    "label": "種雄牛名",
                    "x": 20,
                    "y": 20,
                    "width": 120,
                    "height": 40,
                    "padding": 4,
                    "enabled": True,
                    "modelDetail": "high",
                },
                {
                    "mappingId": "sire_2",
                    "targetPath": "saleCalves.1.sireName",
                    "label": "種雄牛名",
                    "x": 20,
                    "y": 70,
                    "width": 120,
                    "height": 40,
                    "padding": 4,
                    "enabled": True,
                    "modelDetail": "high",
                },
            ],
            "documentIdentity": None,
            "documentIdentityFields": [],
        },
    )
    assert mapping.status_code == 200, mapping.text
    for name in ("勝忠平", "光晴重"):
        added = client.post("/api/v1/sire-names", json={"name": name})
        assert added.status_code == 201

    legacy = client.get(f"/api/v1/templates/{template_id}/candidate-settings")
    assert legacy.status_code == 200
    assert legacy.json()["sets"][0]["name"] == "種雄牛名"
    assert legacy.json()["sets"][0]["allowedSuffixes"] == ["ET"]
    assert legacy.json()["sets"][0]["bindings"][0]["targetPattern"] == (
        "saleCalves[].sireName"
    )

    saved = client.put(
        f"/api/v1/templates/{template_id}/candidate-settings",
        json={
            "templateVersionId": version_id,
            "sets": [
                {
                    "name": "種雄牛名",
                    "values": ["勝忠平", "光晴重", "勝忠平"],
                    "allowedSuffixes": ["ET"],
                    "targetPatterns": ["saleCalves[].sireName"],
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["sets"][0]["values"] == ["勝忠平", "光晴重"]
    assert body["sets"][0]["bindings"] == [
        {"targetPattern": "saleCalves[].sireName", "label": "種雄牛名"}
    ]

    fetched = client.get(f"/api/v1/templates/{template_id}/candidate-settings")
    assert fetched.status_code == 200
    assert fetched.json() == body
    with client.app.state.database.session() as session:
        snapshot = TemplateCandidateService.snapshot(session, template_id)
    assert snapshot["templateCandidatePools"] == [
        {
            "candidateSetId": body["sets"][0]["id"],
            "name": "種雄牛名",
            "targetPattern": "saleCalves[].sireName",
            "candidates": ["勝忠平", "光晴重"],
            "allowedSuffixes": ["ET"],
        }
    ]
    assert len(snapshot["templateCandidatePoolsHash"]) == 64

    invalid = client.put(
        f"/api/v1/templates/{template_id}/candidate-settings",
        json={
            "templateVersionId": version_id,
            "sets": [
                {
                    "name": "不正な適用先",
                    "values": ["A"],
                    "targetPatterns": ["document.unknown"],
                }
            ],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "TEMPLATE_CANDIDATE_SETTINGS_INVALID"
