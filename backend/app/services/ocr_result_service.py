from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import ConfirmedResult, ProcessingJob
from app.db.session import Database, begin_write
from app.domain.enums import JobStatus, JobType, RevisionSource
from app.domain.field_targets import field_key
from app.domain.ocr_models import OcrDocument, ReadingStatus
from app.repositories.job_repository import JobRepository
from app.repositories.ocr_result_repository import OcrResultRepository
from app.services.document_series import DocumentSeries
from app.services.excel_export_service import ExcelExportService
from app.services.ocr_result_preview import _field_previews
from app.services.ocr_result_types import CorrectionValues as CorrectionValues
from app.services.spreadsheet_template_output_service import SpreadsheetTemplateOutputService
from app.services.template_item_join_service import item_joins
from app.storage.local_storage import LocalStorageService
from app.validation.business_validators import DeterministicValidator, ValidationReport


class OcrResultService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage
        self._validator = DeterministicValidator()
        self._excel = ExcelExportService()
        self._spreadsheet_template_output = SpreadsheetTemplateOutputService(storage)

    @staticmethod
    def _error(code: str, detail: str, status: int = 409) -> AppError:
        return AppError(status=status, code=code, title=code, detail=detail)

    def _load(self, session: Session, job_id: str):
        job = JobRepository(session).get(job_id)
        if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
            raise self._error("RESULT_NOT_FOUND", "指定された読み取り結果が見つかりません。", 404)
        if "jobPages" in job.config_snapshot_json:
            raise self._error("MULTI_PAGE_REVIEW_REQUIRED", "ページ別レビューを使用してください。")
        stored = OcrResultRepository(session).get_by_job(job_id)
        if stored is None:
            raise self._error("RESULT_NOT_FOUND", "指定された読み取り結果が見つかりません。", 404)
        if job.status not in {JobStatus.REVIEW_REQUIRED.value, JobStatus.CONFIRMED.value}:
            raise self._error("RESULT_NOT_READY", "読み取り結果は準備完了後に取得できます。")
        OcrDocument.model_validate(stored.current_corrected_json)
        return job, stored

    @staticmethod
    def _begin_mutation(session: Session) -> None:
        begin_write(session)

    def _response(self, session: Session, job: ProcessingJob, stored) -> dict[str, Any]:
        snapshot = job.config_snapshot_json
        if not isinstance(snapshot.get("reviewStructure"), dict) or not isinstance(
            snapshot.get("recordStructure"), dict
        ):
            raise self._error("RESULT_SNAPSHOT_INVALID", "受付時の表示構造がありません。")
        confirmed = OcrResultRepository(session).get_confirmed(job.id)
        return {
            "jobId": job.id,
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
            "confirmed": job.status == JobStatus.CONFIRMED.value,
            "fieldPreviews": _field_previews(job, stored.current_corrected_json),
            "recordStructure": snapshot["recordStructure"],
            "reviewStructure": snapshot["reviewStructure"],
            # The user types the name on the review screen; nothing is filled in automatically.
            "documentName": confirmed.document_name
            if confirmed and confirmed.document_name
            else "",
            "series": DocumentSeries(session).review_info(job),
        }

    @staticmethod
    def _proposed_name(session: Session, job: ProcessingJob, document: dict[str, Any]) -> str:
        from app.services.template_document_name_service import document_name_rule

        config = job.config_snapshot_json.get("templateSnapshot", {}).get("config", {})
        # A rule changed after the template was made wins over the one in the snapshot.
        spec = (
            document_name_rule(session, job.template_version_id)
            or config.get("documentName")
            or {}
        )
        values = []
        for path in spec.get("fieldPaths", []):
            key = field_key(path)
            reading = document.get("fields", {}).get(key, {})
            value = reading.get("normalized") or reading.get("raw")
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
        if values:
            return (
                str(spec.get("prefix", ""))
                + str(spec.get("separator", " ")).join(values)
                + str(spec.get("suffix", ""))
            )
        return str(config.get("name") or "読み取り結果")

    def get(self, job_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            job, stored = self._load(session, job_id)
            return self._response(session, job, stored)

    def correct(self, job_id: str, values: CorrectionValues) -> dict[str, Any]:
        with self._database.transaction() as session:
            return self.correct_in_transaction(session, job_id, values)

    def correct_in_transaction(
        self, session: Session, job_id: str, values: CorrectionValues,
        *, source: RevisionSource = RevisionSource.USER_EDIT,
    ) -> dict[str, Any]:
        """Validate and save inside the caller's transaction; never commit or roll it back."""
        job, stored = self._load(session, job_id)
        if job.status != JobStatus.REVIEW_REQUIRED.value:
            raise self._error(
                "INVALID_STATE_TRANSITION", "確定済みです。再編集を開始してください。"
            )
        if stored.revision != values.base_revision:
            raise self._error(
                "REVISION_CONFLICT", "別の修正が保存されています。最新結果を取得してください。"
            )
        current = OcrDocument.model_validate(stored.current_corrected_json)
        if set(current.fields) != set(values.fields):
            raise self._error(
                "FIELD_BINDING_CHANGED", "補正で読み取り欄を追加・削除できません。"
            )
        for key, reading in values.fields.items():
            before = current.fields[key]
            if (
                reading.source_region_id != before.source_region_id
                or reading.bounding_box != before.bounding_box
            ):
                raise self._error(
                    "FIELD_BINDING_CHANGED", "補正で切り出し画像との対応を変更できません。"
                )
        candidate = current.model_copy(
            update={"fields": values.fields, "review_items": values.review_items}, deep=True
        )
        from app.domain.checklist import apply_write_ins

        apply_write_ins(
            candidate,
            job.config_snapshot_json.get("templateSnapshot", {}).get("config", {}).get(
                "fieldMappings", []
            ),
        )
        # Derived views must follow the saved correction, never a mutable current template.
        from app.domain.generic_data import build_generic_data, build_generic_values

        candidate.generic_data = build_generic_data(
            candidate, job.config_snapshot_json.get("genericTemplateDefinition")
        )
        candidate.generic_values = build_generic_values(candidate.generic_data)
        image_issues = [
            issue
            for issue in stored.validation_json.get("issues", [])
            if issue.get("code") in self._validator.DETERMINISTIC_REVIEW_CODES
        ]
        from app.validation.business_validators import ValidationIssue

        report = ValidationReport(
            not any(i.get("severity") == "error" for i in image_issues),
            [ValidationIssue(**issue) for issue in image_issues],
        )
        candidate = self._validator.merge_review_items(candidate, report)
        updated = OcrResultRepository(session).save_correction(
            result=stored,
            base_revision=values.base_revision,
            candidate=candidate.model_dump(mode="json", by_alias=True),
            report=report,
            source=source,
        )
        if updated is None:
            raise self._error("REVISION_CONFLICT", "最新結果を取得してください。")
        return self._response(session, job, updated)

    def reopen(self, job_id: str, *, base_revision: int) -> dict[str, Any]:
        with self._database.session() as session:
            self._begin_mutation(session)
            job, stored = self._load(session, job_id)
            if job.status != JobStatus.CONFIRMED.value:
                raise self._error("INVALID_STATE_TRANSITION", "確定済みの結果だけ再編集できます。")
            if stored.revision != base_revision:
                raise self._error("REVISION_CONFLICT", "最新結果を取得してください。")
            confirmed = OcrResultRepository(session).get_confirmed(job.id)
            if confirmed is None or confirmed.ocr_result_revision != base_revision:
                raise self._error("CONFIRMED_SNAPSHOT_INVALID", "確定結果の世代が一致しません。")
            DocumentSeries(session).require_no_successor(job, "再編集")
            self._advance_lifecycle(session, stored, RevisionSource.REOPEN)
            job.status = JobStatus.REVIEW_REQUIRED.value
            job.confirmed_at = None
            session.commit()
            return self._response(session, job, stored)

    def confirm(
        self,
        job_id: str,
        *,
        base_revision: int,
        document_name: str | None = None,
        accept_identity_mismatch: bool = False,
    ) -> dict[str, Any]:
        with self._database.session() as session:
            self._begin_mutation(session)
            job, stored = self._load(session, job_id)
            if job.status != JobStatus.REVIEW_REQUIRED.value:
                raise self._error("INVALID_STATE_TRANSITION", "この結果は確定できません。")
            if stored.revision != base_revision:
                raise self._error(
                    "REVISION_CONFLICT", "別の修正が保存されています。最新結果を取得してください。"
                )
            document = OcrDocument.model_validate(stored.current_corrected_json)
            if not stored.validation_json.get("valid", False) or any(
                i.severity == "error" for i in document.review_items
            ):
                raise self._error(
                    "UNRESOLVED_REVIEW_ITEMS", "エラー項目を解消してから確定してください。"
                )
            config = job.config_snapshot_json.get("templateSnapshot", {}).get("config", {})
            from app.domain.checklist import require_resolved_checklists
            require_resolved_checklists(document, config.get("fieldMappings", []))
            for hint in config.get("documentIdentityFields", []):
                reading = document.fields.get(field_key(hint["targetPath"]))
                value = (reading.normalized or reading.raw) if reading else None
                if reading is None or reading.status != ReadingStatus.READABLE or not value:
                    raise self._error(
                        "DOCUMENT_IDENTITY_REQUIRED", f"{hint['label']}を確認して入力してください。"
                    )
                if hint.get("valueType") == "digits":
                    compact = unicodedata.normalize("NFKC", value)
                    if not re.fullmatch(r"[0-9]+", compact) or (
                        hint.get("exactLength") and len(compact) != hint["exactLength"]
                    ):
                        raise self._error(
                            "DOCUMENT_IDENTITY_FORMAT_INVALID",
                            f"{hint['label']}の形式を確認してください。",
                        )
            name = (
                re.sub(
                    r'[\\/:*?"<>|\x00-\x1f]',
                    "_",
                    document_name
                    or self._proposed_name(session, job, stored.current_corrected_json),
                )
                .strip()
                .rstrip(".")
            )
            if not name or len(name) > 180:
                raise self._error(
                    "DOCUMENT_NAME_INVALID", "書類名を1〜180文字で入力してください。", 422
                )
            DocumentSeries(session).require_predecessor_unchanged(job)
            mismatched = DocumentSeries.identity_mismatches(job, stored.current_corrected_json)
            if mismatched and not accept_identity_mismatch:
                # An update whose identity differs must be continued explicitly by the user.
                raise self._error(
                    "SERIES_IDENTITY_MISMATCH",
                    f"前の版と{'・'.join(mismatched)}が一致しません。"
                    "同じ書類の更新として続けるか確認してください。",
                )
            now = datetime.now(timezone.utc)
            # Prepare output before committing; failed generation leaves the result editable.
            content, file_name = self._render_xlsx(
                session, job, stored.current_corrected_json, name, now
            )
            self._advance_lifecycle(session, stored, RevisionSource.CONFIRM)
            relative = f"exports/{job.id}/confirmed-r{stored.revision}.xlsx"
            self._storage.write_bytes(relative, content)
            confirmed = OcrResultRepository(session).get_confirmed(job.id)
            if confirmed is None:
                confirmed = ConfirmedResult(job_id=job.id)
                session.add(confirmed)
            confirmed.ocr_result_revision = stored.revision
            confirmed.confirmed_json = stored.current_corrected_json
            confirmed.confirmed_at = now
            confirmed.export_relative_path = relative
            confirmed.document_name = name
            from app.db.mutation_models import OcrConfirmationRevision
            session.add(OcrConfirmationRevision(
                job_id=job.id, revision=stored.revision,
                confirmed_json=stored.current_corrected_json, confirmed_at=now,
                document_name=name, export_relative_path=relative,
            ))
            job.status = JobStatus.CONFIRMED.value
            job.confirmed_at = now
            session.commit()
            return self._response(session, job, stored)

    def _advance_lifecycle(self, session: Session, stored, source: RevisionSource) -> None:
        OcrResultRepository(session).advance_revision(stored, source)

    def _render_xlsx(
        self,
        session: Session,
        job: ProcessingJob,
        document: dict[str, Any],
        name: str,
        when: datetime,
    ):
        filled = self._spreadsheet_template_output.render(
            session, job=job, document=document, document_name=name, confirmed_at=when
        )
        if filled is not None:
            return filled.content, filled.file_name
        table = self._excel.build_table(
            document,
            config_snapshot=job.config_snapshot_json,
            document_name=name,
            confirmed_at=when,
            joins=item_joins(session, (job.config_snapshot_json or {}).get("templateId")),
        )
        return self._excel.render_xlsx(table), table.file_name

    def export_path(self, job_id: str, format: str = "xlsx"):
        if format != "xlsx":
            raise self._error("EXPORT_FORMAT_UNSUPPORTED", "XLSX形式を指定してください。", 422)
        with self._database.session() as session:
            job, stored = self._load(session, job_id)
            confirmed = OcrResultRepository(session).get_confirmed(job.id)
            if (
                job.status != JobStatus.CONFIRMED.value
                or confirmed is None
                or confirmed.ocr_result_revision != stored.revision
            ):
                raise self._error("INVALID_STATE_TRANSITION", "確定済みの結果だけ取得できます。")
            content, file_name = self._render_xlsx(
                session,
                job,
                confirmed.confirmed_json,
                confirmed.document_name or "読み取り結果",
                confirmed.confirmed_at,
            )
            if not self._storage.requires_immutable_paths or not self._storage.exists(
                confirmed.export_relative_path
            ):
                self._storage.write_bytes(confirmed.export_relative_path, content)
            return (
                self._storage.resolve(confirmed.export_relative_path),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                file_name,
            )

    def excel_table(self, job_id: str) -> dict[str, object]:
        with self._database.session() as session:
            job, stored = self._load(session, job_id)
            confirmed = OcrResultRepository(session).get_confirmed(job.id)
            if job.status != JobStatus.CONFIRMED.value or confirmed is None:
                raise self._error("INVALID_STATE_TRANSITION", "確定済みの結果だけ取得できます。")
            return self._excel.build_table(
                confirmed.confirmed_json,
                config_snapshot=job.config_snapshot_json,
                document_name=confirmed.document_name,
                confirmed_at=confirmed.confirmed_at,
                joins=item_joins(session, (job.config_snapshot_json or {}).get("templateId")),
            ).payload()
