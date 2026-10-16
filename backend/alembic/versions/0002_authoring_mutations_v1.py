"""Shared authoring revision and durable mutation receipt foundation."""
from alembic import op

revision = "authoring_mutations_v1"
down_revision = "generic_schema_v2"
branch_labels = None
depends_on = None

_TABLES = (
    """CREATE TABLE mutation_requests (
	"key" VARCHAR(128) NOT NULL,
	owner_id VARCHAR(100) NOT NULL,
	resource_kind VARCHAR(40) NOT NULL,
	resource_id VARCHAR(36) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	state VARCHAR(20) NOT NULL,
	result_json JSON,
	error_json JSON,
	created_at DATETIME NOT NULL,
	completed_at DATETIME,
	PRIMARY KEY ("key"),
	CHECK (state IN ('PENDING', 'SUCCEEDED', 'FAILED'))
)""",
    """CREATE INDEX ix_mutation_requests_resource_id ON mutation_requests (resource_id)""",
    """CREATE TABLE resource_operations (
	id VARCHAR(36) NOT NULL,
	request_key VARCHAR(128) NOT NULL,
	resource_kind VARCHAR(40) NOT NULL,
	resource_id VARCHAR(36) NOT NULL,
	operation VARCHAR(100) NOT NULL,
	before_revision INTEGER NOT NULL,
	after_revision INTEGER NOT NULL,
	before_json JSON NOT NULL,
	after_json JSON NOT NULL,
	inverse_json JSON,
	undo_of_id VARCHAR(36),
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CHECK (after_revision > before_revision),
	UNIQUE (request_key),
	FOREIGN KEY(request_key) REFERENCES mutation_requests ("key"),
	UNIQUE (undo_of_id),
	FOREIGN KEY(undo_of_id) REFERENCES resource_operations (id)
)""",
    """CREATE INDEX ix_resource_operations_resource_id ON resource_operations (resource_id)""",
    """CREATE TABLE template_authoring_revisions (
	template_version_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	source VARCHAR(100) NOT NULL,
	status VARCHAR(20) NOT NULL,
	config_relative_path TEXT NOT NULL,
	layout_json JSON NOT NULL,
	quality_json JSON NOT NULL,
	clean_template_artifact_id VARCHAR(36),
	mask_artifact_id VARCHAR(36),
	created_at DATETIME NOT NULL,
	PRIMARY KEY (template_version_id, revision),
	FOREIGN KEY(template_version_id) REFERENCES template_versions (id) ON DELETE CASCADE
)""",
    """CREATE TABLE ocr_confirmation_revisions (
	job_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	confirmed_json JSON NOT NULL,
	confirmed_at DATETIME NOT NULL,
	document_name TEXT,
	export_relative_path TEXT NOT NULL,
	PRIMARY KEY (job_id, revision),
	FOREIGN KEY(job_id) REFERENCES processing_jobs (id) ON DELETE CASCADE
)""",
)


def upgrade():
    connection = op.get_bind()
    connection.exec_driver_sql("ALTER TABLE template_versions ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)")
    for statement in _TABLES:
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql("""INSERT INTO template_authoring_revisions
        (template_version_id, revision, source, status, config_relative_path, layout_json, quality_json,
         clean_template_artifact_id, mask_artifact_id, created_at)
        SELECT id, revision, 'migration', status, config_relative_path, layout_json, quality_json,
               clean_template_artifact_id, mask_artifact_id, CURRENT_TIMESTAMP
        FROM template_versions""")
    connection.exec_driver_sql("""INSERT INTO ocr_confirmation_revisions
        (job_id, revision, confirmed_json, confirmed_at, document_name, export_relative_path)
        SELECT job_id, ocr_result_revision, confirmed_json, confirmed_at, document_name, export_relative_path
        FROM confirmed_results""")


def downgrade():
    raise RuntimeError("Restore the protected pre-migration DB and matching artifacts; history must not be discarded")
