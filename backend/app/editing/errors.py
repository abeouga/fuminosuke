from typing import Any

from app.core.errors import AppError


def reject(code: str, message: str, status: int = 409, **metadata: Any) -> AppError:
    return AppError(status=status, code=code, title=code, detail=message, metadata=metadata)


def problem(error: AppError) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": error.detail,
        "retryable": error.status >= 500,
        "fieldErrors": [item.model_dump(mode="json", by_alias=True) for item in error.field_errors],
        "candidateHandles": error.metadata.get("candidateHandles", []),
        "currentRevision": error.metadata.get("currentRevision"),
    }
