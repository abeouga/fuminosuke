from __future__ import annotations

import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from PIL import Image
from sqlalchemy import select

from app.db.models import JobArtifact, ProcessingJob, Template, TemplateVersion
from app.domain.template_config import (
    DocumentIdentityConfig,
    FieldMappingConfig,
    build_blank_template_config,
)
from app.services.template_version_service import TemplateVersionService


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


def _edit(client: TestClient, version: str) -> dict:
    response = client.post(f"/api/v1/template-versions/{version}/edit")
    assert response.status_code == 200, response.text
    return response.json()


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
