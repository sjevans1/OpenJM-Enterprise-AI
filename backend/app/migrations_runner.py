"""Versioned application-metadata migration runner (#7).

Wraps Alembic so the application can migrate its own metadata database at
startup, on both SQLite and PostgreSQL, with a safe path for deployments that
predate the migration framework.

Legacy adoption
---------------
Deployments created by the previous bootstrap (``create_all`` + ad-hoc
``ALTER TABLE``) have all the VS1-VS4 tables but no ``alembic_version``. Rather
than replaying the baseline DDL against a populated database, the runner
detects that shape, verifies the tables really are present, and *stamps* the
baseline revision. The subsequent revisions then upgrade additively.

Nothing here is applied to a customer's connected structured data sources.
This module only ever touches OpenJM's own application-metadata database.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

BASELINE_REVISION = "0001_vs4_baseline"

# Tables the baseline revision creates. Their presence means the database is a
# real VS1-VS4 deployment (not an empty file).
_BASELINE_TABLES = frozenset(
    {
        "conversations",
        "messages",
        "documents",
        "data_sources",
        "execution_traces",
        "saved_reports",
        "report_definition_versions",
        "report_runs",
    }
)


def sync_url_for(database_url: str) -> str:
    """Map the application's async URL onto the matching sync driver.

    Alembic runs synchronously here: one migration connection at startup is
    simpler and more debuggable than an async migration path, and the
    revisions themselves stay dialect-neutral.
    """
    if database_url.startswith("sqlite+aiosqlite:"):
        return database_url.replace("sqlite+aiosqlite:", "sqlite:", 1)
    if database_url.startswith("postgresql+asyncpg:"):
        return database_url.replace("postgresql+asyncpg:", "postgresql+psycopg:", 1)
    return database_url


def _alembic_config(sync_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", sync_url)
    return config


def _existing_tables(sync_url: str) -> set[str]:
    engine = create_engine(sync_url, future=True)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _baseline_present(tables: set[str]) -> bool:
    # A partially-created database (a crash mid-bootstrap) must not be stamped:
    # let Alembic create whatever is missing from scratch instead.
    return _BASELINE_TABLES.issubset(tables)


def adopt_and_upgrade(database_url: str) -> dict:
    """Bring an application-metadata database to the latest schema revision.

    Returns a small dict describing what happened, which the caller can log.
    """
    sync_url = sync_url_for(database_url)
    config = _alembic_config(sync_url)
    tables = _existing_tables(sync_url)
    recorded = current_revision_from_tables(sync_url, tables)
    adopted = False

    # Adopt any database that carries the baseline tables but no recorded
    # revision: the classic pre-migration deployment (no alembic_version table
    # at all) and the partially-stamped case (table present, no version row).
    if recorded is None and _baseline_present(tables):
        logger.info("Adopting pre-migration database: stamping baseline revision")
        command.stamp(config, BASELINE_REVISION)
        adopted = True

    command.upgrade(config, "head")
    return {"adopted_baseline": adopted, "adopted_tables": sorted(tables)}


def current_revision_from_tables(sync_url: str, tables: set[str]) -> str | None:
    """Read the recorded revision, avoiding a connection when the table is absent."""
    if "alembic_version" not in tables:
        return None
    return current_revision(sync_url)


def current_revision(database_url: str) -> str | None:
    sync_url = sync_url_for(database_url)
    from alembic.runtime.migration import MigrationContext

    engine = create_engine(sync_url, future=True)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
