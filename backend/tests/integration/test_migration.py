import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic import command
from alembic.config import Config


def test_initial_migration_creates_all_tables(monkeypatch: object, tmp_path: Path) -> None:
    data_dir = tmp_path / "migration-data"
    data_dir.mkdir()
    database_path = data_dir / "app.db"
    monkeypatch.setenv("DATA_DIR", str(data_dir))  # type: ignore[attr-defined]
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")  # type: ignore[attr-defined]
    monkeypatch.setenv("LOG_FILE", str(data_dir / "logs" / "app.log"))  # type: ignore[attr-defined]

    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    tables = set(inspect(engine).get_table_names())
    assert {
        "templates",
        "template_versions",
        "processing_jobs",
        "job_inputs",
        "job_artifacts",
        "ocr_results",
        "ocr_result_revisions",
        "confirmed_results",
        "confirmed_record_fields",
        "sire_names",
        "google_oauth_accounts",
        "spreadsheet_edit_events",
        "template_definition_revisions",
        "output_layout_template_revisions",
        "template_output_folders",
        "template_candidate_sets",
        "template_candidate_bindings",
        "template_package_identities",
        "template_package_installations",
        "template_package_drive_release_syncs",
        "template_package_drive_draft_syncs",
        "document_folders",
        "document_folder_drive_mappings",
        "document_library_sync_states",
        "spreadsheet_output_operations",
        "ledger_documents",
        "ledger_document_pages",
        "job_events",
        "alembic_version",
    } <= tables
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0030_template_package_drive_sync"
        )
    package_identity_columns = {
        column["name"]
        for column in inspect(engine).get_columns("template_package_identities")
    }
    assert {"publisher_id", "package_id", "local_template_id"} <= package_identity_columns
    package_installation_columns = {
        column["name"]
        for column in inspect(engine).get_columns("template_package_installations")
    }
    assert {
        "publisher_id",
        "package_id",
        "release_version",
        "content_digest",
        "local_template_id",
        "local_template_version_id",
        "local_job_id",
        "manifest_json",
        "canonical_template_json",
        "canonical_quality_json",
        "output_layout_json",
        "local_mapping_json",
    } <= package_installation_columns
    package_installation_unique = {
        constraint["name"]
        for constraint in inspect(engine).get_unique_constraints(
            "template_package_installations"
        )
    }
    assert "uq_template_package_installation_release" in package_installation_unique
    release_sync_columns = {
        column["name"]
        for column in inspect(engine).get_columns("template_package_drive_release_syncs")
    }
    assert {
        "google_subject",
        "publisher_id",
        "package_id",
        "release_version",
        "content_digest",
        "package_sha256",
        "drive_file_id",
        "drive_folder_id",
        "drive_version",
        "drive_head_revision_id",
        "drive_etag",
    } <= release_sync_columns
    draft_sync_columns = {
        column["name"]
        for column in inspect(engine).get_columns("template_package_drive_draft_syncs")
    }
    assert {
        "google_subject",
        "draft_id",
        "publisher_id",
        "package_id",
        "revision",
        "base_release_version",
        "content_digest",
        "package_sha256",
        "drive_file_id",
        "drive_folder_id",
        "drive_version",
        "drive_head_revision_id",
        "drive_etag",
    } <= draft_sync_columns
    oauth_columns = {
        column["name"] for column in inspect(engine).get_columns("google_oauth_accounts")
    }
    assert {
        "spreadsheet_id",
        "spreadsheet_url",
        "drive_root_folder_id",
        "default_output_folder_id",
        "default_output_folder_path",
    } <= oauth_columns
    ledger_columns = {column["name"] for column in inspect(engine).get_columns("ledger_documents")}
    assert {
        "spreadsheet_id",
        "spreadsheet_url",
        "drive_google_subject",
        "drive_ownership_confirmed_at",
        "drive_ownership_confirmation_source",
        "drive_folder_id",
        "drive_root_folder_id",
        "drive_folder_path",
        "spreadsheet_sync_status",
        "spreadsheet_sync_error",
    } <= ledger_columns
    mapping_columns = {
        column["name"]
        for column in inspect(engine).get_columns("document_folder_drive_mappings")
    }
    assert {
        "google_subject",
        "document_folder_id",
        "drive_root_folder_id",
        "drive_folder_id",
        "drive_name",
        "drive_parent_folder_id",
        "drive_modified_at",
        "last_synced_at",
    } <= mapping_columns
    mapping_unique_constraints = {
        constraint["name"]
        for constraint in inspect(engine).get_unique_constraints(
            "document_folder_drive_mappings"
        )
    }
    assert {
        "uq_document_folder_drive_mapping_local_root",
        "uq_document_folder_drive_mapping_remote",
    } <= mapping_unique_constraints
    sync_state_columns = {
        column["name"]
        for column in inspect(engine).get_columns("document_library_sync_states")
    }
    assert {
        "google_subject",
        "status",
        "last_attempt_at",
        "last_success_at",
        "error",
        "conflicts_json",
        "updated_at",
    } <= sync_state_columns
    operation_columns = {
        column["name"]
        for column in inspect(engine).get_columns("spreadsheet_output_operations")
    }
    assert {
        "operation_id",
        "document_id",
        "google_subject",
        "source_job_id",
        "source_revision",
        "drive_root_folder_id",
        "drive_parent_folder_id",
        "drive_name",
        "library_location_version",
        "spreadsheet_id",
    } <= operation_columns
    confirmed_columns = {
        column["name"] for column in inspect(engine).get_columns("confirmed_results")
    }
    assert "individual_id" in confirmed_columns
    confirmed_indexes = {
        index["name"] for index in inspect(engine).get_indexes("confirmed_results")
    }
    assert "ix_confirmed_results_individual_id" in confirmed_indexes
    template_version_columns = {
        column["name"] for column in inspect(engine).get_columns("template_versions")
    }
    assert "layout_json" in template_version_columns
    record_field_indexes = {
        index["name"] for index in inspect(engine).get_indexes("confirmed_record_fields")
    }
    assert "ix_confirmed_record_field_lookup" in record_field_indexes
    job_indexes = {index["name"]: index for index in inspect(engine).get_indexes("processing_jobs")}
    assert job_indexes["ix_processing_jobs_idempotency_key"]["unique"] == 1
    processing_job_columns = {
        column["name"] for column in inspect(engine).get_columns("processing_jobs")
    }
    job_input_columns = {column["name"] for column in inspect(engine).get_columns("job_inputs")}
    assert "template_snapshot_bytes" in processing_job_columns
    assert "content_bytes" in job_input_columns
    with engine.connect() as connection:
        sire_names = list(
            connection.scalars(text("SELECT name FROM sire_names ORDER BY created_at, id"))
        )
    assert sire_names == ["勝忠平", "光晴重", "重波平", "百合茂"]


