from pathlib import Path

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config
from app.db.models import ProcessingJob


def test_page_review_upgrade_and_downgrade_preserve_legacy_snapshot(monkeypatch, tmp_path):
    data_dir = tmp_path / "migration"
    data_dir.mkdir()
    database_path = data_dir / "app.db"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("LOG_FILE", str(data_dir / "logs" / "app.log"))
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "0031_template_field_identity")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with Session(engine) as session:
        legacy = ProcessingJob(
            job_type="EXTRACT_DOCUMENT",
            status="FAILED",
            request_hash="f" * 64,
            config_snapshot_json={"immutable": {"mapping": "arbitrary-field"}},
        )
        session.add(legacy)
        session.commit()
        job_id = legacy.id
        before = session.execute(select(ProcessingJob.__table__)).mappings().all()
    for revision in ("0032_paged_review", "0031_template_field_identity", "0032_paged_review"):
        if revision == "0031_template_field_identity":
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)
        with Session(engine) as session:
            assert session.execute(select(ProcessingJob.__table__)).mappings().all() == before
            assert session.get(ProcessingJob, job_id).status == "FAILED"
        assert ("paged_reviews" in inspect(engine).get_table_names()) == (
            revision == "0032_paged_review"
        )
    engine.dispose()
