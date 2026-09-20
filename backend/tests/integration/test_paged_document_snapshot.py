import copy
import hashlib

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.layout_analysis.models import LayoutDefinition
from app.services.paged_document_snapshot import load_document_snapshot
from app.services.print_layout_service import digest
from app.services.review_display_structure import build_review_display_structure
from tests.integration.test_job_pages import accept, pages_case  # noqa: F401
from tests.integration.test_print_layout_api import print_template  # noqa: F401


def test_accepted_document_and_workbook_survive_live_changes_and_validate_owned_copy(
    client,
    pages_case,  # noqa: F811 - imported pytest fixture
    monkeypatch,
):
    service, manifest, _ = pages_case
    with service.database.session() as session:
        version = session.get(TemplateVersion, manifest["templateVersionId"])
        layout_before = copy.deepcopy(version.layout_json)
    job = accept(pages_case, monkeypatch)
    frozen = copy.deepcopy(job.config_snapshot_json)
    snapshot = load_document_snapshot(job, service.storage)
    assert frozen["jobPages"]["schemaVersion"] == 2
    assert snapshot["layout"] == layout_before
    assert snapshot["spreadsheetOutput"]["cells"] == layout_before["cells"]
    assert snapshot["reviewStructure"] == build_review_display_structure(
        LayoutDefinition.model_validate(layout_before), snapshot["config"]["fieldMappings"]
    )
    assert snapshot["logicalFieldIds"].keys() == {"layout_xlsx_C2", "layout_xlsx_C32"}
    assert {b["mappingId"]: b["anchorCell"] for b in snapshot["outputBindings"]} == {
        "layout_xlsx_C2": "C2",
        "layout_xlsx_C32": "C32",
    }
    owned = snapshot["sourceWorkbook"]
    original_bytes = service.storage.read_bytes(owned["relativePath"])
    with service.database.session() as session:
        saved = session.get(ProcessingJob, job.id)
        assert saved.config_snapshot_json == frozen
        artifact = session.get(JobArtifact, owned["artifactId"])
        assert artifact.job_id == job.id and artifact.artifact_name == "acceptedSourceWorkbook"
        assert artifact.sha256 == hashlib.sha256(original_bytes).hexdigest()
        version = session.get(TemplateVersion, job.template_version_id)
        original = session.get(JobArtifact, owned["sourceIdentity"]["artifactId"])
        service.storage.write_bytes(original.relative_path, b"changed original (test only)")
        service.storage.write_text(version.config_relative_path, "changed live config")
        version.layout_json = {}
        session.commit()
        assert load_document_snapshot(saved, service.storage) == snapshot
        assert service.validate(session, saved) == frozen["jobPages"]["pages"]
    # Owned-copy corruption fails before extraction/provider; live source is not a fallback.
    service.storage.write_bytes(owned["relativePath"], b"changed accepted copy")
    with pytest.raises(AppError) as error:
        load_document_snapshot(job, service.storage)
    assert error.value.code == "PAGE_OUTPUT_SOURCE_CHANGED"
    service.storage.write_bytes(owned["relativePath"], original_bytes)
    for kind in ("destination", "layout", "config", "sheet", "path"):
        broken = copy.deepcopy(frozen)
        doc = broken["jobPages"]["documentSnapshot"]
        if kind == "destination":
            doc["outputBindings"][0]["anchorCell"] = "A1"
        elif kind == "layout":
            doc["layout"]["cells"] = []
        elif kind == "config":
            doc["config"]["fieldMappings"][0]["label"] = "different frozen definition"
        elif kind == "sheet":
            doc["spreadsheetOutput"]["sourceSheetName"] = "another worksheet"
        else:
            doc["sourceWorkbook"]["relativePath"] = "../outside.xlsx"
        broken["jobPagesSha256"] = digest(broken["jobPages"])
        job.config_snapshot_json = broken
        with pytest.raises(AppError):
            load_document_snapshot(job, service.storage)
    # Old paged acceptance stays executable and is never backfilled from today's template.
    legacy = copy.deepcopy(frozen)
    legacy["jobPages"]["schemaVersion"] = 1
    legacy["jobPages"].pop("documentSnapshot")
    legacy["jobPagesSha256"] = digest(legacy["jobPages"])
    job.config_snapshot_json = legacy
    with service.database.session() as session:
        assert service.validate(session, job) == legacy["jobPages"]["pages"]
    with pytest.raises(AppError) as error:
        load_document_snapshot(job, service.storage)
    assert error.value.code == "PAGE_OUTPUT_SNAPSHOT_REQUIRED"
    with service.database.session() as session:
        assert session.get(ProcessingJob, job.id).config_snapshot_json == frozen
        assert (
            len(
                session.scalars(
                    select(JobArtifact).where(
                        JobArtifact.job_id == job.id,
                        JobArtifact.artifact_name == "acceptedSourceWorkbook",
                    )
                ).all()
            )
            == 1
        )
