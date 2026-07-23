from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import RawSettings, Settings
from app.db.base import Base
from app.db.session import Database
from app.main import create_app


@pytest.fixture
def settings_factory(tmp_path: Path) -> Callable[..., Settings]:
    def factory(**overrides: object) -> Settings:
        data_dir = Path(overrides.pop("data_dir", tmp_path / "data"))
        values: dict[str, object] = {
            "app_env": "test",
            "data_dir": data_dir,
            "database_url": f"sqlite:///{(data_dir / 'app.db').as_posix()}",
            "log_file": data_dir / "logs" / "app.log",
            "ocr_provider": "mock",
        }
        values.update(overrides)
        # Unit/integration tests must not inherit the developer's real .env
        # (especially OCR provider, API key, or persistent database paths).
        return Settings.from_raw(RawSettings(_env_file=None, **values))

    return factory


@pytest.fixture
def client(settings_factory: Callable[..., Settings]) -> Iterator[TestClient]:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    database.dispose()
    with TestClient(create_app(settings)) as test_client:
        yield test_client
