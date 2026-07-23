from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.core.errors import ApiModel, AppError
from app.repositories.artifact_repository import ArtifactRepository

router = APIRouter(tags=["artifacts"])


class ArtifactItem(ApiModel):
    name: str
    media_type: str
    url: str


class ArtifactList(ApiModel):
    items: list[ArtifactItem]


@router.get(
    "/jobs/{job_id}/artifacts", response_model=ArtifactList, response_model_by_alias=True
)
def list_artifacts(request: Request, job_id: str) -> ArtifactList:
    database = request.app.state.database
    with database.session() as session:
        artifacts = ArtifactRepository(session).list_for_job(job_id)
        return ArtifactList(
            items=[
                ArtifactItem(
                    name=item.artifact_name,
                    media_type=item.media_type,
                    url=f"/api/v1/jobs/{job_id}/artifacts/{item.artifact_name}",
                )
                for item in artifacts
            ]
        )


@router.get("/jobs/{job_id}/artifacts/{artifact_name}", response_class=FileResponse)
def get_artifact(request: Request, job_id: str, artifact_name: str) -> FileResponse:
    database = request.app.state.database
    with database.session() as session:
        job = request.app.state.job_service.get(job_id)
        artifact = ArtifactRepository(session).get_by_name(
            job_id=job_id,
            attempt_number=job.attempt_number,
            artifact_name=artifact_name,
        )
        if artifact is None:
            raise AppError(
                status=404,
                code="ARTIFACT_NOT_FOUND",
                title="Artifact not found",
                detail="指定された生成物が見つかりません。",
            )
        path = request.app.state.storage.resolve(artifact.relative_path)
        if not path.is_file():
            raise AppError(
                status=404,
                code="ARTIFACT_NOT_FOUND",
                title="Artifact not found",
                detail="指定された生成物が見つかりません。",
            )
        return FileResponse(
            path=path,
            media_type=artifact.media_type,
            filename=artifact.artifact_name,
        )
