from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import JobStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


def uuid_string() -> str:
    return str(uuid4())


class SireName(Base):
    __tablename__ = "sire_names"
    __table_args__ = (
        UniqueConstraint(
            "normalized_name",
            name="uq_sire_names_normalized_name",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str] = mapped_column(Text, nullable=False)
    active_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("template_versions.id", name="fk_templates_active_version_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version_number", name="uq_template_version_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    canonical_width: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_height: Mapped[int] = mapped_column(Integer, nullable=False)
    source_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True
    )
    clean_template_artifact_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("job_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    mask_artifact_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("job_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    config_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    layout_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=JobStatus.CREATED.value, index=True
    )
    template_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("template_versions.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str | None] = mapped_column(String(40), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        unique=True,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    progress_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message_safe: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    inputs: Mapped[list[JobInput]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )
    artifacts: Mapped[list[JobArtifact]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="JobArtifact.job_id",
    )
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )


class JobInput(Base):
    __tablename__ = "job_inputs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    input_index: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    file_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    job: Mapped[ProcessingJob] = relationship(back_populates="inputs")


class JobArtifact(Base):
    __tablename__ = "job_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "attempt_number", "artifact_name", name="uq_job_artifact_name"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    job: Mapped[ProcessingJob] = relationship(
        back_populates="artifacts", foreign_keys=[job_id]
    )


class OcrResult(Base):
    __tablename__ = "ocr_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("processing_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_candidate_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    current_corrected_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    api_call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class OcrResultRevision(Base):
    __tablename__ = "ocr_result_revisions"
    __table_args__ = (
        UniqueConstraint("ocr_result_id", "revision", name="uq_ocr_result_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    ocr_result_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ocr_results.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ConfirmedResult(Base):
    __tablename__ = "confirmed_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("processing_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    ocr_result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    individual_id: Mapped[str | None] = mapped_column(
        String(10), nullable=True, index=True
    )
    confirmed_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    export_relative_path: Mapped[str] = mapped_column(Text, nullable=False)


class ConfirmedRecordField(Base):
    __tablename__ = "confirmed_record_fields"
    __table_args__ = (
        UniqueConstraint(
            "confirmed_result_id",
            "section_key",
            "record_order",
            "field_key",
            name="uq_confirmed_record_field",
        ),
        Index(
            "ix_confirmed_record_field_lookup",
            "section_key",
            "field_key",
            "normalized_value",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    confirmed_result_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("confirmed_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_key: Mapped[str] = mapped_column(String(100), nullable=False)
    section_title: Mapped[str] = mapped_column(String(200), nullable=False)
    record_order: Mapped[int] = mapped_column(Integer, nullable=False)
    record_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    field_key: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    event_code: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    job: Mapped[ProcessingJob] = relationship(back_populates="events")
