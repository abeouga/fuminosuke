from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable

import numpy as np
import pytest
import yaml
from openpyxl import Workbook, load_workbook  # type: ignore[import-untyped]
from openpyxl.comments import Comment  # type: ignore[import-untyped]
from PIL import Image, PngImagePlugin

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    JobArtifact,
    OutputLayoutTemplateRevision,
    ProcessingJob,
    Template,
    TemplateCandidateBinding,
    TemplateCandidateSet,
    TemplateDefinitionRevision,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode, TemplateVersionStatus
from app.domain.template_config import FieldMappingConfig, build_blank_template_config
from app.image_processing.artifacts import ArtifactWriter
from app.storage.local_storage import LocalStorageService
from app.template_packages.export_models import (
    TemplateExportRequest,
    TemplatePackageExportError,
)
from app.template_packages.export_service import TemplateExportService
from app.template_packages.package_serializer import GeneratedTemplatePackageValidator


def _png_with_metadata(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), color=(240, 240, 240))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Author", "local-user")
    output = io.BytesIO()
    image.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def _workbook(
    *,
    sheet_title: str = "帳票",
    hidden_sheet: bool = False,
    hidden_row: bool = False,
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title
    sheet["A1"] = "氏名"
    sheet["A1"].comment = Comment("internal note", "local-user")
    sheet["B1"] = "link"
    sheet["B1"].hyperlink = "https://example.invalid/private"
    workbook.properties.creator = "local-user"
    workbook.properties.lastModifiedBy = "local-user"
    if hidden_sheet:
        hidden = workbook.create_sheet("非表示")
        hidden.sheet_state = "hidden"
    if hidden_row:
        sheet.row_dimensions[2].hidden = True
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _create_exportable_template(
    settings: Settings,
    *,
    mode: TemplateMode = TemplateMode.REFERENCE_TEMPLATE,
    workbook: bytes | None = None,
    source_sheet_name: str = "帳票",
) -> tuple[Database, LocalStorageService, str, str]:
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    with database.session() as session:
        job = ProcessingJob(
            job_type=JobType.PREPARE_TEMPLATE.value,
            status=JobStatus.REVIEW_REQUIRED.value,
            request_hash="a" * 64,
            config_snapshot_json={},
            mode=mode.value,
        )
        template = Template(name="面接カード", document_type="interview-card")
        session.add_all([job, template])
        session.commit()
        job_id = job.id
        template_id = template.id

    writer = ArtifactWriter(database, storage)
    clean = writer.bytes(
        job_id=job_id,
        attempt_number=1,
        name="cleanTemplate",
        file_name="clean.png",
        media_type="image/png",
        content=_png_with_metadata(300, 100),
        width=300,
        height=100,
    )
    writer.image(
        job_id=job_id,
        attempt_number=1,
        name="preview",
        file_name="preview.png",
        image=np.full((100, 300, 3), 255, dtype=np.uint8),
    )
    writer.image(
        job_id=job_id,
        attempt_number=1,
        name="handwritingMask",
        file_name="mask.png",
        image=np.zeros((100, 300), dtype=np.uint8),
        rgb=False,
    )
    if workbook is not None:
        writer.bytes(
            job_id=job_id,
            attempt_number=1,
            name="sourceWorkbook",
            file_name="source.xlsx",
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            content=workbook,
        )

    config = build_blank_template_config(
        template_id=template_id,
        template_version=1,
        name="面接カード",
        canonical_width=300,
        canonical_height=100,
    ).model_copy(
        update={
            "field_mappings": [
                FieldMappingConfig(
                    mapping_id="person-name",
                    target_path="document.personName",
                    label="氏名",
                    x=100,
                    y=10,
                    width=150,
                    height=30,
                )
            ]
        },
        deep=True,
    )
    config_path = storage.write_text(
        f"templates/{template_id}/versions/1/template.yaml",
        yaml.safe_dump(config.model_dump(mode="json", by_alias=True), allow_unicode=True),
    )
    quality: dict[str, object] = {
        "layoutVerified": True,
        "layoutFinalized": True,
        "warnings": ["SAFE_WARNING", "C:\\private\\local.txt"],
    }
    if mode is TemplateMode.SPREADSHEET_TEMPLATE:
        quality.update(
            {
                "sourceFileName": "source.xlsx",
                "sourceSheetName": source_sheet_name,
            }
        )
    layout = {
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
    }
    with database.session() as session:
        version = TemplateVersion(
            template_id=template_id,
            version_number=1,
            mode=mode.value,
            status=TemplateVersionStatus.ACTIVE.value,
            canonical_width=300,
            canonical_height=100,
            source_job_id=job_id,
            clean_template_artifact_id=clean.id,
            mask_artifact_id=None,
            config_relative_path=config_path,
            quality_json=quality,
            layout_json=layout,
        )
        session.add(version)
        session.flush()
        fetched_template = session.get(Template, template_id)
        assert fetched_template is not None
        fetched_template.active_version_id = version.id
        candidate = TemplateCandidateSet(
            template_id=template_id,
            name="氏名候補",
            normalized_name="氏名候補",
            values_json=["山田太郎", "佐藤花子"],
            allowed_suffixes_json=[],
        )
        session.add(candidate)
        session.flush()
        session.add(
            TemplateCandidateBinding(
                template_id=template_id,
                candidate_set_id=candidate.id,
                target_pattern="document.personName",
                field_label="氏名",
            )
        )
        session.add(
            TemplateDefinitionRevision(
                template_version_id=version.id,
                revision=1,
                source="AI_GENERATED",
                definition_json={"private": "not-exported"},
                validation_json={"valid": False, "issues": []},
            )
        )
        session.add(
            OutputLayoutTemplateRevision(
                owner_key="google-user-private",
                template_version_id=version.id,
                revision=1,
                source_sheet_name="帳票レイアウト",
                source_job_id=job_id,
                layout_json={
                    "schemaVersion": 1,
                    "sheetName": "帳票レイアウト",
                    "rowCount": 1,
                    "columnCount": 1,
                    "cells": [
                        {
                            "row": 0,
                            "column": 0,
                            "value": {"stringValue": "氏名"},
                            "note": "llm-ocr:field:document.personName",
                        }
                    ],
                    "merges": [],
                    "rowHeights": {},
                    "columnWidths": {},
                    "bindingCount": 1,
                    "sourceTemplateVersionId": version.id,
                },
            )
        )
        session.commit()
        return database, storage, template_id, version.id


def _request(template_id: str, version_id: str, **overrides: object) -> TemplateExportRequest:
    values: dict[str, object] = {
        "templateId": template_id,
        "templateVersionId": version_id,
        "publisherId": "jp.example",
        "packageId": "interview-card",
        "version": "1.0.0",
    }
    values.update(overrides)
    return TemplateExportRequest.model_validate(values)


def test_generic_export_is_self_contained_and_strips_local_data(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database, storage, template_id, version_id = _create_exportable_template(settings)
    try:
        service = TemplateExportService(database, storage)
        default_inspection = service.inspect(_request(template_id, version_id))
        assert default_inspection.output_layout_included is False
        request = _request(
            template_id,
            version_id,
            outputLayoutOwnerKey="google-user-private",
        )
        inspection = service.inspect(request)
        exported = service.export(
            request,
            confirmed_inspection_digest=inspection.confirmation_digest,
            write_to_storage=True,
        )

        assert inspection.candidate_sets[0].values == ["山田太郎", "佐藤花子"]
        assert inspection.output_layout_included is True
        assert exported.storage_relative_path is not None
        assert storage.exists(exported.storage_relative_path)
        manifest = GeneratedTemplatePackageValidator().validate(exported.content)
        assert manifest.content_digest == inspection.content_digest

        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            names = set(archive.namelist())
            template_payload = json.loads(archive.read("template.json"))
            layout_payload = json.loads(archive.read("optional/output-layout.json"))
            package_text = "\n".join(
                archive.read(name).decode("utf-8", errors="ignore") for name in names
            )
            with Image.open(io.BytesIO(archive.read("assets/clean-template.png"))) as image:
                assert image.info == {}
        assert "optional/spreadsheet-source.xlsx" not in names
        assert (
            "approvedDefinition" not in template_payload
            or template_payload["approvedDefinition"] is None
        )
        assert layout_payload["cells"][0]["binding"] == "document.personName"
        assert "sourceTemplateVersionId" not in package_text
        assert "google-user-private" not in package_text
        assert template_id not in package_text
        assert version_id not in package_text
        assert "C:\\private" not in package_text
    finally:
        database.dispose()


def test_regular_template_rejects_source_workbook(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database, storage, template_id, version_id = _create_exportable_template(
        settings,
        workbook=_workbook(),
    )
    try:
        with pytest.raises(TemplatePackageExportError) as caught:
            TemplateExportService(database, storage).inspect(_request(template_id, version_id))
        assert caught.value.code == "SPREADSHEET_SOURCE_MODE_MISMATCH"
    finally:
        database.dispose()


def test_export_rejects_detectable_api_secret_in_candidate_values(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database, storage, template_id, version_id = _create_exportable_template(settings)
    try:
        with database.session() as session:
            candidate = session.query(TemplateCandidateSet).filter_by(
                template_id=template_id
            ).one()
            candidate.values_json = ["sk-test_secret_1234567890"]
            session.commit()
        with pytest.raises(TemplatePackageExportError) as caught:
            TemplateExportService(database, storage).inspect(_request(template_id, version_id))
        assert caught.value.code == "TEMPLATE_PACKAGE_SECRET_DETECTED"
    finally:
        database.dispose()


def test_export_requires_current_inspection_confirmation(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database, storage, template_id, version_id = _create_exportable_template(settings)
    try:
        with pytest.raises(TemplatePackageExportError) as caught:
            TemplateExportService(database, storage).export(
                _request(template_id, version_id),
                confirmed_inspection_digest=f"sha256:{'0' * 64}",
            )
        assert caught.value.code == "TEMPLATE_EXPORT_CONFIRMATION_MISMATCH"
    finally:
        database.dispose()


def test_export_stops_on_artifact_hash_mismatch(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database, storage, template_id, version_id = _create_exportable_template(settings)
    try:
        with database.session() as session:
            version = session.get(TemplateVersion, version_id)
            assert version is not None and version.clean_template_artifact_id is not None
            artifact = session.get(JobArtifact, version.clean_template_artifact_id)
            assert artifact is not None
            storage.write_bytes(artifact.relative_path, b"changed")
        with pytest.raises(TemplatePackageExportError) as caught:
            TemplateExportService(database, storage).inspect(_request(template_id, version_id))
        assert caught.value.code == "TEMPLATE_ASSET_HASH_MISMATCH"
    finally:
        database.dispose()


def test_spreadsheet_export_sanitizes_xlsx_and_is_digest_stable(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database, storage, template_id, version_id = _create_exportable_template(
        settings,
        mode=TemplateMode.SPREADSHEET_TEMPLATE,
        workbook=_workbook(),
    )
    try:
        service = TemplateExportService(database, storage)
        request = _request(template_id, version_id)
        first = service.inspect(request)
        second = service.inspect(request)
        assert first.content_digest == second.content_digest
        assert first.spreadsheet_source_included is True
        exported = service.export(
            request, confirmed_inspection_digest=first.confirmation_digest
        )
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            workbook_bytes = archive.read("optional/spreadsheet-source.xlsx")
        assert b"local-user" not in workbook_bytes
        sanitized = load_workbook(io.BytesIO(workbook_bytes), keep_links=False)
        assert sanitized.properties.creator is None
        assert sanitized.properties.lastModifiedBy is None
        assert sanitized["帳票"]["A1"].comment is None
        assert sanitized["帳票"]["B1"].hyperlink is None
    finally:
        database.dispose()


def test_spreadsheet_export_preserves_exact_sheet_name(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database, storage, template_id, version_id = _create_exportable_template(
        settings,
        mode=TemplateMode.SPREADSHEET_TEMPLATE,
        workbook=_workbook(sheet_title="帳票 "),
        source_sheet_name="帳票 ",
    )
    try:
        service = TemplateExportService(database, storage)
        request = _request(template_id, version_id)
        inspection = service.inspect(request)
        exported = service.export(
            request,
            confirmed_inspection_digest=inspection.confirmation_digest,
        )
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            workbook_bytes = archive.read("optional/spreadsheet-source.xlsx")
            template_payload = json.loads(archive.read("template.json"))
        sanitized = load_workbook(io.BytesIO(workbook_bytes), keep_links=False)
        assert sanitized.sheetnames[0] == "帳票 "
        assert template_payload["spreadsheet"]["sheetName"] == "帳票 "
    finally:
        database.dispose()


@pytest.mark.parametrize(
    ("workbook", "expected_code"),
    [
        (_workbook(hidden_sheet=True), "SPREADSHEET_SOURCE_HIDDEN_SHEET"),
        (_workbook(hidden_row=True), "SPREADSHEET_SOURCE_HIDDEN_CONTENT"),
    ],
)
def test_spreadsheet_export_rejects_hidden_information(
    settings_factory: Callable[..., Settings],
    workbook: bytes,
    expected_code: str,
) -> None:
    settings = settings_factory()
    database, storage, template_id, version_id = _create_exportable_template(
        settings,
        mode=TemplateMode.SPREADSHEET_TEMPLATE,
        workbook=workbook,
    )
    try:
        with pytest.raises(TemplatePackageExportError) as caught:
            TemplateExportService(database, storage).inspect(_request(template_id, version_id))
        assert caught.value.code == expected_code
    finally:
        database.dispose()