def test_upgrade_deduplicates_legacy_idempotency_keys(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    database_path, config = _migration_database(monkeypatch, tmp_path, "duplicates")
    command.upgrade(config, "0005_sire_name_pool")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        for job_id, created_at in (
            ("job-a", "2026-07-01 00:00:00"),
            ("job-b", "2026-07-02 00:00:00"),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO processing_jobs (
                        id, job_type, status, idempotency_key, request_hash,
                        attempt_number, progress_percent, cancel_requested,
                        config_snapshot_json, created_at
                    ) VALUES (
                        :id, 'EXTRACT_DOCUMENT', 'FAILED', 'duplicate-key',
                        :request_hash, 1, 0, 0, '{}', :created_at
                    )
                    """
                ),
                {
                    "id": job_id,
                    "request_hash": job_id * 32,
                    "created_at": created_at,
                },
            )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        keys = connection.execute(
            text(
                """
                SELECT id, idempotency_key
                FROM processing_jobs
                ORDER BY id
                """
            )
        ).all()
    assert [tuple(row) for row in keys] == [("job-a", "duplicate-key"), ("job-b", None)]


def test_upgrade_repairs_individual_id_from_corrected_normalized_reading(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    database_path, config = _migration_database(monkeypatch, tmp_path, "individual-id")
    command.upgrade(config, "0006_unique_job_idempotency_key")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    confirmed_document = {
        "document": {
            "unifiedEarTag": {
                "raw": "OCR誤読",
                "normalized": "123-4567-890",
            }
        }
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO processing_jobs (
                    id, job_type, status, request_hash, attempt_number,
                    progress_percent, cancel_requested, config_snapshot_json,
                    created_at
                ) VALUES (
                    'job-confirmed', 'EXTRACT_DOCUMENT', 'CONFIRMED',
                    :request_hash, 1, 100, 0, '{}', '2026-07-01 00:00:00'
                )
                """
            ),
            {"request_hash": "a" * 64},
        )
        connection.execute(
            text(
                """
                INSERT INTO confirmed_results (
                    id, job_id, ocr_result_revision, confirmed_json,
                    confirmed_at, export_relative_path, individual_id
                ) VALUES (
                    'confirmed-1', 'job-confirmed', 1, :document,
                    '2026-07-01 00:00:00', 'exports/result.json', NULL
                )
                """
            ),
            {"document": json.dumps(confirmed_document, ensure_ascii=False)},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        repaired = connection.scalar(
            text(
                """
                SELECT individual_id
                FROM confirmed_results
                WHERE id = 'confirmed-1'
                """
            )
        )
    assert repaired == "1234567890"


def test_drive_sync_migration_backfills_legacy_root_and_unambiguous_owner(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    database_path, config = _migration_database(monkeypatch, tmp_path, "drive-owner")
    command.upgrade(config, "0023_document_matching_and_names")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        for subject, email, root_id, active in (
            ("subject-a", "a@example.com", "root-a", 0),
            ("subject-b", "b@example.com", "root-b", 1),
            ("subject-c", "c@example.com", "shared-root", 0),
            ("subject-d", "d@example.com", "shared-root", 0),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO google_oauth_accounts (
                        google_subject, email, encrypted_refresh_token, scopes,
                        default_output_folder_id, is_active, created_at, updated_at
                    ) VALUES (
                        :subject, :email, 'encrypted', 'drive.file',
                        :root_id, :active, '2026-08-01 00:00:00',
                        '2026-08-01 00:00:00'
                    )
                    """
                ),
                {
                    "subject": subject,
                    "email": email,
                    "root_id": root_id,
                    "active": active,
                },
            )
        connection.execute(
            text(
                """
                INSERT INTO templates (
                    id, name, document_type, created_at, updated_at
                ) VALUES (
                    'template-1', '帳票', 'default',
                    '2026-08-01 00:00:00', '2026-08-01 00:00:00'
                )
                """
            )
        )
        for document_id, drive_folder_id in (
            ("document-a", "root-a"),
            ("document-shared", "shared-root"),
            ("document-unknown", "unknown-root"),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO ledger_documents (
                        id, template_id, individual_id, current_document_json,
                        drive_folder_id, created_at, updated_at
                    ) VALUES (
                        :id, 'template-1', '1234567890', '{}', :drive_folder_id,
                        '2026-08-01 00:00:00', '2026-08-01 00:00:00'
                    )
                    """
                ),
                {"id": document_id, "drive_folder_id": drive_folder_id},
            )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, drive_root_folder_id, drive_google_subject
                FROM ledger_documents
                ORDER BY id
                """
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("document-a", "root-a", None),
        ("document-shared", "shared-root", None),
        ("document-unknown", "unknown-root", None),
    ]


def test_migration_backfills_current_confirmed_spreadsheet_binding(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    database_path, config = _migration_database(monkeypatch, tmp_path, "sheet-binding")
    command.upgrade(config, "0026_drive_change_token")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO templates (
                id, name, document_type, created_at, updated_at
            ) VALUES (
                'template-binding', '帳票', 'default', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            INSERT INTO template_versions (
                id, template_id, version_number, mode, status,
                canonical_width, canonical_height, config_relative_path,
                quality_json, layout_json, created_at
            ) VALUES (
                'version-binding', 'template-binding', 1, 'REFERENCE_TEMPLATE',
                'ACTIVE', 1000, 1400, 'templates/binding.json', '{}', '{}',
                CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            INSERT INTO processing_jobs (
                id, job_type, status, template_version_id, request_hash,
                attempt_number, progress_percent, cancel_requested,
                config_snapshot_json, created_at
            ) VALUES (
                'job-binding', 'EXTRACT_DOCUMENT', 'CONFIRMED', 'version-binding',
                :request_hash, 1, 100, 0, '{}', CURRENT_TIMESTAMP
            )
        """), {"request_hash": "b" * 64})
        connection.execute(text("""
            INSERT INTO ledger_documents (
                id, template_id, individual_id, current_job_id,
                current_document_json, library_name, created_at, updated_at
            ) VALUES (
                'document-binding', 'template-binding', '1234567890',
                'job-binding', '{}', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            INSERT INTO confirmed_results (
                id, job_id, ocr_result_revision, confirmed_json, confirmed_at,
                export_relative_path, spreadsheet_id, spreadsheet_url,
                spreadsheet_name, spreadsheet_revision, spreadsheet_sync_status
            ) VALUES (
                'confirmed-binding', 'job-binding', 1, '{}', CURRENT_TIMESTAMP,
                'exports/binding.json', 'sheet-binding',
                'https://docs.google.com/spreadsheets/d/sheet-binding/edit',
                '確定済み書類', 1, 'synced'
            )
        """))

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(text("""
            SELECT spreadsheet_id, spreadsheet_url, spreadsheet_sync_status,
                   library_name, document_name
            FROM ledger_documents
            WHERE id = 'document-binding'
        """)).one()
    assert tuple(row) == (
        "sheet-binding",
        "https://docs.google.com/spreadsheets/d/sheet-binding/edit",
        "synced",
        "確定済み書類",
        "確定済み書類",
    )


