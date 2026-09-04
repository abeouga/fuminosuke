from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from datetime import UTC, datetime

from openpyxl import Workbook, load_workbook

from app.core.config import Settings
from app.db.base import Base
from app.db.models import JobArtifact, ProcessingJob, Template, TemplateVersion
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode, TemplateVersionStatus
from app.services.spreadsheet_template_output_service import SpreadsheetTemplateOutputService
from app.storage.local_storage import LocalStorageService


def test_blank_value_does_not_erase_fixed_source_content(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "様式 "
    worksheet["A1"] = "固定文字"
    worksheet.merge_cells("A1:B1")
    worksheet["C1"] = "=1+1"
    source = io.BytesIO()
    workbook.save(source)
    source_bytes = source.getvalue()
    source_path = storage.write_bytes("artifacts/source.xlsx", source_bytes)

    try:
        with database.session() as session:
            source_job = ProcessingJob(
                job_type=JobType.PREPARE_TEMPLATE.value,
                status=JobStatus.CONFIRMED.value,
                request_hash="s" * 64,
                config_snapshot_json={},
            )
            session.add(source_job)
            session.flush()
            session.add(
                JobArtifact(
                    job_id=source_job.id,
                    attempt_number=1,
                    artifact_name="sourceWorkbook",
                    media_type=(
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    ),
                    relative_path=source_path,
                    sha256=hashlib.sha256(source_bytes).hexdigest(),
                )
            )
            template = Template(name="面接カード", document_type="spreadsheet")
            session.add(template)
            session.flush()
            version = TemplateVersion(
                template_id=template.id,
                version_number=1,
                mode=TemplateMode.SPREADSHEET_TEMPLATE.value,
                status=TemplateVersionStatus.ACTIVE.value,
                canonical_width=1240,
                canonical_height=1754,
                source_job_id=source_job.id,
                config_relative_path="templates/interview.json",
                quality_json={
                    "sourceSheetName": "様式 ",
                    "sourceFileName": "面接カード.xlsx",
                },
                layout_json={
                    "cells": [
                        {"id": "xlsx_B1", "x": 0, "y": 0, "width": 10, "height": 10},
                        {"id": "xlsx_D2", "x": 10, "y": 10, "width": 10, "height": 10},
                    ],
                    "cellMerges": [],
                },
            )
            session.add(version)
            session.flush()
            spreadsheet_output = SpreadsheetTemplateOutputService(storage).snapshot(
                session,
                version=version,
            )
            assert spreadsheet_output is not None
            job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.CONFIRMED.value,
                template_version_id=version.id,
                request_hash="e" * 64,
                config_snapshot_json={
                    "spreadsheetOutput": spreadsheet_output,
                    "fieldMappings": [
                        {
                            "mappingId": "layout_xlsx_B1",
                            "targetPath": "customFields.blankValue",
                            "enabled": True,
                        },
                        {
                            "mappingId": "layout_xlsx_D2",
                            "targetPath": "customFields.readableValue",
                            "enabled": True,
                        },
                    ]
                },
            )
            session.add(job)
            session.flush()

            rendered = SpreadsheetTemplateOutputService(storage).render(
                session,
                job=job,
                document={
                    "customFields": {
                        "blankValue": {
                            "raw": None,
                            "normalized": None,
                            "status": "blank",
                        },
                        "readableValue": {
                            "raw": "入力値",
                            "normalized": "入力値",
                            "status": "readable",
                        },
                    }
                },
                document_name="面接カード",
                confirmed_at=datetime(2026, 9, 1, tzinfo=UTC),
            )

        assert rendered is not None
        output = load_workbook(io.BytesIO(rendered.content), data_only=False, keep_links=True)
        assert output.sheetnames == ["面接カード"]
        assert output["面接カード"]["A1"].value == "固定文字"
        assert output["面接カード"]["C1"].value == "=1+1"
        assert output["面接カード"]["D2"].value == "入力値"
    finally:
        database.dispose()
