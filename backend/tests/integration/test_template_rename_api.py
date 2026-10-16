from fastapi.testclient import TestClient

from app.db.models import Template


def test_rename_template_persists_and_updates_list_and_detail(client: TestClient) -> None:
    database = client.app.state.database
    with database.session() as session:
        template = Template(name="変更前の帳票名", document_type="spreadsheet")
        session.add(template)
        session.commit()
        template_id = template.id

    response = client.patch(
        f"/api/v1/templates/{template_id}/name",
        json={"name": "  変更後の帳票名  "},
    )

    assert response.status_code == 200
    assert response.json() == {"id": template_id, "name": "変更後の帳票名"}
    assert client.get(f"/api/v1/templates/{template_id}").json()["name"] == "変更後の帳票名"
    assert any(
        item["id"] == template_id and item["name"] == "変更後の帳票名"
        for item in client.get("/api/v1/templates").json()
    )
    with database.session() as session:
        assert session.get(Template, template_id).name == "変更後の帳票名"


def test_rename_template_rejects_blank_and_duplicate_names(client: TestClient) -> None:
    database = client.app.state.database
    with database.session() as session:
        first = Template(name="対象帳票", document_type="legacy")
        second = Template(name="e\u0301", document_type="spreadsheet")
        session.add_all([first, second])
        session.commit()
        first_id = first.id

    blank = client.patch(f"/api/v1/templates/{first_id}/name", json={"name": "   "})
    duplicate = client.patch(f"/api/v1/templates/{first_id}/name", json={"name": "\u00e9"})

    assert blank.status_code == 422
    assert blank.json()["code"] == "VALIDATION_FAILED"
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "TEMPLATE_NAME_CONFLICT"


def test_rename_missing_template_returns_not_found(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/templates/missing-template/name",
        json={"name": "新しい名前"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "TEMPLATE_NOT_FOUND"
