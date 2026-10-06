"""Alembic environment for OpenJM Enterprise AI.

Runs synchronously against a dialect-neutral URL. ``app.migrations_runner``
translates the application's async URL (aiosqlite/asyncpg) into the matching
sync driver before invoking Alembic, so the same revision files serve both the
SQLite and PostgreSQL application-metadata backends.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base  # noqa: E402
from app import models  # noqa: F401,E402  (registers every table on Base.metadata)

config = context.config

if config.config_file_name is not None and os.path.exists(config.config_file_name):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite cannot ALTER most things in place; batch mode recreates the
        # table while preserving rows. Alembic decides per-operation.
        render_as_batch=connection.dialect.name == "sqlite",
    )


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
