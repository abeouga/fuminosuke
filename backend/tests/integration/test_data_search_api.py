from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.db.models import (
    ConfirmedResult,
    LedgerDocument,
    LedgerDocumentPage,
    ProcessingJob,
    Template,
    TemplateCandidateBinding,
    TemplateCandidateSet,
    TemplateVersion,
)


def _reading(value: str) -> dict[str, object]:
    return {
        "raw": value,
        "normalized": value,
        "status": "readable",
        "sourceRegionId": f"region-{value}",
    }


def _seed_confirmed_ledger(client: TestClient) -> tuple[str, str]:
    database = client.app.state.database
    now = datetime(2026, 8, 23, 1, 2, tzinfo=UTC)
    document = {
        "document": {
            "unifiedEarTag": _reading("123-4567-890"),
            "cattleName": _reading("こよみ"),
            "birthDate": _reading("2020-10-07"),
        },
        "saleCalves": [
            {
                "sireName": _reading("光晴重"),
                "priceYen": _reading("400000"),
            },
            {
                "sireName": _reading("福之姫"),
                "priceYen": _reading("700000"),
            },
        ],
        "pedigree": {},
        "calvings": [],
        "customFields": {},
    }
    field_mappings = [
        {
            "mappingId": "identity",
            "targetPath": "document.unifiedEarTag",
            "label": "個体識別番号",
        },
        {
            "mappingId": "name",
            "targetPath": "document.cattleName",
            "label": "名号",
        },
        {
            "mappingId": "birth-date",
            "targetPath": "document.birthDate",
            "label": "生年月日",
        },
        {
            "mappingId": "sire-1",
            "targetPath": "saleCalves.0.sireName",
            "label": "種雄牛名",
        },
        {
            "mappingId": "price-1",
            "targetPath": "saleCalves.0.priceYen",
            "label": "価格",
        },
        {
            "mappingId": "sire-2",
            "targetPath": "saleCalves.1.sireName",
            "label": "種雄牛名",
        },
        {
            "mappingId": "price-2",
            "targetPath": "saleCalves.1.priceYen",
            "label": "価格",
        },
    ]
    with database.session() as session:
        template = Template(name="繁殖台帳", document_type="cow-ledger")
        session.add(template)
        session.flush()
        version = TemplateVersion(
            template_id=template.id,
            version_number=1,
            mode="SINGLE",
            status="ACTIVE",
            canonical_width=1000,
            canonical_height=700,
            config_relative_path="templates/test/template.yaml",
        )
        session.add(version)
        session.flush()
        template.active_version_id = version.id
        job = ProcessingJob(
            job_type="EXTRACT_DOCUMENT",
            status="CONFIRMED",
            template_version_id=version.id,
            request_hash="a" * 64,
            confirmed_at=now,
            config_snapshot_json={
                "fieldMappings": field_mappings,
                "templateSnapshot": {
                    "config": {
                        "documentIdentityFields": [
                            {
                                "targetPath": "document.cattleName",
                                "label": "名号",
                                "matchRole": "confirmation",
                                "displayRole": "title",
                            },
                            {
                                "targetPath": "document.unifiedEarTag",
                                "label": "個体識別番号",
                                "matchRole": "confirmation",
                                "displayRole": "subtitle",
                            },
                        ]
                    }
                },
            },
        )
        session.add(job)
        session.flush()
        confirmed = ConfirmedResult(
            job_id=job.id,
            ocr_result_revision=1,
            individual_id="1234567890",
            confirmed_json=document,
            confirmed_at=now,
            export_relative_path="exports/test/confirmed.json",
        )
        ledger = LedgerDocument(
            template_id=template.id,
            individual_id="1234567890",
            current_job_id=job.id,
            current_document_json=document,
            spreadsheet_url="https://docs.google.com/spreadsheets/d/test/edit",
        )
        session.add_all([confirmed, ledger])
        session.flush()
        session.add(
            LedgerDocumentPage(
                ledger_document_id=ledger.id,
                job_id=job.id,
                page_number=1,
                relation_type="NEW_DOCUMENT",
                active=True,
                page_content_json=document,
                confirmed_at=now,
            )
        )
        candidate_set = TemplateCandidateSet(
            template_id=template.id,
            name="種雄牛名",
            normalized_name="種雄牛名",
            values_json=["光晴重", "福之姫"],
            allowed_suffixes_json=["ET"],
        )
        session.add(candidate_set)
        session.flush()
        session.add(
            TemplateCandidateBinding(
                template_id=template.id,
                candidate_set_id=candidate_set.id,
                target_pattern="saleCalves[].sireName",
                field_label="種雄牛名",
            )
        )
        session.commit()
        return template.id, ledger.id


