from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import (
    ProcessingJob,
    Template,
    TemplateFieldIdentity,
    TemplatePackageIdentity,
    TemplatePackageInstallation,
    TemplateVersion,
)
from app.domain.template_config import DocumentIdentityConfig, build_blank_template_config
from app.layout_analysis.models import LayoutDefinition
from app.repositories.template_repository import TemplateRepository
from app.services.job_service import JobService
from app.services.layout_service import LayoutService
from app.services.template_management_service import TemplateManagementService
from app.template_packages.activation_service import TemplatePackageActivationService

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/mark-mapping-validation.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["state"])
def test_shared_mark_validation_cases(case):
    payload = copy.deepcopy(FIXTURE["layout"])
    mark = payload["cells"][2]["markSets"][0]
    mark["targetPath"] = case["targetPath"]
    mark["options"] = [FIXTURE["option"]] if case["hasOptions"] else []
    error = LayoutService.mapping_validation_error(LayoutDefinition.model_validate(payload))
    assert (error.code if error else None) == case["code"]
    state = "valid" if error is None else "unassigned" if not mark["targetPath"] else "incomplete"
    assert state == case["state"]
    if state == "incomplete":
        assert "mark_incomplete" in error.detail
        assert "choice" in error.detail
        assert mark["targetPath"] in error.detail
        assert error.field_errors[0].field == "cells.mark_incomplete.markSets.choice.options"


