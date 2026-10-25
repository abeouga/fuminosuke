"""Local/RDS PostgreSQL schema for the business tables.

The SQLite revision chain starts from a frozen SQLite DDL baseline, so a new PostgreSQL schema is
created from the ORM metadata (as the cloud tenants are) and stamped at the current alembic head.
Later revisions then run on PostgreSQL through the usual `alembic upgrade head`.
"""

from __future__ import annotations

from sqlalchemy import Connection, MetaData, inspect, text

from app.db.base import Base


def business_metadata() -> MetaData:
    """All business tables, as alembic sees them (every model module imported)."""
    from app.ai import models as _ai  # noqa: F401
    from app.assistant import models as _assistant  # noqa: F401
    from app.assistant import stream_models as _stream  # noqa: F401
    from app.assistant import voice_models as _voice  # noqa: F401
    from app.db import models as _db  # noqa: F401
    from app.db import mutation_models as _mutation  # noqa: F401
    from app.editing import models as _editing  # noqa: F401
    from app.editing.infrastructure import command_models as _commands  # noqa: F401
    from app.template_analysis import models as _analysis  # noqa: F401

    # A private copy: create_all mutates metadata while isolating cyclic foreign keys.
    metadata = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)
    return metadata


def has_alembic_version(connection: Connection, schema: str) -> bool:
    return "alembic_version" in inspect(connection).get_table_names(schema=schema)


def create_baseline(connection: Connection, schema: str, head: str) -> None:
    """Create every table in an empty schema and record `head` as applied."""
    if inspect(connection).get_table_names(schema=schema):
        raise RuntimeError(f'Schema "{schema}" already has tables but no alembic version')
    business_metadata().create_all(connection)
    quote = connection.dialect.identifier_preparer.quote
    # Deferrable like the cloud tenants: bulk copies can insert rows that reference each other.
    for table_name in inspect(connection).get_table_names(schema=schema):
        for constraint in inspect(connection).get_foreign_keys(table_name, schema=schema):
            if constraint["name"] is None:
                raise RuntimeError("PostgreSQL foreign key has no name")
            connection.execute(text(
                f"ALTER TABLE {quote(table_name)} ALTER CONSTRAINT {quote(constraint['name'])} "
                "DEFERRABLE INITIALLY IMMEDIATE"
            ))
    connection.execute(text(
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, "
        "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
    ))
    connection.execute(text("INSERT INTO alembic_version VALUES (:head)"), {"head": head})
