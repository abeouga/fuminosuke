"""Create the Phase 1 local persistence schema."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "templates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("active_version_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["active_version_id"], ["template_versions.id"], name="fk_templates_active_version_id"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("template_version_id", sa.String(length=36), nullable=True),
        sa.Column("mode", sa.String(length=40), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("progress_stage", sa.String(length=50), nullable=True),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("config_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message_safe", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["template_version_id"], ["template_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_processing_jobs_idempotency_key", "processing_jobs", ["idempotency_key"])
    op.create_index("ix_processing_jobs_job_type", "processing_jobs", ["job_type"])
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"])
    op.create_table(
        "job_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("artifact_name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id", "attempt_number", "artifact_name", name="uq_job_artifact_name"
        ),
    )
    op.create_index("ix_job_artifacts_job_id", "job_artifacts", ["job_id"])
    op.create_table(
        "template_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("canonical_width", sa.Integer(), nullable=False),
        sa.Column("canonical_height", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.String(length=36), nullable=True),
        sa.Column("clean_template_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("mask_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("config_relative_path", sa.Text(), nullable=False),
        sa.Column("quality_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["clean_template_artifact_id"], ["job_artifacts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["mask_artifact_id"], ["job_artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_job_id"], ["processing_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "version_number", name="uq_template_version_number"),
    )
    op.create_index("ix_template_versions_template_id", "template_versions", ["template_id"])
    op.create_table(
        "job_inputs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("input_index", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("file_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_inputs_job_id", "job_inputs", ["job_id"])
    op.create_table(
        "ocr_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("raw_candidate_json", sa.JSON(), nullable=False),
        sa.Column("current_corrected_json", sa.JSON(), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("api_call_count", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_table(
        "ocr_result_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("ocr_result_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ocr_result_id"], ["ocr_results.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ocr_result_id", "revision", name="uq_ocr_result_revision"),
    )
    op.create_index(
        "ix_ocr_result_revisions_ocr_result_id", "ocr_result_revisions", ["ocr_result_id"]
    )
    op.create_table(
        "confirmed_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("ocr_result_revision", sa.Integer(), nullable=False),
        sa.Column("confirmed_json", sa.JSON(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("export_relative_path", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("event_code", sa.String(length=100), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_events_job_id", table_name="job_events")
    op.drop_table("job_events")
    op.drop_table("confirmed_results")
    op.drop_index("ix_ocr_result_revisions_ocr_result_id", table_name="ocr_result_revisions")
    op.drop_table("ocr_result_revisions")
    op.drop_table("ocr_results")
    op.drop_index("ix_job_inputs_job_id", table_name="job_inputs")
    op.drop_table("job_inputs")
    op.drop_index("ix_template_versions_template_id", table_name="template_versions")
    op.drop_table("template_versions")
    op.drop_index("ix_job_artifacts_job_id", table_name="job_artifacts")
    op.drop_table("job_artifacts")
    op.drop_index("ix_processing_jobs_status", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_job_type", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_idempotency_key", table_name="processing_jobs")
    op.drop_table("processing_jobs")
    op.drop_table("templates")
