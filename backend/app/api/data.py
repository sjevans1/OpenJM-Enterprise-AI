import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db import get_db
from app.models import DataSource
from app.schemas import (
    DataSourceCreate,
    DataSourceEnabledUpdate,
    DataSourceOut,
    DataSourceSchemaRefreshResult,
    DataSourceTestResult,
)
from app.services.credentials import CredentialVaultError, credential_vault
from app.services.data_sources import (
    DataSourceError,
    authorized_objects_from_schema,
    decode_schema,
    discover_source_schema,
    encode_schema,
    normalize_connection_uri,
    test_source_connection,
)


router = APIRouter(prefix="/data/sources", tags=["data"])
settings = get_settings()


def _source_out(source: DataSource) -> DataSourceOut:
    return DataSourceOut(
        id=source.id,
        name=source.name,
        engine=source.engine,
        status=source.status,
        enabled=source.enabled,
        tables=decode_schema(source.schema_json),
        last_error=source.last_error,
        last_schema_refresh=source.last_schema_refresh,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


async def _owned_source(db: AsyncSession, source_id: str) -> DataSource | None:
    result = await db.execute(
        select(DataSource).where(
            DataSource.id == source_id,
            DataSource.user_id == settings.dev_user_id,
        )
    )
    return result.scalars().first()


def _safe_error(exc: Exception, secret: str | None = None) -> str:
    message = str(exc)
    if secret:
        message = message.replace(secret, "[redacted]")
    return message[:1000]


@router.get("", response_model=list[DataSourceOut])
async def list_sources(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DataSource)
        .where(DataSource.user_id == settings.dev_user_id)
        .order_by(DataSource.created_at.desc())
    )
    return [_source_out(source) for source in result.scalars().all()]


@router.post("", response_model=DataSourceOut)
async def create_source(
    request: DataSourceCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        normalized_uri = normalize_connection_uri(request.engine, request.connection_uri)
        encrypted = credential_vault.encrypt(normalized_uri)
    except (DataSourceError, CredentialVaultError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    source = DataSource(
        user_id=settings.dev_user_id,
        name=request.name.strip(),
        engine=request.engine,
        connection_secret=encrypted,
        status="untested",
        enabled=request.enabled,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return _source_out(source)


@router.get("/{source_id}", response_model=DataSourceOut)
async def get_source(source_id: str, db: AsyncSession = Depends(get_db)):
    source = await _owned_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    return _source_out(source)


@router.post("/{source_id}/test", response_model=DataSourceTestResult)
async def test_source(source_id: str, db: AsyncSession = Depends(get_db)):
    source = await _owned_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")

    try:
        connection_uri = credential_vault.decrypt(source.connection_secret)
        await test_source_connection(source.engine, connection_uri)
    except (CredentialVaultError, DataSourceError) as exc:
        source.status = "error"
        source.last_error = _safe_error(exc)
        await db.commit()
        return DataSourceTestResult(
            source_id=source.id,
            status=source.status,
            ok=False,
            detail=source.last_error or "Connection test failed",
        )

    source.status = "connected"
    source.last_error = None
    await db.commit()
    return DataSourceTestResult(
        source_id=source.id,
        status=source.status,
        ok=True,
        detail="Connection successful",
    )


@router.post(
    "/{source_id}/refresh",
    response_model=DataSourceSchemaRefreshResult,
)
async def refresh_source_schema(
    source_id: str,
    db: AsyncSession = Depends(get_db),
):
    source = await _owned_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")

    try:
        connection_uri = credential_vault.decrypt(source.connection_secret)
        tables = await discover_source_schema(source.engine, connection_uri)
    except (CredentialVaultError, DataSourceError) as exc:
        source.status = "error"
        source.last_error = _safe_error(exc)
        await db.commit()
        raise HTTPException(status_code=400, detail=source.last_error) from exc

    source.schema_json = encode_schema(tables)
    source.authorized_objects_json = json.dumps(
        authorized_objects_from_schema(tables)
    )
    source.status = "connected"
    source.last_error = None
    source.last_schema_refresh = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(source)
    return DataSourceSchemaRefreshResult(
        source=_source_out(source),
        table_count=len(tables),
    )


@router.patch("/{source_id}", response_model=DataSourceOut)
async def update_source(
    source_id: str,
    request: DataSourceEnabledUpdate,
    db: AsyncSession = Depends(get_db),
):
    source = await _owned_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    source.enabled = request.enabled
    source.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(source)
    return _source_out(source)


@router.delete("/{source_id}")
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db)):
    source = await _owned_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    await db.delete(source)
    await db.commit()
    return {"deleted": True, "source_id": source_id}
