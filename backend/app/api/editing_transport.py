from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.dependencies import get_app_settings
from app.core.errors import AppError, FieldError
from app.editing.errors import problem, reject

logger = logging.getLogger(__name__)


class EditingRoute(APIRoute):
    def body_limit(self, request: Request) -> int:
        return 262_144

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            try:
                if request.method in {"POST", "PUT"}:
                    data = bytearray()
                    async for chunk in request.stream():
                        if len(data) + len(chunk) > self.body_limit(request):
                            raise reject(
                                "EDIT_BATCH_TOO_LARGE", "要求のサイズ上限を超えています。", 413
                            )
                        data.extend(chunk)
                    request._body = bytes(data)
                return await handler(request)
            except RequestValidationError as error:
                # The screen shows one plain sentence; the failing fields go to the log.
                logger.warning(
                    "Editing request validation failed",
                    extra={"validationErrors": [
                        {"field": ".".join(str(part) for part in item["loc"]),
                         "message": item["msg"]}
                        for item in error.errors()[:20]
                    ]},
                )
                problem_error = AppError(
                    status=422,
                    code="INVALID_ARGUMENT",
                    title="Invalid editing request",
                    detail="この変更は保存できませんでした。画面を再読み込みしてから、もう一度操作してください。",
                    field_errors=[
                        FieldError(
                            field=".".join(str(part) for part in item["loc"]),
                            message=item["msg"],
                        )
                        for item in error.errors()
                    ],
                )
                return JSONResponse(status_code=422, content=problem(problem_error))
            except AppError as error:
                return JSONResponse(status_code=error.status, content=problem(error))

        return bounded


def local_request(request: Request) -> None:
    if getattr(request.app.state, "cloud_authenticated", False):
        return  # The outer gateway has authenticated the cookie and checked CSRF/origin.
    settings = get_app_settings(request)
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"} or (
        (origin := request.headers.get("origin")) is not None
        and origin not in settings.app.frontend_origins
    ):
        raise reject("EDITING_ORIGIN_REJECTED", "ローカル画面からの編集だけを受け付けます。", 403)


Token = Annotated[str, Header(alias="X-Editing-Session", min_length=32, max_length=100)]
