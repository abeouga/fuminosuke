from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.core.errors import ProblemDetails
from app.services.portable_backup_service import PortableBackupService

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
BACKUP_PATH = "/api/v1/system-backups/portable"


async def block_mutations_during_backup(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    service = cast(
        PortableBackupService | None,
        getattr(request.app.state, "portable_backup_service", None),
    )
    is_backup_create = request.method == "POST" and request.url.path == BACKUP_PATH
    is_mutation = request.method in MUTATING_METHODS and not is_backup_create
    mutation_registered = False
    if is_mutation and service is not None:
        mutation_registered = service.begin_mutation()
        if not mutation_registered:
            problem = ProblemDetails(
                title="Portable backup in progress",
                status=423,
                code="BACKUP_IN_PROGRESS",
                detail=(
                    "持ち出しバックアップを作成中です。完了するまで保存や読み取りを"
                    "開始できません。"
                ),
            )
            return JSONResponse(
                status_code=problem.status,
                content=problem.model_dump(by_alias=True, mode="json"),
                media_type="application/problem+json",
            )
    try:
        return await call_next(request)
    finally:
        if mutation_registered and service is not None:
            service.end_mutation()
