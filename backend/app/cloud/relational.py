"""Shared PostgreSQL control storage. Never selects a tenant from a browser parameter."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import DBAPIError


class ControlConnection:
    def __init__(self, connection: Connection, lock_name: str) -> None:
        self.connection, self.lock_name = connection, lock_name

    def execute(self, statement: str, parameters: Sequence[Any] = ()) -> CursorResult[Any]:
        if statement == "BEGIN IMMEDIATE":
            return self.connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"),
                {"name": "fuminosuke-control-" + self.lock_name},
            )
        pieces = statement.split("?")
        if len(pieces) != len(parameters) + 1:
            raise ValueError("Control SQL parameter mismatch")
        sql = "".join(
            part + (f":p{i}" if i < len(parameters) else "") for i, part in enumerate(pieces)
        )
        return self.connection.execute(text(sql), {f"p{i}": v for i, v in enumerate(parameters)})

    def executescript(self, statement: str) -> None:
        # Fixed internal DDL only, never document/user SQL.
        for sql in statement.replace(" REAL", " DOUBLE PRECISION").split(";"):
            if sql.strip():
                self.execute(sql)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()


class ControlDatabase:
    def __init__(self, url: str, installation: str, *, administrative: bool = False) -> None:
        if str(UUID(installation)) != installation:
            raise ValueError("Use the fixed installation UUID")
        self.url, self.installation = url, installation
        self.administrative = administrative
        self.epoch: int | None = None
        self.lease_pid: int | None = None
        bootstrap = create_engine(url, pool_pre_ping=True)
        try:
            with bootstrap.begin() as db:
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended('fuminosuke-bootstrap',0))")
                )
                present = db.execute(
                    text("SELECT 1 FROM pg_namespace WHERE nspname='beta_control'")
                ).first()
                if present:
                    if not inspect(db).has_table("installation", schema="beta_control"):
                        raise ValueError("Unrecognized cloud control schema")
                elif (
                    inspect(db).get_table_names(schema="public")
                    or db.execute(
                        text(
                            "SELECT 1 FROM pg_namespace "
                            "WHERE nspname NOT IN ('public','information_schema') "
                            "AND nspname NOT LIKE 'pg_%'"
                        )
                    ).first()
                ):
                    raise ValueError("Cloud persistence requires a new dedicated database")
                db.execute(text("CREATE SCHEMA IF NOT EXISTS beta_control"))
                db.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS beta_control.installation "
                        "(id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL)"
                    )
                )
                db.execute(
                    text(
                        "INSERT INTO beta_control.installation VALUES (1,:identity) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"identity": installation},
                )
                if (
                    db.execute(
                        text("SELECT identity FROM beta_control.installation WHERE id=1")
                    ).scalar_one()
                    != installation
                ):
                    raise ValueError("Cloud database belongs to another installation")
                db.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS beta_control.runtime_fence "
                        "(id INTEGER PRIMARY KEY CHECK(id=1), epoch BIGINT NOT NULL)"
                    )
                )
                db.execute(
                    text(
                        "INSERT INTO beta_control.runtime_fence VALUES(1,0) ON CONFLICT DO NOTHING"
                    )
                )
                db.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS beta_control.recovery_state "
                        "(id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL "
                        "CHECK(state IN ('READY','RESTORING','RETIRED')))"
                    )
                )
                db.execute(
                    text(
                        "INSERT INTO beta_control.recovery_state VALUES(1,'READY') "
                        "ON CONFLICT DO NOTHING"
                    )
                )
                db.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS beta_control.objects "
                        "(tenant TEXT NOT NULL, path TEXT NOT NULL, sha TEXT NOT NULL, "
                        "bytes BIGINT NOT NULL CHECK(bytes>=0), state TEXT NOT NULL "
                        "CHECK(state IN ('STAGING','READY','DELETED')), PRIMARY KEY(tenant,path))"
                    )
                )
        finally:
            bootstrap.dispose()
        self.engine: Engine = create_engine(
            url,
            # One connection is the runtime lease; concurrent readings each publish files here.
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            pool_recycle=300,
            connect_args={"options": "-c search_path=beta_control -c lock_timeout=10000"},
        )

    @contextmanager
    def connect(self, lock_name: str) -> Iterator[ControlConnection]:
        with self.engine.begin() as connection:
            self.fence(connection)
            yield ControlConnection(connection, lock_name)

    @contextmanager
    def read(self) -> Iterator[ControlConnection]:
        """A single read outside a transaction and without the runtime fence.

        Only for reads that decide nothing durable by themselves (the per-operation access
        check runs thousands of times while a document is stored); every write still goes
        through connect(), which fences.
        """
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            yield ControlConnection(connection, "read")

    def fence(self, connection: Connection) -> None:
        if self.administrative:
            cursor = connection.connection.cursor()
            try:
                cursor.execute(
                    "SELECT pg_try_advisory_xact_lock("
                    "hashtextextended('fuminosuke-administration',0))"
                )
                owned = cursor.fetchone()
                if not owned or not owned[0]:
                    raise RuntimeError("Offline maintenance owns account admission")
                cursor.execute("SELECT state FROM beta_control.recovery_state WHERE id=1 FOR SHARE")
                row = cursor.fetchone()
                if not row or row[0] != "READY":
                    raise RuntimeError("Incomplete or retired database cannot admit accounts")
            finally:
                cursor.close()
            return
        if self.epoch is None or self.lease_pid is None:
            raise RuntimeError("Cloud runtime lease is not held")
        # DBAPI avoids re-entering SQLAlchemy's before_cursor_execute hook. The row lock
        # lasts through the business commit; a replacement runtime cannot pass it.
        cursor = connection.connection.cursor()
        try:
            cursor.execute(
                "SELECT epoch FROM beta_control.runtime_fence WHERE id=1 AND EXISTS "
                "(SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory' AND granted "
                "AND mode='ExclusiveLock' AND objsubid=1 "
                "AND classid::bigint="
                "((hashtextextended('fuminosuke-runtime',0)>>32)&4294967295) "
                "AND objid::bigint=(hashtextextended('fuminosuke-runtime',0)&4294967295)) "
                "FOR SHARE",
                (self.lease_pid,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row is None or row[0] != self.epoch:
            raise RuntimeError("Cloud runtime ownership has expired")

    @contextmanager
    def runtime_lease(
        self, *, allow_retired: bool = False, maintenance: bool = True
    ) -> Iterator[None]:
        if self.administrative:
            raise RuntimeError("Administrative connections cannot own a business runtime")
        # Session lock is held by a dedicated connection for the complete runtime/maintenance.
        with self.engine.connect() as connection:
            owned = connection.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended('fuminosuke-runtime',0))")
            ).scalar_one()
            connection.commit()
            if not owned:
                raise RuntimeError("Another cloud runtime or maintenance owns this installation")
            admin_owned = False
            try:
                if maintenance:
                    admin_owned = connection.execute(
                        text(
                            "SELECT pg_try_advisory_lock("
                            "hashtextextended('fuminosuke-administration',0))"
                        )
                    ).scalar_one()
                    connection.commit()
                    if not admin_owned:
                        raise RuntimeError("Account administration is in progress")
                self.lease_pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
                self.epoch = connection.execute(
                    text("UPDATE runtime_fence SET epoch=epoch+1 WHERE id=1 RETURNING epoch")
                ).scalar_one()
                connection.commit()
                state = connection.execute(
                    text("SELECT state FROM recovery_state WHERE id=1")
                ).scalar_one()
                if state != "READY" and not (allow_retired and state == "RETIRED"):
                    raise RuntimeError("Incomplete database restore cannot be served")
                connection.commit()
                yield
            finally:
                self.epoch = None
                self.lease_pid = None
                if not connection.invalidated:
                    try:
                        connection.execute(
                            text(
                                "SELECT pg_advisory_unlock("
                                "hashtextextended('fuminosuke-runtime',0))"
                            )
                        )
                        if admin_owned:
                            connection.execute(
                                text(
                                    "SELECT pg_advisory_unlock("
                                    "hashtextextended('fuminosuke-administration',0))"
                                )
                            )
                        connection.commit()
                    except DBAPIError:
                        # A dead keeper already lost its session lock; never reuse its socket.
                        connection.invalidate()

    def dispose(self) -> None:
        self.engine.dispose()
