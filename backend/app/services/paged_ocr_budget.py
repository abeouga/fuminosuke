"""Document-wide, non-refundable reservations outside OCR computation."""

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import PagedOcrBudget, PagedOcrCall, ProcessingJob
from app.db.session import Database
from app.domain.print_layout import print_error
from app.services.print_layout_service import digest
from app.services.template_mutation import begin_immediate

ADAPTER = "paged-ocr-job-budget-v1"


def budget_policy(max_calls: int) -> dict[str, Any]:
    return {"adapter": ADAPTER, "scope": "job", "maxCalls": max_calls}


def _limit(job: ProcessingJob) -> int:
    bundle = job.config_snapshot_json.get("jobPages", {})
    policy = bundle.get("ocrCallBudget")
    maximum = policy.get("maxCalls") if isinstance(policy, dict) else None
    if (
        type(maximum) is not int
        or maximum < 1
        or policy != budget_policy(maximum)
        or digest(bundle) != job.config_snapshot_json.get("jobPagesSha256")
        or maximum != bundle.get("executionConfig", {}).get("ocr", {}).get("max_calls_per_job")
    ):
        raise print_error(
            "受付時のOCR呼び出し予算を検証できません。", "PAGE_OCR_BUDGET_REQUIRED", 409
        )
    return maximum


def initialize_budget(session: Session, job: ProcessingJob) -> None:
    """Only in the new-acceptance transaction, never on execution or retry."""
    session.add(
        PagedOcrBudget(
            job_id=job.id,
            snapshot_sha256=job.config_snapshot_json["jobPagesSha256"],
            max_calls=_limit(job),
            used_calls=0,
        )
    )


class PagedCallBudget:
    def __init__(
        self,
        database: Database,
        job_id: str,
        page_id: str,
        attempt: int,
        snapshot_sha: str,
        protected_calls: int = 0,
    ):
        if type(protected_calls) is not int or protected_calls < 0:
            raise ValueError("Invalid protected page budget")
        self.protected_calls = protected_calls
        self.database = database
        self.job_id, self.page_id, self.attempt, self.snapshot_sha = (
            job_id,
            page_id,
            attempt,
            snapshot_sha,
        )

    def _load(self, session: Session) -> PagedOcrBudget:
        job = session.get(ProcessingJob, self.job_id)
        if job is None:
            raise print_error("OCRジョブがありません。", "JOB_NOT_FOUND", 404)
        if job.cancel_requested:
            raise print_error("読み取りは取り消されました。", "JOB_CANCELLED", 409)
        if job.attempt_number != self.attempt or job.status != "PROCESSING":
            raise print_error("実行中のOCR試行と一致しません。", "PAGE_OCR_ATTEMPT_CHANGED", 409)
        maximum = _limit(job)
        bundle = job.config_snapshot_json["jobPages"]
        if self.snapshot_sha != job.config_snapshot_json["jobPagesSha256"] or not any(
            p["pageId"] == self.page_id
            and p.get("ocrProvider") == "openai"
            and p.get("ocrEnabled") is True
            for p in bundle["pages"]
        ):
            raise print_error(
                "OCR予算のページ所有権が一致しません。", "PAGE_OCR_BUDGET_INVALID", 409
            )
        budget = session.get(PagedOcrBudget, self.job_id)
        count, last = session.execute(
            select(func.count(), func.max(PagedOcrCall.call_number)).where(
                PagedOcrCall.job_id == self.job_id
            )
        ).one()
        if (
            budget is None
            or budget.snapshot_sha256 != self.snapshot_sha
            or budget.max_calls != maximum
            or not 0 <= budget.used_calls <= maximum
            or count != budget.used_calls
            or (last or 0) != budget.used_calls
        ):
            raise print_error("保存済みOCR予算が一致しません。", "PAGE_OCR_BUDGET_INVALID", 409)
        return budget

    def remaining(self) -> int:
        with self.database.session() as session:
            # One consistent WAL snapshot across counter/ledger reads.
            session.execute(text("BEGIN"))
            budget = self._load(session)
            return max(0, budget.max_calls - budget.used_calls - self.protected_calls)

    def reserve(self) -> int | None:
        with self.database.session() as session:
            begin_immediate(session)
            budget = self._load(session)
            if budget.used_calls >= budget.max_calls - self.protected_calls:
                return None
            budget.used_calls += 1
            ordinal = budget.used_calls
            session.add(
                PagedOcrCall(
                    job_id=self.job_id,
                    call_number=ordinal,
                    page_id=self.page_id,
                    attempt_number=self.attempt,
                )
            )
            session.commit()  # No provider send may precede this commit.
            return ordinal
