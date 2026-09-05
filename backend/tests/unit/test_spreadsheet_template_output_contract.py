from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Border, PatternFill, Side
from openpyxl.workbook.defined_name import DefinedName

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import (
    ConfirmedResult,
    JobArtifact,
    LedgerDocument,
    ProcessingJob,
    Template,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode, TemplateVersionStatus
from app.jobs.runner import JobRunner
from app.layout_analysis.models import LayoutDefinition
from app.services.job_service import JobService, UploadPayload
from app.services.layout_service import LayoutService
from app.services.ocr_result_service import OcrResultService
from app.services.spreadsheet_template_output_service import (
    FilledSpreadsheetTemplate,
    SpreadsheetTemplateOutputService,
)
from app.storage.local_storage import LocalStorageService
from tests.synthetic.image_factory import encode_png, ledger_image

_CONFIRMED_AT = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
_OUTPUT_NAME = "面接カード_山田太郎"
_UNRELATED_SHEETS = (
    "売却子牛一覧",
    "繁殖履歴",
    "帳票一覧",
    "帳票レイアウト",
    "記載例",
)


@dataclass(frozen=True, slots=True)
class _OutputCase:
    database: Database
    storage: LocalStorageService
    job_id: str
    version_id: str
    source_path: str
    source_bytes: bytes
    document: dict[str, Any]


class _RecordingRunner:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


class _DriveUploadCapture:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []

    def document_target(
        self,
        _document_id: str,
        _expected_subject: str | None = None,
    ) -> None:
        return None

    def create_spreadsheet_from_xlsx(self, **kwargs: Any) -> tuple[str, str, str]:
        self.uploads.append(kwargs)
        return (
            "phase4-sheet-id",
            "https://docs.google.com/spreadsheets/d/phase4-sheet-id/edit",
            _OUTPUT_NAME,
        )


def _layout_cell(
    cell_id: str,
    *,
    x: int,
    y: int,
    role: str = "input",
    text: str = "",
) -> dict[str, Any]:
    return {
        "id": cell_id,
        "role": role,
        "x": x,
        "y": y,
        "width": 10,
        "height": 10,
        "confidence": 1.0,
        "text": text,
        "printedLabelText": text,
        "textConfidence": 1.0 if text else None,
        "textDirection": "horizontal",
        "textAlignment": "left",
        "source": "manual",
        "markSets": [],
        "compositeSubfields": [],
    }


def _source_workbook_bytes(
    *,
    existing_merges: tuple[str, ...] = (),
    protected_formula_in_merge: bool = False,
    cross_sheet_formula: bool = False,
) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "様式"
    for title in _UNRELATED_SHEETS:
        workbook.create_sheet(title)

    worksheet["B1"] = "-"
    if protected_formula_in_merge:
        worksheet["C1"] = "=1+1"
    worksheet["H1"] = "変更してはいけない固定文字"
    worksheet["I1"] = "=SUM(J1:J2)"
    if cross_sheet_formula:
        worksheet["H2"] = "='記載例'!A1"
    worksheet["J1"] = 1234.5
    worksheet["J1"].number_format = "#,##0.00"
    worksheet.column_dimensions["H"].width = 24.5
    worksheet.row_dimensions[1].height = 33.0
    thin = Side(style="thin", color="000000")
    worksheet["H1"].border = Border(left=thin, right=thin, top=thin, bottom=thin)
    worksheet["H1"].fill = PatternFill(fill_type="solid", fgColor="FFF2CC")
    worksheet.page_setup.orientation = "landscape"
    worksheet.page_margins.left = 0.25
    worksheet.print_area = "A1:J10"
    workbook.defined_names.add(
        DefinedName("RemovedSheetOnly", attr_text="'記載例'!$A$1")
    )
    workbook.defined_names.add(
        DefinedName("ResultSheetOnly", attr_text="'様式'!$H$1")
    )
    for excel_range in existing_merges:
        worksheet.merge_cells(excel_range)

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _layout() -> dict[str, Any]:
    horizontal_parts = [
        {
            "cell": _layout_cell("xlsx_A1", x=0, y=0),
            "partType": "input",
            "literal": "",
        },
        {
            "cell": _layout_cell("xlsx_B1", x=10, y=0, role="label", text="-"),
            "partType": "literal",
            "literal": "-",
        },
        {
            "cell": _layout_cell("xlsx_C1", x=20, y=0),
            "partType": "input",
            "literal": "",
        },
        {
            "cell": _layout_cell("xlsx_D1", x=30, y=0, role="ignored"),
            "partType": "ignored",
            "literal": "",
        },
    ]
    vertical_parts = [
        {
            "cell": _layout_cell("xlsx_A3", x=0, y=20),
            "partType": "input",
            "literal": "",
        },
        {
            "cell": _layout_cell("xlsx_A4", x=0, y=30),
            "partType": "input",
            "literal": "",
        },
    ]
    return {
        "layoutSchemaVersion": 3,
        "canonicalWidth": 100,
        "canonicalHeight": 100,
        "cells": [
            {
                **_layout_cell("manual_merge_postal", x=0, y=0),
                "width": 40,
            },
            {
                **_layout_cell("manual_merge_vertical", x=0, y=20),
                "height": 20,
            },
            _layout_cell("xlsx_D5", x=30, y=40),
        ],
        "cellMerges": [
            {
                "id": "cell_merge_postal",
                "parentCellId": "manual_merge_postal",
                "direction": "horizontal",
                "parts": horizontal_parts,
            },
            {
                "id": "cell_merge_vertical",
                "parentCellId": "manual_merge_vertical",
                "direction": "vertical",
                "parts": vertical_parts,
            },
        ],
        "relations": [],
        "labelPool": [],
        "recordSections": [],
        "recordGroups": [],
        "recordStructureVerified": False,
        "allowCrossAxisLabels": False,
        "warnings": [],
    }


def _short_horizontal_layout() -> dict[str, Any]:
    layout = _layout()
    postal_merge = layout["cellMerges"][0]
    postal_merge["parts"] = postal_merge["parts"][:2]
    postal_parent = next(
        cell for cell in layout["cells"] if cell["id"] == "manual_merge_postal"
    )
    postal_parent["width"] = 20
    return layout


def _rectangular_layout() -> dict[str, Any]:
    layout = _layout()
    rectangular_parts = [
        {
            "cell": _layout_cell(
                f"xlsx_{column}{row}",
                x=(column_index * 10),
                y=(row - 6) * 10 + 50,
            ),
            "partType": "input",
            "literal": "",
        }
        for row in (6, 7)
        for column_index, column in enumerate(("B", "C"), start=1)
    ]
    layout["cellMerges"][0] = {
        "id": "cell_merge_postal",
        "parentCellId": "manual_merge_postal",
        "direction": "horizontal",
        "parts": rectangular_parts,
    }
    return layout


def _mapping(
    mapping_id: str,
    target_path: str,
    source_region_id: str,
    bounding_box: list[int],
) -> dict[str, Any]:
    return {
        "mappingId": mapping_id,
        "openAiMappingId": f"openai_{mapping_id}",
        "targetPath": target_path,
        "sourceRegionId": source_region_id,
        "boundingBox": bounding_box,
        "crop": {"bbox": bounding_box},
        "fieldPreview": {"artifactName": f"fieldCrop.{mapping_id}"},
        "enabled": True,
    }


def _field(value: str) -> dict[str, Any]:
    return {
        "raw": value,
        "normalized": value,
        "status": "readable",
        "alternatives": [],
        "reviewReason": None,
    }


def _template_config_json(
    *,
    template_id: str,
    canonical_width: int,
    canonical_height: int,
) -> str:
    return json.dumps(
        {
            "templateId": template_id,
            "templateVersion": 1,
            "name": "面接カード",
            "canonicalWidth": canonical_width,
            "canonicalHeight": canonical_height,
            "registration": {
                "preferredMethod": "ORB_HOMOGRAPHY",
                "minFeatureMatches": 4,
                "minInliers": 4,
                "minInlierRatio": 0.1,
                "maxReprojectionErrorPx": 10,
                "minEccScore": 0.1,
            },
            "regions": [],
            "fieldMappings": [
                {
                    "mappingId": "layout_manual_merge_postal",
                    "targetPath": "document.cattleName",
                    "label": "郵便番号",
                    "x": 0,
                    "y": 0,
                    "width": 40,
                    "height": 10,
                    "padding": 0,
                    "enabled": True,
                }
            ],
            "marks": [],
            "export": {"csvColumns": []},
        },
        ensure_ascii=False,
    )


@contextmanager
def _output_case(
    settings_factory: Callable[..., Settings],
    *,
    existing_merges: tuple[str, ...] = (),
    layout: dict[str, Any] | None = None,
    protected_formula_in_merge: bool = False,
    cross_sheet_formula: bool = False,
) -> Iterator[_OutputCase]:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    source_bytes = _source_workbook_bytes(
        existing_merges=existing_merges,
        protected_formula_in_merge=protected_formula_in_merge,
        cross_sheet_formula=cross_sheet_formula,
    )
    source_path = storage.write_bytes("phase1/source.xlsx", source_bytes)
    document = {
        "customFields": {
            "postalCode": _field("123-4567"),
            "verticalValue": _field("縦結合値"),
            "normalValue": _field("通常値"),
        }
    }
    mappings = [
        _mapping(
            "layout_manual_merge_postal",
            "customFields.postalCode",
            "region_postal",
            [0, 0, 40, 10],
        ),
        _mapping(
            "layout_manual_merge_vertical",
            "customFields.verticalValue",
            "region_vertical",
            [0, 20, 10, 40],
        ),
        _mapping(
            "layout_xlsx_D5",
            "customFields.normalValue",
            "region_normal",
            [30, 40, 40, 50],
        ),
    ]
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
                canonical_width=100,
                canonical_height=100,
                source_job_id=source_job.id,
                config_relative_path="templates/interview.json",
                quality_json={
                    "sourceSheetName": "様式",
                    "sourceFileName": "元の面接カード.xlsx",
                },
                layout_json=copy.deepcopy(layout) if layout is not None else _layout(),
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
                    "fieldMappings": mappings,
                    "spreadsheetOutput": spreadsheet_output,
                },
            )
            session.add(job)
            session.commit()
            case = _OutputCase(
                database=database,
                storage=storage,
                job_id=job.id,
                version_id=version.id,
                source_path=source_path,
                source_bytes=source_bytes,
                document=document,
            )
        yield case
    finally:
        database.dispose()


