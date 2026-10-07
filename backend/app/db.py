from collections.abc import AsyncIterator
from datetime import datetime, timezone
import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


settings = get_settings()
engine = create_async_engine(settings.database_url, future=True)
logger = logging.getLogger(__name__)


def enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """Make declared SQLite foreign keys effective on a SQLite connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


if settings.database_url.startswith("sqlite"):
    event.listen(engine.sync_engine, "connect", enable_sqlite_foreign_keys)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


_REPORT_RUN_IMMUTABILITY_TRIGGERS_SQL = [
    """CREATE TRIGGER trg_report_runs_protect_identity
BEFORE UPDATE OF tenant_id, user_id, report_id, definition_id, definition_version,
requested_mode, idempotency_key, request_fingerprint, started_at, deadline_at
ON report_runs FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'report run identity fields are immutable')
    WHERE  NEW.user_id IS NOT OLD.user_id
        OR NEW.tenant_id IS NOT OLD.tenant_id
       OR NEW.report_id IS NOT OLD.report_id
       OR NEW.definition_id IS NOT OLD.definition_id
       OR NEW.definition_version IS NOT OLD.definition_version
       OR NEW.requested_mode IS NOT OLD.requested_mode
       OR NEW.idempotency_key IS NOT OLD.idempotency_key
       OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
       OR NEW.started_at IS NOT OLD.started_at
       OR NEW.deadline_at IS NOT OLD.deadline_at;
END;""",
    """CREATE TRIGGER trg_report_runs_terminal_immutable
BEFORE UPDATE ON report_runs FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'terminal report runs are immutable')
    WHERE OLD.status IN ('succeeded','failed','interrupted')
       OR (NEW.status != 'running' AND OLD.status != 'running');
    SELECT RAISE(ABORT, 'status regression is not permitted')
    WHERE OLD.status = 'succeeded' AND NEW.status IN ('running','failed','interrupted')
       OR OLD.status = 'failed' AND NEW.status IN ('running','succeeded','interrupted')
       OR OLD.status = 'interrupted' AND NEW.status IN ('running','succeeded','failed');
