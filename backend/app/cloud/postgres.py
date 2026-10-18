"""New cloud schemas only. Never upgrades or copies the normal local database."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable

from sqlalchemy import Connection, MetaData, create_engine, inspect, text
from sqlalchemy.schema import AddConstraint, CreateIndex

from app.core.config import DatabaseSettings
from app.db.metadata import Base
from app.db.session import create_database_engine


def tenant_settings(url: str, tenant: str) -> DatabaseSettings:
    if not re.fullmatch(r"[0-9a-f]{32}", tenant):
        raise ValueError("Invalid server-owned tenant identity")
    return DatabaseSettings(url=url, schema_name="tenant_" + tenant)


def initialize_tenant(
    settings: DatabaseSettings, *, fence: Callable[[Connection], None] | None = None
) -> None:
    if not settings.schema_name:
        raise ValueError("Missing tenant schema")
    engine = create_database_engine(settings)
    admin = create_engine(settings.url)
    try:
        # SQLAlchemy isolates cyclic FKs during create_all by mutating its metadata.
        # A private copy per tenant prevents missing FKs and process-order fingerprints.
        metadata = MetaData()
        for table in Base.metadata.tables.values():
            table.to_metadata(metadata)
        definition = []
        for table in sorted(metadata.tables.values(), key=lambda t: t.name):
            definition.append(
                {
                    "table": table.name,
                    "columns": [
                        (
                            c.name,
                            str(c.type.compile(dialect=engine.dialect)),
                            c.nullable,
                            str(getattr(c.server_default, "arg", type(c.server_default).__name__))
                            if c.server_default is not None
                            else None,
                        )
                        for c in table.columns
                    ],
                    "constraints": sorted(
                        str(
                            AddConstraint(c, isolate_from_table=False).compile(
                                dialect=engine.dialect
                            )
                        )
                        for c in table.constraints
                    ),
                    "indexes": sorted(
                        str(CreateIndex(i).compile(dialect=engine.dialect)) for i in table.indexes
                    ),
                }
            )
        fingerprint = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
        with admin.begin() as db:
            if fence:
                fence(db)
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:name,0))"),
                {"name": settings.schema_name},
            )
            db.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.schema_name}"'))
        with engine.begin() as db:
            if fence:
                fence(db)
            db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),0))"))
            db.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS cloud_schema_version "
                    "(id INTEGER PRIMARY KEY CHECK(id=1), fingerprint TEXT NOT NULL)"
                )
            )
            old = db.execute(
                text("SELECT fingerprint FROM cloud_schema_version WHERE id=1")
            ).scalar()
            if old is not None:
                if old != fingerprint:
                    raise RuntimeError("Explicit cloud schema migration is required")
                return
            metadata.create_all(db)
            # Restore may defer FK checks; normal writes keep immediate checks.
            for table_name in inspect(db).get_table_names(schema=settings.schema_name):
                for constraint in inspect(db).get_foreign_keys(table_name):
                    name = constraint["name"]
                    if name is None:
                        raise RuntimeError("PostgreSQL foreign key has no name")
                    quote = engine.dialect.identifier_preparer.quote
                    db.execute(
                        text(
                            f"ALTER TABLE {quote(table_name)} ALTER CONSTRAINT {quote(name)} "
                            "DEFERRABLE INITIALLY IMMEDIATE"
                        )
                    )
            db.execute(
                text("INSERT INTO cloud_schema_version VALUES(1,:fingerprint)"),
                {"fingerprint": fingerprint},
            )
    finally:
        engine.dispose()
        admin.dispose()
