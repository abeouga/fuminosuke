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
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
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
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class GoogleOAuthAccount(Base):
    __tablename__ = "google_oauth_accounts"

    google_subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    spreadsheet_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    spreadsheet_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_root_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drive_root_folder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_output_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_output_folder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_output_folder_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
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
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class TemplateOutputFolder(Base):
    __tablename__ = "template_output_folders"

    google_subject: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("google_oauth_accounts.google_subject", ondelete="CASCADE"),
        primary_key=True,
    )
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), primary_key=True
    )
    folder_id: Mapped[str] = mapped_column(String(255), nullable=False)
    folder_name: Mapped[str] = mapped_column(String(255), nullable=False)
    folder_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TemplateCandidateSet(Base):
    __tablename__ = "template_candidate_sets"
    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "normalized_name",
            name="uq_template_candidate_sets_template_name",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False)
    values_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    allowed_suffixes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TemplateCandidateBinding(Base):
    __tablename__ = "template_candidate_bindings"
    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "target_pattern",
            name="uq_template_candidate_bindings_target",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_set_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("template_candidate_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_pattern: Mapped[str] = mapped_column(String(200), nullable=False)
    field_label: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
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


class TemplateDefinitionRevision(Base):
    __tablename__ = "template_definition_revisions"
    __table_args__ = (
        UniqueConstraint(
            "template_version_id",
            "revision",
            name="uq_template_definition_revision",
        ),
        Index(
            "ix_template_definition_latest",
            "template_version_id",
            "revision",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    template_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("template_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class OutputLayoutTemplateRevision(Base):
    __tablename__ = "output_layout_template_revisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_key",
            "template_version_id",
            "revision",
            name="uq_output_layout_template_revision",
        ),
        Index(
            "ix_output_layout_template_latest",
            "owner_key",
            "template_version_id",
            "revision",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    owner_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    template_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("template_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    layout_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_sheet_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_job_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("processing_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


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
    template_snapshot_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
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


class DocumentFolder(Base):
    __tablename__ = "document_folders"
    __table_args__ = (
        Index(
            "uq_document_folders_root_normalized_name",
            "normalized_name",
            unique=True,
            sqlite_where=text("parent_id IS NULL"),
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index(
            "uq_document_folders_child_normalized_name",
            "parent_id",
            "normalized_name",
            unique=True,
            sqlite_where=text("parent_id IS NOT NULL"),
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    parent_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("document_folders.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class DocumentFolderDriveMapping(Base):
    __tablename__ = "document_folder_drive_mappings"
    __table_args__ = (
        UniqueConstraint(
            "google_subject",
            "document_folder_id",
            "drive_root_folder_id",
            name="uq_document_folder_drive_mapping_local_root",
        ),
        UniqueConstraint(
            "google_subject",
            "drive_folder_id",
            name="uq_document_folder_drive_mapping_remote",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    google_subject: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("google_oauth_accounts.google_subject", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_folder_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("document_folders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    drive_root_folder_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    drive_folder_id: Mapped[str] = mapped_column(String(255), nullable=False)
    drive_name: Mapped[str] = mapped_column(String(255), nullable=False)
    drive_parent_folder_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    drive_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class DocumentLibrarySyncState(Base):
    __tablename__ = "document_library_sync_states"

    google_subject: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("google_oauth_accounts.google_subject", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="IDLE")
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflicts_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    drive_change_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_roots_json: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class SpreadsheetOutputOperation(Base):
    __tablename__ = "spreadsheet_output_operations"

    operation_id: Mapped[str] = mapped_column(String(124), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ledger_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    google_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    drive_root_folder_id: Mapped[str] = mapped_column(String(255), nullable=False)
    drive_parent_folder_id: Mapped[str] = mapped_column(String(255), nullable=False)
    drive_name: Mapped[str] = mapped_column(String(180), nullable=False)
    drive_folder_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    library_folder_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    library_location_version: Mapped[int] = mapped_column(Integer, nullable=False)
    spreadsheet_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class LedgerDocument(Base):
    __tablename__ = "ledger_documents"
    __table_args__ = (Index("ix_ledger_documents_identity", "template_id", "individual_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    individual_id: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    current_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True
    )
    current_document_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    spreadsheet_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    spreadsheet_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_google_subject: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    drive_root_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drive_ownership_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    drive_ownership_confirmation_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    drive_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drive_folder_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    library_folder_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("document_folders.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    library_location_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    library_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    document_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    identity_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    identity_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    library_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    spreadsheet_sync_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    spreadsheet_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class LedgerDocumentPage(Base):
    __tablename__ = "ledger_document_pages"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_ledger_document_pages_job_id"),
        Index("ix_ledger_document_pages_position", "ledger_document_id", "page_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    ledger_document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ledger_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    replaces_page_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ledger_document_pages.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    page_content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    content_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    job: Mapped[ProcessingJob] = relationship(back_populates="inputs")


class JobArtifact(Base):
    __tablename__ = "job_artifacts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number", "artifact_name", name="uq_job_artifact_name"),
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

    job: Mapped[ProcessingJob] = relationship(back_populates="artifacts", foreign_keys=[job_id])


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
    __table_args__ = (UniqueConstraint("ocr_result_id", "revision", name="uq_ocr_result_revision"),)

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


class SpreadsheetEditEvent(Base):
    __tablename__ = "spreadsheet_edit_events"

    edit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("processing_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    record_id: Mapped[str] = mapped_column(String(200), nullable=False)
    field_id: Mapped[str] = mapped_column(String(300), nullable=False)
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_sheet: Mapped[str] = mapped_column(String(200), nullable=False)
    source_cell: Mapped[str] = mapped_column(String(50), nullable=False)
    spreadsheet_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    individual_id: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    confirmed_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    export_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    spreadsheet_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    spreadsheet_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    spreadsheet_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    spreadsheet_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spreadsheet_sync_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    spreadsheet_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
