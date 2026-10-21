from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import AppError, FieldError, ProblemDetails

logger = logging.getLogger(__name__)


def _response(problem: ProblemDetails) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(by_alias=True, mode="json"),
        media_type="application/problem+json",
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "Application request failed",
            extra={"errorCode": exc.code, "status": exc.status, **exc.metadata},
        )
        return _response(
            ProblemDetails(
                title=exc.title,
                status=exc.status,
                code=exc.code,
                detail=exc.detail,
                job_id=exc.job_id,
                field_errors=exc.field_errors,
            )
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            FieldError(
                field=".".join(str(item) for item in error["loc"]),
                message=str(error["msg"]),
            )
            for error in exc.errors()
        ]
        logger.warning(
            "Request validation failed",
            extra={
                "validationErrors": [
                    error.model_dump(mode="json", by_alias=True) for error in errors
                ]
            },
        )
        return _response(
            ProblemDetails(
                title="Request validation failed",
                status=422,
                code="VALIDATION_FAILED",
                detail="送った内容を受け付けられませんでした。画面を再読み込みしてから、もう一度操作してください。",
                field_errors=errors,
            )
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled request failure", exc_info=exc)
        return _response(
            ProblemDetails(
                title="Internal processing error",
                status=500,
                code="INTERNAL_PROCESSING_ERROR",
                detail="処理中に問題が起きました。もう一度試してください。続く場合は管理者に連絡してください。",
            )
        )

