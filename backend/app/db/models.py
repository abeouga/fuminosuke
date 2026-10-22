from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


class TemplateDisplayName(Base):
    """A name a person gave an item on the result screen (e.g. 「番地)」→「番地」).

    Display only: saved values, keys, mappings, OCR requests and accepted snapshots keep
    the template's own name. Kept per template series, so every result of it shows it.
    """

    __tablename__ = "template_display_names"
    __table_args__ = (
        UniqueConstraint("template_id", "target_path", name="uq_template_display_names_target"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_path: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TemplateDocumentName(Base):
    """How a document of a template series is named (changed after the template is made).

    Only proposes the name typed on the review screen; it replaces the template's own
    ``documentName`` without a new version, and readings and snapshots are unchanged.
    ``rule_json``: ``{fieldPaths, separator, prefix, suffix}``.
    """

    __tablename__ = "template_document_names"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    rule_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TemplateItemJoin(Base):
    """Items shown as one value on the result screen and the list XLSX (e.g. an address).

    Display only, per template series: each item keeps its own reading, crop, warning and
    correction; the joined value is built from them in the given order with the printed
    text around each item (``members_json``: ``[{targetPath, before, after}]``).
    """

    __tablename__ = "template_item_joins"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # "join": one value; "order": only the display order of the items under a heading.
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="join")
    # Put between two non-empty items of a join that have no printed text between them.
    separator: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    members_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TemplateFieldHint(Base):
    """Reading settings of a template series (keywords and an instruction).

    One entry covers one item: a single field, or every row cell of a table column.
    A target belongs to at most one entry (checked by the service).
    """

    __tablename__ = "template_field_hints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_patterns_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    field_label: Mapped[str] = mapped_column(String(100), nullable=False)
    keywords_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    keywords_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    keywords_forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # No longer read or written (2026-10-22): the reading instruction is the item's memory
    # (``template_field_memories``); kept so old rows stay readable for the migration.
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TemplateFieldMemory(Base):
    """A writing rule learned from corrections made before confirming (2026-10-22).

    Rules only (e.g. thousands separated by a comma), never a document's own values. The
    rules of an item are sent as its reading instruction; accepted jobs keep the rules
    they were accepted with (``templateFieldHints``).
    """

    __tablename__ = "template_field_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # One item: a field, or every row cell of a table column.
    target_patterns_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    field_label: Mapped[str] = mapped_column(String(100), nullable=False)
    rule: Mapped[str] = mapped_column(String(200), nullable=False)
    example_before: Mapped[str | None] = mapped_column(String(200), nullable=True)
    example_after: Mapped[str | None] = mapped_column(String(200), nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TemplateFieldMemoryRun(Base):
    """A confirmation already learned from, so a re-confirmation is not learned twice."""

    __tablename__ = "template_field_memory_runs"
    __table_args__ = (
        UniqueConstraint("job_id", "revision", name="uq_template_field_memory_runs_job"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version_number", name="uq_template_version_number"),
        CheckConstraint("revision >= 1"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": revision}
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


class TemplateLogicalField(Base):
    __tablename__ = "template_logical_fields"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False
    )


class TemplateFieldIdentity(Base):
    __tablename__ = "template_field_identities"

    template_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_versions.id", ondelete="CASCADE"), primary_key=True
    )
    mapping_id: Mapped[str] = mapped_column(String(220), primary_key=True)
    logical_field_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_logical_fields.id"), nullable=False
    )
    present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


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


class TemplatePackageIdentity(Base):
    __tablename__ = "template_package_identities"

    publisher_id: Mapped[str] = mapped_column(String(253), primary_key=True)
    package_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    local_template_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("templates.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TemplatePackageInstallation(Base):
    __tablename__ = "template_package_installations"
    __table_args__ = (
        UniqueConstraint(
            "publisher_id",
            "package_id",
            "release_version",
            name="uq_template_package_installation_release",
        ),
        ForeignKeyConstraint(
            ["publisher_id", "package_id"],
            [
                "template_package_identities.publisher_id",
                "template_package_identities.package_id",
            ],
            ondelete="CASCADE",
            name="fk_template_package_installation_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    publisher_id: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    package_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    release_version: Mapped[str] = mapped_column(String(100), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    local_template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False
    )
    local_template_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("template_versions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    local_job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(24), nullable=False, default="FILE")
    trust_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="UNVERIFIED"
    )
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    canonical_template_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    canonical_quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_layout_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    local_mapping_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    installed_at: Mapped[datetime] = mapped_column(
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
    confirmed_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    export_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    document_name: Mapped[str | None] = mapped_column(String(180), nullable=True)




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


class PagedReview(Base):
    """One revision covers every page; legacy OcrResult remains untouched."""

    __tablename__ = "paged_reviews"

    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_pages_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    current_pages_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document_name: Mapped[str | None] = mapped_column(String(180), nullable=True)


class PagedReviewRevision(Base):
    __tablename__ = "paged_review_revisions"

    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("paged_reviews.job_id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    page_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    pages_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PagedConfirmation(Base):
    __tablename__ = "paged_confirmations"

    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("paged_reviews.job_id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pages_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    document_name: Mapped[str] = mapped_column(String(180), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PagedOcrBudget(Base):
    __tablename__ = "paged_ocr_budgets"
    __table_args__ = (
        CheckConstraint("max_calls > 0 AND used_calls >= 0 AND used_calls <= max_calls"),
    )

    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    used_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PagedOcrCall(Base):
    __tablename__ = "paged_ocr_calls"
    __table_args__ = (CheckConstraint("call_number > 0 AND attempt_number > 0"),)

    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("paged_ocr_budgets.job_id", ondelete="CASCADE"), primary_key=True
    )
    call_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class DocumentSeriesLink(Base):
    """A job read as the next version of an earlier confirmed job (same paper document).

    Only the predecessor's confirmed snapshot it was based on is recorded; the predecessor
    itself is never modified. One predecessor has at most one successor (no branching).
    """

    __tablename__ = "document_series_links"
    __table_args__ = (
        CheckConstraint("job_id <> predecessor_job_id"),
        CheckConstraint("predecessor_revision > 0"),
        CheckConstraint("mode IN ('full', 'blank_only', 'compare_all')"),
    )

    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    predecessor_job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("processing_jobs.id"), nullable=False, unique=True
    )
    series_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    predecessor_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    predecessor_confirmed_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
