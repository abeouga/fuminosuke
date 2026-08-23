from __future__ import annotations

from collections.abc import Callable

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import ProcessingJob, Template, TemplateVersion
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateVersionStatus
from app.services.output_layout_template_service import OutputLayoutTemplateService


def _job_with_template(database: Database) -> tuple[str, str]:
    with database.session() as session:
        template = Template(name="汎用帳票", document_type="generic")
        session.add(template)
        session.flush()
        version = TemplateVersion(
            template_id=template.id,
            version_number=1,
            mode="fixed_form",
            status=TemplateVersionStatus.ACTIVE.value,
            canonical_width=1000,
            canonical_height=1400,
            config_relative_path="templates/generic.yaml",
            quality_json={},
            layout_json={},
        )
        session.add(version)
        session.flush()
        job = ProcessingJob(
            job_type=JobType.EXTRACT_DOCUMENT.value,
            status=JobStatus.REVIEW_REQUIRED.value,
            template_version_id=version.id,
            request_hash="o" * 64,
            config_snapshot_json={},
        )
        session.add(job)
        session.commit()
        return job.id, version.id


def _layout(template_version_id: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "sheetName": "帳票レイアウト",
        "rowCount": 4,
        "columnCount": 2,
        "cells": [],
        "merges": [],
        "rowHeights": {},
        "columnWidths": {},
        "bindingCount": 3,
        "sourceTemplateVersionId": template_version_id,
    }


def test_output_layout_is_versioned_per_owner_and_template(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    try:
        job_id, version_id = _job_with_template(database)
        service = OutputLayoutTemplateService(database, lambda: "google-user-1")

        initial = service.status_for_job(job_id)
        first = service.save_for_job(job_id, _layout(version_id))
        second = service.save_for_job(job_id, _layout(version_id))

        assert initial["active"] is False
        assert first["revision"] == 1
        assert second["revision"] == 2
        assert service.latest_layout(version_id) == _layout(version_id)
        assert (
            OutputLayoutTemplateService(database, lambda: "other-user").latest_layout(version_id)
            is None
        )
    finally:
        database.dispose()


def test_output_layout_rejects_a_sheet_from_another_template(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    try:
        job_id, _version_id = _job_with_template(database)
        service = OutputLayoutTemplateService(database, lambda: "google-user-1")

        with pytest.raises(AppError) as raised:
            service.save_for_job(job_id, _layout("different-template"))

        assert raised.value.code == "OUTPUT_LAYOUT_TEMPLATE_MISMATCH"
    finally:
        database.dispose()
