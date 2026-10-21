from __future__ import annotations

from pathlib import Path
from typing import Annotated

import cv2
import numpy as np
from fastapi import APIRouter, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from app.core.errors import ApiModel, AppError
from app.db.models import JobArtifact
from app.repositories.artifact_repository import ArtifactRepository

router = APIRouter(tags=["artifacts"])


class ArtifactItem(ApiModel):
    name: str
    media_type: str
    url: str


class ArtifactList(ApiModel):
    items: list[ArtifactItem]


class ArtifactRecoveryResponse(ApiModel):
    artifact_count: int
    restored_source_from_database: bool


@router.get("/jobs/{job_id}/artifacts", response_model=ArtifactList, response_model_by_alias=True)
def list_artifacts(request: Request, job_id: str) -> ArtifactList:
    database = request.app.state.database
    with database.session() as session:
        artifacts = ArtifactRepository(session).list_for_job(job_id)
        return ArtifactList(
            items=[
                ArtifactItem(
                    name=item.artifact_name.split("~", 1)[0],
                    media_type=item.media_type,
                    url=(
                        f"/api/v1/jobs/{job_id}/artifacts/{item.artifact_name}?v={item.sha256[:16]}"
                    ),
                )
                for item in artifacts
            ]
        )


def _not_found() -> AppError:
    return AppError(
        status=404,
        code="ARTIFACT_NOT_FOUND",
        title="Artifact not found",
        detail="指定された生成物が見つかりません。",
    )


def _artifact_file(request: Request, job_id: str, artifact_name: str) -> tuple[Path, JobArtifact]:
    database = request.app.state.database
    with database.session() as session:
        job = request.app.state.job_service.get(job_id)
        artifact = ArtifactRepository(session).get_by_name(
            job_id=job_id,
            attempt_number=job.attempt_number,
            artifact_name=artifact_name,
        )
    if artifact is None:
        raise _not_found()
    path = request.app.state.storage.resolve(artifact.relative_path)
    if not path.is_file():
        request.app.state.artifact_recovery.ensure_artifact(job_id, artifact_name)
        with database.session() as session:
            artifact = ArtifactRepository(session).get_by_name(
                job_id=job_id,
                attempt_number=job.attempt_number,
                artifact_name=artifact_name,
            )
            if artifact is None:
                raise _not_found()
            path = request.app.state.storage.resolve(artifact.relative_path)
    if not path.is_file():
        raise _not_found()
    return path, artifact


@router.get("/jobs/{job_id}/artifacts/{artifact_name}", response_class=FileResponse)
def get_artifact(request: Request, job_id: str, artifact_name: str) -> FileResponse:
    path, artifact = _artifact_file(request, job_id, artifact_name)
    return FileResponse(
        path=path,
        media_type=artifact.media_type,
        filename=artifact.artifact_name,
    )


@router.get("/jobs/{job_id}/artifacts/{artifact_name}/thumbnail")
def get_artifact_thumbnail(
    request: Request,
    job_id: str,
    artifact_name: str,
    width: int = Query(default=360, ge=120, le=800),
) -> Response:
    """A small JPEG of an image artifact (for document lists); the artifact itself is unchanged."""
    path, artifact = _artifact_file(request, job_id, artifact_name)
    if not artifact.media_type.startswith("image/"):
        raise _not_found()
    tag = f'"{artifact.sha256[:32]}-{width}"'
    headers = {"ETag": tag, "Cache-Control": "private, max-age=300"}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise _not_found()
    height, current = image.shape[:2]
    if current > width:
        image = cv2.resize(
            image, (width, max(1, round(height * width / current))), interpolation=cv2.INTER_AREA
        )
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise _not_found()
    return Response(content=encoded.tobytes(), media_type="image/jpeg", headers=headers)


@router.post(
    "/jobs/{job_id}/artifacts/recover",
    response_model=ArtifactRecoveryResponse,
    response_model_by_alias=True,
)
async def recover_artifacts(
    request: Request,
    job_id: str,
    source_image: Annotated[UploadFile, File()],
) -> ArtifactRecoveryResponse:
    max_bytes = request.app.state.settings.storage.max_upload_bytes
    content = await source_image.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise AppError(
            status=413,
            code="UPLOAD_TOO_LARGE",
            title="Upload is too large",
            detail="画像ファイルが許容サイズを超えています。",
        )
    result = request.app.state.artifact_recovery.recover(
        job_id,
        source_bytes=content,
    )
    return ArtifactRecoveryResponse(
        artifact_count=result.artifact_count,
        restored_source_from_database=result.restored_source_from_database,
    )
