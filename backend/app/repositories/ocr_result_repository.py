from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import ConfirmedResult, OcrResult, OcrResultRevision
from app.domain.enums import RevisionSource
from app.ocr.base import OcrProviderResponse
from app.validation.business_validators import ValidationReport


class OcrResultRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_job(self, job_id: str) -> OcrResult | None:
        return self._session.scalar(select(OcrResult).where(OcrResult.job_id == job_id))

    def get_confirmed(self, job_id: str) -> ConfirmedResult | None:
        return self._session.scalar(
            select(ConfirmedResult).where(ConfirmedResult.job_id == job_id)
        )

    def list_confirmed_by_individual_id(
        self, individual_id: str
    ) -> list[ConfirmedResult]:
        return list(
            self._session.scalars(
                select(ConfirmedResult)
                .where(ConfirmedResult.individual_id == individual_id)
                .order_by(ConfirmedResult.confirmed_at.desc())
            )
        )

    def save_provider_result(
        self,
        *,
        job_id: str,
        response: OcrProviderResponse,
        report: ValidationReport,
    ) -> OcrResult:
        candidate = response.document.model_dump(mode="json", by_alias=True)
        validation = report.to_dict()
        result = self.get_by_job(job_id)
        if result is None:
            result = OcrResult(
                job_id=job_id,
                schema_version=response.document.schema_version,
                model_id=response.model_id,
                prompt_version=response.prompt_version,
                provider=response.provider,
                raw_candidate_json=candidate,
                current_corrected_json=candidate,
                validation_json=validation,
                revision=1,
                api_call_count=response.api_call_count,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                estimated_cost=response.estimated_cost,
            )
            self._session.add(result)
            self._session.flush()
            source = RevisionSource.OCR
        else:
            result.revision += 1
            result.schema_version = response.document.schema_version
            result.model_id = response.model_id
            result.prompt_version = response.prompt_version
            result.provider = response.provider
            result.raw_candidate_json = candidate
            result.current_corrected_json = candidate
            result.validation_json = validation
            result.api_call_count += response.api_call_count
            result.input_tokens = response.input_tokens
            result.output_tokens = response.output_tokens
            result.estimated_cost = response.estimated_cost
            source = RevisionSource.REPROCESS
        self._session.add(
            OcrResultRevision(
                ocr_result_id=result.id,
                revision=result.revision,
                source=source.value,
                result_json=candidate,
                validation_json=validation,
            )
        )
        self._session.commit()
        self._session.refresh(result)
        return result

    def save_correction(
        self,
        *,
        result: OcrResult,
        base_revision: int,
        candidate: dict[str, object],
        report: ValidationReport,
    ) -> OcrResult | None:
        next_revision = base_revision + 1
        validation = report.to_dict()
        changed = self._session.execute(
            update(OcrResult)
            .where(OcrResult.id == result.id, OcrResult.revision == base_revision)
            .values(
                current_corrected_json=candidate,
                validation_json=validation,
                revision=next_revision,
            )
        )
        if changed.rowcount != 1:
            self._session.rollback()
            return None
        self._session.add(
            OcrResultRevision(
                ocr_result_id=result.id,
                revision=next_revision,
                source=RevisionSource.USER_EDIT.value,
                result_json=candidate,
                validation_json=validation,
            )
        )
        self._session.commit()
        return self.get_by_job(result.job_id)
