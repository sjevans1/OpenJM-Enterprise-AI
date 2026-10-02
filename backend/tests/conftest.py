"""Shared test fixtures for OpenJM backend tests."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base


@pytest.fixture
async def session():
    """In-memory SQLite async session for backend tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        yield db

    await engine.dispose()
