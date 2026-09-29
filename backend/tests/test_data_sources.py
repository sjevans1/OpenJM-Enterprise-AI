import sqlite3

import pytest

from app.services.data_sources import (
    DataSourceError,
    discover_source_schema,
    normalize_connection_uri,
)


def test_normalize_sqlite_uri_uses_async_driver(tmp_path):
    db_path = tmp_path / "client.db"
    uri = normalize_connection_uri("sqlite", f"sqlite:///{db_path}")

    assert uri.startswith("sqlite+aiosqlite:///")
    assert str(db_path) in uri


def test_normalize_rejects_wrong_engine():
    with pytest.raises(DataSourceError):
        normalize_connection_uri(
            "postgresql",
            "sqlite:///client.db",
        )


@pytest.mark.asyncio
async def test_discover_sqlite_schema(tmp_path):
    db_path = tmp_path / "demo.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                total REAL NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES customers(id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()

    tables = await discover_source_schema("sqlite", f"sqlite:///{db_path}")

    by_name = {table.name: table for table in tables}
    assert set(by_name) == {"customers", "orders"}
    assert {column.name for column in by_name["customers"].columns} == {"id", "name"}
    assert by_name["customers"].primary_key == ["id"]
    assert by_name["orders"].foreign_keys[0].referred_table == "customers"
