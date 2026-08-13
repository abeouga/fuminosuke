from __future__ import annotations

from app.domain.enums import JobType, TemplateMode


def job_input_bounds(
    job_type: JobType,
    mode: TemplateMode | None,
    *,
    min_consensus_images: int,
    max_consensus_images: int,
) -> tuple[int, int]:
    if job_type is JobType.EXTRACT_DOCUMENT:
        return 1, 1
    if job_type is JobType.PREPARE_TEMPLATE and mode is not None:
        if mode is TemplateMode.MULTI_SCAN_CONSENSUS:
            return min_consensus_images, max_consensus_images
        return 1, 1
    raise ValueError("Unsupported job type or template mode")
