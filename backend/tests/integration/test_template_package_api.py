from __future__ import annotations

import hashlib
import io
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.db.models import (
    OutputLayoutTemplateRevision,
    Template,
    TemplateCandidateSet,
    TemplatePackageInstallation,
    TemplateVersion,
)
from app.template_packages.activation_service import TemplatePackageActivationService
from app.template_packages.canonical_models import (
    CanonicalOutputLayout,
    CanonicalTemplate,
    CanonicalTemplateQuality,
)
from app.template_packages.package_reader import TemplatePackageReader
from app.template_packages.package_serializer import (
    GeneratedTemplatePackageValidator,
    TemplatePackageSerializer,
)


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (300, 100), color=(250, 250, 250)).save(output, format="PNG")
    return output.getvalue()


def _package(
    *,
    version: str = "1.0.0",
    candidate: str = "山田太郎",
    name: str = "面接カード",
    document_type: str = "interview-card",
) -> bytes:
    template = CanonicalTemplate.model_validate(
        {
            "name": name,
            "documentType": document_type,
            "mode": "REFERENCE_TEMPLATE",
            "canonicalWidth": 300,
            "canonicalHeight": 100,
            "runtime": {
                "registration": {
                    "preferredMethod": "ORB_HOMOGRAPHY",
                    "minFeatureMatches": 4,
                    "minInliers": 4,
                    "minInlierRatio": 0.3,
                    "maxReprojectionErrorPx": 8,
                    "minEccScore": 0.4,
                },
                "regions": [],
                "fieldMappings": [
                    {
                        "mappingId": "person-name",
                        "targetPath": "document.personName",
                        "label": "氏名",
                        "x": 100,
                        "y": 10,
                        "width": 150,
                        "height": 30,
                    }
                ],
                "documentIdentityFields": [
                    {
                        "mappingId": "person-name",
                        "targetPath": "document.personName",
                        "label": "氏名",
                        "matchRole": "primary",
                        "displayRole": "title",
                    }
                ],
                "documentName": {"fieldPaths": ["document.personName"]},
                "marks": [],
                "export": {"csvColumns": ["document.personName"]},
            },
            "layout": {
                "layoutSchemaVersion": 3,
                "canonicalWidth": 300,
                "canonicalHeight": 100,
                "cells": [
                    {
                        "id": "value-name",
                        "role": "input",
                        "x": 100,
                        "y": 10,
                        "width": 150,
                        "height": 30,
                        "confidence": 1,
                    }
                ],
                "relations": [
                    {
                        "id": "relation-name",
                        "inputCellId": "value-name",
                        "labelCellIds": [],
                        "axis": "horizontal",
                        "order": 1,
                        "targetPath": "document.personName",
                    }
                ],
                "recordStructureVerified": True,
            },
            "candidateSets": [
                {
                    "id": "candidate-set-001",
                    "name": "氏名候補",
                    "values": [candidate],
                    "targetPatterns": ["document.personName"],
                }
            ],
            "assets": {
                "cleanTemplate": "assets/clean-template.png",
                "outputLayout": "optional/output-layout.json",
            },
        }
    )
    quality = CanonicalTemplateQuality(
        document_boundary_verified=True,
        layout_verified=True,
        layout_finalized=True,
        record_structure_verified=True,
        definition_approved=False,
        definition_valid=False,
        field_mapping_count=1,
    )
    output_layout = CanonicalOutputLayout.model_validate(
        {
            "sheetName": "帳票レイアウト",
            "rowCount": 1,
            "columnCount": 1,
            "cells": [
                {
                    "row": 0,
                    "column": 0,
                    "value": {"stringValue": "氏名"},
                    "binding": "document.personName",
                }
            ],
        }
    )
    return TemplatePackageSerializer().build(
        canonical_template=template,
        quality=quality,
        output_layout=output_layout,
        asset_payloads={"assets/clean-template.png": _png()},
        publisher_id="jp.example",
        package_id="interview-card",
        release_version=version,
        exported_at=datetime(2026, 9, 1, tzinfo=UTC),
    ).content