def test_drive_sync_migration_rejects_duplicates_before_creating_indexes(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    database_path, config = _migration_database(monkeypatch, tmp_path, "folder-duplicates")
    command.upgrade(config, "0023_document_matching_and_names")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO document_folders (
                    id, parent_id, name, normalized_name, created_at, updated_at
                ) VALUES
                    ('parent', NULL, '親', '親', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('child-a', 'parent', '同名', '同名', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('child-b', 'parent', '同名', '同名', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )

    with pytest.raises(RuntimeError, match="Duplicate document-library folder names"):
        command.upgrade(config, "head")

    assert {
        index["name"] for index in inspect(engine).get_indexes("document_folders")
    }.isdisjoint(
        {
            "uq_document_folders_root_normalized_name",
            "uq_document_folders_child_normalized_name",
        }
    )
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0023_document_matching_and_names"
        )


def test_drive_sync_sqlite_downgrade_preserves_ledger_page_history(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    database_path, config = _migration_database(monkeypatch, tmp_path, "safe-downgrade")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.execute(text("""
            INSERT INTO templates (
                id, name, document_type, created_at, updated_at
            ) VALUES (
                'template-downgrade', '帳票', 'default', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            INSERT INTO processing_jobs (
                id, job_type, status, request_hash, attempt_number,
                progress_percent, cancel_requested, config_snapshot_json, created_at
            ) VALUES (
                'job-downgrade', 'EXTRACT_DOCUMENT', 'CONFIRMED', :request_hash,
                1, 100, 0, '{}', CURRENT_TIMESTAMP
            )
        """), {"request_hash": "d" * 64})
        connection.execute(text("""
            INSERT INTO ledger_documents (
                id, template_id, individual_id, current_job_id,
                current_document_json, created_at, updated_at
            ) VALUES (
                'document-downgrade', 'template-downgrade', '1234567890',
                'job-downgrade', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            INSERT INTO ledger_document_pages (
                id, ledger_document_id, job_id, page_number, relation_type,
                active, page_content_json, created_at
            ) VALUES (
                'page-downgrade', 'document-downgrade', 'job-downgrade', 1,
                'ORIGINAL', 1, '{}', CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            INSERT INTO spreadsheet_output_operations (
                operation_id, document_id, google_subject, source_job_id,
                source_revision, drive_root_folder_id, drive_parent_folder_id,
                drive_name, library_location_version, created_at, updated_at
            ) VALUES (
                'operation-downgrade', 'document-downgrade', 'subject-1',
                'job-downgrade', 1, 'root-a', 'root-a', '帳票', 1,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))

    command.downgrade(config, "0023_document_matching_and_names")

    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT COUNT(*) FROM ledger_documents WHERE id='document-downgrade'"
        )) == 1
        assert connection.scalar(text(
            "SELECT COUNT(*) FROM ledger_document_pages WHERE id='page-downgrade'"
        )) == 1
        assert "spreadsheet_output_operations" not in inspect(engine).get_table_names()

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT COUNT(*) FROM ledger_document_pages WHERE id='page-downgrade'"
        )) == 1


def _migration_database(
    monkeypatch: object,
    tmp_path: Path,
    name: str,
) -> tuple[Path, Config]:
    data_dir = tmp_path / name
    data_dir.mkdir()
    database_path = data_dir / "app.db"
    monkeypatch.setenv("DATA_DIR", str(data_dir))  # type: ignore[attr-defined]
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")  # type: ignore[attr-defined]
    monkeypatch.setenv("LOG_FILE", str(data_dir / "logs" / "app.log"))  # type: ignore[attr-defined]
    return database_path, Config(str(Path(__file__).parents[2] / "alembic.ini"))
