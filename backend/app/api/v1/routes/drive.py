from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from app.api.auth_dependencies import require_authenticated_user
from app.core.errors import ApiModel
from app.services.google_drive_service import GoogleDriveService
from app.services.google_oauth_service import AuthenticatedUser

router = APIRouter(prefix="/google-drive", tags=["google-drive"])


class SelectFolderRequest(ApiModel):
    folder_id: str = Field(min_length=1, max_length=255)


def _service(request: Request) -> GoogleDriveService:
    return cast(GoogleDriveService, request.app.state.google_drive)


@router.get("/output-folder")
def output_folder(
    request: Request,
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> dict[str, Any]:
    return _service(request).output_folder(user.subject if user else None)


@router.post("/picker-session")
def picker_session(
    request: Request,
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> JSONResponse:
    return JSONResponse(
        _service(request).picker_session(user.subject if user else None),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.put("/output-folder")
def select_output_folder(
    request: Request,
    body: SelectFolderRequest,
    user: Annotated[AuthenticatedUser | None, Depends(require_authenticated_user)],
) -> dict[str, Any]:
    return _service(request).select_output_folder(
        body.folder_id,
        user.subject if user else None,
    )
