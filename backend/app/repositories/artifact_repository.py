from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobArtifact


class ArtifactRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        job_id: str,
        attempt_number: int,
        artifact_name: str,
        media_type: str,
        relative_path: str,
        sha256: str,
        width: int | None = None,
        height: int | None = None,
    ) -> JobArtifact:
        artifact = JobArtifact(
            job_id=job_id,
            attempt_number=attempt_number,
            artifact_name=artifact_name,
            media_type=media_type,
            relative_path=relative_path,
            sha256=sha256,
            width=width,
            height=height,
        )
        self._session.add(artifact)
        self._session.commit()
        self._session.refresh(artifact)
        return artifact

    def upsert(
        self,
        *,
        job_id: str,
        attempt_number: int,
        artifact_name: str,
        media_type: str,
        relative_path: str,
        sha256: str,
        width: int | None = None,
        height: int | None = None,
    ) -> JobArtifact:
        artifact = self.get_by_name(
            job_id=job_id,
            attempt_number=attempt_number,
            artifact_name=artifact_name,
        )
        if artifact is None:
            return self.create(
                job_id=job_id,
                attempt_number=attempt_number,
                artifact_name=artifact_name,
                media_type=media_type,
                relative_path=relative_path,
                sha256=sha256,
                width=width,
                height=height,
            )
        artifact.media_type = media_type
        artifact.relative_path = relative_path
        artifact.sha256 = sha256
        artifact.width = width
        artifact.height = height
        self._session.commit()
        self._session.refresh(artifact)
        return artifact

    def upsert_many(self, rows: list[dict[str, Any]]) -> list[JobArtifact]:
        """upsert() for several artifacts of one job attempt, committed once."""
        if not rows:
            return []
        job_id, attempt_number = rows[0]["job_id"], rows[0]["attempt_number"]
        if any(r["job_id"] != job_id or r["attempt_number"] != attempt_number for r in rows):
            raise ValueError("upsert_many takes one job attempt")
        existing = {
            artifact.artifact_name: artifact
            for artifact in self._session.scalars(
                select(JobArtifact).where(
                    JobArtifact.job_id == job_id,
                    JobArtifact.attempt_number == attempt_number,
                    JobArtifact.artifact_name.in_([r["artifact_name"] for r in rows]),
                )
            )
        }
        artifacts: list[JobArtifact] = []
        for row in rows:
            artifact = existing.get(row["artifact_name"])
            if artifact is None:
                artifact = JobArtifact(**row)
                self._session.add(artifact)
                existing[row["artifact_name"]] = artifact
            else:
                for key in ("media_type", "relative_path", "sha256", "width", "height"):
                    setattr(artifact, key, row.get(key))
            artifacts.append(artifact)
        self._session.commit()
        return artifacts

    def get_by_name(
        self, *, job_id: str, attempt_number: int, artifact_name: str
    ) -> JobArtifact | None:
        statement = select(JobArtifact).where(
            JobArtifact.job_id == job_id,
            JobArtifact.attempt_number == attempt_number,
            JobArtifact.artifact_name == artifact_name,
        )
        return self._session.scalar(statement)

    def list_for_job(self, job_id: str, *, attempt_number: int | None = None) -> list[JobArtifact]:
        statement = select(JobArtifact).where(JobArtifact.job_id == job_id)
        if attempt_number is not None:
            statement = statement.where(JobArtifact.attempt_number == attempt_number)
        statement = statement.order_by(JobArtifact.attempt_number, JobArtifact.created_at, JobArtifact.id)
        return list(self._session.scalars(statement))

    def delete_for_job(self, job_id: str) -> int:
        artifacts = self.list_for_job(job_id)
        for artifact in artifacts:
            self._session.delete(artifact)
        self._session.commit()
        return len(artifacts)
