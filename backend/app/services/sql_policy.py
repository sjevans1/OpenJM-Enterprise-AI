from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope


class SQLPolicyError(RuntimeError):
    pass


FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Merge,
    exp.Command,
)

DANGEROUS_FUNCTIONS = {
    "PG_SLEEP",
    "SLEEP",
    "BENCHMARK",
    "LOAD_FILE",
}


@dataclass(frozen=True)
class SQLPolicyDecision:
    sql: str
    tables: tuple[str, ...]
    row_limit: int
    limit_added_or_capped: bool


def _table_name(table: exp.Table) -> str:
    name = table.name.lower()
    db = (table.db or "").lower()
    catalog = (table.catalog or "").lower()
    return ".".join(part for part in (catalog, db, name) if part)


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).upper()
    try:
        return str(node.sql_name()).upper()
    except Exception:
        return node.__class__.__name__.upper()


def _limit_value(query: exp.Query) -> int | None:
    limit = query.args.get("limit")
    if not limit:
        return None
    expression = limit.expression
    if not isinstance(expression, exp.Literal) or not expression.is_int:
        raise SQLPolicyError("LIMIT must be a literal integer")
    value = int(expression.this)
    if value < 0:
        raise SQLPolicyError("LIMIT cannot be negative")
    return value


def validate_and_rewrite_sql(
    sql: str,
    *,
    dialect: str,
    allowed_tables: set[str],
    allowed_columns: dict[str, set[str]] | None = None,
    max_rows: int,
    require_exact_table_match: bool = False,
) -> SQLPolicyDecision:
    """Validate model-proposed SQL and apply the OpenJM read-only row bound."""
    if not sql.strip():
        raise SQLPolicyError("SQL is empty")
    if max_rows < 1:
        raise SQLPolicyError("Configured maximum row count must be positive")

    try:
        statements = [item for item in sqlglot.parse(sql, read=dialect) if item is not None]
    except Exception as exc:
        raise SQLPolicyError(f"SQL could not be parsed: {exc}") from exc

    if len(statements) != 1:
        raise SQLPolicyError("Exactly one SQL statement is allowed")

    query = statements[0]
    if not isinstance(query, exp.Query):
        raise SQLPolicyError("Only SELECT/CTE read queries are allowed")
    if require_exact_table_match and any(
        with_clause.args.get("recursive")
        for with_clause in query.find_all(exp.With)
    ):
        raise SQLPolicyError("Recursive CTEs are not supported in pinned reports")

    for node_type in FORBIDDEN_NODES:
        if query.find(node_type):
            raise SQLPolicyError(
                f"Forbidden SQL operation: {node_type.__name__.upper()}"
            )

    for function in query.find_all(exp.Func):
        name = _function_name(function)
        if name in DANGEROUS_FUNCTIONS:
            raise SQLPolicyError(f"Function {name} is not allowed")

    normalized_allowed = {item.lower() for item in allowed_tables}
    # Resolve physical tables with SQLGlot's lexical source scopes.
    # A CTE/subquery is a Scope, not a physical exp.Table. A globally
    # collected CTE-name set can accidentally hide a real table in an outer
    # or unrelated nested scope when aliases collide.
    try:
        query_scopes = traverse_scope(query)
        referenced_tables = tuple(sorted({
            _table_name(source)
            for query_scope in query_scopes
            for _, source in query_scope.selected_sources.values()
            if isinstance(source, exp.Table)
        }))
    except Exception as exc:
        raise SQLPolicyError("SQL source scopes could not be validated") from exc

    if not referenced_tables:
        raise SQLPolicyError("Structured queries must reference an authorized table")

    unauthorized = [
        table
        for table in referenced_tables
        if table not in normalized_allowed
        and (
            require_exact_table_match
            or table.split(".")[-1] not in normalized_allowed
        )
    ]
    if unauthorized:
        raise SQLPolicyError(
            "Query references unauthorized or unknown tables: "
            + ", ".join(unauthorized)
        )

    if allowed_columns:
        normalized_columns = {
            table.lower(): {column.lower() for column in columns}
            for table, columns in allowed_columns.items()
        }
        table_nodes = list(query.find_all(exp.Table))
        aliases: dict[str, str] = {}
        for table in table_nodes:
            canonical = _table_name(table)
            aliases[table.name.lower()] = canonical
            if table.alias:
                aliases[table.alias.lower()] = canonical

        all_columns = set().union(*normalized_columns.values()) if normalized_columns else set()
        select_aliases = {
            str(alias.alias).lower()
            for alias in query.find_all(exp.Alias)
            if alias.alias
        }
        for column in query.find_all(exp.Column):
            name = column.name.lower()
            if name == "*":
                continue
            qualifier = (column.table or "").lower()
            if qualifier:
                canonical = aliases.get(qualifier, qualifier)
                candidates = (
                    normalized_columns.get(canonical)
                    or normalized_columns.get(canonical.split(".")[-1])
                )
                if candidates is not None and name not in candidates:
                    raise SQLPolicyError(
                        f"Query references unknown column: {column.sql(dialect=dialect)}"
                    )
            elif name not in all_columns and name not in select_aliases:
                raise SQLPolicyError(f"Query references unknown column: {column.name}")

    existing_limit = _limit_value(query)
    target_limit = max_rows if existing_limit is None else min(existing_limit, max_rows)
    bounded = query.limit(target_limit, copy=True)

    return SQLPolicyDecision(
        sql=bounded.sql(dialect=dialect),
        tables=referenced_tables,
        row_limit=target_limit,
        limit_added_or_capped=(existing_limit is None or existing_limit > max_rows),
    )