def test_catalog_and_quick_search_use_template_metadata(client: TestClient) -> None:
    template_id, ledger_id = _seed_confirmed_ledger(client)

    catalog = client.get("/api/v1/data-search/catalog")

    assert catalog.status_code == 200
    template = catalog.json()["templates"][0]
    assert template["templateId"] == template_id
    assert template["templateName"] == "繁殖台帳"
    assert template["documentCount"] == 1
    sire = next(field for field in template["fields"] if field["label"] == "種雄牛名")
    assert sire["fieldKey"] == "saleCalves[].sireName"
    assert sire["suggestions"] == ["光晴重", "福之姫"]

    searched = client.post(
        "/api/v1/data-search",
        json={"query": "1234567890", "page": 1, "pageSize": 20},
    )

    assert searched.status_code == 200
    assert searched.json()["count"] == 1
    item = searched.json()["items"][0]
    assert item["documentId"] == ledger_id
    assert item["displayTitle"] == "こよみ"
    assert item["displaySubtitle"] == "123-4567-890"


def test_repeated_filters_must_match_the_same_row(client: TestClient) -> None:
    template_id, _ledger_id = _seed_confirmed_ledger(client)
    different_rows = client.post(
        "/api/v1/data-search",
        json={
            "templateId": template_id,
            "filters": [
                {
                    "fieldKey": "saleCalves[].sireName",
                    "operator": "exact",
                    "value": "光晴重",
                },
                {
                    "fieldKey": "saleCalves[].priceYen",
                    "operator": "gte",
                    "value": "500000",
                },
            ],
        },
    )
    same_row = client.post(
        "/api/v1/data-search",
        json={
            "templateId": template_id,
            "filters": [
                {
                    "fieldKey": "saleCalves[].sireName",
                    "operator": "exact",
                    "value": "福之姫",
                },
                {
                    "fieldKey": "saleCalves[].priceYen",
                    "operator": "gte",
                    "value": "500000",
                },
            ],
        },
    )

    assert different_rows.status_code == 200
    assert different_rows.json()["count"] == 0
    assert same_row.status_code == 200
    assert same_row.json()["count"] == 1
    assert {match["recordOrder"] for match in same_row.json()["items"][0]["matches"]} == {2}


def test_date_filter_supports_an_inclusive_period(client: TestClient) -> None:
    template_id, _ledger_id = _seed_confirmed_ledger(client)

    inside = client.post(
        "/api/v1/data-search",
        json={
            "templateId": template_id,
            "filters": [
                {
                    "fieldKey": "document.birthDate",
                    "operator": "between",
                    "value": "2020-10-01",
                    "valueTo": "2020-10-31",
                }
            ],
        },
    )
    outside = client.post(
        "/api/v1/data-search",
        json={
            "templateId": template_id,
            "filters": [
                {
                    "fieldKey": "document.birthDate",
                    "operator": "between",
                    "value": "2020-11-01",
                    "valueTo": "2020-11-30",
                }
            ],
        },
    )

    assert inside.status_code == 200
    assert inside.json()["count"] == 1
    assert outside.status_code == 200
    assert outside.json()["count"] == 0
