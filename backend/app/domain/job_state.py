from app.core.errors import AppError
from app.domain.enums import JobStatus

ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
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
            detail=f"ジョブ状態を {current.value} から {target.value} へ変更できません。",
        )

