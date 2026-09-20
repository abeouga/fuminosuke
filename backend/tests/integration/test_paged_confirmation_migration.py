import json
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config
from app.db.models import ProcessingJob


def test_confirmation_migration_preserves_existing_page_values(monkeypatch, tmp_path):
    data = tmp_path / "migration"
    data.mkdir()
    path = data / "app.db"
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    monkeypatch.setenv("LOG_FILE", str(data / "app.log"))
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "0032_paged_review")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with Session(engine) as session:
        job = ProcessingJob(
            job_type="EXTRACT_DOCUMENT",
            status="REVIEW_REQUIRED",
            request_hash="a" * 64,
            config_snapshot_json={"preserve": True},
        )
        session.add(job)
        session.commit()
        job_id = job.id
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO paged_reviews "
                "(job_id,snapshot_sha256,raw_pages_json,current_pages_json,revision) "
                "VALUES (:job,:sha,:raw,:current,7)"
            ),
            {
                "job": job_id,
                "sha": "b" * 64,
                "raw": json.dumps({"raw": "原候補"}),
                "current": json.dumps({"corrected": "補正済み"}),
            },
        )
        before = dict(connection.execute(text("SELECT * FROM paged_reviews")).mappings().one())
        old_job = dict(connection.execute(text("SELECT * FROM processing_jobs")).mappings().one())
    for revision in ("0033_paged_confirmation", "0032_paged_review", "0033_paged_confirmation"):
        if revision == "0032_paged_review":
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)
        with engine.connect() as connection:
            row = dict(connection.execute(text("SELECT * FROM paged_reviews")).mappings().one())
            assert {k: row[k] for k in before} == before
            assert (
                dict(connection.execute(text("SELECT * FROM processing_jobs")).mappings().one())
                == old_job
            )
        assert ("paged_confirmations" in inspect(engine).get_table_names()) == (
            revision == "0033_paged_confirmation"
        )
    engine.dispose()
