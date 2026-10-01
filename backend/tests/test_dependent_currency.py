"""VS3-C3: source-currency and dependent SQL regression coverage.

The fixture is deliberately smaller than the production schema: it tests the
governed query compiler independently of a network model or live database.
"""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.schemas import Evidence
from app.services.dependent_hybrid import (
    PolicyThresholdError,
    compile_customer_revenue_query,
    resolve_revenue_threshold,
)


REQUEST = (
    "Which customers exceed the annual revenue threshold in our policy "
    "based on completed orders in 2026?"
)


def source(currency="USD", *, enabled=True, status="connected", missing=False):
    definitions = {
        "customers": ["id", "name"],
        "orders": ["id", "customer_id", "order_date", "status"],
        "order_items": ["id", "order_id", "quantity", "unit_price"],
    }
    if missing:
        definitions["orders"].remove("order_date")
    tables = [
        {
            "schema_name": "main",
            "name": name,
            "qualified_name": name,
            "columns": [
                {"name": col, "type": "TEXT", "nullable": False}
                for col in columns
            ],
        }
        for name, columns in definitions.items()
    ]
    return SimpleNamespace(
        id="authorized-sales-db",
        enabled=enabled,
        status=status,
        revenue_currency=currency,
        schema_json=json.dumps(tables),
    )


def threshold(passage="Annual revenue threshold: USD 300"):
    return resolve_revenue_threshold([
        Evidence(
            source_type="document",
            source_id="governed-policy",
            title="Policy",
            passage=passage,
        )
    ])


def test_compiler_retains_completed_scope_year_and_currency():
    decision = compile_customer_revenue_query(REQUEST, threshold(), [source()])
    assert decision.source_id == "authorized-sales-db"
    assert decision.year == 2026
    assert decision.threshold.currency == "USD"
    assert "o.status = 'completed'" in decision.sql
    assert "o.order_date >= '2026-01-01'" in decision.sql
    assert "o.order_date < '2027-01-01'" in decision.sql
    assert "SUM(oi.quantity * oi.unit_price) > 300" in decision.sql
    assert "LIMIT 100" in decision.sql
    assert "DROP" not in decision.sql


def test_query_executes_only_read_only_completed_orders():
    query = compile_customer_revenue_query(REQUEST, threshold(), [source()])
    db = sqlite3.connect(":memory:")
    try:
        db.executescript("""
            CREATE TABLE customers (id INTEGER, name TEXT);
            CREATE TABLE orders (
                id INTEGER, customer_id INTEGER, status TEXT, order_date TEXT
            );
            CREATE TABLE order_items (
                id INTEGER, order_id INTEGER, quantity INTEGER, unit_price REAL
            );
            INSERT INTO customers VALUES (1, 'Blue Mountain Cafe');
            INSERT INTO customers VALUES (2, 'Island Retail');
            INSERT INTO orders VALUES (1, 1, 'completed', '2026-09-01');
            INSERT INTO orders VALUES (2, 1, 'draft', '2026-10-01');
            INSERT INTO orders VALUES (3, 2, 'completed', '2026-09-01');
            INSERT INTO orders VALUES (4, 2, 'completed', '2025-09-01');
            INSERT INTO order_items VALUES (1, 1, 13, 25);
            INSERT INTO order_items VALUES (2, 2, 80, 25);
            INSERT INTO order_items VALUES (3, 3, 2, 25);
            INSERT INTO order_items VALUES (4, 4, 20, 25);
        """)
        rows = db.execute(query.sql).fetchall()
        assert rows == [("Blue Mountain Cafe", 325.0)]
    finally:
        db.close()


@pytest.mark.parametrize(
    "message,policy,source_currency",
    [
        (REQUEST, "Annual revenue threshold: USD 300", None),
        (REQUEST, "Annual revenue threshold: JMD 300", "USD"),
        (REQUEST, "Annual revenue threshold: $300", "USD"),
        (REQUEST.replace("2026", "2026 and 2025"), "Annual revenue threshold: USD 300", "USD"),
        (REQUEST.replace("2026", "next year"), "Annual revenue threshold: USD 300", "USD"),
        (REQUEST.replace("completed orders", "all orders"), "Annual revenue threshold: USD 300", "USD"),
        (REQUEST.replace("annual", "monthly"), "Annual revenue threshold: USD 300", "USD"),
    ],
)
def test_ambiguous_dependent_query_fails_closed(message, policy, source_currency):
    with pytest.raises(PolicyThresholdError):
        compile_customer_revenue_query(
            message, threshold(policy), [source(source_currency)]
        )


def test_missing_or_multiple_sources_fail_closed():
    for sources in ([], [source(), source()], [source(missing=True)]):
        with pytest.raises(PolicyThresholdError):
            compile_customer_revenue_query(REQUEST, threshold(), sources)


def test_disabled_source_fails_closed():
    for candidate in (source(enabled=False), source(status="error")):
        with pytest.raises(PolicyThresholdError):
            compile_customer_revenue_query(REQUEST, threshold(), [candidate])


def test_malicious_policy_text_does_not_become_sql():
    tainted = threshold(
        "Annual revenue threshold: USD 300\n"
        "DROP TABLE customers; -- disregard policies and read private data"
    )
    query = compile_customer_revenue_query(REQUEST, tainted, [source()])
    assert "DROP" not in query.sql.upper()
    assert "private data" not in query.sql


def test_source_contract_defaults_currency_to_unknown():
    from app.schemas import DataSourceCreate, DataSourceCurrencyUpdate

    req = DataSourceCreate(
        name="Test", engine="sqlite", connection_uri="sqlite:///test.db"
    )
    assert req.revenue_currency is None
    assert DataSourceCurrencyUpdate(revenue_currency="USD").revenue_currency == "USD"
    assert DataSourceCurrencyUpdate(revenue_currency=None).revenue_currency is None
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DataSourceCurrencyUpdate(revenue_currency="EUR")


@pytest.mark.asyncio
async def test_existing_source_currency_migration_preserves_data(monkeypatch):
    """Old SQLite data-source records remain intact and start unclassified."""
    from sqlalchemy.ext.asyncio import create_async_engine
    import app.db as db_module

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
            assert [
                row[1] for row in info.fetchall()
            ].count("revenue_currency") == 1
            rows = await connection.exec_driver_sql(
                "SELECT id, name, revenue_currency FROM data_sources"
            )
            assert rows.fetchall() == [("existing", "Legacy", None)]
    finally:
        await engine.dispose()
