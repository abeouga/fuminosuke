from __future__ import annotations

from typing import cast

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import ProblemDetails
from app.services.portable_backup_service import PortableBackupService

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
API_PREFIX = "/api/v1"
BACKUP_CREATE_PATH = f"{API_PREFIX}/system-backups/portable"
BACKUP_CONTROL_PREFIX = f"{API_PREFIX}/system-backups/"
HEALTH_PATH = f"{API_PREFIX}/health"


class DataOperationBarrierMiddleware:
    """Hold request accounting through the complete response body lifecycle."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", ""))
        if method == "OPTIONS" or self._is_control_request(method, path):
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        service = cast(
            PortableBackupService | None,
            getattr(getattr(app, "state", None), "portable_backup_service", None),
        )
        if service is None:
            await self.app(scope, receive, send)
            return
        mutating = method in MUTATING_METHODS
        registered = service.begin_request(mutating=mutating)
        if not registered:
            problem = ProblemDetails(
                title="Portable data operation in progress",
                status=423,
                code="BACKUP_IN_PROGRESS",
                detail=(
                    "持ち出しバックアップの書き出しまたは取り込みを処理中です。"
                    "完了するまで別の画面操作は開始できません。"
                ),
            )
            response = JSONResponse(
                status_code=problem.status,
                content=problem.model_dump(by_alias=True, mode="json"),
                media_type="application/problem+json",
            )
            await response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            service.end_request(mutating=mutating)

    @staticmethod
    def _is_control_request(method: str, path: str) -> bool:
        if method == "GET" and (path == HEALTH_PATH or path.startswith(BACKUP_CONTROL_PREFIX)):
            return True
        if method == "POST" and path == BACKUP_CREATE_PATH:
            return True
        return (
            method == "POST"
            and path.startswith(f"{API_PREFIX}/system-backups/portable-imports/")
            and path.endswith("/apply")
        )
