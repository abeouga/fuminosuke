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
        "job_events",
        "alembic_version",
    } <= tables
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0002_individual_record_index"
        )
    confirmed_columns = {
        column["name"] for column in inspect(engine).get_columns("confirmed_results")
    }
    assert "individual_id" in confirmed_columns
    confirmed_indexes = {
        index["name"] for index in inspect(engine).get_indexes("confirmed_results")
    }
    assert "ix_confirmed_results_individual_id" in confirmed_indexes
