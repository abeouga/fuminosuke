from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy import select

from app.api.v1.routes.shared import JobAccepted, job_accepted
from app.api.v1.routes.uploads import read_upload
from app.db.models import JobArtifact
from app.domain.job_pages import PagedSubmission
from app.domain.print_layout import print_error
from app.services.job_page_service import JobPageService

router = APIRouter(tags=["ocr"])


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
