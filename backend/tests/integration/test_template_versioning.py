from __future__ import annotations

import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from PIL import Image
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import (
    JobArtifact,
    OutputLayoutTemplateRevision,
    ProcessingJob,
    Template,
    TemplateCandidateBinding,
    TemplateCandidateSet,
    TemplateVersion,
)
from app.domain.template_config import (
    DocumentIdentityConfig,
    FieldMappingConfig,
    build_blank_template_config,
    parse_template_config,
)
from app.services.print_layout_service import PrintLayoutService, digest
from app.services.reference_qualification_service import ReferenceQualificationService
from app.services.template_duplicate_service import TemplateDuplicateService
from app.services.template_version_service import TemplateVersionService
from tests.integration.test_calc_qualification_plan import (  # noqa: F401
    calc_fixture,
    calc_reference,
    prepared_fixture,
    print_template,
    saved_template,
)
from tests.integration.test_candidate_readiness_api import reviewed_pages  # noqa: F401
from tests.integration.test_ocr_review_api import _active_template


@pytest.fixture
def versioned_source(client: TestClient) -> tuple[str, bytes]:
    database, storage = client.app.state.database, client.app.state.storage
    image = BytesIO()
    Image.new("RGB", (300, 100), "white").save(image, format="PNG")
    png = image.getvalue()
    workbook = Workbook()
    workbook.active.title = "原本"
    workbook.active["C1"] = "印刷済み"
    book = BytesIO()
    workbook.save(book)
    with database.session() as session:
        template = Template(name="Versioning", document_type="spreadsheet")
        job = ProcessingJob(
            job_type="PREPARE_TEMPLATE",
            mode="SPREADSHEET_TEMPLATE",
            status="CONFIRMED",
            request_hash="f" * 64,
            config_snapshot_json={},
        )
        session.add_all([template, job])
        session.flush()
        artifacts = []
        for name, filename, content, media in (
            ("cleanTemplate", "clean.png", png, "image/png"),
            ("normalized", "normalized.png", png, "image/png"),
            (
                "sourceWorkbook",
                "source.xlsx",
                book.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        ):
            path = storage.write_bytes(f"jobs/{job.id}/1/{filename}", content)
            artifact = JobArtifact(
                job_id=job.id,
                attempt_number=1,
                artifact_name=name,
                relative_path=path,
                media_type=media,
                sha256=hashlib.sha256(content).hexdigest(),
                width=300,
                height=100,
            )
            session.add(artifact)
            artifacts.append(artifact)
        session.flush()
        mappings = [
            FieldMappingConfig(
                mapping_id=f"layout_xlsx_{cell}",
                target_path=target,
                label=label,
                x=i * 100,
                y=0,
                width=90,
                height=30,
            )
            for i, (cell, target, label) in enumerate(
                (
                    ("A1", "document.cattleName", "氏名"),
                    ("B1", "customFields.address", "住所"),
                )
            )
        ]
        config = build_blank_template_config(
            template_id=template.id,
            template_version=1,
            name=template.name,
            canonical_width=300,
            canonical_height=100,
        ).model_copy(
            update={
                "field_mappings": mappings,
                "document_identity": DocumentIdentityConfig(
                    mapping_id=mappings[0].mapping_id,
                    target_path=mappings[0].target_path,
                    label="氏名",
                ),
            }
        )
        path = storage.write_text(
            f"templates/{template.id}/versions/1/template.json",
            config.model_dump_json(by_alias=True),
        )
        layout = {
            "canonicalWidth": 300,
            "canonicalHeight": 100,
            "detector": "xlsx-structure",
            "cells": [
                {
                    "id": m.mapping_id.removeprefix("layout_"),
                    "role": "input",
                    "x": m.x,
                    "y": m.y,
                    "width": m.width,
                    "height": m.height,
                    "confidence": 1,
                    "printedLabelText": m.label,
                }
                for m in mappings
            ],
            "relations": [
                {
                    "id": f"r{i}",
                    "inputCellId": m.mapping_id.removeprefix("layout_"),
                    "labelCellIds": [],
                    "axis": "horizontal",
                    "order": 1,
                    "targetPath": m.target_path,
                }
                for i, m in enumerate(mappings)
            ],
            "cellMerges": [],
            "recordStructureVerified": True,
        }
        version = TemplateVersion(
            template_id=template.id,
            version_number=1,
            status="ACTIVE",
            mode="SPREADSHEET_TEMPLATE",
            source_job_id=job.id,
            canonical_width=300,
            canonical_height=100,
            clean_template_artifact_id=artifacts[0].id,
            config_relative_path=path,
            quality_json={
                "layoutVerified": True,
                "layoutFinalized": True,
                "layoutVerificationDetector": "xlsx-structure",
                "fieldMappingCount": 2,
                "sourceSheetName": "原本",
                "sourceFileName": "source.xlsx",
            },
            layout_json=layout,
        )
        session.add(version)
        session.flush()
        template.active_version_id = version.id
        session.commit()
        return version.id, png


def _get(client: TestClient, version: str, resource: str) -> object:
    response = client.get(f"/api/v1/template-versions/{version}/{resource}")
    assert response.status_code == 200, response.text
    return response.json()


def _edit(client: TestClient, version: str, *, revision: str | None = None) -> dict:
    params = {"base_revision": revision} if revision is not None else None
    response = client.post(f"/api/v1/template-versions/{version}/edit", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_duplicate_creates_an_independent_editable_template(client, versioned_source):
    source_id, _ = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    with database.session() as session:
        source = session.get(TemplateVersion, source_id)
        source_template_id = source.template_id
        candidate = TemplateCandidateSet(
            template_id=source_template_id, name="氏名候補", normalized_name="氏名候補",
            values_json=["花子"], allowed_suffixes_json=[],
        )
        session.add(candidate)
        session.flush()
        session.add(TemplateCandidateBinding(
            template_id=source_template_id, candidate_set_id=candidate.id,
            target_pattern="document.cattleName", field_label="氏名",
        ))
        session.add(OutputLayoutTemplateRevision(
            owner_key="local", template_version_id=source_id, revision=1,
            layout_json={"sourceTemplateVersionId": source_id, "sheetName": "原本"},
            source_sheet_name="原本", source_job_id=source.source_job_id,
        ))
        session.commit()

    response = client.post(
        f"/api/v1/template-versions/{source_id}/duplicate",
        json={"name": "Versioning のコピー"},
    )
    assert response.status_code == 201, response.text
    copied = response.json()
    assert copied["templateId"] != source_template_id
    assert copied["templateVersionId"] != source_id
    with database.session() as session:
        source = session.get(TemplateVersion, source_id)
        target = session.get(TemplateVersion, copied["templateVersionId"])
        template = session.get(Template, copied["templateId"])
        target_id = target.id
        copied_template_id = template.id
        assert source.status == "ACTIVE"
        assert template.name == "Versioning のコピー"
        assert template.active_version_id is None
        assert target.template_id == template.id
        assert target.version_number == 1
        assert target.status == "DRAFT"
        assert target.source_job_id != source.source_job_id
        assert target.clean_template_artifact_id != source.clean_template_artifact_id
        assert "forkParentVersionId" not in target.quality_json
        assert target.quality_json["layoutVerified"] is False
        original_config = parse_template_config(
            storage.read_bytes(source.config_relative_path).decode()
        )
        copied_config = parse_template_config(
            storage.read_bytes(target.config_relative_path).decode()
        )
        assert copied_config.template_id == template.id
        assert copied_config.template_version == 1
        assert copied_config.name == template.name
        assert {m.target_path for m in copied_config.field_mappings} == {
            m.target_path for m in original_config.field_mappings
        }
        assert {m.mapping_id for m in copied_config.field_mappings}.isdisjoint(
            m.mapping_id for m in original_config.field_mappings
        )
        assert {c["id"] for c in target.layout_json["cells"]}.isdisjoint(
            c["id"] for c in source.layout_json["cells"]
        )
        old_artifacts = list(session.scalars(select(JobArtifact).where(
            JobArtifact.job_id == source.source_job_id,
        )))
        new_artifacts = list(session.scalars(select(JobArtifact).where(
            JobArtifact.job_id == target.source_job_id,
        )))
        assert len(new_artifacts) == len(old_artifacts)
        assert {a.relative_path for a in old_artifacts}.isdisjoint(
            a.relative_path for a in new_artifacts
        )
        assert sorted(a.sha256 for a in new_artifacts) == sorted(a.sha256 for a in old_artifacts)
        copied_sets = list(session.scalars(select(TemplateCandidateSet).where(
            TemplateCandidateSet.template_id == template.id,
        )))
        assert len(copied_sets) == 1
        assert copied_sets[0].id != candidate.id
        assert copied_sets[0].values_json == ["花子"]
        copied_output = session.scalar(select(OutputLayoutTemplateRevision).where(
            OutputLayoutTemplateRevision.template_version_id == target_id,
        ))
        assert copied_output.layout_json["sourceTemplateVersionId"] == target_id
        assert copied_output.source_job_id == target.source_job_id

    old_ids = client.get(f"/api/v1/template-versions/{source_id}/field-identities").json()
    new_ids = client.get(
        f"/api/v1/template-versions/{copied['templateVersionId']}/field-identities"
    ).json()
    assert set(old_ids.values()).isdisjoint(new_ids.values())
    changed_candidates = client.put(
        f"/api/v1/templates/{copied_template_id}/candidate-settings",
        json={
            "templateVersionId": target_id,
            "sets": [{
                "name": "氏名候補", "values": ["次郎"], "allowedSuffixes": [],
                "targetPatterns": ["document.cattleName"],
            }],
        },
    )
    assert changed_candidates.status_code == 200, changed_candidates.text
    original_candidates = client.get(
        f"/api/v1/templates/{source_template_id}/candidate-settings"
    ).json()
    assert original_candidates["sets"][0]["values"] == ["花子"]
    assert client.delete(f"/api/v1/templates/{source_template_id}").status_code == 200
    assert client.get(f"/api/v1/templates/{copied_template_id}").status_code == 200
    assert client.get(f"/api/v1/template-versions/{target_id}/layout").status_code == 200


def test_duplicate_for_reading_activates_an_independent_single_page_copy(
    client, versioned_source,
):
    source_id, _ = versioned_source
    database = client.app.state.database
    response = client.post(
        f"/api/v1/template-versions/{source_id}/duplicate",
        json={"name": "Versioning の読み取り用コピー", "activate": True},
    )
    assert response.status_code == 201, response.text
    copied = response.json()
    with database.session() as session:
        source = session.get(TemplateVersion, source_id)
        target = session.get(TemplateVersion, copied["templateVersionId"])
        template = session.get(Template, copied["templateId"])
        assert source.status == "ACTIVE"
        assert target.status == "ACTIVE"
        assert template.active_version_id == target.id
        assert template.id != source.template_id
        assert target.source_job_id != source.source_job_id
        assert target.quality_json["layoutVerified"] is True
        assert target.quality_json["layoutVerificationFingerprint"]
    assert client.get(f"/api/v1/templates/{copied['templateId']}").json()[
        "activeVersionId"
    ] == copied["templateVersionId"]


def test_duplicate_for_reading_prepares_its_own_paged_readiness(
    client, reviewed_pages,  # noqa: F811
):
    source_id, url, ready, _ = reviewed_pages
    assert client.post(url + "/ocr-readiness", params={
        "base_revision": ready["revision"],
    }).status_code == 200
    assert client.post(
        f"/api/v1/template-versions/{source_id}/approve",
        params={"base_revision": ready["revision"]},
    ).status_code == 200

    response = client.post(
        f"/api/v1/template-versions/{source_id}/duplicate",
        json={"name": "ページ帳票の読み取り用コピー", "activate": True},
    )
    assert response.status_code == 201, response.text
    copied = response.json()
    with client.app.state.database.session() as session:
        source = session.get(TemplateVersion, source_id)
        target = session.get(TemplateVersion, copied["templateVersionId"])
        assert source.status == target.status == "ACTIVE"
        assert target.quality_json["candidateReadiness"]["artifactId"] != (
            source.quality_json["candidateReadiness"]["artifactId"]
        )
        assert target.quality_json["printLayout"]["printFiles"]["xlsx"]["artifactId"] != (
            source.quality_json["printLayout"]["printFiles"]["xlsx"]["artifactId"]
        )
    catalog = client.get(f"/api/v1/template-versions/{copied['templateVersionId']}/ocr-pages")
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["available"] is True


@pytest.mark.parametrize("mode", ["REFERENCE_TEMPLATE", "SINGLE_IMAGE_EXPERIMENTAL"])
def test_duplicate_for_reading_reverifies_an_image_template(client, mode):
    source_id = _active_template(client)
    if mode == "SINGLE_IMAGE_EXPERIMENTAL":
        with client.app.state.database.session() as session:
            source = session.get(TemplateVersion, source_id)
            source.mode = mode
            session.get(ProcessingJob, source.source_job_id).mode = mode
            session.commit()
    response = client.post(
        f"/api/v1/template-versions/{source_id}/duplicate",
        json={"name": "画像帳票の読み取り用コピー", "activate": True},
    )
    assert response.status_code == 201, response.text
    copied = response.json()
    with client.app.state.database.session() as session:
        source = session.get(TemplateVersion, source_id)
        target = session.get(TemplateVersion, copied["templateVersionId"])
        assert source.status == target.status == "ACTIVE"
        assert target.mode == mode
        assert target.source_job_id != source.source_job_id
        assert target.quality_json["layoutVerificationFingerprint"] != (
            source.quality_json["layoutVerificationFingerprint"]
        )
    mappings = client.get(
        f"/api/v1/template-versions/{copied['templateVersionId']}/field-mappings"
    )
    assert mappings.status_code == 200, mappings.text
    assert mappings.json()["validForOcr"] is True


def test_duplicate_page_template_keeps_pages_but_requires_its_own_readiness(
    client, versioned_source,
):
    source_id, png = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    with database.session() as session:
        source = session.get(TemplateVersion, source_id)
        job = session.get(ProcessingJob, source.source_job_id)
        reference_path = storage.write_bytes(f"jobs/{job.id}/1/reference.png", png)
        reference = JobArtifact(
            job_id=job.id, attempt_number=1, artifact_name="pageReference",
            relative_path=reference_path, media_type="image/png",
            sha256=hashlib.sha256(png).hexdigest(), width=300, height=100,
        )
        session.add(reference)
        session.flush()
        workbook = session.scalar(select(JobArtifact).where(
            JobArtifact.job_id == job.id, JobArtifact.artifact_name == "sourceWorkbook",
        ))
        source.quality_json = {
            **source.quality_json,
            "candidateReadiness": {"status": "CANDIDATE_READY", "versionId": source_id},
            "printLayout": {
                "sourceSha256": workbook.sha256,
                "layoutSha256": digest(source.layout_json),
                "printFiles": {"renderKey": "old"},
                "pages": [{
                    "page": 1, "range": "A1:C3",
                    "referenceImage": {"artifactId": reference.id,
                                       "relativePath": reference.relative_path,
                                       "sha256": reference.sha256},
                }],
            },
        }
        session.commit()
    response = client.post(
        f"/api/v1/template-versions/{source_id}/duplicate",
        json={"name": "ページ帳票のコピー"},
    )
    assert response.status_code == 201, response.text
    copied_id = response.json()["templateVersionId"]
    with database.session() as session:
        copied = session.get(TemplateVersion, copied_id)
        plan = copied.quality_json["printLayout"]
        assert copied.status == "DRAFT"
        assert plan["pages"][0]["range"] == "A1:C3"
        assert plan["pages"][0]["referenceImage"]["artifactId"] != reference.id
        assert plan["pages"][0]["referenceImage"]["relativePath"] != reference_path
        assert "printFiles" not in plan
        assert "candidateReadiness" not in copied.quality_json
    assert client.get(f"/api/v1/template-versions/{copied_id}/print-layout").status_code == 200
    refused = client.post(
        f"/api/v1/template-versions/{source_id}/duplicate",
        json={"name": "ページ帳票の未検証コピー", "activate": True},
    )
    assert refused.status_code == 409, refused.text
    with database.session() as session:
        assert [item.name for item in session.scalars(select(Template))] == [
            "Versioning", "ページ帳票のコピー",
        ]


def test_duplicate_name_conflict_leaves_the_source_unchanged(client, versioned_source):
    source_id, _ = versioned_source
    database = client.app.state.database
    with database.session() as session:
        before = session.scalar(select(TemplateVersion).where(TemplateVersion.id == source_id))
        source_template_id = before.template_id
    response = client.post(
        f"/api/v1/template-versions/{source_id}/duplicate",
        json={"name": "  versioning  "},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "TEMPLATE_NAME_CONFLICT"
    with database.session() as session:
        assert len(list(session.scalars(select(Template)))) == 1
        source = session.get(TemplateVersion, source_id)
        assert source.template_id == source_template_id
        assert source.status == "ACTIVE"


@pytest.mark.parametrize("mode", ["REFERENCE_TEMPLATE", "SINGLE_IMAGE_EXPERIMENTAL"])
def test_duplicate_image_modes_use_separate_source_artifacts(client, versioned_source, mode):
    source_id, _ = versioned_source
    database = client.app.state.database
    with database.session() as session:
        source = session.get(TemplateVersion, source_id)
        source.mode = mode
        session.get(ProcessingJob, source.source_job_id).mode = mode
        session.commit()
    response = client.post(
        f"/api/v1/template-versions/{source_id}/duplicate",
        json={"name": f"Image copy {mode}"},
    )
    assert response.status_code == 201, response.text
    with database.session() as session:
        original = session.get(TemplateVersion, source_id)
        copied = session.get(TemplateVersion, response.json()["templateVersionId"])
        assert copied.mode == mode
        assert copied.clean_template_artifact_id != original.clean_template_artifact_id
        assert copied.source_job_id != original.source_job_id


def test_duplicate_rejects_changed_source_bytes_without_creating_a_copy(
    client, versioned_source,
):
    source_id, _ = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    with database.session() as session:
        source = session.get(TemplateVersion, source_id)
        artifact = session.get(JobArtifact, source.clean_template_artifact_id)
        storage.write_bytes(artifact.relative_path, b"changed")
    response = client.post(
        f"/api/v1/template-versions/{source_id}/duplicate",
        json={"name": "Must not exist"},
    )
    assert response.status_code == 409
    with database.session() as session:
        assert len(list(session.scalars(select(Template)))) == 1
        assert session.get(TemplateVersion, source_id).status == "ACTIVE"


def test_duplicate_rejects_a_stale_page_reference(client, versioned_source):
    source_id, _ = versioned_source
    database = client.app.state.database
    with database.session() as session:
        source = session.get(TemplateVersion, source_id)
        workbook = session.scalar(select(JobArtifact).where(
            JobArtifact.job_id == source.source_job_id,
            JobArtifact.artifact_name == "sourceWorkbook",
        ))
        reference = session.get(JobArtifact, source.clean_template_artifact_id)
        source.quality_json = {
            **source.quality_json,
            "printLayout": {
                "sourceSha256": workbook.sha256,
                "layoutSha256": digest(source.layout_json),
                "pages": [{
                    "page": 1, "range": "A1:C3",
                    "referenceImage": {
                        "artifactId": reference.id,
                        "relativePath": reference.relative_path,
                        "sha256": "0" * 64,
                    },
                }],
            },
        }
        session.commit()
    response = client.post(
        f"/api/v1/template-versions/{source_id}/duplicate",
        json={"name": "Invalid page copy"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "PRINT_REFERENCE_NOT_VALIDATED"
    with database.session() as session:
        assert len(list(session.scalars(select(Template)))) == 1


def test_duplicate_copy_failure_rolls_back_new_records_and_files(
    client, versioned_source, monkeypatch,
):
    source_id, _ = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    original_write = storage.write_bytes
    written: list[str] = []

    def fail_second_copy(path, content):
        normalized = Path(path).as_posix()
        if normalized.startswith("jobs/") and written:
            raise OSError("injected duplicate copy failure")
        stored = original_write(path, content)
        if normalized.startswith("jobs/"):
            written.append(stored)
        return stored

    monkeypatch.setattr(storage, "write_bytes", fail_second_copy)
    with pytest.raises(OSError, match="injected duplicate copy failure"):
        TemplateDuplicateService(database, storage).duplicate(source_id, "Failed copy")
    assert written
    assert all(not storage.exists(path) for path in written)
    with database.session() as session:
        assert len(list(session.scalars(select(Template)))) == 1
        assert len(list(session.scalars(select(TemplateVersion)))) == 1
        assert len(list(session.scalars(select(ProcessingJob)))) == 1
        assert len(list(session.scalars(select(JobArtifact)))) == 3


def test_duplicate_generic_definition_regenerates_mapping_and_logical_ids(
    client, versioned_source,
):
    from app.db.models import TemplateDefinitionRevision
    from app.domain.generic_template_mapping import generic_field_locations
    from app.domain.template_definition import TemplateDefinition, TemplateField
    from app.layout_analysis.models import LayoutDefinition
    from app.services.template_definition_service import TemplateDefinitionService

    source_id, _ = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    with database.session() as session:
        source = session.get(TemplateVersion, source_id)
        definition = TemplateDefinition(
            template_version_id=source_id,
            template_name="Versioning", state="APPROVED",
            canonical_width=300, canonical_height=100, confidence=1,
            cells=TemplateDefinitionService._cells_from_layout(
                LayoutDefinition.model_validate(source.layout_json)
            ),
            standalone_fields=[TemplateField(
                id="name-field", key="name", name="氏名",
                value_cell_id="xlsx_A1", confidence=1,
            )],
        )
        original_key = next(generic_field_locations(definition)).mapping_key
        config = parse_template_config(storage.read_bytes(source.config_relative_path).decode())
        mapping = config.field_mappings[0].model_copy(update={
            "mapping_id": original_key,
            "target_path": f"customFields.{original_key}",
        })
        config = config.model_copy(update={
            "field_mappings": [mapping], "document_identity": None,
        })
        storage.write_text(source.config_relative_path, config.model_dump_json(by_alias=True))
        session.add(TemplateDefinitionRevision(
            template_version_id=source_id, revision=1, source="APPROVED",
            definition_json=definition.model_dump(mode="json", by_alias=True),
            validation_json={"valid": True, "issues": []},
        ))
        session.commit()
    original_ids = _get(client, source_id, "field-identities")
    response = client.post(
        f"/api/v1/template-versions/{source_id}/duplicate",
        json={"name": "Independent generic copy"},
    )
    assert response.status_code == 201, response.text
    copied_id = response.json()["templateVersionId"]
    with database.session() as session:
        stored = session.scalar(select(TemplateDefinitionRevision).where(
            TemplateDefinitionRevision.template_version_id == copied_id,
        ))
        copied_definition = TemplateDefinition.model_validate(stored.definition_json)
        new_key = next(generic_field_locations(copied_definition)).mapping_key
        assert new_key != original_key
        assert copied_definition.template_version_id == copied_id
        assert copied_definition.template_name == "Independent generic copy"
        assert copied_definition.standalone_fields[0].value_cell_id != "xlsx_A1"
    copied_ids = _get(client, copied_id, "field-identities")
    assert copied_ids[new_key] != original_ids[original_key]
    assert _get(client, copied_id, "field-mappings")["mappings"][0]["mappingId"] == new_key


def _make_paged(client: TestClient, version_id: str, **quality_updates: object) -> str:
    database = client.app.state.database
    with database.session() as session:
        version = session.get(TemplateVersion, version_id)
        source = session.scalar(
            select(JobArtifact).where(
                JobArtifact.job_id == version.source_job_id,
                JobArtifact.artifact_name == "sourceWorkbook",
            )
        )
        quality = copy.deepcopy(version.quality_json)
        quality.update(quality_updates)
        quality["printLayout"] = {
            "sourceSha256": source.sha256,
            "geometryModelVersion": "wsl-calc-print-grid-v1",
            "layoutSha256": "1" * 64,
            "pages": [],
            **copy.deepcopy(quality.get("printLayout", {})),
        }
        version.quality_json = quality
        session.commit()
        return PrintLayoutService.revision(version)


def test_authoring_shapes_and_headings_persist_without_changing_existing_identity(
    client, versioned_source,
):
    v1, _ = versioned_source
    original = _get(client, v1, "layout")
    v2 = _edit(client, v1)["id"]
    layout = _get(client, v2, "layout")
    identities = _get(client, v2, "field-identities")
    relations = copy.deepcopy(layout["relations"])
    base = {"x": 10, "y": 60, "width": 40, "height": 30, "confidence": 1,
            "source": "manual", "role": "parent_label"}
    layout["cells"].extend([
        {**base, "id": "headingA", "text": "振込情報"},
        {**base, "id": "virtualB", "text": "記入日", "virtual": True,
         "parentLabelCellId": "headingA"},
        {**base, "id": "polygonC", "text": "", "role": "input", "shapeType": "polygon",
         "vertices": [{"x": 10, "y": 60}, {"x": 50, "y": 60}, {"x": 10, "y": 90}],
         "parentLabelCellId": "virtualB"},
    ])
    layout["cells"][0]["parentLabelCellId"] = "virtualB"
    layout["relations"].append({"id": "polygonMeaning", "inputCellId": "polygonC",
                                "labelCellIds": [], "targetPath": "customFields.漢字2",
                                "axis": "horizontal", "order": 1})
    for _ in range(2):
        response = client.put(f"/api/v1/template-versions/{v2}/layout", json=layout)
        assert response.status_code == 200, response.text
        layout = _get(client, v2, "layout")
    cells = {cell["id"]: cell for cell in layout["cells"]}
    assert cells["virtualB"]["virtual"] is True
    assert cells["virtualB"]["parentLabelCellId"] == "headingA"
    assert cells["polygonC"]["parentLabelCellId"] == "virtualB"
    assert len(cells["polygonC"]["vertices"]) == 3
    assert layout["relations"][:len(relations)] == relations
    saved_ids = _get(client, v2, "field-identities")
    assert all(saved_ids[key] == value for key, value in identities.items())
    mappings = _get(client, v2, "field-mappings")["mappings"]
    assert not any(item["mappingId"] == "layout_virtualB" for item in mappings)
    polygon_mapping = next(item for item in mappings if item["mappingId"] == "layout_polygonC")
    assert polygon_mapping["vertices"] == cells["polygonC"]["vertices"]
    response = client.put(
        f"/api/v1/template-versions/{v2}/field-mappings", json={"mappings": mappings},
    )
    assert response.status_code == 200, response.text
    after = next(item for item in _get(client, v2, "field-mappings")["mappings"]
                 if item["mappingId"] == "layout_polygonC")
    assert after["vertices"] == polygon_mapping["vertices"]
    assert _get(client, v1, "layout") == original


def test_fork_edit_preserves_v1_and_logical_provenance(client, versioned_source):
    v1, _ = versioned_source
    original_layout = _get(client, v1, "layout")
    original_mappings = _get(client, v1, "field-mappings")
    original_ids = _get(client, v1, "field-identities")
    draft = _edit(client, v1)
    v2 = draft["id"]
    assert draft["versionNumber"] == 2 and draft["status"] == "DRAFT"
    assert _edit(client, v1)["id"] == _edit(client, v2)["id"] == v2
    new_ids = _get(client, v2, "field-identities")
    assert set(original_ids).isdisjoint(new_ids)
    assert set(original_ids.values()) == set(new_ids.values())
    layout = _get(client, v2, "layout")
    assert {c["id"] for c in original_layout["cells"]}.isdisjoint(c["id"] for c in layout["cells"])
    # Rename and move the inherited physical cell; remove the second field.
    layout["cells"][0]["x"] += 5
    layout["cells"][0]["printedLabelText"] = "名前"
    layout["cells"] = layout["cells"][:1]
    layout["relations"] = layout["relations"][:1]
    # New field at the same position with the same label must not inherit identity.
    added = {**layout["cells"][0], "id": "xlsx_B1_new", "printedLabelText": "氏名"}
    layout["cells"].append(added)
    layout["relations"].append(
        {
            **layout["relations"][0],
            "id": "new-relation",
            "inputCellId": added["id"],
            "targetPath": "customFields.new",
        }
    )
    for _ in range(2):
        response = client.put(f"/api/v1/template-versions/{v2}/layout", json=layout)
        assert response.status_code == 200, response.text
    saved_ids = _get(client, v2, "field-identities")
    inherited_key = "layout_" + layout["cells"][0]["id"]
    assert saved_ids[inherited_key] == new_ids[inherited_key]
    assert saved_ids["layout_xlsx_B1_new"] not in original_ids.values()
    assert len(saved_ids) == 2
    assert _get(client, v1, "layout") == original_layout
    assert _get(client, v1, "field-mappings") == original_mappings
    assert _get(client, v1, "field-identities") == original_ids
    assert _edit(client, v1)["id"] == v2
    with client.app.state.database.session() as session:
        source = session.get(TemplateVersion, v1)
        assert (
            len(
                list(
                    session.scalars(
                        select(TemplateVersion).where(
                            TemplateVersion.template_id == source.template_id
                        )
                    )
                )
            )
            == 2
        )


def test_immutable_endpoints_and_discard(client, versioned_source):
    v1, png = versioned_source
    original = _get(client, v1, "layout")
    for suffix, kwargs in (
        ("layout", {"json": original}),
        (
            "handwriting-mask",
            {
                "files": {"file": ("mask.png", png, "image/png")},
            },
        ),
    ):
        response = client.put(f"/api/v1/template-versions/{v1}/{suffix}", **kwargs)
        assert response.status_code == 409
        assert response.json()["code"] == "TEMPLATE_VERSION_IMMUTABLE"
    response = client.post(f"/api/v1/template-versions/{v1}/layout/verify")
    assert response.status_code == 409
    draft = _edit(client, v1)
    assert client.post(f"/api/v1/template-versions/{draft['id']}/reprocess").status_code == 409
    assert client.post(f"/api/v1/template-versions/{draft['id']}/reject").status_code == 200
    with client.app.state.database.session() as session:
        source = session.get(TemplateVersion, v1)
        assert source.status == "ACTIVE"
        assert session.get(Template, source.template_id).active_version_id == v1
    assert _get(client, v1, "layout") == original
    assert _edit(client, v1)["versionNumber"] == 3


def test_concurrent_edit_creates_one_draft(client, versioned_source):
    v1, _ = versioned_source
    service = TemplateVersionService(client.app.state.database, client.app.state.storage)
    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(pool.map(lambda _: service.edit(v1).id, range(4)))
    assert len(set(versions)) == 1


def test_paged_edit_requires_current_revision_and_reuses_one_matching_draft(
    client, versioned_source,
):
    v1, _ = versioned_source
    revision = _make_paged(client, v1)
    missing = client.post(f"/api/v1/template-versions/{v1}/edit")
    assert missing.status_code == 409
    assert missing.json()["code"] == "TEMPLATE_VERSION_REVISION_REQUIRED"
    stale = client.post(
        f"/api/v1/template-versions/{v1}/edit",
        params={"base_revision": "0" * 64},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "TEMPLATE_VERSION_CONFLICT"
    v2 = _edit(client, v1, revision=revision)["id"]
    assert _edit(client, v1, revision=revision)["id"] == v2
    with client.app.state.database.session() as session:
        source = session.get(TemplateVersion, v1)
        versions = list(
            session.scalars(
                select(TemplateVersion).where(TemplateVersion.template_id == source.template_id)
            )
        )
        assert [(item.id, item.status) for item in versions] == [(v1, "ACTIVE"), (v2, "DRAFT")]


def test_edit_rejects_existing_draft_from_another_parent(client, versioned_source):
    v1, _ = versioned_source
    revision = _make_paged(client, v1)
    v2 = _edit(client, v1, revision=revision)["id"]
    with client.app.state.database.session() as session:
        draft = session.get(TemplateVersion, v2)
        quality = copy.deepcopy(draft.quality_json)
        quality["forkParentVersionId"] = "not-the-requested-version"
        draft.quality_json = quality
        session.commit()
    response = client.post(
        f"/api/v1/template-versions/{v1}/edit", params={"base_revision": revision}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "TEMPLATE_DRAFT_LINEAGE_MISMATCH"


def test_edit_rejects_existing_draft_with_different_source_workbook(
    client, versioned_source,
):
    v1, _ = versioned_source
    revision = _make_paged(client, v1)
    v2 = _edit(client, v1, revision=revision)["id"]
    changed = b"different workbook identity"
    with client.app.state.database.session() as session:
        draft = session.get(TemplateVersion, v2)
        artifact = session.scalar(
            select(JobArtifact).where(
                JobArtifact.job_id == draft.source_job_id,
                JobArtifact.artifact_name == "sourceWorkbook",
            )
        )
        client.app.state.storage.write_bytes(artifact.relative_path, changed)
        artifact.sha256 = hashlib.sha256(changed).hexdigest()
        quality = copy.deepcopy(draft.quality_json)
        quality["printLayout"]["sourceSha256"] = artifact.sha256
        draft.quality_json = quality
        session.commit()
    response = client.post(
        f"/api/v1/template-versions/{v1}/edit", params={"base_revision": revision}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "TEMPLATE_DRAFT_SOURCE_MISMATCH"


def test_edit_rejects_multiple_drafts_without_selecting_one(client, versioned_source):
    v1, _ = versioned_source
    v2 = _edit(client, v1)["id"]
    with client.app.state.database.session() as session:
        draft = session.get(TemplateVersion, v2)
        session.add(
            TemplateVersion(
                template_id=draft.template_id,
                version_number=3,
                mode=draft.mode,
                status="DRAFT",
                canonical_width=draft.canonical_width,
                canonical_height=draft.canonical_height,
                source_job_id=draft.source_job_id,
                clean_template_artifact_id=draft.clean_template_artifact_id,
                config_relative_path=draft.config_relative_path,
                quality_json=copy.deepcopy(draft.quality_json),
                layout_json=copy.deepcopy(draft.layout_json),
            )
        )
        session.commit()
    response = client.post(f"/api/v1/template-versions/{v1}/edit")
    assert response.status_code == 409
    assert response.json()["code"] == "TEMPLATE_DRAFT_AMBIGUOUS"
    selected = client.post(f"/api/v1/template-versions/{v2}/edit")
    assert selected.status_code == 409
    assert selected.json()["code"] == "TEMPLATE_DRAFT_AMBIGUOUS"


def test_fork_rejects_missing_source_workbook(client, versioned_source):
    v1, _ = versioned_source
    with client.app.state.database.session() as session:
        source = session.get(TemplateVersion, v1)
        artifact = session.scalar(
            select(JobArtifact).where(
                JobArtifact.job_id == source.source_job_id,
                JobArtifact.artifact_name == "sourceWorkbook",
            )
        )
        session.delete(artifact)
        session.commit()
    response = client.post(f"/api/v1/template-versions/{v1}/edit")
    assert response.status_code == 409
    assert response.json()["code"] == "TEMPLATE_SOURCE_WORKBOOK_MISSING"


def test_fork_artifact_paths_use_artifact_identity_and_preserve_bytes(
    client, versioned_source,
):
    v1, _ = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    added = {
        "duplicateA": ("history/a/shared.bin", b"first"),
        "duplicateB": ("history/b/shared.bin", b"second"),
    }
    with database.session() as session:
        source = session.get(TemplateVersion, v1)
        for name, (path, content) in added.items():
            storage.write_bytes(path, content)
            session.add(
                JobArtifact(
                    job_id=source.source_job_id,
                    attempt_number=1,
                    artifact_name=name,
                    relative_path=path,
                    media_type="application/octet-stream",
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
        session.commit()
    v2 = _edit(client, v1)["id"]
    with database.session() as session:
        source = session.get(TemplateVersion, v1)
        draft = session.get(TemplateVersion, v2)
        source_artifacts = {
            item.artifact_name: item
            for item in session.scalars(
                select(JobArtifact).where(JobArtifact.job_id == source.source_job_id)
            )
        }
        copied = {
            item.artifact_name: item
            for item in session.scalars(
                select(JobArtifact).where(JobArtifact.job_id == draft.source_job_id)
            )
        }
        destinations = {copied[name].relative_path for name in added}
        assert len(destinations) == 2
        for name, (_, original_content) in added.items():
            clone = copied[name]
            assert Path(clone.relative_path).stem == clone.id
            assert storage.read_bytes(clone.relative_path) == original_content
            assert clone.sha256 == hashlib.sha256(original_content).hexdigest()
            assert storage.read_bytes(source_artifacts[name].relative_path) == original_content


def test_fork_rejects_source_artifact_sha_mismatch(client, versioned_source):
    v1, _ = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    with database.session() as session:
        source = session.get(TemplateVersion, v1)
        artifact = session.scalar(
            select(JobArtifact).where(
                JobArtifact.job_id == source.source_job_id,
                JobArtifact.artifact_name == "normalized",
            )
        )
        storage.write_bytes(artifact.relative_path, b"changed after artifact creation")
    with pytest.raises(AppError) as failure:
        TemplateVersionService(database, storage).edit(v1)
    assert failure.value.code == "TEMPLATE_ARTIFACT_SOURCE_CHANGED"
    with database.session() as session:
        assert len(list(session.scalars(select(TemplateVersion)))) == 1


def test_fork_rejects_destination_collision(client, versioned_source, monkeypatch):
    v1, _ = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    original_exists = storage.exists

    def collide(path):
        return Path(path).as_posix().startswith("jobs/") or original_exists(path)

    monkeypatch.setattr(storage, "exists", collide)
    with pytest.raises(AppError) as failure:
        TemplateVersionService(database, storage).edit(v1)
    assert failure.value.code == "TEMPLATE_ARTIFACT_DESTINATION_COLLISION"
    with database.session() as session:
        assert len(list(session.scalars(select(TemplateVersion)))) == 1
        assert len(list(session.scalars(select(ProcessingJob)))) == 1


def test_fork_detects_destination_change_and_leaves_no_success_records(
    client, versioned_source, monkeypatch,
):
    v1, _ = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    original_write = storage.write_bytes
    written: list[str] = []

    def tamper(path, content):
        stored = original_write(path, content)
        normalized = Path(stored).as_posix()
        if normalized.startswith("jobs/"):
            storage.resolve(stored).write_bytes(b"tampered after copy")
            written.append(stored)
        return stored

    monkeypatch.setattr(storage, "write_bytes", tamper)
    with pytest.raises(AppError) as failure:
        TemplateVersionService(database, storage).edit(v1)
    assert failure.value.code == "TEMPLATE_ARTIFACT_COPY_CHANGED"
    assert written
    assert all(not storage.exists(path) for path in written)
    with database.session() as session:
        assert len(list(session.scalars(select(TemplateVersion)))) == 1
        assert len(list(session.scalars(select(JobArtifact)))) == 3


def test_fork_copy_failure_removes_only_new_files_and_rolls_back_records(
    client, versioned_source, monkeypatch,
):
    v1, _ = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    original_write = storage.write_bytes
    written: list[str] = []
    copy_count = 0

    def fail_second_copy(path, content):
        nonlocal copy_count
        normalized = Path(path).as_posix()
        if normalized.startswith("jobs/"):
            copy_count += 1
            if copy_count == 2:
                raise OSError("injected artifact copy failure")
        stored = original_write(path, content)
        if normalized.startswith("jobs/"):
            written.append(stored)
        return stored

    monkeypatch.setattr(storage, "write_bytes", fail_second_copy)
    with pytest.raises(OSError, match="injected artifact copy failure"):
        TemplateVersionService(database, storage).edit(v1)
    assert written
    assert all(not storage.exists(path) for path in written)
    with database.session() as session:
        source = session.get(TemplateVersion, v1)
        assert session.get(Template, source.template_id).active_version_id == v1
        assert len(list(session.scalars(select(TemplateVersion)))) == 1
        assert len(list(session.scalars(select(ProcessingJob)))) == 1
        assert len(list(session.scalars(select(JobArtifact)))) == 3


def test_fork_does_not_inherit_current_qualification_or_prepared_graph(
    client, versioned_source,
):
    v1, _ = versioned_source
    revision = _make_paged(
        client,
        v1,
        qualified=True,
        environmentQualified=True,
        geometryQualified=True,
        referenceQualification={
            "status": "PASS",
            "qualified": True,
            "environmentQualified": True,
            "geometryQualified": True,
            "preparedArtifactGraphSha256": "a" * 64,
        },
        printLayout={
            "printFiles": {"graphSha256": "b" * 64},
            "pages": [
                {
                    "page": 1,
                    "referenceImage": {
                        "artifactId": "old-reference",
                        "sha256": "c" * 64,
                    },
                }
            ],
        },
    )
    v2 = _edit(client, v1, revision=revision)["id"]
    with client.app.state.database.session() as session:
        draft = session.get(TemplateVersion, v2)
        quality = draft.quality_json
        assert "referenceQualification" not in quality
        assert all(
            quality.get(key) is not True
            for key in ("qualified", "environmentQualified", "geometryQualified")
        )
        assert "printFiles" not in quality["printLayout"]
        job = session.get(ProcessingJob, draft.source_job_id)
        assert "printFiles" not in job.config_snapshot_json["printLayout"]
    assert ReferenceQualificationService(
        client.app.state.database, client.app.state.storage
    ).status(v2) == {"status": "UNVERIFIED", "qualified": False}


def test_draft_cannot_restore_ancestor_physical_ids(client, versioned_source):
    v1, _ = versioned_source
    v2 = _edit(client, v1)["id"]
    response = client.put(f"/api/v1/template-versions/{v2}/layout", json=_get(client, v1, "layout"))
    assert response.status_code == 409
    assert response.json()["code"] == "TEMPLATE_PHYSICAL_ID_SHARED"


def test_failed_fork_rolls_back_database_and_can_be_retried(client, versioned_source, monkeypatch):
    v1, _ = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    original_write = storage.write_text

    def fail_config(path, content):
        if path.endswith("/versions/2/template.yaml"):
            raise OSError("injected disk failure")
        return original_write(path, content)

    monkeypatch.setattr(storage, "write_text", fail_config)
    with pytest.raises(OSError, match="injected"):
        TemplateVersionService(database, storage).edit(v1)
    with database.session() as session:
        source = session.get(TemplateVersion, v1)
        assert session.get(Template, source.template_id).active_version_id == v1
        assert len(list(session.scalars(select(TemplateVersion)))) == 1
        assert len(list(session.scalars(select(ProcessingJob)))) == 1
    monkeypatch.setattr(storage, "write_text", original_write)
    assert _edit(client, v1)["versionNumber"] == 2


def test_generic_definition_fork_remaps_physical_keys_and_keeps_logical_ids(
    client, versioned_source
):
    from app.db.models import TemplateDefinitionRevision
    from app.domain.generic_template_mapping import generic_field_locations
    from app.domain.template_config import parse_template_config
    from app.domain.template_definition import TemplateDefinition, TemplateField
    from app.layout_analysis.models import LayoutDefinition
    from app.services.template_definition_service import TemplateDefinitionService

    v1, _ = versioned_source
    database, storage = client.app.state.database, client.app.state.storage
    with database.session() as session:
        source = session.get(TemplateVersion, v1)
        definition = TemplateDefinition(
            template_version_id=v1,
            template_name="Versioning",
            state="APPROVED",
            canonical_width=300,
            canonical_height=100,
            confidence=1,
            cells=TemplateDefinitionService._cells_from_layout(
                LayoutDefinition.model_validate(source.layout_json)
            ),
            standalone_fields=[
                TemplateField(
                    id="name-field", key="name", name="氏名", value_cell_id="xlsx_A1", confidence=1
                )
            ],
        )
        key = next(generic_field_locations(definition)).mapping_key
        config = parse_template_config(storage.read_bytes(source.config_relative_path).decode())
        mapping = config.field_mappings[0].model_copy(
            update={
                "mapping_id": key,
                "target_path": f"customFields.{key}",
            }
        )
        config = config.model_copy(update={"field_mappings": [mapping], "document_identity": None})
        storage.write_text(source.config_relative_path, config.model_dump_json(by_alias=True))
        session.add(
            TemplateDefinitionRevision(
                template_version_id=v1,
                revision=1,
                source="APPROVED",
                definition_json=definition.model_dump(mode="json", by_alias=True),
                validation_json={"valid": True, "issues": []},
            )
        )
        session.commit()
    before = _get(client, v1, "field-identities")
    v2 = _edit(client, v1)["id"]
    with database.session() as session:
        copied = session.scalar(
            select(TemplateDefinitionRevision).where(
                TemplateDefinitionRevision.template_version_id == v2
            )
        )
        new_definition = TemplateDefinition.model_validate(copied.definition_json)
        new_key = next(generic_field_locations(new_definition)).mapping_key
        assert new_key != key
        assert new_definition.template_version_id == v2
        assert new_definition.standalone_fields[0].value_cell_id != "xlsx_A1"
    after = _get(client, v2, "field-identities")
    assert after[new_key] == before[key]
    assert _get(client, v2, "field-mappings")["mappings"][0]["mappingId"] == new_key


def test_old_job_review_correction_confirm_and_retry_after_publish(client):
    from tests.integration.test_ocr_review_api import _active_template, _wait_for
    from tests.synthetic.image_factory import encode_png, ledger_image

    v1 = _active_template(client)
    created = client.post(
        "/api/v1/ocr-jobs",
        data={
            "templateVersionId": v1,
            "motherIndividualId": "1234567890",
            "ocrEnabled": "true",
        },
        files={"file": ("filled.png", encode_png(ledger_image(handwriting_seed=5)), "image/png")},
    )
    assert created.status_code == 202
    job_id = created.json()["jobId"]
    _wait_for(client, job_id, "REVIEW_REQUIRED")
    before = client.get(f"/api/v1/ocr-jobs/{job_id}/result").json()
    with client.app.state.database.session() as session:
        job = session.get(ProcessingJob, job_id)
        snapshot = copy.deepcopy(job.config_snapshot_json)
        template_bytes = job.template_snapshot_bytes
    draft = _edit(client, v1)
    v2 = draft["id"]
    layout = _get(client, v2, "layout")
    layout["cells"][1]["x"] += 2
    assert client.put(f"/api/v1/template-versions/{v2}/layout", json=layout).status_code == 200
    assert client.post(f"/api/v1/template-versions/{v2}/layout/verify").status_code == 200
    assert client.post(f"/api/v1/template-versions/{v2}/approve").status_code == 200
    after = client.get(f"/api/v1/ocr-jobs/{job_id}/result").json()
    assert after["result"] == before["result"]
    result = copy.deepcopy(after["result"])
    reading = result["document"]["cattleName"]
    original_region, original_box = reading["sourceRegionId"], reading["boundingBox"]
    reading.update(raw="修正花子", normalized="修正花子", status="readable")
    payload = {
        key: result[key]
        for key in (
            "document",
            "saleCalves",
            "pedigree",
            "calvings",
            "customFields",
            "reviewItems",
        )
    }
    saved = client.put(
        f"/api/v1/ocr-jobs/{job_id}/corrections",
        json={"baseRevision": after["revision"], **payload},
    )
    assert saved.status_code == 200, saved.text
    saved_reading = saved.json()["result"]["document"]["cattleName"]
    assert saved_reading["sourceRegionId"] == original_region
    assert saved_reading["boundingBox"] == original_box
    confirmed = client.post(
        f"/api/v1/ocr-jobs/{job_id}/confirm",
        json={
            "baseRevision": saved.json()["revision"],
            "documentName": "V1修正結果",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed"] is True
    assert confirmed.json()["result"]["templateVersion"] == "1"
    assert client.get(f"/api/v1/ocr-jobs/{job_id}/export?format=xlsx").status_code == 200
    with client.app.state.database.session() as session:
        persisted = session.get(ProcessingJob, job_id)
        assert persisted.config_snapshot_json == snapshot
        assert persisted.template_snapshot_bytes == template_bytes
        # Exercise the supported failed-job retry boundary using the same input.
        persisted.status = "FAILED"
        session.commit()
    retried = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert retried.status_code == 200
    _wait_for(client, job_id, "REVIEW_REQUIRED")
    with client.app.state.database.session() as session:
        persisted = session.get(ProcessingJob, job_id)
        assert persisted.config_snapshot_json == snapshot
        assert persisted.template_snapshot_bytes == template_bytes
        assert persisted.template_version_id == v1


def test_publish_failure_is_atomic(client, versioned_source, monkeypatch):
    from sqlalchemy.orm import Session

    from app.repositories.template_repository import TemplateRepository

    v1, _ = versioned_source
    v2 = _edit(client, v1)["id"]
    assert client.post(f"/api/v1/template-versions/{v2}/layout/verify").status_code == 200
    database = client.app.state.database

    def fail_commit(session):
        session.flush()
        raise RuntimeError("injected commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", fail_commit)
        with (
            pytest.raises(RuntimeError, match="injected commit failure"),
            database.session() as session,
        ):
            TemplateRepository(session).approve(v2)
    with database.session() as session:
        old, draft = session.get(TemplateVersion, v1), session.get(TemplateVersion, v2)
        assert old.status == "ACTIVE"
        assert draft.status == "DRAFT"
        assert session.get(Template, old.template_id).active_version_id == v1
        assert session.get(ProcessingJob, draft.source_job_id).status == "REVIEW_REQUIRED"
    assert client.post(f"/api/v1/template-versions/{v2}/approve").status_code == 200


def test_publish_and_job_snapshots_stay_on_their_versions(client, versioned_source):
    import asyncio
    from typing import cast

    from app.jobs.runner import JobRunner
    from app.services.job_service import JobService, UploadPayload
    from app.services.spreadsheet_template_output_service import SpreadsheetTemplateOutputService

    class Runner:
        async def enqueue(self, job_id):
            pass

    database, storage = client.app.state.database, client.app.state.storage
    service = JobService(
        settings=client.app.state.settings,
        database=database,
        storage=storage,
        runner=cast(JobRunner, Runner()),
    )
    v1, png = versioned_source

    def submit(version):
        return asyncio.run(
            service.create_ocr_job(
                template_version_id=version,
                mother_individual_id=None,
                upload=UploadPayload(content=png, declared_media_type="image/png"),
                ocr_enabled=False,
                idempotency_key=None,
            )
        )

    old_job = submit(v1)
    before = copy.deepcopy(old_job.config_snapshot_json)
    draft = _edit(client, v1)
    v2 = draft["id"]
    assert client.post(f"/api/v1/template-versions/{v2}/layout/verify").status_code == 200
    response = client.post(f"/api/v1/template-versions/{v2}/approve")
    assert response.status_code == 200, response.text
    new_job = submit(v2)
    assert new_job.config_snapshot_json["templateVersionId"] == v2
    assert set(before["logicalFieldIds"].values()) == set(
        new_job.config_snapshot_json["logicalFieldIds"].values()
    )
    assert (
        before["spreadsheetOutput"]["sourceWorkbook"]["artifactId"]
        != new_job.config_snapshot_json["spreadsheetOutput"]["sourceWorkbook"]["artifactId"]
    )
    with database.session() as session:
        persisted = session.get(ProcessingJob, old_job.id)
        assert persisted.config_snapshot_json == before
        persisted.status = "FAILED"
        session.commit()
    retried = asyncio.run(service.retry(old_job.id))
    assert retried.config_snapshot_json == before
    assert retried.template_version_id == v1
    # Render each accepted snapshot, including the archived version's workbook.
    for job in (retried, new_job):
        with database.session() as session:
            output = SpreadsheetTemplateOutputService(storage).render(
                session,
                job=job,
                document={
                    "document": {
                        "cattleName": {"raw": "山田", "normalized": "山田", "status": "readable"}
                    }
                },
                document_name="保存結果",
                confirmed_at=datetime.now(UTC),
            )
        workbook = load_workbook(BytesIO(output.content))
        assert workbook.active["A1"].value == "山田"
        assert workbook.active["C1"].value == "印刷済み"
    assert (
        client.put(
            f"/api/v1/template-versions/{v1}/layout", json=_get(client, v1, "layout")
        ).status_code
        == 409
    )
