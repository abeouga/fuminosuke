import pytest

from app.core.errors import AppError
from app.domain.enums import JobStatus
from app.domain.job_state import require_transition


def test_allows_retry_from_interrupted() -> None:
    require_transition(JobStatus.INTERRUPTED, JobStatus.QUEUED)


def test_rejects_processing_to_confirmed() -> None:
    with pytest.raises(AppError) as caught:
        require_transition(JobStatus.PROCESSING, JobStatus.CONFIRMED)
    assert caught.value.code == "INVALID_STATE_TRANSITION"

