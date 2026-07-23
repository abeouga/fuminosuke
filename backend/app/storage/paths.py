from __future__ import annotations

from pathlib import Path, PurePosixPath
from uuid import UUID


def _uuid_part(value: str) -> str:
    return str(UUID(value))


def upload_path(job_id: str, extension: str) -> PurePosixPath:
    normalized_extension = extension.lower().lstrip(".")
    if normalized_extension not in {"jpg", "jpeg", "png"}:
        raise ValueError("Unsupported detected extension")
    return PurePosixPath("uploads") / _uuid_part(job_id) / f"original.{normalized_extension}"


def upload_manifest_path(job_id: str) -> PurePosixPath:
    return PurePosixPath("uploads") / _uuid_part(job_id) / "manifest.json"


def artifact_path(job_id: str, attempt_number: int, artifact_name: str) -> PurePosixPath:
    if attempt_number < 1:
        raise ValueError("attempt_number must be positive")
    safe_name = Path(artifact_name).name
    if safe_name != artifact_name or safe_name in {"", ".", ".."}:
        raise ValueError("artifact_name must be a plain file name")
    return (
        PurePosixPath("artifacts")
        / _uuid_part(job_id)
        / "attempts"
        / str(attempt_number)
        / safe_name
    )


def export_path(job_id: str, file_name: str) -> PurePosixPath:
    if file_name not in {"confirmed_result.json", "confirmed_result.csv"}:
        raise ValueError("Unsupported export file name")
    return PurePosixPath("exports") / _uuid_part(job_id) / file_name

