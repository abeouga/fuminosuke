from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import DatabaseSettings


def create_sqlite_engine(settings: DatabaseSettings) -> Engine:
    database_name = make_url(settings.url).database
    if database_name and database_name != ":memory:":
        Path(database_name).parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {
        "connect_args": {"check_same_thread": False},
        "pool_pre_ping": True,
    }
    if settings.url.endswith(":memory:"):
        kwargs["poolclass"] = StaticPool

    engine = create_engine(settings.url, **kwargs)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        if not settings.url.endswith(":memory:"):
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


class Database:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.engine = create_sqlite_engine(settings)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def check_connection(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()
