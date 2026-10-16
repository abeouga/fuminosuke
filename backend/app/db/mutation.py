from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import event, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.errors import AppError
from app.db.models import TemplateVersion
from app.db.mutation_models import TemplateAuthoringRevision


def revision_conflict() -> AppError:
    return AppError(
        status=409,
        code="REVISION_CONFLICT",
        title="Revision conflict",
        detail="別の変更が保存されています。入力を保持して最新の状態を取得してください。",
    )


def require_revision(version: TemplateVersion, expected: int) -> None:
    if version.revision != expected:
        raise revision_conflict()


def touch_template_version(version: TemplateVersion) -> None:
    """Advance the shared generation when a related, series-owned record changes."""
    flag_modified(version, "quality_json")


@dataclass
class TemplateWriteScope:
    version_id: str
    revision: int
    source: str
    committed: Callable[[int], None] | None = None


_write_scope: ContextVar[TemplateWriteScope | None] = ContextVar(
    "template_write_scope", default=None
)


@contextmanager
def template_write_scope(scope: TemplateWriteScope) -> Iterator[TemplateWriteScope]:
    token = _write_scope.set(scope)
    try:
        yield scope
    finally:
        _write_scope.reset(token)


@event.listens_for(Session, "before_commit")
def check_source_generation(session: Session) -> None:
    if session.info.get("mutation_transaction_owned") and not session.info.get(
        "mutation_owner_committing"
    ):
        raise RuntimeError("Only the transaction owner may commit this mutation")
    scope = _write_scope.get()
    if scope is not None:
        current = session.scalar(
            select(TemplateVersion.revision).where(TemplateVersion.id == scope.version_id)
        )
        if current != scope.revision:
            raise revision_conflict()


@event.listens_for(Session, "before_flush")
def check_template_writes(session: Session, _context: object, _instances: object) -> None:
    scope = _write_scope.get()
    changed = [
        item
        for item in session.dirty
        if isinstance(item, TemplateVersion) and session.is_modified(item)
    ]
    for version in changed:
        if scope is not None and version.id == scope.version_id:
            require_revision(version, scope.revision)
    pending = session.info.setdefault("template_revision_history", [])
    pending.extend((item, scope.source if scope else "internal") for item in changed)
    pending.extend((item, "created") for item in session.new if isinstance(item, TemplateVersion))


@event.listens_for(Session, "after_flush_postexec")
def append_template_history(session: Session, _context: object) -> None:
    for version, source in session.info.pop("template_revision_history", []):
        session.add(
            TemplateAuthoringRevision(
                template_version_id=version.id,
                revision=version.revision,
                source=source,
                status=version.status,
                config_relative_path=version.config_relative_path,
                layout_json=copy.deepcopy(version.layout_json),
                quality_json=copy.deepcopy(version.quality_json),
                clean_template_artifact_id=version.clean_template_artifact_id,
                mask_artifact_id=version.mask_artifact_id,
            )
        )
        scope = _write_scope.get()
        if scope is not None and version.id == scope.version_id:
            # Multiple flushes in one transaction are still checked against their
            # own generation. Rollback restores the scope's last committed value.
            session.info.setdefault("template_scope_before", scope.revision)
            scope.revision = version.revision
            session.info["template_scope"] = scope


@event.listens_for(Session, "after_commit")
def publish_template_revision(session: Session) -> None:
    scope = session.info.pop("template_scope", None)
    session.info.pop("template_scope_before", None)
    if scope is not None and scope.committed is not None:
        scope.committed(scope.revision)


@event.listens_for(Session, "after_rollback")
def discard_template_revision(session: Session) -> None:
    scope = session.info.pop("template_scope", None)
    previous = session.info.pop("template_scope_before", None)
    session.info.pop("template_revision_history", None)
    if scope is not None and previous is not None:
        scope.revision = previous


@event.listens_for(Session, "after_transaction_end")
def restore_uncommitted_scope(session: Session, transaction: object) -> None:
    if getattr(transaction, "parent", None) is None:
        # Closing a read/preparation session rolls back implicitly. Restore a
        # flushed scope without expiring detached read models on normal exit.
        discard_template_revision(session)
