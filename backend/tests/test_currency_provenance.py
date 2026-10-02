"""Currency provenance contract and non-destructive migration tests."""
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

import app.db as db_module
from app.schemas import DataSourceCreate, DataSourceCurrencyUpdate


def test_source_currency_defaults_unknown_and_accepts_bounded_attestation():
    request = DataSourceCreate(
        name="Legacy",
        engine="sqlite",
        connection_uri="sqlite:///legacy.db",
    )
    assert request.revenue_currency is None
    assert DataSourceCurrencyUpdate(revenue_currency="USD").revenue_currency == "USD"
    assert DataSourceCurrencyUpdate(revenue_currency="JMD").revenue_currency == "JMD"
    assert DataSourceCurrencyUpdate(revenue_currency=None).revenue_currency is None
    with pytest.raises(ValidationError):
        DataSourceCurrencyUpdate.model_validate({"revenue_currency": "EUR"})


@pytest.mark.asyncio
async def test_currency_migration_is_idempotent_and_preserves_existing_rows(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                "CREATE TABLE data_sources (id VARCHAR(36) PRIMARY KEY, "
                "name VARCHAR(240) NOT NULL)"
            )
            await connection.exec_driver_sql(
                "INSERT INTO data_sources (id, name) VALUES ('existing', 'Legacy')"
            )
        monkeypatch.setattr(db_module, "engine", engine)
        await db_module._migrate_add_revenue_currency()
        await db_module._migrate_add_revenue_currency()
        async with engine.connect() as connection:
            info = await connection.exec_driver_sql("PRAGMA table_info(data_sources)")
            columns = [tuple(row)[1] for row in info.fetchall()]
            assert columns.count("revenue_currency") == 1
            rows = await connection.exec_driver_sql(
                "SELECT id, name, revenue_currency FROM data_sources"
            )
            assert rows.fetchall() == [("existing", "Legacy", None)]
    finally:
        await engine.dispose()
