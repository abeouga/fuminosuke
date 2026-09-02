from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import (
    ConfirmedRecordField,
    ConfirmedResult,
    LedgerDocument,
    OcrResult,
    OcrResultRevision,
    ProcessingJob,
    SpreadsheetEditEvent,
    SpreadsheetOutputOperation,
    Template,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, RevisionSource, TemplateMode
from app.domain.generic_data import build_generic_data, build_generic_values
from app.domain.individual_id import individual_id_from_document
from app.domain.numeric_values import normalize_document_numbers
from app.domain.ocr_models import (
    Calving,
    DocumentIdentity,
    FieldReading,
    OcrDocument,
    Pedigree,
    ReviewItem,
    SaleCalf,
)
from app.domain.template_config import load_template_config
from app.repositories.job_repository import JobRepository
from app.repositories.ocr_result_repository import OcrResultRepository
from app.services.document_reconciliation_service import (
    DocumentReconciliationService,
    ResolutionAction,
)
from app.services.excel_export_service import ExcelExportService
from app.services.google_drive_service import GoogleDriveService
from app.services.google_sheets_sync_service import GoogleSheetsSyncService, SpreadsheetEdit
from app.services.review_display_structure import build_review_display_structure
from app.services.spreadsheet_template_output_service import SpreadsheetTemplateOutputService
from app.storage.local_storage import LocalStorageService
from app.validation.business_validators import DeterministicValidator


@dataclass(frozen=True, slots=True)
class CorrectionValues:
    base_revision: int
    document: DocumentIdentity
    sale_calves: list[SaleCalf]
    pedigree: Pedigree
    calvings: list[Calving]
    custom_fields: dict[str, FieldReading]
    review_items: list[ReviewItem]


@dataclass(frozen=True, slots=True)
class SpreadsheetEditOutcome:
    status: str
    revision: int | None
    error_code: str | None = None
    error_message: str | None = None


class OcrResultService:
    _SHEET_FIELD_PATH = re.compile(
        r"^(?:(?:document|pedigree)\.[A-Za-z][A-Za-z0-9]*|"
        r"(?:saleCalves|calvings)\.\d+\.[A-Za-z][A-Za-z0-9]*)$"
    )

    def __init__(
        self,
        database: Database,
        storage: LocalStorageService,
        google_sheets: GoogleSheetsSyncService | None = None,
        google_drive: GoogleDriveService | None = None,
    ) -> None:
        self._database = database
        self._storage = storage
        self._validator = DeterministicValidator()
        self._excel = ExcelExportService()
        self._spreadsheet_template_output = SpreadsheetTemplateOutputService(storage)
        self._google_sheets = google_sheets
        self._google_drive = google_drive
        self._documents = DocumentReconciliationService(self._validator)

    def get(
        self,
        job_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            if job.status not in {
                JobStatus.REVIEW_REQUIRED.value,
                JobStatus.CONFIRMED.value,
            }:
                raise AppError(
                    status=409,
                    code="RESULT_NOT_READY",
                    title="OCR result is not ready",
                    detail="OCR結果はレビュー準備完了後に取得できます。",
                )
            repository = OcrResultRepository(session)
            stored = repository.get_by_job(job_id)
            if stored is None:
                raise self._not_found()
            is_confirmed = job.status == JobStatus.CONFIRMED.value
            confirmed = repository.get_confirmed(job_id) if is_confirmed else None
            return self._response(
                stored,
                job=job,
                confirmed=is_confirmed,
                individual_id=confirmed.individual_id if confirmed else None,
                expected_subject=expected_subject,
            )

    def correct(
        self,
        job_id: str,
        values: CorrectionValues,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._database.session() as session:
            self._begin_mutation(session)
            jobs = JobRepository(session)
            job = jobs.get(job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            if job.status != JobStatus.REVIEW_REQUIRED.value:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="OCR result cannot be edited",
                    detail="REVIEW_REQUIRED状態のOCR結果だけを修正できます。",
                )
            repository = OcrResultRepository(session)
            stored = repository.get_by_job(job_id)
            if stored is None:
                raise self._not_found()
            current = OcrDocument.model_validate(stored.current_corrected_json)
            candidate = current.model_copy(
                update={
                    "document": values.document,
                    "sale_calves": values.sale_calves,
                    "pedigree": values.pedigree,
                    "calvings": values.calvings,
                    "custom_fields": values.custom_fields,
                    "review_items": values.review_items,
                },
                deep=True,
            )
            candidate = OcrDocument.model_validate(candidate.model_dump(mode="json", by_alias=True))
            candidate.generic_data = build_generic_data(
                candidate,
                job.config_snapshot_json.get("genericTemplateDefinition"),
            )
            candidate.generic_values = build_generic_values(candidate.generic_data)
            candidate = normalize_document_numbers(candidate)
            report = self._validator.validate(
                candidate,
                configured_paths=self._configured_paths(job),
            )
            candidate = self._validator.merge_review_items(candidate, report)
            serialized = candidate.model_dump(mode="json", by_alias=True)
            updated = repository.save_correction(
                result=stored,
                base_revision=values.base_revision,
                candidate=serialized,
                report=report,
            )
            if updated is None:
                raise AppError(
                    status=409,
                    code="REVISION_CONFLICT",
                    title="OCR result revision conflict",
                    detail="別の修正が保存されています。最新結果を再取得してください。",
                )
            resolution = self._documents.payload(
                session, job=job, document_json=updated.current_corrected_json
            )
            if self._google_sheets is not None and self._may_sync_before_confirmation(resolution):
                self._google_sheets.sync(
                    job_id=job_id,
                    revision=updated.revision,
                    document=updated.current_corrected_json,
                    confirmed=False,
                    template_version_id=job.template_version_id,
                    document_id=self._documents.document_id_for_job(session, job_id),
                    expected_subject=expected_subject,
                )
            return self._response(
                updated,
                job=job,
                confirmed=False,
                document_resolution=resolution,
                expected_subject=expected_subject,
            )

    def resolve_document(
        self,
        job_id: str,
        *,
        action: ResolutionAction,
        target_document_id: str | None = None,
        target_job_id: str | None = None,
        target_page_id: str | None = None,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._database.session() as session:
            self._begin_mutation(session)
            job = JobRepository(session).get(job_id)
            repository = OcrResultRepository(session)
            stored = repository.get_by_job(job_id)
            if job is None or stored is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            if job.status != JobStatus.REVIEW_REQUIRED.value:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="Document cannot be assigned",
                    detail="確認中の読取結果だけ保存先を選択できます。",
                )
            updated = self._documents.resolve(
                session,
                job=job,
                stored=stored,
                action=action,
                target_document_id=target_document_id,
                target_job_id=target_job_id,
                target_page_id=target_page_id,
                configured_paths=self._configured_paths(job),
            )
            session.commit()
            resolution = self._documents.payload(
                session, job=job, document_json=updated.current_corrected_json
            )
            return self._response(
                updated,
                job=job,
                confirmed=False,
                document_resolution=resolution,
                expected_subject=expected_subject,
            )

    def apply_spreadsheet_edit(self, edit: SpreadsheetEdit) -> SpreadsheetEditOutcome:
        sync_payload: tuple[int, dict[str, Any], bool, str | None, str | None] | None = None
        with self._database.session() as session:
            self._begin_mutation(session)
            existing = session.get(SpreadsheetEditEvent, edit.edit_id)
            if existing is not None:
                if not self._spreadsheet_edit_matches_event(edit, existing):
                    return SpreadsheetEditOutcome(
                        status="FAILED",
                        revision=None,
                        error_code="SHEET_EDIT_ID_CONFLICT",
                        error_message=(
                            "同じ編集IDが別のSheet編集に使用されています。"
                            "Sheetを再読み込みしてください。"
                        ),
                    )
                return SpreadsheetEditOutcome(
                    status=existing.status,
                    revision=existing.applied_revision,
                    error_code=existing.error_code,
                    error_message=existing.error_message,
                )
            job = JobRepository(session).get(edit.job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                return SpreadsheetEditOutcome(
                    status="FAILED",
                    revision=None,
                    error_code="OCR_RESULT_NOT_FOUND",
                    error_message="対象のOCR結果が見つかりません。",
                )
            event = SpreadsheetEditEvent(
                edit_id=edit.edit_id,
                job_id=edit.job_id,
                record_id=edit.record_id,
                field_id=edit.field_id,
                previous_value=edit.previous_value,
                new_value=edit.new_value,
                base_revision=edit.base_revision,
                source_sheet=edit.source_sheet,
                source_cell=edit.source_cell,
                spreadsheet_id=edit.spreadsheet_id,
                google_subject=edit.google_subject,
                status="PROCESSING",
            )
            session.add(event)
            if job.status not in {
                JobStatus.REVIEW_REQUIRED.value,
                JobStatus.CONFIRMED.value,
            }:
                return self._fail_spreadsheet_edit(
                    session,
                    event,
                    "INVALID_STATE_TRANSITION",
                    "レビュー可能または確定済みのOCR結果だけを修正できます。",
                )
            repository = OcrResultRepository(session)
            stored = repository.get_by_job(edit.job_id)
            if stored is None:
                return self._fail_spreadsheet_edit(
                    session,
                    event,
                    "OCR_RESULT_NOT_FOUND",
                    "対象のOCR結果が見つかりません。",
                )
            document_id = self._documents.document_id_for_job(session, edit.job_id)
            if edit.spreadsheet_id is not None:
                ledger = (
                    session.get(LedgerDocument, document_id)
                    if document_id is not None
                    else None
                )
                if (
                    ledger is None
                    or ledger.spreadsheet_id != edit.spreadsheet_id
                    or (
                        edit.google_subject is not None
                        and ledger.drive_google_subject != edit.google_subject
                    )
                ):
                    return self._fail_spreadsheet_edit(
                        session,
                        event,
                        "SHEET_EDIT_TARGET_MISMATCH",
                        "このSheet編集は対象書類またはGoogleアカウントと一致しません。",
                    )
            if not edit.edit_id or edit.base_revision < 1:
                return self._fail_spreadsheet_edit(
                    session,
                    event,
                    "SHEET_EDIT_INVALID",
                    "編集イベントのIDまたはrevisionが不正です。",
                )
            try:
                candidate, previous = self._apply_sheet_value(
                    OcrDocument.model_validate(stored.current_corrected_json),
                    field_id=edit.field_id,
                    record_id=edit.record_id,
                    value=edit.new_value,
                )
            except (IndexError, KeyError, TypeError, ValueError):
                return self._fail_spreadsheet_edit(
                    session,
                    event,
                    "SHEET_FIELD_NOT_EDITABLE",
                    "このセルは通常修正の対象ではありません。詳細修正画面を使用してください。",
                )
            if stored.revision != edit.base_revision:
                safe_to_rebase = previous == edit.previous_value
                if edit.previous_value is None:
                    base = session.scalar(
                        select(OcrResultRevision).where(
                            OcrResultRevision.ocr_result_id == stored.id,
                            OcrResultRevision.revision == edit.base_revision,
                        )
                    )
                    if base is not None:
                        try:
                            _, base_value = self._apply_sheet_value(
                                OcrDocument.model_validate(base.result_json),
                                field_id=edit.field_id,
                                record_id=edit.record_id,
                                value=edit.new_value,
                            )
                        except (IndexError, KeyError, TypeError, ValueError):
                            base_value = None
                        safe_to_rebase = base_value is not None and base_value == previous
                if not safe_to_rebase:
                    return self._fail_spreadsheet_edit(
                        session,
                        event,
                        "REVISION_CONFLICT",
                        "別の修正が先に保存されています。Sheetを再読み込みしてください。",
                    )
            candidate = normalize_document_numbers(candidate)
            report = self._validator.validate(
                candidate,
                configured_paths=self._configured_paths(job),
            )
            candidate = self._validator.merge_review_items(candidate, report)
            serialized = candidate.model_dump(mode="json", by_alias=True)
            was_confirmed = job.status == JobStatus.CONFIRMED.value
            updated = repository.save_correction(
                result=stored,
                base_revision=stored.revision,
                candidate=serialized,
                report=report,
                source=RevisionSource.SHEET_EDIT,
                commit=False,
            )
            if updated is None:
                return self._fail_spreadsheet_edit(
                    session,
                    event,
                    "REVISION_CONFLICT",
                    "別の修正が先に保存されています。Sheetを再読み込みしてください。",
                )
            remains_confirmed = False
            if was_confirmed:
                has_error = any(item.severity == "error" for item in candidate.review_items)
                if report.valid and not has_error:
                    if self._uses_cattle_schema(session, job):
                        try:
                            individual_id = individual_id_from_document(candidate)
                        except ValueError:
                            individual_id = None
                    else:
                        individual_id = self._documents.document_key(job.id)
                    if individual_id is not None:
                        current_confirmed = repository.get_confirmed(job.id)
                        document_id = self._documents.document_id_for_job(session, job.id)
                        ledger = session.get(LedgerDocument, document_id) if document_id else None
                        document_name = (
                            (ledger.document_name or ledger.library_name)
                            if ledger is not None
                            else None
                        ) or (
                            current_confirmed.spreadsheet_name
                            if current_confirmed is not None
                            else None
                        ) or self._documents.proposed_document_name(job, serialized)
                        self._persist_confirmed_snapshot(
                            session,
                            repository=repository,
                            job=job,
                            stored=updated,
                            serialized=serialized,
                            individual_id=individual_id,
                            document_name=document_name,
                        )
                        remains_confirmed = True
                if not remains_confirmed:
                    job.status = JobStatus.REVIEW_REQUIRED.value
                    job.confirmed_at = None
            event.status = "APPLIED"
            event.applied_revision = updated.revision
            event.processed_at = datetime.now(UTC)
            document_id = self._documents.document_id_for_job(session, edit.job_id)
            if remains_confirmed and document_id:
                ledger = session.get(LedgerDocument, document_id)
                if ledger is not None:
                    ledger.current_document_json = serialized
                    ledger.current_job_id = edit.job_id
            session.commit()
            sync_payload = (
                updated.revision,
                serialized,
                remains_confirmed,
                job.template_version_id,
                document_id,
            )
            outcome = SpreadsheetEditOutcome(status="APPLIED", revision=updated.revision)
        if self._google_sheets is not None and sync_payload is not None:
            revision, document, confirmed, template_version_id, document_id = sync_payload
            self._google_sheets.sync(
                job_id=edit.job_id,
                revision=revision,
                document=document,
                confirmed=confirmed,
                template_version_id=template_version_id,
                document_id=document_id,
                expected_subject=edit.google_subject,
            )
        return outcome

    def reopen(
        self,
        job_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._database.session() as session:
            self._begin_mutation(session)
            job = JobRepository(session).get(job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            repository = OcrResultRepository(session)
            stored = repository.get_by_job(job_id)
            if stored is None:
                raise self._not_found()
            confirmed_result = repository.get_confirmed(job_id)
            if job.status == JobStatus.CONFIRMED.value:
                if confirmed_result is None:
                    raise AppError(
                        status=409,
                        code="CONFIRMED_RESULT_MISSING",
                        title="Confirmed OCR result is missing",
                        detail="確定済みデータを取得できないため、編集を開始できません。",
                    )
                job.status = JobStatus.REVIEW_REQUIRED.value
                job.confirmed_at = None
                session.commit()
            elif job.status != JobStatus.REVIEW_REQUIRED.value:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="OCR result cannot be reopened",
                    detail="確定済み、または編集中のOCR結果だけを編集できます。",
                )
            if (
                self._google_sheets is not None
                and not self._is_spreadsheet_template(session, job)
            ):
                document_id = self._documents.document_id_for_job(session, job_id)
                self._google_sheets.sync(
                    job_id=job_id,
                    revision=stored.revision,
                    document=self._normalized_document_json(stored.current_corrected_json),
                    confirmed=False,
                    template_version_id=job.template_version_id,
                    document_id=document_id,
                    expected_subject=expected_subject,
                )
            return self._response(
                stored,
                job=job,
                confirmed=False,
                individual_id=(
                    confirmed_result.individual_id if confirmed_result is not None else None
                ),
                expected_subject=expected_subject,
            )

    @staticmethod
    def _spreadsheet_edit_matches_event(
        edit: SpreadsheetEdit,
        event: SpreadsheetEditEvent,
    ) -> bool:
        return (
            event.job_id == edit.job_id
            and event.record_id == edit.record_id
            and event.field_id == edit.field_id
            and event.previous_value == edit.previous_value
            and event.new_value == edit.new_value
            and event.base_revision == edit.base_revision
            and event.source_sheet == edit.source_sheet
            and event.source_cell == edit.source_cell
            and event.spreadsheet_id == edit.spreadsheet_id
            and event.google_subject == edit.google_subject
        )

    def confirm(
        self,
        job_id: str,
        *,
        base_revision: int,
        document_name: str | None = None,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        name_sync_operation_id: str | None = None
        with self._database.session() as session:
            self._begin_mutation(session)
            jobs = JobRepository(session)
            job = jobs.get(job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            repository = OcrResultRepository(session)
            if job.status != JobStatus.REVIEW_REQUIRED.value:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="OCR result cannot be confirmed",
                    detail="このOCR結果は確定できません。",
                )
            stored = repository.get_by_job(job_id)
            if stored is None:
                raise self._not_found()
            if stored.revision != base_revision:
                raise AppError(
                    status=409,
                    code="REVISION_CONFLICT",
                    title="OCR result revision conflict",
                    detail="別の修正が保存されています。最新結果を再取得してください。",
                )
            original_document = OcrDocument.model_validate(stored.current_corrected_json)
            document = normalize_document_numbers(original_document)
            report = self._validator.validate(
                document,
                configured_paths=self._configured_paths(job),
            )
            document = self._validator.merge_review_items(document, report)
            has_unresolved_error = any(item.severity == "error" for item in document.review_items)
            if not report.valid or has_unresolved_error:
                raise AppError(
                    status=409,
                    code="UNRESOLVED_REVIEW_ITEMS",
                    title="OCR result has unresolved errors",
                    detail="errorレベルのレビュー項目を解消してから確定してください。",
                )
            is_cattle_document = self._uses_cattle_schema(session, job)
            is_spreadsheet_template = self._is_spreadsheet_template(session, job)
            if is_cattle_document:
                try:
                    individual_id = individual_id_from_document(document)
                except ValueError as exc:
                    raise AppError(
                        status=409,
                        code="INDIVIDUAL_ID_REQUIRED",
                        title="Individual identification number is required",
                        detail="個体識別番号を10桁の数字で確定してから保存してください。",
                    ) from exc
            else:
                individual_id = self._documents.document_key(job.id)
            serialized = document.model_dump(mode="json", by_alias=True)
            if serialized != stored.current_corrected_json:
                updated = repository.save_correction(
                    result=stored,
                    base_revision=base_revision,
                    candidate=serialized,
                    report=report,
                    commit=False,
                )
                if updated is None:
                    raise AppError(
                        status=409,
                        code="REVISION_CONFLICT",
                        title="OCR result revision conflict",
                        detail="別の修正が保存されています。最新結果を再取得してください。",
                    )
                stored = updated
            ledger, page = self._documents.ensure_for_confirmation(
                session,
                job=job,
                document_json=serialized,
                configured_paths=self._configured_paths(job),
            )
            previous_confirmed = repository.get_confirmed(job_id)
            existing_document_name = (
                (ledger.document_name or ledger.library_name)
                if ledger is not None
                else None
            ) or (
                previous_confirmed.spreadsheet_name
                if previous_confirmed is not None
                else None
            )
            resolved_document_name = self._normalize_spreadsheet_name(
                document_name
                or existing_document_name
                or self._documents.proposed_document_name(job, serialized)
            )
            if (
                not is_spreadsheet_template
                and ledger is not None
                and ledger.spreadsheet_id
            ):
                candidate_operation_id = f"name:{job_id}:{stored.revision}"[:124]
                name_intent = session.get(
                    SpreadsheetOutputOperation,
                    candidate_operation_id,
                )
                prior_output_intent = session.scalar(
                    select(SpreadsheetOutputOperation)
                    .where(
                        SpreadsheetOutputOperation.document_id == ledger.id,
                        SpreadsheetOutputOperation.spreadsheet_id
                        == ledger.spreadsheet_id,
                        SpreadsheetOutputOperation.operation_id
                        != candidate_operation_id,
                    )
                    .order_by(SpreadsheetOutputOperation.updated_at.desc())
                    .limit(1)
                )
                persisted_drive_name = (
                    prior_output_intent.drive_name
                    if prior_output_intent is not None
                    else None
                )
                # A migrated row may have a Drive file id but no saved name
                # baseline.  Still create an intent so a later full sync cannot
                # silently replace the newly confirmed local name with an
                # unknown remote name.  Reconciliation will surface the missing
                # baseline as a conflict instead of guessing.
                name_changed = existing_document_name != resolved_document_name
                remote_baseline_changed = (
                    persisted_drive_name is not None
                    and persisted_drive_name != resolved_document_name
                )
                if name_intent is not None:
                    if (
                        name_intent.document_id != ledger.id
                        or name_intent.spreadsheet_id != ledger.spreadsheet_id
                        or name_intent.source_job_id != job_id
                        or name_intent.source_revision != stored.revision
                    ):
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_NAME_OPERATION_CONFLICT",
                            title="Spreadsheet name operation conflicts",
                            detail="同じ名前同期処理が別の書類に使用されています。",
                        )
                    if name_intent.drive_name != resolved_document_name:
                        name_sync_operation_id = candidate_operation_id
                elif name_changed or remote_baseline_changed:
                    if (
                        ledger.drive_google_subject is None
                        or ledger.drive_root_folder_id is None
                        or ledger.drive_folder_id is None
                    ):
                        raise AppError(
                            status=409,
                            code="DRIVE_DOCUMENT_OWNER_UNCONFIRMED",
                            title="Drive document ownership is not confirmed",
                            detail=(
                                "Google Drive上の既存書類名を変更する前に、"
                                "書類ライブラリで所有アカウントを確認してください。"
                            ),
                        )
                    name_intent = SpreadsheetOutputOperation(
                        operation_id=candidate_operation_id,
                        document_id=ledger.id,
                        google_subject=ledger.drive_google_subject,
                        source_job_id=job_id,
                        source_revision=stored.revision,
                        drive_root_folder_id=ledger.drive_root_folder_id,
                        drive_parent_folder_id=ledger.drive_folder_id,
                        # Empty string is the persisted "unknown baseline"
                        # marker for legacy rows whose remote name was never
                        # recorded.  It keeps the non-null schema invariant and
                        # forces reconciliation to report a conflict instead of
                        # guessing or allowing full sync to overwrite the local
                        # name.
                        drive_name=(
                            persisted_drive_name
                            if persisted_drive_name is not None
                            else existing_document_name or ""
                        ),
                        drive_folder_path=ledger.drive_folder_path,
                        library_folder_id=ledger.library_folder_id,
                        library_location_version=ledger.library_location_version,
                        spreadsheet_id=ledger.spreadsheet_id,
                    )
                    session.add(name_intent)
                    name_sync_operation_id = candidate_operation_id
            self._persist_confirmed_snapshot(
                session,
                repository=repository,
                job=job,
                stored=stored,
                serialized=serialized,
                individual_id=individual_id,
                document_name=resolved_document_name,
            )
            if ledger is not None and page is not None:
                ledger.document_name = resolved_document_name
                ledger.library_name = resolved_document_name
                self._documents.mark_confirmed(
                    session,
                    ledger,
                    page,
                    job_id=job_id,
                    document_json=serialized,
                    individual_id=ledger.individual_id,
                    job=job,
                )
            session.commit()
            if is_spreadsheet_template:
                self._publish_spreadsheet_template(job_id, expected_subject)
            else:
                if (
                    name_sync_operation_id is not None
                    and self._google_drive is not None
                    and ledger is not None
                ):
                    self._google_drive.reconcile_document_name(
                        ledger.id,
                        resolved_document_name,
                        operation_id=name_sync_operation_id,
                        expected_subject=expected_subject,
                    )
            if not is_spreadsheet_template and self._google_sheets is not None:
                self._google_sheets.sync(
                    job_id=job_id,
                    revision=stored.revision,
                    document=serialized,
                    confirmed=True,
                    template_version_id=job.template_version_id,
                    document_id=ledger.id if ledger is not None else None,
                    expected_subject=expected_subject,
                )
            return self._response(
                stored,
                job=job,
                confirmed=True,
                individual_id=individual_id,
                expected_subject=expected_subject,
            )

    def sync_spreadsheet(
        self,
        job_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        name_retry: tuple[str, str, str] | None = None
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            stored = OcrResultRepository(session).get_by_job(job_id)
            if job is None or stored is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            confirmed = job.status == JobStatus.CONFIRMED.value
            resolution = self._documents.payload(
                session, job=job, document_json=stored.current_corrected_json
            )
            if resolution["required"]:
                raise AppError(
                    status=409,
                    code="DOCUMENT_RESOLUTION_REQUIRED",
                    title="Document destination must be selected",
                    detail="同じ個体の台帳があります。保存方法を先に選択してください。",
                )
            is_spreadsheet_template = self._is_spreadsheet_template(session, job)
            document_id = self._documents.document_id_for_job(session, job_id)
            confirmed_result = (
                OcrResultRepository(session).get_confirmed(job_id) if confirmed else None
            )
            if (
                confirmed
                and not is_spreadsheet_template
                and document_id is not None
                and self._google_drive is not None
            ):
                ledger = session.get(LedgerDocument, document_id)
                operation_id = f"name:{job_id}:{stored.revision}"[:124]
                intent = session.get(SpreadsheetOutputOperation, operation_id)
                desired_name = (
                    (ledger.document_name or ledger.library_name)
                    if ledger is not None
                    else None
                ) or (
                    confirmed_result.spreadsheet_name
                    if confirmed_result is not None
                    else None
                )
                if (
                    intent is not None
                    and desired_name is not None
                    and intent.drive_name != desired_name
                ):
                    name_retry = (document_id, desired_name, operation_id)
            revision = stored.revision
            document = self._normalized_document_json(stored.current_corrected_json)
            template_version_id = job.template_version_id
            individual_id = (
                confirmed_result.individual_id if confirmed_result is not None else None
            )

        if is_spreadsheet_template:
            if confirmed:
                self._publish_spreadsheet_template(job_id, expected_subject)
        else:
            if name_retry is not None and self._google_drive is not None:
                self._google_drive.reconcile_document_name(
                    name_retry[0],
                    name_retry[1],
                    operation_id=name_retry[2],
                    expected_subject=expected_subject,
                )
            if self._google_sheets is not None:
                self._google_sheets.sync(
                    job_id=job_id,
                    revision=revision,
                    document=document,
                    confirmed=confirmed,
                    template_version_id=template_version_id,
                    document_id=document_id,
                    expected_subject=expected_subject,
                )

        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            stored = OcrResultRepository(session).get_by_job(job_id)
            if job is None or stored is None:
                raise self._not_found()
            return self._response(
                stored,
                job=job,
                confirmed=confirmed,
                individual_id=individual_id,
                expected_subject=expected_subject,
            )

    def rename_spreadsheet(
        self,
        job_id: str,
        file_name: str,
        expected_subject: str | None = None,
    ) -> dict[str, object | None]:
        if self._google_drive is None:
            raise AppError(
                status=409,
                code="GOOGLE_DRIVE_DISABLED",
                title="Google Drive is not available",
                detail="Google Drive連携が有効ではありません。",
            )
        normalized = self._normalize_spreadsheet_name(file_name)
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            confirmed = OcrResultRepository(session).get_confirmed(job_id)
            if (
                job is None
                or confirmed is None
                or job.status != JobStatus.CONFIRMED.value
                or not self._is_spreadsheet_template(session, job)
            ):
                raise AppError(
                    status=409,
                    code="SPREADSHEET_RENAME_NOT_AVAILABLE",
                    title="Spreadsheet rename is not available",
                    detail="確定済みのXLSXテンプレート出力だけ名前を変更できます。",
                )
            if not confirmed.spreadsheet_id:
                raise AppError(
                    status=409,
                    code="SPREADSHEET_OUTPUT_NOT_READY",
                    title="Spreadsheet output is not ready",
                    detail="Drive上の入力済みスプレッドシートを作成してから名前を変更してください。",
                )
            spreadsheet_id = confirmed.spreadsheet_id

        renamed = self._google_drive.rename_spreadsheet(
            spreadsheet_id,
            normalized,
            expected_subject,
        )
        with self._database.session() as session:
            current = OcrResultRepository(session).get_confirmed(job_id)
            if current is None or current.spreadsheet_id != spreadsheet_id:
                raise AppError(
                    status=409,
                    code="SPREADSHEET_OUTPUT_CHANGED",
                    title="Spreadsheet output changed",
                    detail="出力先が更新されました。画面を再読み込みしてください。",
                )
            current.spreadsheet_name = renamed
            document_id = self._documents.document_id_for_job(session, job_id)
            ledger = session.get(LedgerDocument, document_id) if document_id else None
            if ledger is not None:
                ledger.document_name = renamed
                ledger.library_name = renamed
            session.commit()
            return {
                "enabled": True,
                "status": "synced",
                "spreadsheetUrl": current.spreadsheet_url,
                "fileName": renamed,
                "lastError": None,
            }

    def document_id(self, job_id: str) -> str | None:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            return self._documents.document_id_for_job(session, job_id)

    def spreadsheet_status(
        self,
        job_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, object | None]:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            if self._is_spreadsheet_template(session, job):
                return self._spreadsheet_template_status(
                    session,
                    job,
                    confirmed=job.status == JobStatus.CONFIRMED.value,
                    expected_subject=expected_subject,
                )
            document_id = self._documents.document_id_for_job(session, job_id)
        if self._google_sheets is None:
            return self._disabled_spreadsheet_status()
        return self._google_sheets.status(
            job_id,
            document_id,
            expected_subject,
        ).payload()

    @staticmethod
    def _begin_mutation(session: Session) -> None:
        bind = session.get_bind()
        if bind.dialect.name == "sqlite":
            # The local application uses SQLite. Taking the write lock before
            # reading status/revision serializes edits and confirmation across
            # multiple browser tabs, so the confirmed snapshot cannot lag
            # behind the latest accepted correction.
            session.execute(text("BEGIN IMMEDIATE"))

    @staticmethod
    def _normalized_document_json(document: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_document_numbers(OcrDocument.model_validate(document))
        return normalized.model_dump(mode="json", by_alias=True)

    @staticmethod
    def _fail_spreadsheet_edit(
        session: Session,
        event: SpreadsheetEditEvent,
        code: str,
        message: str,
    ) -> SpreadsheetEditOutcome:
        event.status = "FAILED"
        event.error_code = code
        event.error_message = message
        event.processed_at = datetime.now(UTC)
        session.commit()
        return SpreadsheetEditOutcome(
            status="FAILED",
            revision=None,
            error_code=code,
            error_message=message,
        )

    @classmethod
    def _apply_sheet_value(
        cls,
        document: OcrDocument,
        *,
        field_id: str,
        record_id: str,
        value: str | None,
    ) -> tuple[OcrDocument, str]:
        if cls._SHEET_FIELD_PATH.fullmatch(field_id) is None:
            raise ValueError("unsupported field path")
        parts = field_id.split(".")
        expected_record_id = (
            ".".join(parts[:2]) if parts[0] in {"saleCalves", "calvings"} else "document"
        )
        if record_id != expected_record_id:
            raise ValueError("record and field do not match")
        payload = document.model_dump(mode="json", by_alias=True)
        container: object = payload
        for part in parts[:-1]:
            if isinstance(container, list):
                container = container[int(part)]
            elif isinstance(container, dict):
                container = container[part]
            else:
                raise TypeError("field path does not resolve")
        if not isinstance(container, dict):
            raise TypeError("field container is not an object")
        reading = container[parts[-1]]
        if not isinstance(reading, dict) or "sourceRegionId" not in reading:
            raise TypeError("field is not an OCR reading")
        normalized = reading.get("normalized")
        raw = reading.get("raw")
        previous = (
            normalized if isinstance(normalized, str) else raw if isinstance(raw, str) else ""
        )
        text_value = value if value is not None and value.strip() else None
        reading.update(
            {
                "raw": text_value,
                "normalized": text_value,
                "status": "readable" if text_value is not None else "blank",
                "reviewReason": None,
            }
        )
        return OcrDocument.model_validate(payload), previous

    def _persist_confirmed_snapshot(
        self,
        session: Session,
        *,
        repository: OcrResultRepository,
        job: ProcessingJob,
        stored: OcrResult,
        serialized: dict[str, Any],
        individual_id: str,
        document_name: str,
    ) -> None:
        json_relative = self._storage.write_text(
            f"exports/{job.id}/confirmed_result.json",
            json.dumps(serialized, ensure_ascii=False, indent=2),
        )
        self._storage.write_text(
            f"exports/{job.id}/confirmed_result.csv",
            self._to_csv(serialized),
        )
        confirmed_at = datetime.now(UTC)
        excel_content, _ = self._render_xlsx(
            session,
            job=job,
            document=serialized,
            individual_id=individual_id,
            confirmed_at=confirmed_at,
        )
        self._storage.write_bytes(
            f"exports/{job.id}/confirmed_result.xlsx",
            excel_content,
        )
        confirmed_result = repository.get_confirmed(job.id)
        if confirmed_result is None:
            confirmed_result = ConfirmedResult(
                job_id=job.id,
                ocr_result_revision=stored.revision,
                individual_id=individual_id,
                confirmed_json=serialized,
                confirmed_at=confirmed_at,
                export_relative_path=json_relative,
                spreadsheet_name=document_name,
            )
            session.add(confirmed_result)
            session.flush()
        else:
            confirmed_result.ocr_result_revision = stored.revision
            confirmed_result.individual_id = individual_id
            confirmed_result.confirmed_json = serialized
            confirmed_result.confirmed_at = confirmed_at
            confirmed_result.export_relative_path = json_relative
            confirmed_result.spreadsheet_name = document_name
            session.execute(
                delete(ConfirmedRecordField).where(
                    ConfirmedRecordField.confirmed_result_id == confirmed_result.id
                )
            )
            session.flush()
        self._index_record_fields(
            session,
            confirmed_result=confirmed_result,
            document=serialized,
            raw_structure=job.config_snapshot_json.get("recordStructure"),
        )
        job.status = JobStatus.CONFIRMED.value
        job.confirmed_at = confirmed_at

    def export_path(self, job_id: str, format_name: str) -> tuple[str, str, str]:
        if format_name not in {"json", "csv", "xlsx"}:
            raise AppError(
                status=422,
                code="VALIDATION_FAILED",
                title="Unsupported export format",
                detail="formatはjson、csv、xlsxのいずれかを指定してください。",
            )
        file_name = f"confirmed_result.{format_name}"
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            confirmed = OcrResultRepository(session).get_confirmed(job_id)
            if job is None or job.status != JobStatus.CONFIRMED.value or confirmed is None:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="Export is not available",
                    detail="確定済みのOCR結果だけをエクスポートできます。",
                )
            if format_name == "xlsx":
                content, file_name = self._render_xlsx(
                    session,
                    job=job,
                    document=confirmed.confirmed_json,
                    individual_id=confirmed.individual_id,
                    confirmed_at=confirmed.confirmed_at,
                )
                if confirmed.spreadsheet_name:
                    file_name = f"{confirmed.spreadsheet_name}.xlsx"
                relative = f"exports/{job_id}/confirmed_result.xlsx"
                self._storage.write_bytes(relative, content)
        relative = f"exports/{job_id}/confirmed_result.{format_name}"
        if not self._storage.exists(relative):
            raise self._not_found()
        media_type = {
            "json": "application/json",
            "csv": "text/csv",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }[format_name]
        return str(self._storage.resolve(relative)), media_type, file_name

    def _render_xlsx(
        self,
        session: Session,
        *,
        job: ProcessingJob,
        document: dict[str, Any],
        individual_id: str,
        confirmed_at: datetime,
    ) -> tuple[bytes, str]:
        filled_template = self._spreadsheet_template_output.render(
            session,
            job=job,
            document=document,
            confirmed_at=confirmed_at,
        )
        if filled_template is not None:
            return filled_template.content, filled_template.file_name
        table = self._excel.build_table(
            document,
            config_snapshot=job.config_snapshot_json,
            individual_id=individual_id,
            confirmed_at=confirmed_at,
        )
        return self._excel.render_xlsx(table), table.file_name

    def excel_table(self, job_id: str) -> dict[str, object]:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            confirmed = OcrResultRepository(session).get_confirmed(job_id)
            if job is None or job.status != JobStatus.CONFIRMED.value or confirmed is None:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="Excel export is not available",
                    detail="確定済みのOCR結果だけをExcelへコピーできます。",
                )
            table = self._excel.build_table(
                confirmed.confirmed_json,
                config_snapshot=job.config_snapshot_json,
                individual_id=confirmed.individual_id,
                confirmed_at=confirmed.confirmed_at,
            )
            payload = table.payload()
            if confirmed.spreadsheet_name:
                payload["fileName"] = f"{confirmed.spreadsheet_name}.xlsx"
            return payload

    def _response(
        self,
        stored: Any,
        *,
        job: ProcessingJob,
        confirmed: bool,
        individual_id: str | None = None,
        document_resolution: dict[str, Any] | None = None,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        snapshot_individual_id = job.config_snapshot_json.get("motherIndividualId")
        resolved_individual_id = individual_id or (
            snapshot_individual_id if isinstance(snapshot_individual_id, str) else None
        )
        resolution = document_resolution or self._document_resolution(job, stored)
        document_name = self._resolved_document_name(
            job,
            stored.current_corrected_json,
            resolution.get("documentId"),
        )
        return {
            "jobId": stored.job_id,
            "revision": stored.revision,
            "result": stored.current_corrected_json,
            "validation": stored.validation_json,
            "provider": stored.provider,
            "modelId": stored.model_id,
            "promptVersion": stored.prompt_version,
            "apiCallCount": stored.api_call_count,
            "inputTokens": stored.input_tokens,
            "outputTokens": stored.output_tokens,
            "estimatedCost": float(stored.estimated_cost)
            if stored.estimated_cost is not None
            else None,
            "confirmed": confirmed,
            "individualId": resolved_individual_id,
            "fieldPreviews": self._field_previews(
                job,
                stored.current_corrected_json,
            ),
            "recordStructure": self._record_structure(job),
            "reviewStructure": self._review_structure(job),
            "documentResolution": resolution,
            "documentName": document_name,
            "spreadsheetSync": self._spreadsheet_status_payload(
                job,
                confirmed=confirmed,
                document_id=resolution.get("documentId"),
                expected_subject=expected_subject,
            ),
        }

    def _resolved_document_name(
        self,
        job: ProcessingJob,
        document: dict[str, Any],
        document_id: object,
    ) -> str:
        with self._database.session() as session:
            ledger = (
                session.get(LedgerDocument, document_id)
                if isinstance(document_id, str)
                else None
            )
            if ledger is not None:
                resolved = ledger.document_name or ledger.library_name
                if resolved:
                    return resolved
            confirmed = OcrResultRepository(session).get_confirmed(job.id)
            if confirmed is not None and confirmed.spreadsheet_name:
                return confirmed.spreadsheet_name
        return self._documents.proposed_document_name(job, document)

    def _spreadsheet_status_payload(
        self,
        job: ProcessingJob,
        *,
        confirmed: bool,
        document_id: object,
        expected_subject: str | None = None,
    ) -> dict[str, object | None]:
        with self._database.session() as session:
            current_job = session.get(ProcessingJob, job.id) or job
            if self._is_spreadsheet_template(session, current_job):
                return self._spreadsheet_template_status(
                    session,
                    current_job,
                    confirmed=confirmed,
                    expected_subject=expected_subject,
                )
        if self._google_sheets is None:
            return self._disabled_spreadsheet_status()
        return self._google_sheets.status(
            job.id,
            document_id if isinstance(document_id, str) else None,
            expected_subject,
        ).payload()

    def _spreadsheet_template_status(
        self,
        session: Session,
        job: ProcessingJob,
        *,
        confirmed: bool,
        expected_subject: str | None = None,
    ) -> dict[str, object | None]:
        if self._google_drive is None:
            return self._disabled_spreadsheet_status()
        stored = OcrResultRepository(session).get_confirmed(job.id)
        if not confirmed:
            return {
                "enabled": True,
                "status": "ready",
                "spreadsheetUrl": None,
                "fileName": None,
                "lastError": None,
            }
        if (
            stored is not None
            and stored.spreadsheet_url
            and stored.spreadsheet_revision == stored.ocr_result_revision
        ):
            document_id = self._documents.document_id_for_job(session, job.id)
            ledger = (
                session.get(LedgerDocument, document_id)
                if document_id is not None
                else None
            )
            if expected_subject is not None and (
                ledger is None
                or ledger.drive_google_subject != expected_subject
            ):
                return {
                    "enabled": True,
                    "status": "failed",
                    "spreadsheetUrl": None,
                    "fileName": stored.spreadsheet_name,
                    "lastError": (
                        "この書類は現在接続中のGoogleアカウントに属していません。"
                    ),
                }
            return {
                "enabled": True,
                "status": "synced",
                "spreadsheetUrl": stored.spreadsheet_url,
                "fileName": stored.spreadsheet_name,
                "lastError": None,
            }
        if stored is not None and stored.spreadsheet_sync_status == "failed":
            return {
                "enabled": True,
                "status": "failed",
                "spreadsheetUrl": None,
                "fileName": stored.spreadsheet_name,
                "lastError": stored.spreadsheet_sync_error,
            }
        return {
            "enabled": True,
            "status": "failed",
            "spreadsheetUrl": None,
            "fileName": stored.spreadsheet_name if stored is not None else None,
            "lastError": "入力済みGoogleスプレッドシートがまだ作成されていません。",
        }

    def _publish_spreadsheet_template(
        self,
        job_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, object | None]:
        if self._google_drive is None:
            return self._disabled_spreadsheet_status()
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            confirmed = OcrResultRepository(session).get_confirmed(job_id)
            if job is None or confirmed is None or not self._is_spreadsheet_template(session, job):
                return self._disabled_spreadsheet_status()
            if (
                confirmed.spreadsheet_id
                and confirmed.spreadsheet_url
                and confirmed.spreadsheet_revision == confirmed.ocr_result_revision
            ):
                document_id = self._documents.document_id_for_job(session, job_id)
                if document_id is not None:
                    self._google_drive.document_target(
                        document_id,
                        expected_subject,
                    )
                    output_intent = session.get(
                        SpreadsheetOutputOperation,
                        f"{job_id}:{confirmed.ocr_result_revision}",
                    )
                    if (
                        output_intent is not None
                        and output_intent.drive_name == confirmed.spreadsheet_name
                    ):
                        return self._spreadsheet_template_status(
                            session,
                            job,
                            confirmed=True,
                            expected_subject=expected_subject,
                        )
                else:
                    return self._spreadsheet_template_status(
                        session,
                        job,
                        confirmed=True,
                        expected_subject=expected_subject,
                    )
            version = session.get(TemplateVersion, job.template_version_id)
            if version is None:
                return self._disabled_spreadsheet_status()
            filled = self._spreadsheet_template_output.render(
                session,
                job=job,
                document=confirmed.confirmed_json,
                confirmed_at=confirmed.confirmed_at,
            )
            template_id = version.template_id
            revision = confirmed.ocr_result_revision
            document_id = self._documents.document_id_for_job(session, job_id)
            if document_id is None:
                error = "出力先の台帳を確認できませんでした。"
                confirmed.spreadsheet_sync_status = "failed"
                confirmed.spreadsheet_sync_error = error
                session.commit()
                return {
                    "enabled": True,
                    "status": "failed",
                    "spreadsheetUrl": None,
                    "fileName": confirmed.spreadsheet_name,
                    "lastError": error,
                }
            generated_name = re.sub(
                r"\.xlsx$", "", filled.file_name, flags=re.IGNORECASE
            ).strip()[:180]
            spreadsheet_name = confirmed.spreadsheet_name or generated_name
            file_name = f"{spreadsheet_name}.xlsx"
            content = filled.content
        try:
            (
                spreadsheet_id,
                spreadsheet_url,
                spreadsheet_name,
            ) = self._google_drive.create_spreadsheet_from_xlsx(
                document_id=document_id,
                template_id=template_id,
                operation_id=f"{job_id}:{revision}",
                source_job_id=job_id,
                source_revision=revision,
                name=file_name,
                content=content,
                expected_subject=expected_subject,
            )
        except Exception as exc:
            error = (
                exc.detail
                if isinstance(exc, AppError)
                else "Googleスプレッドシートを作成できませんでした。"
            )
            with self._database.session() as session:
                self._begin_mutation(session)
                current = OcrResultRepository(session).get_confirmed(job_id)
                if (
                    current is not None
                    and current.ocr_result_revision == revision
                ):
                    current.spreadsheet_sync_status = "failed"
                    current.spreadsheet_sync_error = error
                    session.commit()
                else:
                    return self._spreadsheet_template_status(
                        session,
                        job,
                        confirmed=True,
                        expected_subject=expected_subject,
                    )
            return {
                "enabled": True,
                "status": "failed",
                "spreadsheetUrl": None,
                "fileName": None,
                "lastError": error,
            }
        with self._database.session() as session:
            self._begin_mutation(session)
            current = OcrResultRepository(session).get_confirmed(job_id)
            if current is None or current.ocr_result_revision != revision:
                return self._spreadsheet_template_status(
                    session,
                    job,
                    confirmed=True,
                    expected_subject=expected_subject,
                )
            ledger = session.get(LedgerDocument, document_id)
            persisted_name = spreadsheet_name
            if ledger is not None and ledger.spreadsheet_id == spreadsheet_id:
                persisted_name = (
                    ledger.document_name
                    or ledger.library_name
                    or spreadsheet_name
                )
            current.spreadsheet_id = spreadsheet_id
            current.spreadsheet_url = spreadsheet_url
            current.spreadsheet_name = persisted_name
            current.spreadsheet_revision = revision
            current.spreadsheet_sync_status = "synced"
            current.spreadsheet_sync_error = None
            session.commit()
        return {
            "enabled": True,
            "status": "synced",
            "spreadsheetUrl": spreadsheet_url,
            "fileName": persisted_name,
            "lastError": None,
        }

    @staticmethod
    def _normalize_spreadsheet_name(file_name: str) -> str:
        normalized = re.sub(r"\.xlsx$", "", file_name.strip(), flags=re.IGNORECASE).strip()
        if not normalized:
            raise AppError(
                status=422,
                code="OUTPUT_FILE_NAME_REQUIRED",
                title="Output file name is required",
                detail="出力ファイル名を入力してください。",
            )
        if len(normalized) > 180:
            raise AppError(
                status=422,
                code="OUTPUT_FILE_NAME_TOO_LONG",
                title="Output file name is too long",
                detail="出力ファイル名は180文字以内で入力してください。",
            )
        if re.search(r'[\\/:*?"<>|\x00-\x1f]', normalized):
            raise AppError(
                status=422,
                code="OUTPUT_FILE_NAME_INVALID",
                title="Output file name is invalid",
                detail='出力ファイル名に \\ / : * ? " < > | は使用できません。',
            )
        return normalized

    @staticmethod
    def _is_spreadsheet_template(session: Session, job: ProcessingJob) -> bool:
        if not job.template_version_id:
            return False
        version = session.get(TemplateVersion, job.template_version_id)
        return bool(version is not None and version.mode == TemplateMode.SPREADSHEET_TEMPLATE.value)

    @staticmethod
    def _disabled_spreadsheet_status() -> dict[str, object | None]:
        return {
            "enabled": False,
            "status": "disabled",
            "spreadsheetUrl": None,
            "fileName": None,
            "lastError": None,
        }

    def _document_resolution(self, job: ProcessingJob, stored: Any) -> dict[str, Any]:
        with self._database.session() as session:
            current_job = session.get(ProcessingJob, job.id) or job
            return self._documents.payload(
                session,
                job=current_job,
                document_json=stored.current_corrected_json,
            )

    @staticmethod
    def _may_sync_before_confirmation(resolution: dict[str, Any]) -> bool:
        return False

    @staticmethod
    def _field_previews(
        job: ProcessingJob,
        document: dict[str, Any],
    ) -> list[dict[str, object]]:
        snapshot = dict(job.config_snapshot_json)
        raw_mappings = snapshot.get("fieldMappings")
        raw_structure = snapshot.get("recordStructure")
        binding_by_mapping: dict[str, dict[str, object]] = {}
        if isinstance(raw_structure, dict):
            raw_bindings = raw_structure.get("fieldBindings")
            if isinstance(raw_bindings, list):
                binding_by_mapping = {
                    mapping_id: raw
                    for raw in raw_bindings
                    if isinstance(raw, dict)
                    and isinstance((mapping_id := raw.get("mappingId")), str)
                }
        previews: list[dict[str, object]] = []
        current_path_by_mapping: dict[str, str] = {}
        current_box_by_mapping: dict[str, dict[str, object] | None] = {}

        def collect_current_readings(value: object, path: str) -> None:
            if isinstance(value, list):
                for index, child in enumerate(value):
                    collect_current_readings(child, f"{path}.{index}")
                return
            if not isinstance(value, dict):
                return
            source_region_id = value.get("sourceRegionId")
            if "status" in value and isinstance(source_region_id, str):
                if source_region_id != "unmapped":
                    current_path_by_mapping[source_region_id] = path
                    box = value.get("boundingBox")
                    current_box_by_mapping[source_region_id] = (
                        box if isinstance(box, dict) else None
                    )
                return
            for key, child in value.items():
                collect_current_readings(child, f"{path}.{key}" if path else key)

        for root in (
            "document",
            "saleCalves",
            "pedigree",
            "calvings",
            "customFields",
        ):
            collect_current_readings(document.get(root), root)

        if isinstance(raw_mappings, list):
            for raw in raw_mappings:
                if not isinstance(raw, dict):
                    continue
                mapping_id = raw.get("mappingId")
                target_path = raw.get("targetPath")
                if not isinstance(mapping_id, str) or not isinstance(target_path, str):
                    continue
                binding = binding_by_mapping.get(mapping_id, {})
                current_box = current_box_by_mapping.get(mapping_id)
                previews.append(
                    {
                        # The reading's source cell is authoritative. This also
                        # remains correct if a collection row is moved or
                        # compacted after the template snapshot was created.
                        "targetPath": current_path_by_mapping.get(mapping_id, target_path),
                        "mappingId": mapping_id,
                        "artifactUrl": (
                            f"/api/v1/jobs/{job.id}/artifacts/fieldCrop.{mapping_id}.alignedColor"
                        ),
                        "boundingBox": current_box
                        if mapping_id in current_box_by_mapping
                        else {
                            "x": raw.get("x"),
                            "y": raw.get("y"),
                            "width": raw.get("width"),
                            "height": raw.get("height"),
                        },
                        "recordGroupId": binding.get("recordGroupId"),
                        "fieldKey": binding.get("fieldKey"),
                        "mappingType": raw.get("mappingType", "text"),
                        "markRecognitionMode": raw.get(
                            "markRecognitionMode", "automatic"
                        ),
                        "markSelectionMode": raw.get(
                            "markSelectionMode", "multiple"
                        ),
                        "markOptions": [
                            {
                                "label": option.get("label"),
                                "value": option.get("value"),
                            }
                            for option in raw.get("markOptions", [])
                            if isinstance(option, dict)
                            and isinstance(option.get("label"), str)
                            and isinstance(option.get("value"), str)
                        ],
                    }
                )
            if previews:
                return previews
        return OcrResultService._legacy_field_previews(job.id, document)

    @staticmethod
    def _record_structure(job: ProcessingJob) -> dict[str, object]:
        raw = job.config_snapshot_json.get("recordStructure")
        return (
            dict(raw)
            if isinstance(raw, dict)
            else {
                "layoutSchemaVersion": 3,
                "verified": False,
                "canonicalWidth": 0,
                "canonicalHeight": 0,
                "sections": [],
                "groups": [],
                "fieldBindings": [],
            }
        )

    def _review_structure(self, job: ProcessingJob) -> dict[str, object]:
        snapshot = job.config_snapshot_json
        raw = snapshot.get("reviewStructure")
        if isinstance(raw, dict):
            return dict(raw)
        # Old jobs do not contain a frozen review layout. Reading the current,
        # mutable template version here could regroup historical results using a
        # layout that did not exist when the job ran, so preserve their flat view.
        return build_review_display_structure(None, snapshot.get("fieldMappings"))

    @staticmethod
    def _configured_paths(job: ProcessingJob) -> set[str] | None:
        raw_mappings = job.config_snapshot_json.get("fieldMappings")
        if not isinstance(raw_mappings, list):
            return None
        paths = {
            target_path
            for raw in raw_mappings
            if isinstance(raw, dict) and isinstance((target_path := raw.get("targetPath")), str)
        }
        return paths

    @classmethod
    def _uses_cattle_schema(cls, session: Session, job: ProcessingJob) -> bool:
        version = (
            session.get(TemplateVersion, job.template_version_id)
            if job.template_version_id
            else None
        )
        template = session.get(Template, version.template_id) if version else None
        if template is not None:
            document_type = template.document_type.casefold()
            if "breeding" in document_type or document_type.startswith("cow-"):
                return True
            if document_type == "spreadsheet":
                return False
        configured_paths = cls._configured_paths(job)
        if configured_paths is None:
            return True
        return any(
            path == "document.unifiedEarTag"
            or path.startswith(("pedigree.", "saleCalves.", "calvings."))
            for path in configured_paths
        )

    @staticmethod
    def _index_record_fields(
        session: Any,
        *,
        confirmed_result: ConfirmedResult,
        document: dict[str, Any],
        raw_structure: object,
    ) -> None:
        section_titles = {
            "document": "基本情報",
            "pedigree": "血統",
            "saleCalves": "売却子牛一覧",
            "calvings": "繁殖履歴",
            "customFields": "追加項目",
        }
        group_keys: dict[tuple[str, int], str] = {}
        if isinstance(raw_structure, dict):
            raw_sections = raw_structure.get("sections")
            raw_groups = raw_structure.get("groups")
            collection_by_section: dict[object, str] = {}
            if isinstance(raw_sections, list):
                for section in raw_sections:
                    if not isinstance(section, dict):
                        continue
                    section_id = section.get("id")
                    collection = section.get("collectionPath")
                    title = section.get("title")
                    if isinstance(section_id, str) and isinstance(collection, str):
                        collection_by_section[section_id] = collection
                        if isinstance(title, str) and title.strip():
                            section_titles[collection] = title.strip()
            if isinstance(raw_groups, list):
                for group in raw_groups:
                    if not isinstance(group, dict):
                        continue
                    collection = collection_by_section.get(group.get("sectionId"))
                    order = group.get("order")
                    value = group.get("recordKeyValue")
                    if (
                        isinstance(collection, str)
                        and isinstance(order, int)
                        and isinstance(value, str)
                        and value.strip()
                    ):
                        group_keys[(collection, order)] = value.strip()
        def add_field(
            *,
            collection: str,
            record_order: int,
            record_key: str | None,
            field_key: str,
            reading: object,
        ) -> None:
            if not isinstance(reading, dict):
                return
            raw_value = reading.get("raw")
            normalized_value = reading.get("normalized")
            raw_text = raw_value if isinstance(raw_value, str) else None
            selected_text = normalized_value if isinstance(normalized_value, str) else raw_text
            if selected_text is None:
                return
            compact = selected_text.replace(",", "").strip()
            try:
                numeric_value = Decimal(compact)
            except InvalidOperation:
                numeric_value = None
            session.add(
                ConfirmedRecordField(
                    confirmed_result_id=confirmed_result.id,
                    section_key=collection,
                    section_title=section_titles[collection],
                    record_order=record_order,
                    record_key=record_key,
                    field_key=field_key[:100],
                    raw_value=raw_text,
                    normalized_value=unicodedata.normalize("NFKC", selected_text)
                    .strip()
                    .casefold(),
                    numeric_value=numeric_value,
                )
            )

        for collection in ("document", "pedigree", "customFields"):
            fields = document.get(collection)
            if not isinstance(fields, dict):
                continue
            for field_key, reading in fields.items():
                add_field(
                    collection=collection,
                    record_order=0,
                    record_key=None,
                    field_key=field_key,
                    reading=reading,
                )
        for collection in ("saleCalves", "calvings"):
            records = document.get(collection)
            if not isinstance(records, list):
                continue
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                record_key = group_keys.get((collection, index + 1))
                parity = record.get("parity")
                if record_key is None and isinstance(parity, dict):
                    key_value = parity.get("normalized") or parity.get("raw")
                    if isinstance(key_value, str) and key_value.strip():
                        record_key = key_value.strip()
                for field_key, reading in record.items():
                    add_field(
                        collection=collection,
                        record_order=index + 1,
                        record_key=record_key,
                        field_key=field_key,
                        reading=reading,
                    )

    @staticmethod
    def _legacy_field_previews(
        job_id: str,
        document: dict[str, Any],
    ) -> list[dict[str, object]]:
        previews: list[dict[str, object]] = []

        def visit(value: object, path: str) -> None:
            if not isinstance(value, dict):
                return
            source_region_id = value.get("sourceRegionId")
            box = value.get("boundingBox")
            if (
                "status" in value
                and isinstance(source_region_id, str)
                and source_region_id != "unmapped"
            ):
                previews.append(
                    {
                        "targetPath": path,
                        "mappingId": source_region_id,
                        "artifactUrl": (
                            f"/api/v1/jobs/{job_id}/artifacts/"
                            f"fieldCrop.{source_region_id}.alignedColor"
                        ),
                        "boundingBox": box if isinstance(box, dict) else None,
                        "recordGroupId": None,
                        "fieldKey": None,
                    }
                )
                return
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                if isinstance(child, list):
                    for index, item in enumerate(child):
                        visit(item, f"{child_path}.{index}")
                else:
                    visit(child, child_path)

        for root in ("document", "saleCalves", "pedigree", "calvings", "customFields"):
            value = document.get(root)
            if isinstance(value, list):
                for index, item in enumerate(value):
                    visit(item, f"{root}.{index}")
            else:
                visit(value, root)
        return previews

    @staticmethod
    def _to_csv(document: dict[str, Any]) -> str:
        configured = load_template_config().export.csv_columns
        bases = [path.removesuffix(".raw") for path in configured]
        columns = [f"{base}.{leaf}" for base in bases for leaf in ("raw", "normalized", "status")]
        row = {
            column: OcrResultService._csv_safe(OcrResultService._value_at(document, column))
            for column in columns
        }
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)
        return "\ufeff" + output.getvalue()

    @staticmethod
    def _value_at(document: dict[str, Any], dotted: str) -> object:
        value: object = document
        for key in dotted.split("."):
            if not isinstance(value, dict):
                return ""
            value = value.get(key, "")
        return "" if value is None else value

    @staticmethod
    def _csv_safe(value: object) -> object:
        if not isinstance(value, str):
            return value
        visible = value.lstrip(" \t\r\n")
        if visible.startswith(("=", "+", "-", "@")):
            return f"'{value}"
        return value

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            status=404,
            code="OCR_RESULT_NOT_FOUND",
            title="OCR result not found",
            detail="指定されたOCR結果が見つかりません。",
        )
