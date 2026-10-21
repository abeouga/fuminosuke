from typing import Any

from app.core.errors import AppError


def reject(code: str, message: str, status: int = 409, **metadata: Any) -> AppError:
    from app.editing.plain_messages import plain

    shown = plain(code, message)
    if shown != message:
        # The check's own wording stays in the log for diagnosis.
        metadata = {**metadata, "internalMessage": message}
    return AppError(status=status, code=code, title=code, detail=shown, metadata=metadata)


def problem(error: AppError) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": error.detail,
        "retryable": error.status >= 500,
        "fieldErrors": [item.model_dump(mode="json", by_alias=True) for item in error.field_errors],
        "candidateHandles": error.metadata.get("candidateHandles", []),
        "currentRevision": error.metadata.get("currentRevision"),
    }
