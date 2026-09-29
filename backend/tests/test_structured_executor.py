import json
import sqlite3

import pytest
from cryptography.fernet import Fernet

from app.models import DataSource
from app.services.credentials import CredentialVault
from app.services.data_sources import (
    authorized_objects_from_schema,
    discover_source_schema,
    encode_schema,
)
from app.services.structured_executor import (
    StructuredExecutionError,
    execute_structured_query,
)
from app.services import structured_executor as executor_module


@pytest.mark.asyncio
async def test_execute_structured_query_reads_bounded_sqlite_data(tmp_path, monkeypatch):
    db_path = tmp_path / "business.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                customer TEXT NOT NULL,
                total REAL NOT NULL
            );
            INSERT INTO orders (customer, total) VALUES
                ('Acme', 125.50),
                ('Acme', 74.50),
                ('Beta', 50.00);
            """
        )
        connection.commit()
    finally:
        connection.close()

    uri = f"sqlite:///{db_path}"
    tables = await discover_source_schema("sqlite", uri)
    vault = CredentialVault(key=Fernet.generate_key())
    monkeypatch.setattr(executor_module, "credential_vault", vault)

    source = DataSource(
        id="source-1",
        user_id="local-admin",
        name="Demo",
        engine="sqlite",
        connection_secret=vault.encrypt(uri),
        status="connected",
        enabled=True,
        schema_json=encode_schema(tables),
        authorized_objects_json=json.dumps(authorized_objects_from_schema(tables)),
    )

    result = await execute_structured_query(
        source,
        "SELECT customer, SUM(total) AS revenue "
        "FROM orders GROUP BY customer ORDER BY revenue DESC",
    )

    assert result.columns == ("customer", "revenue")
    assert result.rows[0][0] == "Acme"
    assert float(result.rows[0][1]) == 200.0
    assert "LIMIT" in result.sql.upper()


@pytest.mark.asyncio
async def test_execute_structured_query_rejects_disabled_source(tmp_path):
    source = DataSource(
        id="source-2",
        user_id="local-admin",
        name="Disabled",
        engine="sqlite",
        connection_secret="encrypted-placeholder",
        status="connected",
        enabled=False,
        schema_json="[]",
        authorized_objects_json="[]",
    )

    with pytest.raises(StructuredExecutionError, match="disabled"):
        await execute_structured_query(source, "SELECT * FROM orders")
