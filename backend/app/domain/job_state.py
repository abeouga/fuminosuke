from app.core.errors import AppError
from app.domain.enums import JobStatus

ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED: frozenset(
        {JobStatus.QUEUED, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.QUEUED: frozenset({JobStatus.PROCESSING, JobStatus.CANCELLED}),
    JobStatus.PROCESSING: frozenset(
        {
            JobStatus.REVIEW_REQUIRED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.INTERRUPTED,
        }
    ),
    JobStatus.REVIEW_REQUIRED: frozenset(
        {JobStatus.CONFIRMED, JobStatus.CANCELLED, JobStatus.QUEUED}
    ),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.CANCELLED: frozenset(),
    JobStatus.INTERRUPTED: frozenset({JobStatus.QUEUED}),
    JobStatus.CONFIRMED: frozenset(),
}


def require_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise AppError(
            status=409,
            code="INVALID_STATE_TRANSITION",
            title="Invalid job state transition",
            detail="現在の処理状態ではこの操作を行えません。画面を再読み込みして状態を確認してください。",
            metadata={"from": current.value, "to": target.value},
        )
