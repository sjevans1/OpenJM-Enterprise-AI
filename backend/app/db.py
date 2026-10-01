from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


settings = get_settings()
engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Safe, non-destructive upgrade for existing deployments: add the
    # requested_mode column to tables that predate this change.  Uses
    # ALTER TABLE so existing rows (with NULL) are preserved.
    await _migrate_add_requested_mode(conn=None)


async def _migrate_add_requested_mode(conn=None) -> None:
    """Add requested_mode column to message and execution_trace tables.

    Only runs ALTER TABLE when the table exists and the column is missing;
    existing rows receive NULL, preserving all prior data.
    """
    for table_name in ("message", "execution_trace"):
        async with engine.connect() as conn:
            # Check table exists first.
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


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
