from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.ai import models as ai_models  # noqa: F401
from app.assistant import models as assistant_models  # noqa: F401
from app.assistant import (
    stream_models,  # noqa: F401
    voice_models,  # noqa: F401
)
from app.core.config import Settings
from app.db import models as database_models  # noqa: F401
from app.db import mutation_models  # noqa: F401
from app.db.base import Base
from app.editing import models as editing_models  # noqa: F401
from app.editing.infrastructure import command_models  # noqa: F401
from app.template_analysis import models as template_analysis_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if config.attributes.get("connection") is None:
    settings = Settings.load()
    config.set_main_option("sqlalchemy.url", settings.database.url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided = config.attributes.get("connection")
    if provided is not None:
        provided.exec_driver_sql("BEGIN IMMEDIATE")
        context.configure(connection=provided, target_metadata=target_metadata,
                          compare_type=True, transactional_ddl=True)
        try:
            with context.begin_transaction():
                context.run_migrations()
            provided.commit()
        except BaseException:
            provided.rollback()
            raise
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"check_same_thread": False},
    )

    with connectable.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.commit()
        # sqlite3 otherwise autocommits DDL before its first DML statement.
        # The schema and alembic version must advance (or roll back) together.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, transactional_ddl=True)
        try:
            with context.begin_transaction():
                context.run_migrations()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise



if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
