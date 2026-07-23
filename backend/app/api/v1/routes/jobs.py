from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status

from app.api.dependencies import get_job_service
from app.api.v1.routes.shared import JobDetail, job_detail

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobDetail, response_model_by_alias=True)
def get_job(request: Request, job_id: str) -> JobDetail:
    return job_detail(get_job_service(request).get(job_id))


@router.post("/jobs/{job_id}/retry", response_model=JobDetail, response_model_by_alias=True)
async def retry_job(request: Request, job_id: str) -> JobDetail:
    return job_detail(await get_job_service(request).retry(job_id))


@router.post("/jobs/{job_id}/cancel", response_model=JobDetail, response_model_by_alias=True)
def cancel_job(request: Request, job_id: str) -> JobDetail:
    return job_detail(get_job_service(request).cancel(job_id))


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    request: Request,
    job_id: str,
    confirm: bool = Query(default=False),
) -> Response:
    get_job_service(request).delete(job_id, confirm=confirm)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

