import pytest

from app.services.sql_policy import SQLPolicyError, validate_and_rewrite_sql


ALLOWED_TABLES = {"customers", "orders"}
ALLOWED_COLUMNS = {
    "customers": {"id", "name"},
    "orders": {"id", "customer_id", "total"},
}


def test_select_is_bounded():
    decision = validate_and_rewrite_sql(
        "SELECT id, total FROM orders ORDER BY total DESC",
        dialect="sqlite",
        allowed_tables=ALLOWED_TABLES,
        allowed_columns=ALLOWED_COLUMNS,
        max_rows=50,
    )

    assert decision.row_limit == 50
    assert decision.limit_added_or_capped is True
    assert "LIMIT 50" in decision.sql.upper()
    assert decision.tables == ("orders",)


def test_lower_existing_limit_is_preserved():
    decision = validate_and_rewrite_sql(
        "SELECT id FROM orders LIMIT 5",
        dialect="sqlite",
        allowed_tables=ALLOWED_TABLES,
        allowed_columns=ALLOWED_COLUMNS,
        max_rows=50,
    )

    assert decision.row_limit == 5
    assert decision.limit_added_or_capped is False
    assert "LIMIT 5" in decision.sql.upper()


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO customers (id, name) VALUES (2, 'X')",
        "UPDATE customers SET name = 'X' WHERE id = 1",
        "DELETE FROM customers WHERE id = 1",
        "DROP TABLE customers",
        "SELECT * FROM customers; SELECT * FROM orders",
    ],
)
def test_mutation_and_multi_statement_sql_is_rejected(sql):
    with pytest.raises(SQLPolicyError):
        validate_and_rewrite_sql(
            sql,
            dialect="sqlite",
            allowed_tables=ALLOWED_TABLES,
            allowed_columns=ALLOWED_COLUMNS,
            max_rows=50,
        )


def test_unknown_table_is_rejected():
    with pytest.raises(SQLPolicyError, match="unauthorized|unknown"):
        validate_and_rewrite_sql(
            "SELECT * FROM payroll",
            dialect="sqlite",
            allowed_tables=ALLOWED_TABLES,
            allowed_columns=ALLOWED_COLUMNS,
            max_rows=50,
        )


def test_unknown_column_is_rejected():
    with pytest.raises(SQLPolicyError, match="unknown column"):
        validate_and_rewrite_sql(
            "SELECT secret_salary FROM customers",
            dialect="sqlite",
            allowed_tables=ALLOWED_TABLES,
            allowed_columns=ALLOWED_COLUMNS,
            max_rows=50,
        )
