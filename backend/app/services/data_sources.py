import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import REPO_ROOT, get_settings
from app.schemas import (
    DataColumnSchema,
    DataForeignKeySchema,
    DataTableSchema,
)


class DataSourceError(RuntimeError):
    pass


SYSTEM_SCHEMAS = {
    "information_schema",
    "pg_catalog",
    "pg_toast",
}


def normalize_connection_uri(engine: str, connection_uri: str) -> str:
    """Normalize supported source URIs to async SQLAlchemy drivers."""
    try:
        url = make_url(connection_uri)
    except Exception as exc:
        raise DataSourceError("Invalid database connection URI") from exc

    backend = url.get_backend_name()
    if engine == "sqlite":
        if backend != "sqlite":
            raise DataSourceError("SQLite source requires a sqlite:// connection URI")
        database = url.database
        if database and database != ":memory:":
            path = Path(database)
            if not path.is_absolute():
                path = (REPO_ROOT / path).resolve()
            url = url.set(database=str(path), drivername="sqlite+aiosqlite")
        else:
            url = url.set(drivername="sqlite+aiosqlite")
    elif engine == "postgresql":
        if backend not in {"postgresql", "postgres"}:
            raise DataSourceError(
                "PostgreSQL source requires a postgresql:// connection URI"
            )
        url = url.set(drivername="postgresql+asyncpg")
    else:
        raise DataSourceError(f"Unsupported data source engine: {engine}")

    return url.render_as_string(hide_password=False)


def create_source_engine(engine: str, connection_uri: str) -> AsyncEngine:
    settings = get_settings()
    normalized = normalize_connection_uri(engine, connection_uri)
    kwargs: dict[str, Any] = {
        "future": True,
        "pool_pre_ping": True,
    }
    if engine == "sqlite":
        kwargs["connect_args"] = {
            "timeout": settings.structured_timeout_seconds,
        }
    elif engine == "postgresql":
        kwargs["connect_args"] = {
            "server_settings": {
                "statement_timeout": str(settings.structured_timeout_seconds * 1000)
            }
        }
    return create_async_engine(normalized, **kwargs)


async def test_source_connection(engine: str, connection_uri: str) -> None:
    source_engine = create_source_engine(engine, connection_uri)
    try:
        async with source_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise DataSourceError(f"Connection test failed: {exc}") from exc
    finally:
        await source_engine.dispose()


def _schema_snapshot(sync_conn, engine: str) -> list[DataTableSchema]:
    inspector = inspect(sync_conn)
    if engine == "sqlite":
        schema_names: Sequence[str | None] = [None]
    else:
        schema_names = [
            name
            for name in inspector.get_schema_names()
            if name not in SYSTEM_SCHEMAS and not name.startswith("pg_")
        ]

    tables: list[DataTableSchema] = []
    for schema_name in schema_names:
        display_schema = "main" if engine == "sqlite" else str(schema_name)
        for table_name in inspector.get_table_names(schema=schema_name):
            pk = inspector.get_pk_constraint(table_name, schema=schema_name) or {}
            pk_columns = list(pk.get("constrained_columns") or [])
            columns = []
            for column in inspector.get_columns(table_name, schema=schema_name):
                columns.append(
                    DataColumnSchema(
                        name=str(column["name"]),
                        type=str(column.get("type", "unknown")),
                        nullable=bool(column.get("nullable", True)),
                        primary_key=str(column["name"]) in pk_columns,
                    )
                )

            foreign_keys = []
            for fk in inspector.get_foreign_keys(table_name, schema=schema_name):
                referred_table = fk.get("referred_table")
                if not referred_table:
                    continue
                foreign_keys.append(
                    DataForeignKeySchema(
                        constrained_columns=list(fk.get("constrained_columns") or []),
                        referred_schema=fk.get("referred_schema"),
                        referred_table=str(referred_table),
                        referred_columns=list(fk.get("referred_columns") or []),
                    )
                )

            qualified = (
                table_name
                if engine == "sqlite"
                else f"{display_schema}.{table_name}"
            )
            tables.append(
                DataTableSchema(
                    schema_name=display_schema,
                    name=table_name,
                    qualified_name=qualified,
                    columns=columns,
                    primary_key=pk_columns,
                    foreign_keys=foreign_keys,
                )
            )

    return sorted(tables, key=lambda item: item.qualified_name.lower())


async def discover_source_schema(
    engine: str,
    connection_uri: str,
) -> list[DataTableSchema]:
    source_engine = create_source_engine(engine, connection_uri)
    try:
        async with source_engine.connect() as conn:
            return await conn.run_sync(lambda sync_conn: _schema_snapshot(sync_conn, engine))
    except Exception as exc:
        raise DataSourceError(f"Schema discovery failed: {exc}") from exc
    finally:
        await source_engine.dispose()


def encode_schema(tables: list[DataTableSchema]) -> str:
    return json.dumps([table.model_dump(mode="json") for table in tables])


def decode_schema(raw: str | None) -> list[DataTableSchema]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
        return [DataTableSchema.model_validate(item) for item in payload]
    except (ValueError, TypeError):
        return []


def authorized_objects_from_schema(tables: list[DataTableSchema]) -> list[str]:
    objects: set[str] = set()
    for table in tables:
        objects.add(table.name.lower())
        objects.add(table.qualified_name.lower())
    return sorted(objects)
