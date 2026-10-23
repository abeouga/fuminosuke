from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Header, Query, Request, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy import select

from app.api.v1.routes.shared import JobAccepted, job_accepted
from app.api.v1.routes.uploads import read_upload
from app.db.models import JobArtifact
from app.domain.job_pages import PagedSubmission
from app.domain.paged_review import PageConfirmation, PageCorrection, PageReopen
from app.domain.print_layout import print_error
from app.services.job_page_service import JobPageService
from app.services.paged_output_service import PagedOutputService
from app.services.paged_review_service import PagedReviewService

router = APIRouter(tags=["ocr"])


@router.get("/ocr-jobs/{job_id}/page-review/output.xlsx")
def output_pages(request: Request, job_id: str, revision: Annotated[int, Query(ge=1)]) -> Response:
    output = PagedOutputService(request.app.state.database, request.app.state.storage).render(
        job_id, revision
    )
    return Response(
        output.content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(output.file_name, safe=""),
            "Cache-Control": "no-store",
            "X-Result-Revision": str(output.revision),
            "ETag": '"' + hashlib.sha256(output.content).hexdigest() + '"',
        },
    )


@router.get("/ocr-jobs/{job_id}/page-review")
def get_page_review(request: Request, job_id: str) -> dict[str, Any]:
    return PagedReviewService(request.app.state.database).get(job_id)


@router.post("/ocr-jobs/{job_id}/page-review/{page_id}/corrections")
def correct_page(
    request: Request,
    job_id: str,
    page_id: str,
    body: PageCorrection,
) -> dict[str, Any]:
    return PagedReviewService(request.app.state.database).correct(job_id, page_id, body)


@router.post("/ocr-jobs/{job_id}/page-review/confirm")
def confirm_pages(request: Request, job_id: str, body: PageConfirmation) -> dict[str, Any]:
    return PagedReviewService(request.app.state.database).confirm(job_id, body)


@router.post("/ocr-jobs/{job_id}/page-review/reopen")
def reopen_pages(request: Request, job_id: str, body: PageReopen) -> dict[str, Any]:
    return PagedReviewService(request.app.state.database).reopen(job_id, body)


@router.post("/ocr-jobs/{job_id}/page-review/cancel-reopen")
def cancel_reopen_pages(request: Request, job_id: str, body: PageReopen) -> dict[str, Any]:
    return PagedReviewService(request.app.state.database).cancel_reopen(job_id, body)


def service(request: Request) -> JobPageService:
    return JobPageService(
        request.app.state.database, request.app.state.storage, request.app.state.settings
    )


@router.get("/template-versions/{version_id}/ocr-pages")
def page_catalog(request: Request, version_id: str) -> dict[str, Any]:
    return service(request).catalog(version_id)


@router.post("/ocr-page-jobs", response_model=JobAccepted, status_code=202)
async def accept_pages(
    request: Request,
    manifest: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    try:
        body = PagedSubmission.model_validate_json(manifest)
    except ValidationError as exc:
        raise print_error("ページ受付manifestが不正です。", "PAGE_BINDING_INVALID") from exc
    if len(files) != len(body.upload_ids):
        raise print_error("入力画像の件数が一致しません。", "PAGE_BINDING_INVALID")
    uploads = [
        await read_upload(f, request.app.state.settings.storage.max_upload_bytes) for f in files
    ]
    job = service(request).accept(body, uploads, idempotency_key)
    # The transaction is committed before enqueue; restart recovery sees QUEUED.
    await request.app.state.job_runner.enqueue(job.id)
    return job_accepted(job)


@router.get("/ocr-jobs/{job_id}/page-results")
def page_results(request: Request, job_id: str) -> dict[str, Any]:
    with request.app.state.database.session() as session:
        artifact = session.scalar(
            select(JobArtifact)
            .where(
                JobArtifact.job_id == job_id,
                JobArtifact.artifact_name == "documentPages",
            )
            .order_by(JobArtifact.attempt_number.desc())
        )
        if artifact is None:
            raise print_error("ページ集約結果はまだありません。", "PAGE_RESULT_NOT_READY", 409)
        content = request.app.state.storage.read_bytes(artifact.relative_path)
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise print_error("結果のSHAが一致しません。", "PAGE_RESULT_CHANGED", 409)
        result: dict[str, Any] = json.loads(content)
        return result
