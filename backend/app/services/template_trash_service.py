"""Deleting a template moves it to the trash; it can be put back or emptied from there.

The documents read with a deleted template either go to the trash with it (hidden from the
shelf and the chat until it is put back) or are shown under another template the user
chose. Moving is display grouping only: a job keeps its accepted version, snapshot,
readings, crops and revisions, so the OCR contract is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import (
    DocumentTemplateMove,
    ProcessingJob,
    Template,
    TemplateTrash,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.repositories.template_repository import TemplateRepository
from app.services.template_mutation import begin_immediate, template_mutation_lock
from app.storage.local_storage import LocalStorageService


def document_owners(session: Session) -> tuple[dict[str, str], set[str], dict[str, str]]:
    """Moved documents (job → template), templates in the trash, and template names."""
    moves = {
        move.job_id: move.template_id for move in session.scalars(select(DocumentTemplateMove))
    }
    trashed: set[str] = set()
    names: dict[str, str] = {}
    for template in session.scalars(select(Template)):
        names[template.id] = template.name
        if template.archived_at is not None:
            trashed.add(template.id)
    return moves, trashed, names


def _documents(session: Session, template_id: str) -> list[ProcessingJob]:
    """Documents shown under the template: read with it and not moved away, or moved to it."""
    moves = {m.job_id: m.template_id for m in session.scalars(select(DocumentTemplateMove))}
    own = session.scalars(
        select(ProcessingJob)
        .join(TemplateVersion, TemplateVersion.id == ProcessingJob.template_version_id)
        .where(
            ProcessingJob.job_type == JobType.EXTRACT_DOCUMENT.value,
            TemplateVersion.template_id == template_id,
        )
    )
    jobs = [job for job in own if job.id not in moves]
    moved_in = [job_id for job_id, owner in moves.items() if owner == template_id]
    if moved_in:
        jobs.extend(session.scalars(select(ProcessingJob).where(ProcessingJob.id.in_(moved_in))))
    return sorted(jobs, key=lambda job: job.created_at, reverse=True)


class TemplateTrashService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage

    def impact(self, template_id: str) -> dict[str, int]:
        with self._database.session() as session:
            if session.get(Template, template_id) is None:
                raise _not_found()
            jobs = _documents(session, template_id)
        return {
            "documents": len(jobs),
            "confirmedDocuments": sum(job.status == JobStatus.CONFIRMED.value for job in jobs),
        }

    def trash(self, template_id: str, move_to: str | None) -> None:
        with template_mutation_lock(), self._database.session() as session:
            begin_immediate(session)
            template = session.get(Template, template_id)
            if template is None or template.archived_at is not None:
                raise _not_found()
            if move_to is not None:
                target = session.get(Template, move_to)
                if target is None or target.archived_at is not None or move_to == template_id:
                    raise AppError(
                        status=422, code="INVALID_MOVE_TARGET", title="Invalid move target",
                        detail="移動先のテンプレートが見つかりません。一覧を更新して選び直してください。",
                    )
                for job in _documents(session, template_id):
                    move = session.get(DocumentTemplateMove, job.id)
                    if move is None:
                        session.add(DocumentTemplateMove(
                            job_id=job.id, template_id=move_to, from_template_id=template_id,
                        ))
                    else:
                        move.template_id = move_to
            versions = TemplateRepository(session).list_versions(template_id)
            session.merge(TemplateTrash(
                template_id=template_id,
                trashed_at=datetime.now(UTC),
                active_version_id=template.active_version_id,
                version_statuses_json={version.id: version.status for version in versions},
                purged_at=None,
            ))
            template.active_version_id, template.archived_at = None, datetime.now(UTC)
            for version in versions:
                version.status = "ARCHIVED"
            session.commit()

    def list(self) -> list[dict[str, Any]]:
        with self._database.session() as session:
            entries = {entry.template_id: entry for entry in session.scalars(select(TemplateTrash))}
            items: list[dict[str, Any]] = []
            templates = session.scalars(
                select(Template).where(Template.archived_at.is_not(None))
                .order_by(Template.archived_at.desc())
            )
            for template in templates:
                entry = entries.get(template.id)
                if entry is not None and entry.purged_at is not None:
                    continue
                versions = TemplateRepository(session).list_versions(template.id)
                shown = next(
                    (v for v in versions if entry is not None and v.id == entry.active_version_id),
                    max(versions, key=lambda v: v.version_number, default=None),
                )
                items.append({
                    "templateId": template.id,
                    "name": template.name,
                    "trashedAt": _utc(
                        entry.trashed_at if entry is not None else template.archived_at
                    ),
                    "documents": len(_documents(session, template.id)),
                    "sourceJobId": shown.source_job_id if shown is not None else None,
                })
        return items

    def restore(self, template_id: str) -> None:
        with template_mutation_lock(), self._database.session() as session:
            begin_immediate(session)
            repository = TemplateRepository(session)
            template = session.get(Template, template_id)
            entry = session.get(TemplateTrash, template_id)
            if template is None or template.archived_at is None or (
                entry is not None and entry.purged_at is not None
            ):
                raise _not_found()
            if repository.name_exists(template.name, exclude_template_id=template_id):
                raise AppError(
                    status=409, code="TEMPLATE_NAME_CONFLICT", title="Template name already exists",
                    detail=f"同じ名前「{template.name}」のテンプレートがあるため戻せません。"
                    "先にどちらかの名前を変えてください。",
                )
            versions = repository.list_versions(template_id)
            if entry is not None:
                statuses = entry.version_statuses_json
                for version in versions:
                    version.status = str(statuses.get(version.id) or version.status)
                active = entry.active_version_id
            else:
                # Deleted before the trash existed: the newest approved version is put back.
                approved = [v for v in versions if v.approved_at is not None]
                newest = max(approved, key=lambda v: v.version_number, default=None)
                for version in versions:
                    if version is newest:
                        version.status = "ACTIVE"
                    elif version.approved_at is None and version.rejected_at is None:
                        version.status = "DRAFT"
                active = newest.id if newest is not None else None
            template.archived_at = None
            session.flush()
            template.active_version_id = active
            if entry is not None:
                session.delete(entry)
            session.commit()

    def purge(self, template_id: str, delete_job: Callable[[str], None]) -> None:
        """Empties one entry: its documents are deleted, then the template if nothing uses it."""
        with self._database.session() as session:
            template = session.get(Template, template_id)
            entry = session.get(TemplateTrash, template_id)
            if template is None or template.archived_at is None or (
                entry is not None and entry.purged_at is not None
            ):
                raise _not_found()
            jobs = _documents(session, template_id)
            if any(job.status == JobStatus.PROCESSING.value for job in jobs):
                raise AppError(
                    status=409, code="TEMPLATE_IN_USE", title="Documents are being read",
                    detail="このテンプレートで読み取り中の書類があります。終わってから削除してください。",
                )
            job_ids = [job.id for job in jobs]
        for job_id in job_ids:  # Newest first, so a later version is removed before its source.
            delete_job(job_id)
        with template_mutation_lock(), self._database.session() as session:
            begin_immediate(session)
            template = session.get(Template, template_id)
            if template is None:
                return
            if self._database.before_delete is not None:
                self._database.before_delete("template", template_id)
            versions = TemplateRepository(session).list_versions(template_id)
            version_ids = [v.id for v in versions]
            referenced = session.scalar(select(ProcessingJob.id).where(
                ProcessingJob.job_type == JobType.EXTRACT_DOCUMENT.value,
                ProcessingJob.template_version_id.in_(version_ids),
            ).limit(1)) if versions else None
            if referenced is not None:
                # Documents moved to another template still read with these versions.
                entry = session.get(TemplateTrash, template_id)
                if entry is None:
                    entry = TemplateTrash(template_id=template_id, active_version_id=None,
                                          version_statuses_json={})
                    session.add(entry)
                entry.purged_at = datetime.now(UTC)
                session.commit()
                return
            # The jobs that prepared the template (its source image and drafts) go with it.
            preparing = set(session.scalars(select(ProcessingJob.id).where(
                ProcessingJob.template_version_id.in_(version_ids),
            ))) | {v.source_job_id for v in versions if v.source_job_id}
            template.active_version_id = None
            session.flush()
            _delete_authoring_records(session, version_ids)
            for version in versions:
                session.delete(version)
            session.flush()
            session.delete(template)
            session.flush()
            preparing -= set(session.scalars(select(TemplateVersion.source_job_id).where(
                TemplateVersion.source_job_id.in_(preparing),
            )))  # Still the source of another template's version.
            jobs = session.scalars(select(ProcessingJob).where(ProcessingJob.id.in_(preparing)))
            for job in jobs:
                if job.job_type != JobType.EXTRACT_DOCUMENT.value:
                    session.delete(job)
                else:
                    preparing.discard(job.id)
            session.commit()
        for relative_path in [f"templates/{template_id}", *(
            f"{directory}/{job_id}" for job_id in preparing
            for directory in ("uploads", "artifacts", "exports")
        )]:
            if self._storage.exists(relative_path):
                self._storage.delete_tree(relative_path)


def _delete_authoring_records(session: Session, version_ids: list[str]) -> None:
    """The template-authoring records point at the versions without ON DELETE CASCADE.

    PostgreSQL refuses to delete a version they still point at (SQLite does not check), so a
    template that was ever edited could not be emptied from the trash in the cloud.
    """
    if not version_ids:
        return
    from sqlalchemy import delete, inspect

    from app.template_analysis.models import (
        AnalysisEvent,
        AnalysisMerge,
        AnalysisPage,
        AnalysisRun,
        AuthoringFacet,
    )

    tables = inspect(session.connection())
    if tables.has_table(AuthoringFacet.__tablename__):
        session.execute(delete(AuthoringFacet).where(AuthoringFacet.version_id.in_(version_ids)))
    if tables.has_table(AnalysisRun.__tablename__):
        runs = select(AnalysisRun.id).where(AnalysisRun.resource_id.in_(version_ids))
        for child in (AnalysisEvent, AnalysisMerge, AnalysisPage):
            session.execute(delete(child).where(child.run_id.in_(runs)))
        session.execute(delete(AnalysisRun).where(AnalysisRun.resource_id.in_(version_ids)))
    session.flush()


def _utc(value: datetime | None) -> datetime | None:
    """SQLite returns stored UTC times without a zone."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _not_found() -> AppError:
    return AppError(
        status=404, code="TEMPLATE_NOT_FOUND", title="Template not found",
        detail="指定されたテンプレートが見つかりません。",
    )
