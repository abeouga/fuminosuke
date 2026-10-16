"""Durable receipts and resource history, committed with the business mutation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import utc_now, uuid_string


class MutationRequest(Base):
    __tablename__ = "mutation_requests"
    __table_args__ = (CheckConstraint("state IN ('PENDING', 'SUCCEEDED', 'FAILED')"),)

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResourceOperation(Base):
    __tablename__ = "resource_operations"
    __table_args__ = (CheckConstraint("after_revision > before_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    request_key: Mapped[str] = mapped_column(
        ForeignKey("mutation_requests.key"), nullable=False, unique=True
    )
    resource_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    before_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    after_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    before_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    after_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    inverse_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    undo_of_id: Mapped[str | None] = mapped_column(
        ForeignKey("resource_operations.id"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TemplateAuthoringRevision(Base):
    __tablename__ = "template_authoring_revisions"

    template_version_id: Mapped[str] = mapped_column(
        ForeignKey("template_versions.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    config_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    layout_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    clean_template_artifact_id: Mapped[str | None] = mapped_column(String(36))
    mask_artifact_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OcrConfirmationRevision(Base):
    __tablename__ = "ocr_confirmation_revisions"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    confirmed_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    document_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    export_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
