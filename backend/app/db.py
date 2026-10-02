from collections.abc import AsyncIterator

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


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Additive, idempotent upgrades preserve all rows from older deployments.
    await _migrate_add_requested_mode(conn=None)
    await _migrate_add_revenue_currency()


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


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
