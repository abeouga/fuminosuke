"""Page orchestration over the existing extractor/request/provider/finalizer."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob
from app.domain.print_layout import print_error
from app.image_processing.artifacts import ArtifactWriter
from app.ocr.openai_provider import OpenAIOcrProvider
from app.services.extraction_service import ImageExtractionService
from app.services.job_page_service import JobPageService
from app.services.ocr_workflow_service import OcrWorkflowService
from app.services.paged_ocr_budget import PagedCallBudget
from app.services.paged_review_service import PagedReviewService
from app.services.print_layout_service import digest

logger = logging.getLogger(__name__)


class PagedOcrExecution:
    def __init__(
        self,
        pages: JobPageService,
        extraction: ImageExtractionService,
        workflow: OcrWorkflowService,
    ):
        self.pages, self.extraction, self.workflow = pages, extraction, workflow

    def execute(self, job_id: str) -> None:
        service = self.pages
        writer = ArtifactWriter(service.database, service.storage)
        with service.database.session() as session:
            job = session.get(ProcessingJob, job_id)
            if job is None or job.cancel_requested:
                return
            pages = service.validate(session, job)  # Validate every image before any provider call.
            attempt = job.attempt_number
            snapshot_sha = job.config_snapshot_json["jobPagesSha256"]
        previous = {page["pageId"]: self._previous_success(job_id, page) for page in pages}
        minimum_calls = [
            OpenAIOcrProvider.minimum_call_count(
                len(page["fieldMappings"]),
                len(page.get("documentContext", {}).get("identityFields", [])),
            )
            if page.get("ocrProvider") == "openai" and page.get("ocrEnabled", True)
            and previous[page["pageId"]] is None
            else 0
            for page in pages
        ]
        openai_page = next((p for p in pages if p.get("ocrProvider") == "openai"), None)
        if openai_page is not None:
            available = PagedCallBudget(
                service.database, job_id, openai_page["pageId"], attempt, snapshot_sha,
            ).remaining()
            if available < sum(minimum_calls):
                raise print_error(
                    "未完了の全ページを読むためのOCR呼び出し予算が不足しています。",
                    "OCR_CALL_BUDGET_TOO_SMALL",
                    409,
                )
        results = []
        for page_index, page in enumerate(pages):
            with service.database.session() as session:
                current = session.get(ProcessingJob, job_id)
                if current is None or current.cancel_requested:
                    return
            result = previous[page["pageId"]]
            if result is None:
                result = {
                    "pageId": page["pageId"],
                    "pageNumber": page["pageNumber"],
                    "inputId": page["inputId"],
                    "inputRevision": page["inputRevision"],
                    "referenceImageSha256": page["templateSnapshot"]["cleanTemplateSha256"],
                    "pageSnapshotSha256": digest(page),
                    "fields": page["fields"],
                    "attemptNumber": attempt,
                }
                try:
                    extracted = self.extraction.process(job_id, page_id=page["pageId"])
                    payload = (
                        self.workflow.process(
                            extracted,
                            reserved_calls_for_other_pages=sum(minimum_calls[page_index + 1:]),
                        )
                        if page.get("ocrProvider") == "openai"
                        else self.workflow.process(extracted)
                    )
                    if payload is None:
                        raise print_error("ページ処理が中断されました。", "JOB_CANCELLED", 409)
                    result.update(status="SUCCESS", extraction=payload)
                except Exception as exc:
                    logger.exception("Page execution failed", extra={
                        "jobId": job_id, "pageId": page["pageId"],
                    })
                    result.update(
                        status="FAILED",
                        errorCode=(
                            exc.code if isinstance(exc, AppError) else "PAGE_EXECUTION_FAILED"
                        ),
                    )
                    if (
                        isinstance(exc, AppError)
                        and exc.code == "PAGED_CROP_CORRESPONDENCE_INVALID"
                    ):
                        result["safetyFailure"] = exc.metadata
                writer.json(
                    job_id=job_id,
                    attempt_number=attempt,
                    name=f"pageResult.{page['pageId']}",
                    file_name=f"pages/{page['pageId']}/result.json",
                    value=result,
                    replace=True,
                )
            results.append(result)
        failed = any(p["status"] != "SUCCESS" for p in results)
        if not failed:
            PagedReviewService(service.database).initialize(job_id, results)
        writer.json(
            job_id=job_id,
            attempt_number=attempt,
            name="documentPages",
            file_name="document_pages.json",
            replace=True,
            value={
                "schemaVersion": 1,
                "jobId": job_id,
                "pages": results,
                "status": "partial_failure" if failed else "completed",
                "reviewAvailable": not failed,
                "outputAvailable": False,
            },
        )
        if failed:
            raise print_error(
                "失敗したページがあります。成功ページを保持して再試行できます。",
                "MULTI_PAGE_PARTIAL_FAILURE",
                409,
            )

    def _previous_success(self, job_id: str, page: dict[str, Any]) -> dict[str, Any] | None:
        with self.pages.database.session() as session:
            artifacts = session.scalars(
                select(JobArtifact)
                .where(
                    JobArtifact.job_id == job_id,
                    JobArtifact.artifact_name == f"pageResult.{page['pageId']}",
                )
                .order_by(JobArtifact.attempt_number.desc())
            )
            for artifact in artifacts:
                content = self.pages.storage.read_bytes(artifact.relative_path)
                if hashlib.sha256(content).hexdigest() != artifact.sha256:
                    raise print_error("保存結果のSHAが一致しません。", "PAGE_RESULT_CHANGED", 409)
                result: dict[str, Any] = json.loads(content)
                if result.get("pageSnapshotSha256") != digest(page):
                    raise print_error(
                        "保存結果のページ対応が一致しません。", "PAGE_RESULT_CHANGED", 409
                    )
                if result.get("status") == "SUCCESS":
                    return result
        return None