@pytest.mark.parametrize("recognition_mode", ["manual", "symbol"])
def test_failed_draft_preserves_mappings_and_identity_and_blocks_consumers(
    client, recognition_mode,
):
    database, storage = client.app.state.database, client.app.state.storage
    payload = copy.deepcopy(FIXTURE["layout"])
    payload["cells"][1]["markSets"][0]["recognitionMode"] = recognition_mode
    valid_subset = copy.deepcopy(payload)
    valid_subset["cells"].pop()
    with database.session() as session:
        template = Template(name="minimal", document_type="minimal")
        job = ProcessingJob(
            job_type="PREPARE_TEMPLATE",
            status="REVIEW_REQUIRED",
            request_hash="a" * 64,
            config_snapshot_json={},
        )
        session.add_all([template, job])
        session.flush()
        config = build_blank_template_config(
            template_id=template.id,
            template_version=1,
            name="minimal",
            canonical_width=100,
            canonical_height=30,
        )
        config_path = storage.write_text(
            "minimal/template.yaml", yaml.safe_dump(config.model_dump(mode="json", by_alias=True))
        )
        version = TemplateVersion(
            template_id=template.id,
            version_number=1,
            mode="REFERENCE_TEMPLATE",
            status="DRAFT",
            source_job_id=job.id,
            canonical_width=100,
            canonical_height=30,
            config_relative_path=config_path,
            quality_json={},
            layout_json={},
        )
        session.add(version)
        session.commit()
        version_id = version.id
    service = LayoutService(database, storage)
    management = TemplateManagementService(database, storage)
    service.save(version_id, LayoutDefinition.model_validate(valid_subset))
    mappings = management.get_field_mappings(version_id)[2]
    saved_mark = next(mapping for mapping in mappings if mapping.mapping_type == "mark")
    assert saved_mark.mark_recognition_mode == recognition_mode
    assert service.get(version_id).cells[1].mark_sets[0].recognition_mode == recognition_mode
    identity = DocumentIdentityConfig(
        mapping_id=mappings[0].mapping_id,
        target_path=mappings[0].target_path,
        label="正常テキスト",
        value_type="text",
        match_role="primary",
        display_role="title",
    )
    management.save_field_mappings(
        version_id, mappings, document_identity=identity, document_identity_fields=[identity]
    )
    before_bytes = storage.read_bytes(config_path)

    def identities():
        with database.session() as session:
            return sorted(
                (row.mapping_id, row.logical_field_id, row.present)
                for row in session.scalars(
                    select(TemplateFieldIdentity).where(
                        TemplateFieldIdentity.template_version_id == version_id
                    )
                )
            )

    before_identity = identities()
    assert len(before_identity) == 2 and all(row[2] for row in before_identity)
    with patch.object(
        TemplateManagementService,
        "save_field_mappings",
        side_effect=AssertionError("invalid layout must not persist mappings"),
    ):
        response = client.put(f"/api/v1/template-versions/{version_id}/layout", json=payload)
    assert response.status_code == 200
    assert LayoutDefinition.model_validate(response.json()).relations == (
        LayoutDefinition.model_validate(payload).relations
    )
    assert storage.read_bytes(config_path) == before_bytes
    assert identities() == before_identity
    assert management.get_field_mappings(version_id)[2] == mappings
    fetched = client.get(f"/api/v1/template-versions/{version_id}/field-mappings").json()
    assert fetched["validForOcr"] is False
    assert fetched["documentIdentity"]["mappingId"] == identity.mapping_id
    assert fetched["documentIdentityFields"][0]["mappingId"] == identity.mapping_id
    assert any(
        "MARK_OPTIONS_REQUIRED" in warning and "mark_incomplete" in warning
        for warning in fetched["warnings"]
    )
    assert fetched["fieldErrors"][0]["field"] == "cells.mark_incomplete.markSets.choice.options"
    job_service = client.app.state.job_service
    with pytest.raises(AppError) as rejected:
        asyncio.run(
            job_service.create_ocr_job(
                template_version_id=version_id,
                mother_individual_id=None,
                upload=None,
                ocr_enabled=True,
                idempotency_key=None,
            )
        )
    assert rejected.value.code == "INVALID_STATE_TRANSITION"
    with database.session() as session:
        version = session.get(TemplateVersion, version_id)
        assert version.quality_json["layoutFinalized"] is False
        assert version.quality_json["layoutVerified"] is False
        # Even qualification shortcuts cannot promote a failed saved layout.
        with pytest.raises(AppError, match="選択肢"):
            TemplateRepository(session).approve(version_id)
        with pytest.raises(AppError, match="選択肢"):
            JobService._field_mapping_snapshot(object(), version, required=False, config=config)
    with (
        patch.object(
            LayoutService,
            "_finalize_handwriting",
            side_effect=AssertionError("must reject before reading"),
        ),
        pytest.raises(AppError) as failure,
    ):
        service.verify(version_id)
    assert failure.value.code == "MARK_OPTIONS_REQUIRED"

    with database.session() as session:
        version = session.get(TemplateVersion, version_id)
        session.add(
            TemplatePackageIdentity(
                publisher_id="test", package_id="minimal", local_template_id=version.template_id
            )
        )
        session.flush()
        installation = TemplatePackageInstallation(
            publisher_id="test",
            package_id="minimal",
            release_version="1",
            content_digest="a" * 64,
            local_template_id=version.template_id,
            local_template_version_id=version.id,
            local_job_id=version.source_job_id,
            manifest_json={},
            canonical_template_json={},
            canonical_quality_json={},
            local_mapping_json={},
        )
        session.add(installation)
        session.commit()
        installation_id = installation.id
    with pytest.raises(AppError) as activation:
        TemplatePackageActivationService(database).activate(
            installation_id,
            expected_active_version_id=None,
            owner_key="test",
        )
    assert activation.value.code == "MARK_OPTIONS_REQUIRED"
    assert storage.read_bytes(config_path) == before_bytes
    assert identities() == before_identity

    payload["cells"][2]["markSets"][0]["options"] = [FIXTURE["option"]]
    repaired = client.put(f"/api/v1/template-versions/{version_id}/layout", json=payload)
    assert repaired.status_code == 200
    persisted = management.get_field_mappings(version_id)[2]
    assert [m.mapping_type for m in persisted] == ["text", "mark", "mark"]
    assert persisted[:2] == mappings
    assert all(row in identities() for row in before_identity)
    fetched = client.get(f"/api/v1/template-versions/{version_id}/field-mappings").json()
    assert fetched["validForOcr"] is True
    assert fetched["fieldErrors"] == []
    assert fetched["documentIdentity"]["mappingId"] == identity.mapping_id
    with database.session() as session:
        assert session.get(TemplateVersion, version_id).quality_json["layoutFinalized"] is True