def _render(case: _OutputCase) -> FilledSpreadsheetTemplate:
    with case.database.session() as session:
        job = session.get(ProcessingJob, case.job_id)
        assert job is not None
        rendered = SpreadsheetTemplateOutputService(case.storage).render(
            session,
            job=job,
            document=case.document,
            document_name=_OUTPUT_NAME,
            confirmed_at=_CONFIRMED_AT,
        )
    assert rendered is not None
    return rendered


def _loaded_output(case: _OutputCase):
    rendered = _render(case)
    return load_workbook(io.BytesIO(rendered.content), data_only=False, keep_links=True)


def test_confirmed_name_is_the_xlsx_basename_and_only_result_sheet_name(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory) as case:
        with case.database.session() as session:
            session.add(
                ConfirmedResult(
                    job_id=case.job_id,
                    ocr_result_revision=1,
                    individual_id="phase1-output-contract",
                    confirmed_json=case.document,
                    confirmed_at=_CONFIRMED_AT,
                    export_relative_path=f"exports/{case.job_id}/confirmed_result.json",
                    spreadsheet_name=_OUTPUT_NAME,
                )
            )
            session.commit()

        path, media_type, file_name = OcrResultService(
            case.database,
            case.storage,
        ).export_path(case.job_id, "xlsx")

        assert media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert file_name == f"{_OUTPUT_NAME}.xlsx"
        assert Path(file_name).stem == _OUTPUT_NAME
        output = load_workbook(path, data_only=False, keep_links=True)
        assert output.sheetnames == [_OUTPUT_NAME]


