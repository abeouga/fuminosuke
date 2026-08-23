from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import OutputLayoutTemplateRevision, ProcessingJob, TemplateVersion
from app.db.session import Database
from app.services.template_mutation import begin_immediate, template_mutation_lock


class OutputLayoutTemplateService:
    """Keeps imported Google Sheets layouts versioned in the local database."""

    def __init__(
        self,
        database: Database,
        owner_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._database = database
        self._owner_provider = owner_provider

    def latest_layout(self, template_version_id: str | None) -> dict[str, Any] | None:
        if not template_version_id:
            return None
        with self._database.session() as session:
            row = self._latest(session, self._owner_key(), template_version_id)
            return dict(row.layout_json) if row is not None else None

    def status_for_job(self, job_id: str) -> dict[str, object | None]:
        with self._database.session() as session:
            job = self._require_job(session, job_id)
            if not job.template_version_id:
                raise self._missing_template_error(job_id)
            row = self._latest(session, self._owner_key(), job.template_version_id)
            return self._payload(row, job.template_version_id)

    def save_for_job(
        self,
        job_id: str,
        layout: dict[str, Any],
    ) -> dict[str, object | None]:
        self._validate_layout(layout)
        owner_key = self._owner_key()
        with template_mutation_lock(), self._database.session() as session:
            begin_immediate(session)
            job = self._require_job(session, job_id)
            if not job.template_version_id:
                raise self._missing_template_error(job_id)
            source_template_version_id = layout.get("sourceTemplateVersionId")
            if source_template_version_id != job.template_version_id:
                raise AppError(
                    status=409,
                    code="OUTPUT_LAYOUT_TEMPLATE_MISMATCH",
                    title="Output layout template does not match",
                    detail=(
                        "Googleスプレッドシートの帳票レイアウトは別のテンプレートから"
                        "出力されています。このOCR結果を再同期してから取り込んでください。"
                    ),
                    job_id=job_id,
                )
            template_version = session.get(TemplateVersion, job.template_version_id)
            if template_version is None:
                raise self._missing_template_error(job_id)
            next_revision = (
                session.scalar(
                    select(func.max(OutputLayoutTemplateRevision.revision)).where(
                        OutputLayoutTemplateRevision.owner_key == owner_key,
                        OutputLayoutTemplateRevision.template_version_id == job.template_version_id,
                    )
                )
                or 0
            ) + 1
            row = OutputLayoutTemplateRevision(
                owner_key=owner_key,
                template_version_id=job.template_version_id,
                revision=next_revision,
                layout_json=layout,
                source_sheet_name=str(layout.get("sheetName") or "帳票レイアウト"),
                source_job_id=job_id,
            )
            session.add(row)
            session.commit()
            return self._payload(row, job.template_version_id)

    def _owner_key(self) -> str:
        if self._owner_provider is None:
            return "local"
        return self._owner_provider() or "local"

    @staticmethod
    def _latest(
        session: Session,
        owner_key: str,
        template_version_id: str,
    ) -> OutputLayoutTemplateRevision | None:
        return session.scalar(
            select(OutputLayoutTemplateRevision)
            .where(
                OutputLayoutTemplateRevision.owner_key == owner_key,
                OutputLayoutTemplateRevision.template_version_id == template_version_id,
            )
            .order_by(OutputLayoutTemplateRevision.revision.desc())
            .limit(1)
        )

    @staticmethod
    def _require_job(session: Session, job_id: str) -> ProcessingJob:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            raise AppError(
                status=404,
                code="OCR_RESULT_NOT_FOUND",
                title="OCR result not found",
                detail="対象のOCR結果が見つかりません。",
                job_id=job_id,
            )
        return job

    @staticmethod
    def _validate_layout(layout: dict[str, Any]) -> None:
        if layout.get("schemaVersion") != 1:
            raise AppError(
                status=422,
                code="OUTPUT_LAYOUT_SCHEMA_INVALID",
                title="Output layout schema is invalid",
                detail="取り込んだ帳票レイアウトの形式が不正です。",
            )
        binding_count = layout.get("bindingCount")
        if not isinstance(binding_count, int) or binding_count < 1:
            raise AppError(
                status=409,
                code="OUTPUT_LAYOUT_BINDING_MISSING",
                title="Output layout bindings are missing",
                detail=(
                    "読み取り項目との対応情報がありません。アプリから帳票レイアウトを"
                    "再出力してから、セルの書式と配置を編集してください。"
                ),
            )

    @staticmethod
    def _missing_template_error(job_id: str) -> AppError:
        return AppError(
            status=409,
            code="OUTPUT_LAYOUT_TEMPLATE_VERSION_REQUIRED",
            title="Template version is required",
            detail="このOCR結果には元のテンプレート版が紐づいていません。",
            job_id=job_id,
        )

    @staticmethod
    def _payload(
        row: OutputLayoutTemplateRevision | None,
        template_version_id: str,
    ) -> dict[str, object | None]:
        if row is None:
            return {
                "active": False,
                "templateVersionId": template_version_id,
                "revision": None,
                "sheetName": "帳票レイアウト",
                "bindingCount": 0,
                "sourceJobId": None,
                "createdAt": None,
            }
        binding_count = row.layout_json.get("bindingCount", 0)
        return {
            "active": True,
            "templateVersionId": template_version_id,
            "revision": row.revision,
            "sheetName": row.source_sheet_name,
            "bindingCount": binding_count if isinstance(binding_count, int) else 0,
            "sourceJobId": row.source_job_id,
            "createdAt": row.created_at.isoformat(),
        }
