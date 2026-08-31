from fastapi.testclient import TestClient

from app.db.models import LedgerDocument, Template


def test_delete_archives_in_use_template_and_removes_it_from_the_api_list(
    client: TestClient,
) -> None:
    database = client.app.state.database
    with database.session() as session:
        template = Template(
            name="in-use-api-template",
            document_type="cow-breeding-ledger",
        )
        session.add(template)
        session.flush()
        ledger = LedgerDocument(
            template_id=template.id,
            individual_id="0000000001",
            current_document_json={},
        )
        session.add(ledger)
        session.commit()
        template_id = template.id
        ledger_id = ledger.id

    response = client.delete(f"/api/v1/templates/{template_id}")

    assert response.status_code == 200
    assert response.json() == {"outcome": "archived"}
    assert template_id not in {
        item["id"] for item in client.get("/api/v1/templates").json()
    }
    with database.session() as session:
        archived = session.get(Template, template_id)
        assert archived is not None
        assert archived.archived_at is not None
        assert session.get(LedgerDocument, ledger_id) is not None


def test_delete_physically_removes_unused_template(client: TestClient) -> None:
    database = client.app.state.database
    with database.session() as session:
        template = Template(
            name="unused-api-template",
            document_type="cow-breeding-ledger",
        )
        session.add(template)
        session.commit()
        template_id = template.id

    response = client.delete(f"/api/v1/templates/{template_id}")

    assert response.status_code == 200
    assert response.json() == {"outcome": "deleted"}
    with database.session() as session:
        assert session.get(Template, template_id) is None


def test_delete_missing_template_returns_not_found(client: TestClient) -> None:
    response = client.delete("/api/v1/templates/missing-template")

    assert response.status_code == 404
    assert response.json()["code"] == "TEMPLATE_NOT_FOUND"
