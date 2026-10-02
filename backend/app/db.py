from collections.abc import AsyncIterator
from datetime import datetime, timezone

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
    """CREATE TRIGGER IF NOT EXISTS trg_report_runs_protect_identity
BEFORE UPDATE OF user_id, report_id, definition_id, definition_version,
requested_mode, idempotency_key, request_fingerprint, started_at, deadline_at
ON report_runs FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'report run identity fields are immutable')
    WHERE NEW.report_id IS NOT OLD.report_id
       OR NEW.definition_id IS NOT OLD.definition_id
       OR NEW.definition_version IS NOT OLD.definition_version
       OR NEW.requested_mode IS NOT OLD.requested_mode
       OR NEW.idempotency_key IS NOT OLD.idempotency_key
       OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
       OR NEW.started_at IS NOT OLD.started_at
       OR NEW.deadline_at IS NOT OLD.deadline_at;
END;""",
    """CREATE TRIGGER IF NOT EXISTS trg_report_runs_terminal_immutable
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


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # aiosqlite executes one statement at a time, so run each trigger
        # statement separately.
        for statement in _REPORT_RUN_IMMUTABILITY_TRIGGERS_SQL:
            await conn.exec_driver_sql(statement)
    # Additive, idempotent upgrades preserve all rows from older deployments.
    await _migrate_add_requested_mode(conn=None)
    await _migrate_add_revenue_currency()
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


async def _recover_stale_report_runs() -> None:
    """Mark expired ``running`` runs as ``interrupted`` at startup.

    Single conditional update; never calls the model, retrieval, SQL engine or
    resumes work. Uses the production ``engine`` so the same connection hook
    (foreign-key PRAGMA) applies. Safe to run repeatedly. Skipped for
    non-SQLite targets or when the report_runs table does not exist yet.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    async with engine.connect() as conn:
        tables = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='report_runs'"
        )
        if tables.first() is None:
            return
    now = datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds")
    async with engine.connect() as conn:
        await conn.exec_driver_sql(
            "UPDATE report_runs SET status='interrupted',"
            " failure_category='deadline_expired',"
            " finished_at=? WHERE status='running' AND deadline_at <= ?",
            (now, now),
        )


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