def _inspect(client: TestClient, content: bytes) -> dict[str, object]:
    response = client.post(
        "/api/v1/template-packages/imports/inspect",
        files={
            "file": (
                "interview-card.llmocr-template",
                content,
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _confirm_and_wait(client: TestClient, token: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/template-packages/imports/confirm",
        json={"confirmationToken": token},
    )
    assert response.status_code == 202, response.text
    operation = response.json()
    for _ in range(100):
        if operation["status"] in {"COMPLETED", "FAILED"}:
            return operation
        time.sleep(0.01)
        operation = client.get(
            f"/api/v1/template-packages/operations/{operation['operationId']}"
        ).json()
    raise AssertionError("template package import did not finish")


def test_import_inspection_uses_independent_reader_and_short_lived_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _package()

    def forbidden_call(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("generated package validator must not be used")

    monkeypatch.setattr(GeneratedTemplatePackageValidator, "validate", forbidden_call)
    inspected = _inspect(client, content)
    assert inspected["status"] == "READY"
    assert inspected["confirmationToken"]
    assert inspected["importInspection"]["formatVersion"] == 1
    assert inspected["importInspection"]["schemaVersion"] == 1
    assert inspected["importInspection"]["trustStatus"] == "UNVERIFIED"
    operation_id = str(inspected["operationId"])
    assert client.app.state.storage.exists(
        f"tmp/template-package-inspections/{operation_id}.llmocr-template"
    )
    stored_operation = client.get(
        f"/api/v1/template-packages/operations/{operation_id}"
    )
    assert stored_operation.status_code == 200
    assert stored_operation.json()["confirmationToken"] is None

    service = client.app.state.template_package_operations
    token_hash = hashlib.sha256(str(inspected["confirmationToken"]).encode()).hexdigest()
    service._tokens[token_hash].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    expired = client.post(
        "/api/v1/template-packages/imports/confirm",
        json={"confirmationToken": inspected["confirmationToken"]},
    )
    assert expired.status_code == 410
    assert expired.json()["code"] == "TEMPLATE_PACKAGE_TOKEN_EXPIRED"


def test_package_sha_change_is_rejected_before_import(client: TestClient) -> None:
    inspected = _inspect(client, _package())
    operation_id = str(inspected["operationId"])
    client.app.state.storage.write_bytes(
        f"tmp/template-package-inspections/{operation_id}.llmocr-template",
        _package(version="1.0.1"),
    )
    response = client.post(
        "/api/v1/template-packages/imports/confirm",
        json={"confirmationToken": inspected["confirmationToken"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "IMPORT_PACKAGE_CHANGED"
    with client.app.state.database.session() as session:
        assert session.scalar(select(func.count()).select_from(TemplatePackageInstallation)) == 0


def test_drive_package_uses_reader_and_remains_inactive(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _package()

    class FakeDriveSync:
        def package_for_inspection(
            self,
            file_id: str,
            *,
            kind: str,
            google_subject: str,
        ) -> tuple[bytes, str]:
            assert file_id == "drive-file-1"
            assert kind == "release"
            assert google_subject == "subject-1"
            return content, "interview-card.llmocr-template"

    monkeypatch.setattr(
        "app.api.v1.routes.template_packages._google_subject",
        lambda _user: "subject-1",
    )
    client.app.state.template_package_drive_sync = FakeDriveSync()
    inspected = client.post(
        "/api/v1/template-packages/drive/inspect",
        json={"fileId": "drive-file-1", "kind": "RELEASE"},
    )
    assert inspected.status_code == 200, inspected.text
    operation = _confirm_and_wait(
        client,
        str(inspected.json()["confirmationToken"]),
    )
    assert operation["status"] == "COMPLETED"
    result = operation["result"]
    assert isinstance(result, dict)
    with client.app.state.database.session() as session:
        installation = session.get(TemplatePackageInstallation, result["installationId"])
        template = session.get(Template, result["localTemplateId"])
        assert installation is not None
        assert installation.source_type == "DRIVE"
        assert installation.trust_status == "UNVERIFIED"
        assert template is not None
        assert template.active_version_id is None


def test_import_activate_export_round_trip_and_activation_idempotency(
    client: TestClient,
) -> None:
    imported = _confirm_and_wait(client, str(_inspect(client, _package())["confirmationToken"]))
    assert imported["status"] == "COMPLETED"
    result = imported["result"]
    assert result["activeVersionChanged"] is False
    template_id = result["localTemplateId"]
    version_id = result["localTemplateVersionId"]
    installation_id = result["installationId"]

    blocked = client.post(f"/api/v1/template-versions/{version_id}/approve")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "TEMPLATE_PACKAGE_ACTIVATION_REQUIRED"

    activated = client.post(
        f"/api/v1/template-packages/installations/{installation_id}/activate",
        json={"expectedActiveVersionId": None},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["activeVersionId"] == version_id
    assert activated.json()["candidateSetCount"] == 1
    assert activated.json()["outputLayoutMaterialized"] is True

    repeated = client.post(
        f"/api/v1/template-packages/installations/{installation_id}/activate",
        json={"expectedActiveVersionId": None},
    )
    assert repeated.status_code == 200
    assert repeated.json()["alreadyActive"] is True

    with client.app.state.database.session() as session:
        template = session.get(Template, template_id)
        assert template is not None and template.active_version_id == version_id
        assert session.scalar(select(func.count()).select_from(TemplateCandidateSet)) == 1
        assert (
            session.scalar(select(func.count()).select_from(OutputLayoutTemplateRevision)) == 1
        )

    inspected_export = client.post(
        "/api/v1/template-packages/exports/inspect",
        json={
            "templateId": template_id,
            "templateVersionId": version_id,
            "publisherId": "jp.receiver",
            "packageId": "interview-card-copy",
            "version": "1.0.0",
            "includeOutputLayout": True,
        },
    )
    assert inspected_export.status_code == 200, inspected_export.text
    export_operation = inspected_export.json()
    assert export_operation["exportInspection"]["candidateSets"][0]["values"] == [
        "山田太郎"
    ]
    exported = client.post(
        "/api/v1/template-packages/exports/confirm",
        json={"confirmationToken": export_operation["confirmationToken"]},
    )
    assert exported.status_code == 200, exported.text
    validated = TemplatePackageReader().read(exported.content)
    assert validated.manifest.publisher_id == "jp.receiver"
    assert validated.manifest.package_id == "interview-card-copy"


def test_activation_conflict_and_failure_rollback(client: TestClient) -> None:
    first = _confirm_and_wait(client, str(_inspect(client, _package())["confirmationToken"]))
    first_result = first["result"]
    client.post(
        f"/api/v1/template-packages/installations/{first_result['installationId']}/activate",
        json={"expectedActiveVersionId": None},
    )
    active_version_id = first_result["localTemplateVersionId"]

    second = _confirm_and_wait(
        client,
        str(_inspect(client, _package(version="2.0.0", candidate="佐藤花子"))["confirmationToken"]),
    )
    second_result = second["result"]
    conflict = client.post(
        f"/api/v1/template-packages/installations/{second_result['installationId']}/activate",
        json={"expectedActiveVersionId": None},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "TEMPLATE_PACKAGE_ACTIVATION_CONFLICT"

    def fail(stage: str) -> None:
        if stage == "after_candidate_materialization":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        TemplatePackageActivationService(client.app.state.database).activate(
            second_result["installationId"],
            expected_active_version_id=active_version_id,
            owner_key="local",
            failure_injector=fail,
        )
    with client.app.state.database.session() as session:
        template = session.get(Template, first_result["localTemplateId"])
        assert template is not None and template.active_version_id == active_version_id
        candidates = list(session.scalars(select(TemplateCandidateSet)))
        assert len(candidates) == 1 and candidates[0].values_json == ["山田太郎"]
        second_version = session.get(TemplateVersion, second_result["localTemplateVersionId"])
        assert second_version is not None and second_version.status == "DRAFT"


def test_activation_applies_version_metadata_without_mutating_it_during_import(
    client: TestClient,
) -> None:
    first = _confirm_and_wait(client, str(_inspect(client, _package())["confirmationToken"]))
    first_result = first["result"]
    activated = client.post(
        f"/api/v1/template-packages/installations/{first_result['installationId']}/activate",
        json={"expectedActiveVersionId": None},
    )
    assert activated.status_code == 200, activated.text

    second = _confirm_and_wait(
        client,
        str(
            _inspect(
                client,
                _package(
                    version="2.0.0",
                    name="面接カード 改訂版",
                    document_type="generic-form",
                ),
            )["confirmationToken"]
        ),
    )
    second_result = second["result"]
    with client.app.state.database.session() as session:
        template = session.get(Template, first_result["localTemplateId"])
        assert template is not None
        assert template.name == "面接カード"
        assert template.document_type == "interview-card"

    activated = client.post(
        f"/api/v1/template-packages/installations/{second_result['installationId']}/activate",
        json={"expectedActiveVersionId": first_result["localTemplateVersionId"]},
    )
    assert activated.status_code == 200, activated.text
    with client.app.state.database.session() as session:
        template = session.get(Template, first_result["localTemplateId"])
        assert template is not None
        assert template.name == "面接カード 改訂版"
        assert template.document_type == "generic-form"