def test_download_and_drive_retry_use_the_same_final_xlsx_bytes(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _output_case(settings_factory) as case:
        rendered = _render(case)
        case.storage.write_bytes(
            f"exports/{case.job_id}/confirmed_result.xlsx",
            rendered.content,
        )
        document_id = "phase4-document"
        with case.database.session() as session:
            version = session.get(TemplateVersion, case.version_id)
            assert version is not None
            session.add(
                ConfirmedResult(
                    job_id=case.job_id,
                    ocr_result_revision=1,
                    individual_id="phase4-output-contract",
                    confirmed_json=case.document,
                    confirmed_at=_CONFIRMED_AT,
                    export_relative_path=f"exports/{case.job_id}/confirmed_result.json",
                    spreadsheet_name=_OUTPUT_NAME,
                )
            )
            session.add(
                LedgerDocument(
                    id=document_id,
                    template_id=version.template_id,
                    individual_id="phase4-output-contract",
                    current_job_id=case.job_id,
                    current_document_json=case.document,
                    document_name=_OUTPUT_NAME,
                    library_name=_OUTPUT_NAME,
                )
            )
            session.commit()

        drive = _DriveUploadCapture()
        service = OcrResultService(
            case.database,
            case.storage,
            google_drive=drive,  # type: ignore[arg-type]
        )
        monkeypatch.setattr(
            service._documents,
            "document_id_for_job",
            lambda _session, _job_id: document_id,
        )

        first = service._publish_spreadsheet_template(case.job_id)
        second = service._publish_spreadsheet_template(case.job_id)
        path, _media_type, file_name = service.export_path(case.job_id, "xlsx")
        downloaded = Path(path).read_bytes()

    assert first["status"] == second["status"] == "synced"
    assert len(drive.uploads) == 2
    assert drive.uploads[0]["name"] == f"{_OUTPUT_NAME}.xlsx"
    assert drive.uploads[0]["content"] == drive.uploads[1]["content"] == downloaded
    assert file_name == f"{_OUTPUT_NAME}.xlsx"
    workbook = load_workbook(io.BytesIO(downloaded), data_only=False, keep_links=True)
    assert workbook.sheetnames == [_OUTPUT_NAME]
    assert "A1:D1" in {str(item) for item in workbook[_OUTPUT_NAME].merged_cells.ranges}


@pytest.mark.parametrize(
    "invalid_name",
    [
        *(f"面接{character}カード" for character in ':\\/?*[]'),
        "あ" * 32,
    ],
)
def test_excel_incompatible_confirmed_name_is_rejected_before_xlsx_generation(
    invalid_name: str,
) -> None:
    with pytest.raises(AppError) as caught:
        OcrResultService._normalize_spreadsheet_name(invalid_name)

    assert caught.value.status == 422
    assert "シート" in caught.value.detail


def test_horizontal_layout_merge_becomes_a_real_excel_merged_range(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory) as case:
        output = _loaded_output(case)

    merged_ranges = {str(cell_range) for cell_range in output[_OUTPUT_NAME].merged_cells.ranges}
    assert "A1:D1" in merged_ranges


def test_horizontal_layout_merge_writes_one_unsplit_value_to_anchor(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory) as case:
        output = _loaded_output(case)

    worksheet = output[_OUTPUT_NAME]
    assert worksheet["A1"].value == "123-4567"
    assert [worksheet[coordinate].value for coordinate in ("B1", "C1", "D1")] == [
        None,
        None,
        None,
    ]


def test_vertical_layout_merge_becomes_a_real_excel_merged_range(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory) as case:
        output = _loaded_output(case)

    merged_ranges = {str(cell_range) for cell_range in output[_OUTPUT_NAME].merged_cells.ranges}
    assert "A3:A4" in merged_ranges


def test_spreadsheet_output_snapshot_captures_source_and_merge_structure(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory) as case, case.database.session() as session:
        job = session.get(ProcessingJob, case.job_id)
        assert job is not None
        snapshot = job.config_snapshot_json["spreadsheetOutput"]

    assert snapshot["schemaVersion"] == 1
    assert snapshot["templateVersionId"] == case.version_id
    assert snapshot["sourceSheetName"] == "様式"
    assert snapshot["sourceWorkbook"]["sha256"] == hashlib.sha256(
        case.source_bytes
    ).hexdigest()
    assert [(merge["excelRange"], merge["anchorCell"]) for merge in snapshot["cellMerges"]] == [
        ("A1:D1", "A1"),
        ("A3:A4", "A3"),
    ]
    assert "fieldMappings" not in snapshot


def test_create_ocr_job_captures_spreadsheet_output_snapshot_at_acceptance(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    source_bytes = _source_workbook_bytes()
    source_path = storage.write_bytes("phase23/source.xlsx", source_bytes)
    clean_image = ledger_image()
    clean_bytes = encode_png(clean_image)
    clean_path = storage.write_bytes("phase23/clean.png", clean_bytes)
    canonical_height, canonical_width = clean_image.shape[:2]
    runner = _RecordingRunner()

    try:
        with database.session() as session:
            source_job = ProcessingJob(
                job_type=JobType.PREPARE_TEMPLATE.value,
                status=JobStatus.CONFIRMED.value,
                request_hash="j" * 64,
                config_snapshot_json={},
            )
            session.add(source_job)
            session.flush()
            source_artifact = JobArtifact(
                job_id=source_job.id,
                attempt_number=1,
                artifact_name="sourceWorkbook",
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                relative_path=source_path,
                sha256=hashlib.sha256(source_bytes).hexdigest(),
            )
            clean_artifact = JobArtifact(
                job_id=source_job.id,
                attempt_number=1,
                artifact_name="cleanTemplate",
                media_type="image/png",
                relative_path=clean_path,
                sha256=hashlib.sha256(clean_bytes).hexdigest(),
                width=canonical_width,
                height=canonical_height,
            )
            session.add_all([source_artifact, clean_artifact])
            template = Template(name="Phase 2 snapshot", document_type="spreadsheet")
            session.add(template)
            session.flush()
            config_path = "phase23/template.json"
            storage.write_text(
                config_path,
                _template_config_json(
                    template_id=template.id,
                    canonical_width=canonical_width,
                    canonical_height=canonical_height,
                ),
            )
            session.flush()
            version = TemplateVersion(
                template_id=template.id,
                version_number=1,
                mode=TemplateMode.SPREADSHEET_TEMPLATE.value,
                status=TemplateVersionStatus.ACTIVE.value,
                canonical_width=canonical_width,
                canonical_height=canonical_height,
                source_job_id=source_job.id,
                clean_template_artifact_id=clean_artifact.id,
                config_relative_path=config_path,
                quality_json={
                    "layoutVerified": True,
                    "sourceSheetName": "様式",
                    "sourceFileName": "元の面接カード.xlsx",
                },
                layout_json=_layout(),
            )
            session.add(version)
            session.commit()
            version_id = version.id
            source_job_id = source_job.id
            source_artifact_id = source_artifact.id

        service = JobService(
            settings=settings,
            database=database,
            storage=storage,
            runner=cast(JobRunner, runner),
        )
        job = asyncio.run(
            service.create_ocr_job(
                template_version_id=version_id,
                mother_individual_id=None,
                upload=UploadPayload(
                    content=encode_png(ledger_image(handwriting_seed=23)),
                    declared_media_type="image/png",
                ),
                ocr_enabled=False,
                idempotency_key=None,
            )
        )

        first_snapshot = copy.deepcopy(job.config_snapshot_json["spreadsheetOutput"])
        assert first_snapshot["templateVersionId"] == version_id
        assert first_snapshot["sourceWorkbook"] == {
            "artifactId": source_artifact_id,
            "sourceJobId": source_job_id,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
        }
        assert first_snapshot["cellMerges"][0]["excelRange"] == "A1:D1"
        assert first_snapshot["cellMerges"][0]["anchorCell"] == "A1"

        with database.session() as session:
            version = session.get(TemplateVersion, version_id)
            assert version is not None
            version.layout_json = _short_horizontal_layout()
            session.commit()

        new_job = asyncio.run(
            service.create_ocr_job(
                template_version_id=version_id,
                mother_individual_id=None,
                upload=UploadPayload(
                    content=encode_png(ledger_image(handwriting_seed=24)),
                    declared_media_type="image/png",
                ),
                ocr_enabled=False,
                idempotency_key=None,
            )
        )
        assert job.config_snapshot_json["spreadsheetOutput"] == first_snapshot
        assert new_job.config_snapshot_json["spreadsheetOutput"]["cellMerges"][0][
            "excelRange"
        ] == "A1:B1"
        assert runner.enqueued == [job.id, new_job.id]
    finally:
        database.dispose()


def test_old_job_uses_its_snapshot_after_live_layout_changes_and_new_job_uses_new_layout(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory) as case:
        with case.database.session() as session:
            old_job = session.get(ProcessingJob, case.job_id)
            version = session.get(TemplateVersion, case.version_id)
            assert old_job is not None
            assert version is not None
            mappings = copy.deepcopy(old_job.config_snapshot_json["fieldMappings"])
            version.layout_json = _short_horizontal_layout()
            session.flush()
            new_snapshot = SpreadsheetTemplateOutputService(case.storage).snapshot(
                session,
                version=version,
            )
            assert new_snapshot is not None
            new_job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.CONFIRMED.value,
                template_version_id=version.id,
                request_hash="n" * 64,
                config_snapshot_json={
                    "fieldMappings": mappings,
                    "spreadsheetOutput": new_snapshot,
                },
            )
            session.add(new_job)
            session.commit()
            new_job_id = new_job.id

        old_output = _loaded_output(case)
        with case.database.session() as session:
            new_job = session.get(ProcessingJob, new_job_id)
            assert new_job is not None
            rendered = SpreadsheetTemplateOutputService(case.storage).render(
                session,
                job=new_job,
                document=case.document,
                document_name=_OUTPUT_NAME,
                confirmed_at=_CONFIRMED_AT,
            )
        assert rendered is not None
        new_output = load_workbook(io.BytesIO(rendered.content), data_only=False, keep_links=True)

    old_ranges = {str(cell_range) for cell_range in old_output[_OUTPUT_NAME].merged_cells.ranges}
    new_ranges = {str(cell_range) for cell_range in new_output[_OUTPUT_NAME].merged_cells.ranges}
    assert "A1:D1" in old_ranges
    assert "A1:B1" not in old_ranges
    assert "A1:B1" in new_ranges
    assert "A1:D1" not in new_ranges


def test_rectangular_layout_merge_is_materialized_without_guessing_missing_cells(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory, layout=_rectangular_layout()) as case:
        output = _loaded_output(case)

    worksheet = output[_OUTPUT_NAME]
    assert "B6:C7" in {str(cell_range) for cell_range in worksheet.merged_cells.ranges}
    assert worksheet["B6"].value == "123-4567"
    assert [worksheet[cell].value for cell in ("C6", "B7", "C7")] == [None, None, None]


def _imported_anchor_layout(*coordinates: str) -> dict[str, Any]:
    layout = _layout()
    layout["cellMerges"][0]["parts"] = [
        {"cell": _layout_cell(f"xlsx_{coordinate}", x=index * 10, y=0),
         "partType": "input", "literal": ""}
        for index, coordinate in enumerate(coordinates)
    ]
    return layout


@pytest.mark.parametrize(
    ("coordinates", "ranges", "expected"),
    [
        (("AD10", "AI10"), ("AD10:AH12", "AI10:AJ12"), "AD10:AJ12"),
        (("AD10", "AD13"), ("AD10:AH12", "AD13:AH15"), "AD10:AH15"),
    ],
)
def test_imported_merged_anchors_round_trip_without_changing_identity(
    settings_factory: Callable[..., Settings],
    coordinates: tuple[str, ...], ranges: tuple[str, ...], expected: str,
) -> None:
    layout = _imported_anchor_layout(*coordinates)
    original = copy.deepcopy(layout)
    with _output_case(settings_factory, layout=layout, existing_merges=(*ranges, "F8:G8")) as case:
        with case.database.session() as session:
            job = session.get(ProcessingJob, case.job_id)
            assert job is not None
            snapshot = job.config_snapshot_json["spreadsheetOutput"]
            assert snapshot["schemaVersion"] == 2
            assert snapshot["cellMerges"][0]["parts"] == original["cellMerges"][0]["parts"]
            assert snapshot["cells"] == original["cells"]
            assert snapshot["cellMerges"][0]["excelRange"] == expected
        output = _loaded_output(case)
        assert case.storage.read_bytes(case.source_path) == case.source_bytes
        ws = output[_OUTPUT_NAME]
        assert ws[coordinates[0]].value == "123-4567"
        assert expected in {str(r) for r in ws.merged_cells.ranges}
        assert "F8:G8" in {str(r) for r in ws.merged_cells.ranges}
        assert ws["H1"].value == "変更してはいけない固定文字"
        assert ws["I1"].value == "=SUM(J1:J2)"
        assert ws["H1"].border.left.style == "thin"
        assert ws["J1"].number_format == "#,##0.00"
    assert layout == original


def test_imported_anchors_do_not_fill_unselected_source_range(
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(AppError) as caught, _output_case(
        settings_factory, layout=_imported_anchor_layout("H16", "M16", "S16"),
        existing_merges=("H16:K18", "L16:L18", "M16:R18", "S16:AC18"),
    ):
        pass
    assert caught.value.code == "SPREADSHEET_OUTPUT_MERGE_INVALID"
    assert "L16" in caught.value.detail
    assert "L17" in caught.value.detail
    assert "L18" in caught.value.detail


def test_remerging_preserves_outer_borders_after_xlsx_reload() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["A1"].border = Border(
        left=Side(style="thick"), top=Side(style="dashed"), right=Side(style="thin"),
        bottom=Side(style="double"),
    )
    worksheet["C1"].border = Border(
        right=Side(style="double"), top=Side(style="dashed"), bottom=Side(style="double"),
    )
    worksheet.merge_cells("A1:B2")
    worksheet.merge_cells("C1:D2")
    merge = _imported_anchor_layout("A1", "C1")["cellMerges"][0]
    service = SpreadsheetTemplateOutputService
    plan = service._merge_plan(
        merge,
        require_snapshot_metadata=False,
        source_ranges=service._source_ranges(worksheet, merge),
    )
    service._apply_merges(worksheet, [plan])
    output = io.BytesIO()
    workbook.save(output)
    reloaded = load_workbook(io.BytesIO(output.getvalue())).active
    assert str(next(iter(reloaded.merged_cells.ranges))) == "A1:D2"
    assert reloaded["A1"].border.left.style == "thick"
    assert reloaded["D1"].border.right.style == "double"
    assert reloaded["D2"].border.right.style == "double"
    assert reloaded["B1"].border.top.style == "dashed"
    assert reloaded["B2"].border.bottom.style == "double"


def test_imported_anchor_merge_rejects_formula_before_acceptance(
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(AppError) as caught, _output_case(
        settings_factory, layout=_imported_anchor_layout("A1", "C1"),
        existing_merges=("A1:B2", "C1:D2"), protected_formula_in_merge=True,
    ):
        pass
    assert caught.value.code == "SPREADSHEET_OUTPUT_MERGE_CONFLICT"


@pytest.mark.parametrize("tamper", ["range", "version"])
def test_source_range_snapshot_cannot_be_reinterpreted(
    settings_factory: Callable[..., Settings], tamper: str,
) -> None:
    with _output_case(
        settings_factory, layout=_imported_anchor_layout("AD10", "AI10"),
        existing_merges=("AD10:AH12", "AI10:AJ12"),
    ) as case:
        with case.database.session() as session:
            job = session.get(ProcessingJob, case.job_id)
            assert job is not None
            config = copy.deepcopy(job.config_snapshot_json)
            if tamper == "range":
                config["spreadsheetOutput"]["cellMerges"][0]["sourceRanges"]["AD10"] = "AD10:AI12"
            else:
                config["spreadsheetOutput"]["schemaVersion"] = 1
            job.config_snapshot_json = config
            session.commit()
        with pytest.raises(AppError):
            _render(case)


def test_source_range_snapshot_does_not_follow_live_template_edits(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(
        settings_factory, layout=_imported_anchor_layout("AD10", "AI10"),
        existing_merges=("AD10:AH12", "AI10:AJ12"),
    ) as case:
        with case.database.session() as session:
            version = session.get(TemplateVersion, case.version_id)
            assert version is not None
            version.layout_json = _imported_anchor_layout("H16", "S16")
            session.commit()
        output = _loaded_output(case)
        assert "AD10:AJ12" in {str(r) for r in output[_OUTPUT_NAME].merged_cells.ranges}
        assert output[_OUTPUT_NAME]["AD10"].value == "123-4567"


def test_source_backed_plans_cannot_overlap_each_other(
    settings_factory: Callable[..., Settings],
) -> None:
    layout = _imported_anchor_layout("AD10", "AI10")
    other = copy.deepcopy(layout["cellMerges"][0])
    other.update(id="other", parentCellId="other_parent")
    other["parts"].append(
        {"cell": _layout_cell("xlsx_AK10", x=20, y=0), "partType": "input", "literal": ""}
    )
    layout["cellMerges"].append(other)
    with pytest.raises(AppError) as caught, _output_case(
        settings_factory, layout=layout,
        existing_merges=("AD10:AH12", "AI10:AJ12", "AK10:AL12"),
    ):
        pass
    assert caught.value.code == "SPREADSHEET_OUTPUT_MERGE_INVALID"


def test_virtual_subcell_cannot_claim_entire_source_merge(
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(AppError), _output_case(
        settings_factory, layout=_imported_anchor_layout("AD10_split_1", "AI10"),
        existing_merges=("AD10:AH12", "AI10:AJ12"),
    ):
        pass


def test_invalid_merge_save_leaves_template_and_source_unchanged(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(
        settings_factory,
        existing_merges=("H16:K18", "L16:L18", "M16:R18", "S16:AC18"),
    ) as case:
        with case.database.session() as session:
            version = session.get(TemplateVersion, case.version_id)
            assert version is not None
            original = copy.deepcopy(version.layout_json)
            quality = copy.deepcopy(version.quality_json)
        proposed = LayoutDefinition.model_validate(_imported_anchor_layout("H16", "M16", "S16"))
        with pytest.raises(AppError) as caught:
            LayoutService(case.database, case.storage).save(case.version_id, proposed)
        assert caught.value.code == "SPREADSHEET_OUTPUT_MERGE_INVALID"
        with case.database.session() as session:
            version = session.get(TemplateVersion, case.version_id)
            assert version is not None
            assert version.layout_json == original
            assert version.quality_json == quality
        assert case.storage.read_bytes(case.source_path) == case.source_bytes


def test_selected_literal_is_allowed_but_input_text_is_not_discarded(
    settings_factory: Callable[..., Settings],
) -> None:
    layout = _imported_anchor_layout("A1", "B1")
    # B1 is an existing literal in the source workbook, not OCR input.
    with pytest.raises(AppError) as caught, _output_case(
        settings_factory, layout=layout, existing_merges=("A1:A2", "B1:B2"),
    ):
        pass
    assert caught.value.code == "SPREADSHEET_OUTPUT_MERGE_CONFLICT"
    layout["cellMerges"][0]["parts"][1].update(partType="literal", literal="-")
    with _output_case(
        settings_factory, layout=layout, existing_merges=("A1:A2", "B1:B2"),
    ) as case:
        output = _loaded_output(case)
        assert output[_OUTPUT_NAME]["A1"].value == "123-4567"
        assert "A1:B2" in {str(r) for r in output[_OUTPUT_NAME].merged_cells.ranges}


def test_non_contiguous_merge_parts_are_rejected(
    settings_factory: Callable[..., Settings],
) -> None:
    invalid_layout = _layout()
    invalid_layout["cellMerges"][0]["parts"].pop(1)

    with (
        pytest.raises(AppError) as caught,
        _output_case(settings_factory, layout=invalid_layout),
    ):
        pass

    assert caught.value.status == 409
    assert caught.value.code == "SPREADSHEET_OUTPUT_MERGE_INVALID"


def test_exact_existing_merge_is_reused_and_unrelated_existing_merge_is_preserved(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(
        settings_factory,
        existing_merges=("A1:D1", "F1:G1"),
    ) as case:
        output = _loaded_output(case)

    merged_ranges = {str(cell_range) for cell_range in output[_OUTPUT_NAME].merged_cells.ranges}
    assert {"A1:D1", "F1:G1"} <= merged_ranges
    assert output[_OUTPUT_NAME]["A1"].value == "123-4567"


@pytest.mark.parametrize("existing_merge", ["A1:B1", "B1:C1", "A1:E1"])
def test_overlapping_or_containing_existing_merge_is_rejected(
    settings_factory: Callable[..., Settings],
    existing_merge: str,
) -> None:
    with (
        _output_case(settings_factory, existing_merges=(existing_merge,)) as case,
        pytest.raises(AppError) as caught,
    ):
        _render(case)

    assert caught.value.status == 409
    assert caught.value.code == "SPREADSHEET_OUTPUT_MERGE_CONFLICT"


def test_formula_inside_new_merge_is_not_silently_destroyed(
    settings_factory: Callable[..., Settings],
) -> None:
    with (
        _output_case(settings_factory, protected_formula_in_merge=True) as case,
        pytest.raises(AppError) as caught,
    ):
        _render(case)

    assert caught.value.status == 409
    assert caught.value.code == "SPREADSHEET_OUTPUT_MERGE_CONFLICT"


def test_result_sheet_reference_to_removed_sheet_is_rejected(
    settings_factory: Callable[..., Settings],
) -> None:
    with (
        _output_case(settings_factory, cross_sheet_formula=True) as case,
        pytest.raises(AppError) as caught,
    ):
        _render(case)

    assert caught.value.status == 409
    assert caught.value.code == "SPREADSHEET_OUTPUT_SHEET_REFERENCE_CONFLICT"


def test_legacy_job_without_spreadsheet_output_snapshot_is_rejected(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory) as case:
        with case.database.session() as session:
            job = session.get(ProcessingJob, case.job_id)
            assert job is not None
            snapshot = dict(job.config_snapshot_json)
            snapshot.pop("spreadsheetOutput")
            job.config_snapshot_json = snapshot
            session.commit()

        with pytest.raises(AppError) as caught:
            _render(case)

    assert caught.value.status == 409
    assert caught.value.code == "SPREADSHEET_OUTPUT_SNAPSHOT_MISSING"


def test_unmerged_field_keeps_its_existing_destination(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory) as case:
        output = _loaded_output(case)

    assert output[_OUTPUT_NAME]["D5"].value == "通常値"


def test_render_does_not_modify_the_source_workbook_bytes(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory) as case:
        before = hashlib.sha256(case.source_bytes).hexdigest()
        _render(case)
        stored_source = case.storage.read_bytes(case.source_path)
        after = hashlib.sha256(stored_source).hexdigest()

    assert after == before
    assert stored_source == case.source_bytes


def test_result_sheet_preserves_unrelated_workbook_structure(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory) as case:
        output = _loaded_output(case)

    worksheet = output[_OUTPUT_NAME]
    assert worksheet.column_dimensions["H"].width == 24.5
    assert worksheet.row_dimensions[1].height == 33.0
    assert worksheet["H1"].value == "変更してはいけない固定文字"
    assert worksheet["H1"].border.left.style == "thin"
    assert worksheet["H1"].fill.fill_type == "solid"
    assert worksheet["I1"].value == "=SUM(J1:J2)"
    assert worksheet["J1"].value == 1234.5
    assert worksheet["J1"].number_format == "#,##0.00"
    assert worksheet.page_setup.orientation == "landscape"
    assert worksheet.page_margins.left == 0.25
    assert "$A$1:$J$10" in str(worksheet.print_area)
    assert "RemovedSheetOnly" not in output.defined_names
    assert output.defined_names["ResultSheetOnly"].attr_text == (
        f"'{_OUTPUT_NAME}'!$H$1"
    )


def test_render_does_not_mutate_ocr_identity_or_template_layout(
    settings_factory: Callable[..., Settings],
) -> None:
    with _output_case(settings_factory) as case:
        with case.database.session() as session:
            job = session.get(ProcessingJob, case.job_id)
            version = session.get(TemplateVersion, case.version_id)
            assert job is not None
            assert version is not None
            mappings_before = copy.deepcopy(job.config_snapshot_json["fieldMappings"])
            layout_before = copy.deepcopy(version.layout_json)

        _render(case)

        with case.database.session() as session:
            job = session.get(ProcessingJob, case.job_id)
            version = session.get(TemplateVersion, case.version_id)
            assert job is not None
            assert version is not None
            assert job.config_snapshot_json["fieldMappings"] == mappings_before
            assert version.layout_json == layout_before

        for before, after in zip(
            mappings_before,
            job.config_snapshot_json["fieldMappings"],
            strict=True,
        ):
            for key in (
                "mappingId",
                "openAiMappingId",
                "targetPath",
                "sourceRegionId",
                "boundingBox",
                "crop",
                "fieldPreview",
            ):
                assert after[key] == before[key]
