from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.pool import StaticPool

from app.ai.budget_contract import ReservationBudget
from app.core.config import DatabaseSettings
from app.db.mutation import revision_conflict


def create_sqlite_engine(settings: DatabaseSettings) -> Engine:
    if make_url(settings.url).get_backend_name() != "sqlite":
        raise ValueError("SQLite engine requires a SQLite URL")
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


def create_database_engine(settings: DatabaseSettings) -> Engine:
    if make_url(settings.url).get_backend_name() == "sqlite":
        return create_sqlite_engine(settings)
    if make_url(settings.url).drivername != "postgresql+psycopg" or not settings.schema_name:
        raise ValueError("PostgreSQL requires psycopg and an explicit tenant schema")
    return create_engine(
        settings.url, pool_size=2, max_overflow=1, pool_pre_ping=True, pool_recycle=300,
        connect_args={"options": f"-c search_path={settings.schema_name} -c lock_timeout=10000"},
    )


def begin_write(session: Session) -> None:
    """Lock before reading mutation state, within this tenant's transaction."""
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
    else:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 0))"))


class Database:
    def __init__(
        self, settings: DatabaseSettings, *, access_check: Callable[[], None] | None = None,
        api_budget: ReservationBudget | None = None,
        before_delete: Callable[[str, str], None] | None = None,
        transaction_fence: Callable[[Connection], None] | None = None,
    ) -> None:
        self.api_budget, self.before_delete = api_budget, before_delete
        self.engine = create_database_engine(settings)
        if access_check is not None:
            # Covers ORM, Core and raw SQL, including background work and commit-time flushes.
            event.listen(self.engine, "before_cursor_execute", lambda *_: access_check())
            event.listen(self.engine, "commit", lambda *_: access_check())
        if transaction_fence is not None:
            event.listen(self.engine, "before_cursor_execute",
                         lambda conn, *_: transaction_fence(conn))
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
        except StaleDataError as exc:
            session.rollback()
            raise revision_conflict() from exc
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Own the commit; domain operations only flush this supplied session."""
        with self.session() as session:
            begin_write(session)
            session.info["mutation_transaction_owned"] = True
            try:
                yield session
                session.info["mutation_owner_committing"] = True
                session.commit()
            except BaseException:
                session.rollback()
                raise
            finally:
                session.info.pop("mutation_transaction_owned", None)
                session.info.pop("mutation_owner_committing", None)

    def check_connection(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()
