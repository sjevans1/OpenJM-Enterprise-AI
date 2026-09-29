import asyncio
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from app.core.config import get_settings
from app.models import DataSource
from app.services.credentials import CredentialVaultError, credential_vault
from app.services.data_sources import (
    DataSourceError,
    authorized_objects_from_schema,
    create_source_engine,
    decode_schema,
)
from app.services.sql_policy import (
    SQLPolicyDecision,
    SQLPolicyError,
    validate_and_rewrite_sql,
)


class StructuredExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class StructuredQueryResult:
    source_id: str
    source_name: str
    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    truncated: bool
    policy: SQLPolicyDecision


def _authorized_tables(source: DataSource) -> set[str]:
    if source.authorized_objects_json:
        try:
            payload = json.loads(source.authorized_objects_json)
            return {str(item).lower() for item in payload}
        except (TypeError, ValueError):
            pass
    return set(authorized_objects_from_schema(decode_schema(source.schema_json)))


def _allowed_columns(source: DataSource) -> dict[str, set[str]]:
    allowed: dict[str, set[str]] = {}
    for table in decode_schema(source.schema_json):
        columns = {column.name.lower() for column in table.columns}
        allowed[table.name.lower()] = columns
        allowed[table.qualified_name.lower()] = columns
    return allowed


def _dialect_for(source: DataSource) -> str:
    if source.engine == "sqlite":
        return "sqlite"
    if source.engine == "postgresql":
        return "postgres"
    raise StructuredExecutionError(f"Unsupported source engine: {source.engine}")


async def execute_structured_query(
    source: DataSource,
    proposed_sql: str,
) -> StructuredQueryResult:
    """Validate and execute one bounded read-only query against an authorized source."""
    settings = get_settings()
    if not source.enabled:
        raise StructuredExecutionError("Data source is disabled")
    if not source.schema_json:
        raise StructuredExecutionError(
            "Data source schema has not been discovered; refresh the source first"
        )

    allowed_tables = _authorized_tables(source)
    allowed_columns = _allowed_columns(source)
    if not allowed_tables:
        raise StructuredExecutionError("Data source has no authorized tables")

    try:
        policy = validate_and_rewrite_sql(
            proposed_sql,
            dialect=_dialect_for(source),
            allowed_tables=allowed_tables,
            allowed_columns=allowed_columns,
            max_rows=settings.structured_max_rows,
        )
        connection_uri = credential_vault.decrypt(source.connection_secret)
    except (SQLPolicyError, CredentialVaultError) as exc:
        raise StructuredExecutionError(str(exc)) from exc

    source_engine = create_source_engine(source.engine, connection_uri)

    async def _execute() -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]:
        async with source_engine.connect() as conn:
            # Defense in depth: the SQL policy blocks writes before execution,
            # and the database session is also switched to read-only mode.
            if source.engine == "sqlite":
                await conn.exec_driver_sql("PRAGMA query_only = ON")
            elif source.engine == "postgresql":
                await conn.execute(text("SET TRANSACTION READ ONLY"))

            result = await conn.execute(text(policy.sql))
            columns = tuple(str(key) for key in result.keys())
            rows = tuple(tuple(row) for row in result.fetchall())
            return columns, rows

    try:
        columns, rows = await asyncio.wait_for(
            _execute(),
            timeout=settings.structured_timeout_seconds,
        )
    except TimeoutError as exc:
        raise StructuredExecutionError("Structured query exceeded execution timeout") from exc
    except Exception as exc:
        raise StructuredExecutionError(f"Structured query execution failed: {exc}") from exc
    finally:
        await source_engine.dispose()

    truncated = bool(
        policy.limit_added_or_capped
        and len(rows) >= policy.row_limit
    )

    return StructuredQueryResult(
        source_id=source.id,
        source_name=source.name,
        sql=policy.sql,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        policy=policy,
    )
