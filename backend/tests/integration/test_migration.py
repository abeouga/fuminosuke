import json
from pathlib import Path

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
        "job_events",
        "alembic_version",
    } <= tables
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0009_user_spreadsheet_targets"
        )
    oauth_columns = {
        column["name"] for column in inspect(engine).get_columns("google_oauth_accounts")
    }
    assert {"spreadsheet_id", "spreadsheet_url"} <= oauth_columns
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
    assert keys == [("job-a", "duplicate-key"), ("job-b", None)]


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
