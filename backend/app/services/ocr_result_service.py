from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.errors import AppError
from app.db.models import ConfirmedResult
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.domain.individual_id import individual_id_from_document
from app.domain.ocr_models import (
    Calving,
    DocumentIdentity,
    OcrDocument,
    Pedigree,
    ReviewItem,
    SaleCalf,
)
from app.domain.template_config import load_template_config
from app.repositories.job_repository import JobRepository
from app.repositories.ocr_result_repository import OcrResultRepository
from app.storage.local_storage import LocalStorageService
from app.validation.business_validators import DeterministicValidator


@dataclass(frozen=True, slots=True)
class CorrectionValues:
    base_revision: int
    document: DocumentIdentity
    sale_calves: list[SaleCalf]
    pedigree: Pedigree
    calvings: list[Calving]
    review_items: list[ReviewItem]


class OcrResultService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage
        self._validator = DeterministicValidator()

    def get(self, job_id: str) -> dict[str, Any]:
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
                confirmed=is_confirmed,
                individual_id=confirmed.individual_id if confirmed else None,
            )

    def correct(self, job_id: str, values: CorrectionValues) -> dict[str, Any]:
        with self._database.session() as session:
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
                    "review_items": values.review_items,
                },
                deep=True,
            )
            candidate = OcrDocument.model_validate(
                candidate.model_dump(mode="json", by_alias=True)
            )
            report = self._validator.validate(candidate)
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
            return self._response(updated, confirmed=False)

    def confirm(self, job_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            jobs = JobRepository(session)
            job = jobs.get(job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise self._not_found()
            repository = OcrResultRepository(session)
            if job.status != JobStatus.REVIEW_REQUIRED.value or repository.get_confirmed(
                job_id
            ):
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="OCR result cannot be confirmed",
                    detail="このOCR結果は確定できません。",
                )
            stored = repository.get_by_job(job_id)
            if stored is None:
                raise self._not_found()
            document = OcrDocument.model_validate(stored.current_corrected_json)
            report = self._validator.validate(document)
            has_unresolved_error = any(
                item.severity == "error" for item in document.review_items
            )
            if not report.valid or has_unresolved_error:
                raise AppError(
                    status=409,
                    code="UNRESOLVED_REVIEW_ITEMS",
                    title="OCR result has unresolved errors",
                    detail="errorレベルのレビュー項目を解消してから確定してください。",
                )
            try:
                individual_id = individual_id_from_document(document)
            except ValueError as exc:
                raise AppError(
                    status=409,
                    code="INDIVIDUAL_ID_REQUIRED",
                    title="Individual identification number is required",
                    detail="個体識別番号を10桁の数字で確定してから保存してください。",
                ) from exc
            serialized = document.model_dump(mode="json", by_alias=True)
            json_relative = self._storage.write_text(
                f"exports/{job_id}/confirmed_result.json",
                json.dumps(serialized, ensure_ascii=False, indent=2),
            )
            self._storage.write_text(
                f"exports/{job_id}/confirmed_result.csv", self._to_csv(serialized)
            )
            confirmed_at = datetime.now(UTC)
            session.add(
                ConfirmedResult(
                    job_id=job_id,
                    ocr_result_revision=stored.revision,
                    individual_id=individual_id,
                    confirmed_json=serialized,
                    confirmed_at=confirmed_at,
                    export_relative_path=json_relative,
                )
            )
            job.status = JobStatus.CONFIRMED.value
            job.confirmed_at = confirmed_at
            session.commit()
            return self._response(stored, confirmed=True, individual_id=individual_id)

    def export_path(self, job_id: str, format_name: str) -> tuple[str, str, str]:
        if format_name not in {"json", "csv"}:
            raise AppError(
                status=422,
                code="VALIDATION_FAILED",
                title="Unsupported export format",
                detail="formatはjsonまたはcsvを指定してください。",
            )
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
        relative = f"exports/{job_id}/confirmed_result.{format_name}"
        if not self._storage.exists(relative):
            raise self._not_found()
        media_type = "application/json" if format_name == "json" else "text/csv"
        return str(self._storage.resolve(relative)), media_type, f"confirmed_result.{format_name}"

    @staticmethod
    def _response(
        stored: Any, *, confirmed: bool, individual_id: str | None = None
    ) -> dict[str, Any]:
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
            "individualId": individual_id,
        }

    @staticmethod
    def _to_csv(document: dict[str, Any]) -> str:
        configured = load_template_config().export.csv_columns
        bases = [path.removesuffix(".raw") for path in configured]
        columns = [f"{base}.{leaf}" for base in bases for leaf in ("raw", "normalized", "status")]
        row = {column: OcrResultService._value_at(document, column) for column in columns}
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
    def _not_found() -> AppError:
        return AppError(
            status=404,
            code="OCR_RESULT_NOT_FOUND",
            title="OCR result not found",
            detail="指定されたOCR結果が見つかりません。",
        )
