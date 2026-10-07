import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require
from app.core.config import get_settings
from app.core.identity import AuthorizationError, Permission, Principal
from app.db import get_db
from app.models import DataSource
from app.schemas import (
    DataSourceCreate,
    DataSourceCurrencyUpdate,
    DataSourceEnabledUpdate,
    DataSourceOut,
    DataSourceSchemaRefreshResult,
    DataSourceTestResult,
)
from app.services import access_governance
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
        revenue_currency=source.revenue_currency,
        status=source.status,
        enabled=source.enabled,
        classification=source.classification,
        department_id=source.department_id,
        tenant_visible=source.tenant_visible,
        tables=decode_schema(source.schema_json),
        last_error=source.last_error,
        last_schema_refresh=source.last_schema_refresh,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


async def _owned_source(
    db: AsyncSession, source_id: str, principal: Principal
) -> DataSource | None:
    """Resolve a source only within the caller's tenant *and* ownership.

    A source belonging to another tenant is indistinguishable from a missing
    one, so a cross-tenant probe cannot enumerate the other tenant's resources.
    """
    result = await db.execute(
        select(DataSource).where(
            DataSource.id == source_id,
            DataSource.tenant_id == principal.tenant_id,
            DataSource.user_id == principal.user_id,
        )
    )
    return result.scalars().first()


def _safe_error(exc: Exception, secret: str | None = None) -> str:
    """Return a fixed public error message, never driver/credential text."""
    return "Data source configuration or connection failed"


@router.get("", response_model=list[DataSourceOut])
async def list_sources(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.DATA_READ)),
):
    result = await db.execute(
        select(DataSource)
        .where(
            DataSource.tenant_id == principal.tenant_id,
            DataSource.user_id == principal.user_id,
        )
        .order_by(DataSource.created_at.desc())
    )
    return [_source_out(source) for source in result.scalars().all()]


@router.post("", response_model=DataSourceOut)
async def create_source(
    request: DataSourceCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.DATA_WRITE)),
):
    try:
        normalized_uri = normalize_connection_uri(request.engine, request.connection_uri)
        encrypted = credential_vault.encrypt(normalized_uri)
    except (DataSourceError, CredentialVaultError) as exc:
        raise HTTPException(status_code=400, detail=_safe_error(exc)) from exc

    source = DataSource(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        name=request.name.strip(),
        engine=request.engine,
        connection_secret=encrypted,
        revenue_currency=request.revenue_currency,
        status="untested",
        enabled=request.enabled,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return _source_out(source)


@router.get("/{source_id}", response_model=DataSourceOut)
async def get_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.DATA_READ)),
):
    source = await _owned_source(db, source_id, principal)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    return _source_out(source)


@router.post("/{source_id}/test", response_model=DataSourceTestResult)
async def test_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.DATA_WRITE)),
):
    source = await _owned_source(db, source_id, principal)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")

    connection_uri: str | None = None
    try:
        connection_uri = credential_vault.decrypt(source.connection_secret)
        await test_source_connection(source.engine, connection_uri)
    except (CredentialVaultError, DataSourceError) as exc:
        source.status = "error"
        source.last_error = _safe_error(exc, connection_uri)
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
    principal: Principal = Depends(require(Permission.DATA_WRITE)),
):
    source = await _owned_source(db, source_id, principal)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")

    connection_uri: str | None = None
    try:
        connection_uri = credential_vault.decrypt(source.connection_secret)
        tables = await discover_source_schema(source.engine, connection_uri)
    except (CredentialVaultError, DataSourceError) as exc:
        source.status = "error"
        source.last_error = _safe_error(exc, connection_uri)
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
    principal: Principal = Depends(require(Permission.DATA_WRITE)),
):
    source = await _owned_source(db, source_id, principal)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    source.enabled = request.enabled
    source.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(source)
    return _source_out(source)


@router.patch("/{source_id}/currency", response_model=DataSourceOut)
async def update_source_currency(
    source_id: str,
    request: DataSourceCurrencyUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.DATA_WRITE)),
):
    """Operator-declared transaction currency, never inferred from amounts."""
    source = await _owned_source(db, source_id, principal)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    source.revenue_currency = request.revenue_currency
    source.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(source)
    return _source_out(source)


class DataSourcePolicyUpdate(BaseModel):
    classification: Literal["public", "internal", "confidential", "highly_restricted"]
    department_id: str | None = Field(default=None, max_length=36)
    tenant_visible: bool | None = None
    allowed_group_ids: list[str] | None = Field(default=None, max_length=200)


@router.patch("/{source_id}/policy", response_model=DataSourceOut)
async def update_source_policy(
    source_id: str,
    payload: DataSourcePolicyUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.DATA_WRITE)),
):
    """Set a governed Data source's classification/policy (steward or admin only).

    The permission gate admits any Data writer; the service then requires a
    tenant admin or a steward whose scope covers the source, and audits it.
    """
    try:
        source = await access_governance.set_data_source_policy(
            db, principal=principal, source_id=source_id, **payload.model_dump()
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if source is None:
        raise HTTPException(status_code=404, detail="Data source not found")
    await db.commit()
    return _source_out(source)


@router.delete("/{source_id}")
async def delete_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.DATA_WRITE)),
):
    source = await _owned_source(db, source_id, principal)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    await db.delete(source)
    await db.commit()
    return {"deleted": True, "source_id": source_id}
