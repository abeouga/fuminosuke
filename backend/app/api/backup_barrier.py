from __future__ import annotations

from typing import cast

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import ProblemDetails
from app.services.portable_backup_service import PortableBackupService

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
API_PREFIX = "/api/v1"
BACKUP_CREATE_PATH = f"{API_PREFIX}/system-backups/portable"
BACKUP_CONTROL_PREFIX = f"{API_PREFIX}/system-backups/"
WORKSPACE_CONTROL_PREFIX = f"{API_PREFIX}/workspace-sync"
AUTH_CONTROL_PREFIX = f"{API_PREFIX}/auth/"
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
        app_state = getattr(app, "state", None)
        workspace_sync = getattr(app_state, "workspace_cloud_sync_service", None)
        mutating = method in MUTATING_METHODS
        if (
            mutating
            and workspace_sync is not None
            and not self._is_read_only_exempt(path)
        ):
            writes_allowed, reason = workspace_sync.writes_allowed()
            if not writes_allowed:
                problem = ProblemDetails(
                    title="Workspace is read only",
                    status=423,
                    code="WORKSPACE_READ_ONLY",
                    detail=reason,
                )
                response = JSONResponse(
                    status_code=problem.status,
                    content=problem.model_dump(by_alias=True, mode="json"),
                    media_type="application/problem+json",
                )
                await response(scope, receive, send)
                return
        service = cast(
            PortableBackupService | None,
            getattr(app_state, "portable_backup_service", None),
        )
        if service is None:
            await self.app(scope, receive, send)
            return
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
        response_status = 500

        async def capture_send(message: Message) -> None:
            nonlocal response_status
            if isinstance(message, dict) and message.get("type") == "http.response.start":
                response_status = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, capture_send)
        finally:
            service.end_request(mutating=mutating)
            if (
                mutating
                and response_status < 400
                and workspace_sync is not None
            ):
                workspace_sync.mark_dirty()

    @staticmethod
    def _is_control_request(method: str, path: str) -> bool:
        if path == WORKSPACE_CONTROL_PREFIX or path.startswith(
            f"{WORKSPACE_CONTROL_PREFIX}/"
        ):
            return True
        if method == "GET" and (path == HEALTH_PATH or path.startswith(BACKUP_CONTROL_PREFIX)):
            return True
        if method == "POST" and path == BACKUP_CREATE_PATH:
            return True
        return (
            method == "POST"
            and path.startswith(f"{API_PREFIX}/system-backups/portable-imports/")
            and path.endswith("/apply")
        )

    @staticmethod
    def _is_read_only_exempt(path: str) -> bool:
        return (
            path.startswith(AUTH_CONTROL_PREFIX)
            or path.startswith(BACKUP_CONTROL_PREFIX)
            or path == WORKSPACE_CONTROL_PREFIX
            or path.startswith(f"{WORKSPACE_CONTROL_PREFIX}/")
        )
