from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config
from app.db.models import ProcessingJob


def test_budget_migration_preserves_accepted_snapshots_without_backfill(monkeypatch, tmp_path):
    path = tmp_path / "migration.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "app.log"))
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "0033_paged_confirmation")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with Session(engine) as session:
        session.add(
            ProcessingJob(
                job_type="EXTRACT_DOCUMENT",
                status="FAILED",
                request_hash="a" * 64,
                config_snapshot_json={"old": "unchanged"},
            )
        )
        session.commit()
    with engine.connect() as connection:
        before = dict(connection.execute(text("SELECT * FROM processing_jobs")).mappings().one())
    for revision in ("0034_paged_ocr_budget", "0033_paged_confirmation", "0034_paged_ocr_budget"):
        if revision == "0033_paged_confirmation":
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)
        tables = inspect(engine).get_table_names()
        assert ("paged_ocr_budgets" in tables) == (revision == "0034_paged_ocr_budget")
        assert ("paged_ocr_calls" in tables) == (revision == "0034_paged_ocr_budget")
        with engine.connect() as connection:
            assert (
                dict(connection.execute(text("SELECT * FROM processing_jobs")).mappings().one())
                == before
            )
            if revision == "0034_paged_ocr_budget":
                assert connection.scalar(text("SELECT COUNT(*) FROM paged_ocr_budgets")) == 0
                assert connection.scalar(text("SELECT COUNT(*) FROM paged_ocr_calls")) == 0
    engine.dispose()
