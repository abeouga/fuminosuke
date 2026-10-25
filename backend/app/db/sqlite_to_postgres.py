"""Copy a local SQLite database into an empty PostgreSQL schema and verify every table.

    python -m app.db.sqlite_to_postgres --sqlite data/app.db            # copy, then verify
    python -m app.db.sqlite_to_postgres --sqlite data/app.db --verify   # verify only

The target comes from DATABASE_URL / DATABASE_SCHEMA. The schema must already be at the alembic
head (`alembic upgrade head`) and empty. The SQLite file is only read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, Integer, MetaData, Table, func, inspect, select, text

from app.core.config import DatabaseSettings, Settings
from app.db.postgres_schema import business_metadata
from app.db.session import create_database_engine, create_sqlite_engine

BATCH = 500
# Present in SQLite but not business data.
IGNORED_SQLITE_TABLES = {"alembic_version", "sqlite_sequence"}


def _ordered(table: Table) -> Any:
    keys = list(table.primary_key.columns) or list(table.columns)
    return select(table).order_by(*keys)


def _rows(engine: Engine, table: Table) -> Iterator[dict[str, Any]]:
    with engine.connect() as connection:
        result = connection.execution_options(stream_results=True).execute(_ordered(table))
        for row in result.mappings():
            yield dict(row)


def _normal(value: Any) -> Any:
    """One comparable form for both databases (SQLite times are naive UTC)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return "sha256:" + hashlib.sha256(bytes(value)).hexdigest()
    if isinstance(value, Decimal):
        return str(value)
    return value


def table_digest(engine: Engine, table: Table) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for row in _rows(engine, table):
        normal = {key: _normal(value) for key, value in row.items()}
        digest.update(json.dumps(normal, sort_keys=True, ensure_ascii=False, default=str).encode())
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


def _check_tables(source: Engine, metadata: MetaData) -> list[str]:
    names = set(inspect(source).get_table_names())
    unknown = sorted(names - set(metadata.tables) - IGNORED_SQLITE_TABLES)
    problems = []
    with source.connect() as connection:
        for name in unknown:
            rows = connection.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one()
            if rows:
                problems.append(f"SQLite table {name} has {rows} rows but no model")
    return problems


def copy(source: Engine, target: Engine, metadata: MetaData) -> None:
    tables = metadata.sorted_tables
    with target.connect() as connection:
        for table in tables:
            if connection.execute(select(func.count()).select_from(table)).scalar_one():
                raise RuntimeError(f"Target table {table.name} is not empty")
    with target.begin() as connection:
        # Rows may reference each other in both directions (templates <-> versions).
        connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        for table in tables:
            batch: list[dict[str, Any]] = []
            copied = 0
            for row in _rows(source, table):
                batch.append(row)
                if len(batch) >= BATCH:
                    connection.execute(table.insert(), batch)
                    copied += len(batch)
                    batch = []
            if batch:
                connection.execute(table.insert(), batch)
                copied += len(batch)
            print(f"copied {table.name}: {copied}", flush=True)
        # Integer keys filled from SQLite: continue numbering after the copied rows.
        for table in tables:
            for column in table.primary_key.columns:
                if isinstance(column.type, Integer) and column.autoincrement is not False:
                    sequence = connection.execute(
                        text("SELECT pg_get_serial_sequence(:table, :column)"),
                        {"table": table.name, "column": column.name},
                    ).scalar()
                    if sequence:
                        connection.execute(text(
                            f"SELECT setval('{sequence}', "
                            f"COALESCE((SELECT max({column.name}) FROM {table.name}), 0) + 1, false)"
                        ))


def verify(source: Engine, target: Engine, metadata: MetaData) -> list[str]:
    problems = []
    for table in metadata.sorted_tables:
        expected = table_digest(source, table)
        actual = table_digest(target, table)
        status = "same" if expected == actual else "DIFFERENT"
        print(f"verify {table.name}: rows {expected[0]} -> {actual[0]} {status}", flush=True)
        if expected != actual:
            problems.append(f"{table.name}: {expected} != {actual}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--verify", action="store_true", help="only compare, do not copy")
    args = parser.parse_args(argv)
    if not args.sqlite.is_file():
        parser.error(f"{args.sqlite} not found")
    settings = Settings.load()
    if settings.database.schema_name is None:
        parser.error("DATABASE_URL must point at PostgreSQL")
    source = create_sqlite_engine(DatabaseSettings(
        url="sqlite+pysqlite:///" + args.sqlite.resolve().as_posix()))
    target = create_database_engine(settings.database)
    metadata = business_metadata()
    problems = _check_tables(source, metadata)
    if problems:
        print("\n".join(problems))
        return 1
    if not args.verify:
        copy(source, target, metadata)
    problems = verify(source, target, metadata)
    print("RESULT:", "OK" if not problems else f"{len(problems)} table(s) differ")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