END;""",
]

_REPORT_RUN_TRIGGER_NAMES = ("trg_report_runs_protect_identity", "trg_report_runs_terminal_immutable")


def _production_engine():
    """Return the module-level production engine (indirection for tests)."""
    return engine


async def install_report_run_triggers(conn) -> None:
    """Drop and recreate the immutability triggers.

    DROP + CREATE (not IF NOT EXISTS) so a corrected trigger body replaces
    the defective trigger already deployed in an upgraded database.
    """
    for name in _REPORT_RUN_TRIGGER_NAMES:
        await conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
    for statement in _REPORT_RUN_IMMUTABILITY_TRIGGERS_SQL:
        await conn.exec_driver_sql(statement)


async def init_db() -> None:
    """Bring the application-metadata database to the latest revision.

    Order matters:

    1. Versioned migrations (Alembic). A database created by the previous
       bootstrap is detected and stamped at the baseline revision first, so no
       row is rewritten.
    2. Local identity bootstrap (tenant + principal + membership) so a
       development deployment has a real, server-resolved identity context.
    3. Stale report-run recovery.
    """
    import asyncio

    from app import models  # noqa: F401
    from app.migrations_runner import adopt_and_upgrade, assert_known_schema_revision
    from app.services.identity import ensure_local_identity

    # Migrate the database this process is actually bound to, not a separately
    # configured URL: the engine is the single source of truth, so a test or an
    # embedding application that replaces it migrates the right database.
    database_url = engine.url.render_as_string(hide_password=False)

    # Refuse to start on a schema this build does not know (a future revision
    # written by a newer release). Runs before any upgrade attempt.
    await asyncio.to_thread(assert_known_schema_revision, database_url)

    result = await asyncio.to_thread(adopt_and_upgrade, database_url)
    if result.get("adopted_baseline"):
        logger.info("Application database adopted at baseline revision")

    # The active-run partial index is part of the baseline revision; keep the
    # idempotent helper for engines created directly by tests.
    await _migrate_add_active_run_index()

    # Bind the bootstrap session to the engine this process is actually using.
    # ``SessionLocal`` is created at import time from settings, so a replaced
    # engine (a test, or an embedding application) would otherwise be migrated
    # and then bootstrapped against a different, empty database.
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await ensure_local_identity(db)

    await _recover_stale_report_runs()


async def _migrate_add_requested_mode(conn=None) -> None:
    """Add requested_mode column to messages and execution_traces tables.

    Only runs ALTER TABLE when the table exists and the column is missing;
    existing rows receive NULL, preserving all prior data.
    """
    for table_name in ("messages", "execution_traces"):
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"
            )
            if not result.fetchall():
                continue
            result = await conn.exec_driver_sql(f"PRAGMA table_info({table_name})")
            existing = {row[1] for row in result.fetchall()}
        if "requested_mode" not in existing:
            async with engine.begin() as alter_conn:
                await alter_conn.exec_driver_sql(
                    f"ALTER TABLE {table_name} ADD COLUMN requested_mode VARCHAR NULL"
                )


async def _migrate_add_revenue_currency() -> None:
    """Idempotently add the operator-declared revenue_currency column.

    Existing data-source rows deliberately receive NULL (unknown currency)
    until an operator attests their currency. No exchange rates are inferred
    and no historic transaction values are rewritten.
    """
    async with engine.begin() as conn:
        table = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='data_sources'"
        )
        if table.first() is None:
            return
        info = await conn.exec_driver_sql("PRAGMA table_info(data_sources)")
        columns = {row[1] for row in info.fetchall()}
        if "revenue_currency" not in columns:
            await conn.exec_driver_sql(
                "ALTER TABLE data_sources ADD COLUMN revenue_currency VARCHAR(3) NULL"
            )


_ACTIVE_RUN_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS "
    "uq_report_run_active_per_definition "
    "ON report_runs (user_id, report_id, definition_id, definition_version) "
    "WHERE status = 'running'"
)


async def _migrate_add_active_run_index(*, engine=None) -> None:
    """Idempotently create the one-active-run-per-definition partial unique index.

    Allows at most one 'running' row per (user_id, report_id, definition_id,
    definition_version).  Terminal rows do not participate, so a finalised
    run does not block a new submission for the same definition.
    Safe to call repeatedly; uses ``IF NOT EXISTS`` at the SQL level.
    Skipped for non-SQLite targets unless a specific engine is supplied,
    and when the report_runs table does not exist yet.
    """
    target_engine = engine if engine is not None else _production_engine()
    if not settings.database_url.startswith("sqlite") and engine is None:
        return
    if not target_engine.url.database or target_engine.url.database == ":memory:":
        return
    async with target_engine.begin() as conn:
        table = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='report_runs'"
        )
        if table.first() is None:
            return
        await conn.exec_driver_sql(_ACTIVE_RUN_INDEX_SQL)


async def _recover_stale_report_runs(*, engine=None) -> int:
    """Mark expired ``running`` runs as ``interrupted`` at startup.

    Single conditional update; never calls the model, retrieval, SQL engine or
    resumes work. Runs inside ``engine.begin()`` so the interruption is
    transactionally COMMITTED, not left pending on a rolled-back connection.
    Safe to run repeatedly. Skipped for non-SQLite targets or when the
    report_runs table does not exist yet. Returns the affected row count.
    """
    target_engine = engine if engine is not None else _production_engine()
    if not settings.database_url.startswith("sqlite") and engine is None:
        return 0
    if not target_engine.url.database or target_engine.url.database == ":memory:":
        # file-backed (or temporary file) SQLite only; shared in-memory is
        # per-engine and cannot carry stale state across restarts anyway.
        return 0
    async with target_engine.connect() as conn:
        tables = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='report_runs'"
        )
        if tables.first() is None:
            return 0
    # The ORM persists datetimes as 'YYYY-MM-DD HH:MM:SS.ffffff' text; compare
    # using the exact same lexical format the writer used.
    now = datetime.now(timezone.utc).isoformat(sep=" ", timespec="microseconds")
    async with target_engine.begin() as conn:
        result = await conn.exec_driver_sql(
            "UPDATE report_runs SET status='interrupted',"
            " failure_category='deadline_expired',"
            " finished_at=? WHERE status='running' AND deadline_at <= ?",
            (now, now),
        )
        return result.rowcount


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
