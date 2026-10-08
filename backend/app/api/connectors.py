"""Connector management API (OpenJM platform control plane).

BV3-C: this is the **raw administration** surface and it sits on the OpenJM
platform plane. Every route requires the explicit ``platform:operations:admin``
capability, never a tenant role: a tenant ``admin``/``owner`` does not inherit raw
connector authority merely because the legacy permission existed. The check is
enforced by the backend on every request; hiding the surface in the UI is not a
security boundary.

End-user connector workflows are unaffected: connector-backed retrieval and the
governed action runtime reach connectors through the service layer under the
tenant permission model, not through these routes.

Each route resolves its connector through a tenant-scoped lookup and audits its
mutation. Responses carry operational metadata only: a credential value is never
returned, and configuration is filtered so a key that looks secret is omitted
rather than echoed.

The API is a management surface, not an authorization surface. Nothing here
decides whether evidence may be served; that decision belongs to the connector
authorization gate, which revalidates against the provider at retrieval time.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_platform
from app.core.connectors import connector_registry
from app.core.identity import Principal
from app.core.platform import PlatformCapability
from app.db import get_db
from app.models import ConnectorInstance, ExternalResource, WorkspaceUserMapping
from app.services.connectors import sync as sync_engine
from app.services.connectors.authorization import (
    create_mapping,
    list_mappings,
    revoke_mapping,
)
from app.services.connectors.base import ConnectorError
from app.services.connectors.ingest import list_resources
from app.services.connectors.service import (
    configure_instance,
    disconnect,
    resolve_instance,
    revoke_credential,
    rotate_credential,
    set_enabled,
    test_connection,
)

router = APIRouter(prefix="/connectors", tags=["connectors"])

_SECRETISH = ("token", "secret", "password", "passwd", "key", "credential", "authorization")


class ConnectorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    connector_type: str = Field(min_length=1, max_length=64)
    version: str | None = Field(default=None, max_length=32)
    config: dict = Field(default_factory=dict)
    credential: dict | None = None
    credential_label: str = Field(default="primary", max_length=120)


class CredentialRotate(BaseModel):
    credential: dict
    label: str | None = Field(default=None, max_length=120)


class SyncRequest(BaseModel):
    run_type: str = Field(default="incremental", max_length=16)
    limit: int | None = None


class MappingCreate(BaseModel):
    principal_id: str = Field(min_length=1, max_length=64)
    external_user_id: str = Field(min_length=1, max_length=255)


class DisconnectRequest(BaseModel):
    purge: bool = False


def _http(exc: ConnectorError) -> HTTPException:
    status = 404 if exc.code in {"connector_not_found", "resource_unknown"} else 400
    if exc.code in {"connector_disabled", "credential_revoked", "credential_missing"}:
        status = 409
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


def _public_config(config_json: str | None) -> dict:
    """Never echo a value whose key looks like a secret."""
    if not config_json:
        return {}
    try:
        parsed = json.loads(config_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        key: value
        for key, value in parsed.items()
        if not any(marker in key.lower() for marker in _SECRETISH)
    }


def _instance_out(instance: ConnectorInstance) -> dict:
    spec_key = f"{instance.connector_type}@{instance.connector_version}"
    return {
        "id": instance.id,
        "name": instance.name,
        "connector_type": instance.connector_type,
        "connector_version": instance.connector_version,
        "connector_key": spec_key,
        "display_name": instance.display_name,
        "enabled": instance.enabled,
        "status": instance.status,
        "health_status": instance.health_status,
        "health_detail": instance.health_detail,
        "config": _public_config(instance.config_json),
        "has_credential": bool(instance.credential_id),
        "last_successful_connection_at": instance.last_successful_connection_at,
        "last_successful_sync_at": instance.last_successful_sync_at,
        "last_reconciliation_at": instance.last_reconciliation_at,
        "last_failure_category": instance.last_failure_category,
        "last_failure_at": instance.last_failure_at,
        "created_at": instance.created_at,
        "updated_at": instance.updated_at,
    }


def _resource_out(resource: ExternalResource) -> dict:
    return {
        "id": resource.id,
        "external_id": resource.external_id,
        "resource_type": resource.resource_type,
        "title": resource.title,
        "external_revision": resource.external_revision,
        "lifecycle_state": resource.lifecycle_state,
        "permission_state": resource.permission_state,
        "quarantine_reason": resource.quarantine_reason,
        "document_id": resource.document_id,
        "last_observed_at": resource.last_observed_at,
        "last_synced_at": resource.last_synced_at,
        "last_reconciled_at": resource.last_reconciled_at,
        "provenance": sync_engine.provenance_for(resource),
    }


async def _instance(
    db: AsyncSession, connector_id: str, principal: Principal
) -> ConnectorInstance:
    try:
        return await resolve_instance(
            db, tenant_id=principal.tenant_id, connector_instance_id=connector_id
        )
    except ConnectorError as exc:
        raise _http(exc) from exc


# ---------------------------------------------------------------------------
# Connector type registry
# ---------------------------------------------------------------------------


@router.get("/types")
async def list_connector_types(
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    return {"types": connector_registry.describe()}


# ---------------------------------------------------------------------------
# Instances
# ---------------------------------------------------------------------------


@router.get("")
async def list_connectors(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    result = await db.execute(
        select(ConnectorInstance)
        .where(ConnectorInstance.tenant_id == principal.tenant_id)
        .order_by(ConnectorInstance.name)
    )
    return {"connectors": [_instance_out(item) for item in result.scalars().all()]}


@router.post("", status_code=201)
async def create_connector(
    payload: ConnectorCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    try:
        instance = await configure_instance(
            db,
            tenant_id=principal.tenant_id,
            actor=principal.principal_id,
            name=payload.name,
            connector_type=payload.connector_type,
            version=payload.version,
            config=payload.config,
            credential=payload.credential,
            credential_label=payload.credential_label,
        )
    except ConnectorError as exc:
        await db.rollback()
        raise _http(exc) from exc
    except Exception:
        await db.rollback()
        raise
    await db.commit()
    return _instance_out(instance)


@router.get("/{connector_id}")
async def get_connector(
    connector_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    runs = await sync_engine.latest_runs(
        db, connector_instance_id=instance.id, tenant_id=principal.tenant_id, limit=10
    )
    payload = _instance_out(instance)
    payload["runs"] = [sync_engine.run_to_dict(run) for run in runs]
    return payload


@router.post("/{connector_id}/test")
async def test_connector(
    connector_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    try:
        result = await test_connection(
            db, instance=instance, actor=principal.principal_id
        )
    except ConnectorError as exc:
        await db.rollback()
        raise _http(exc) from exc
    await db.commit()
    return {
        "ok": result.ok,
        "detail": result.detail,
        "connector": _instance_out(result.instance),
    }


@router.post("/{connector_id}/enable")
async def enable_connector(
    connector_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    try:
        await set_enabled(db, instance=instance, enabled=True, actor=principal.principal_id)
    except ConnectorError as exc:
        await db.rollback()
        raise _http(exc) from exc
    await db.commit()
    return _instance_out(instance)


@router.post("/{connector_id}/disable")
async def disable_connector(
    connector_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    await set_enabled(db, instance=instance, enabled=False, actor=principal.principal_id)
    await db.commit()
    return _instance_out(instance)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@router.post("/{connector_id}/credentials/rotate")
async def rotate_connector_credential(
    connector_id: str,
    payload: CredentialRotate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    try:
        credential = await rotate_credential(
            db,
            instance=instance,
            actor=principal.principal_id,
            credential=payload.credential,
            label=payload.label,
        )
    except ConnectorError as exc:
        await db.rollback()
        raise _http(exc) from exc
    await db.commit()
    return {
        "rotated": True,
        "credential_id": credential.id,
        "version": credential.version,
        "connector": _instance_out(instance),
    }


@router.post("/{connector_id}/credentials/revoke")
async def revoke_connector_credential(
    connector_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    revoked = await revoke_credential(db, instance=instance, actor=principal.principal_id)
    await db.commit()
    return {"revoked": revoked, "connector": _instance_out(instance)}


# ---------------------------------------------------------------------------
# Disconnect / purge
# ---------------------------------------------------------------------------


@router.post("/{connector_id}/disconnect")
async def disconnect_connector(
    connector_id: str,
    payload: DisconnectRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    quarantined = await disconnect(
        db, instance=instance, actor=principal.principal_id, purge=payload.purge
    )
    await db.commit()
    return {"quarantined": quarantined, "connector": _instance_out(instance)}


# ---------------------------------------------------------------------------
# Synchronization
# ---------------------------------------------------------------------------


@router.post("/{connector_id}/sync")
async def sync_connector(
    connector_id: str,
    payload: SyncRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    try:
        run = await sync_engine.run_sync(
            db,
            instance=instance,
            actor=principal.principal_id,
            run_type=payload.run_type,
            limit=payload.limit,
        )
    except ConnectorError as exc:
        await db.rollback()
        raise _http(exc) from exc
    return sync_engine.run_to_dict(run)


@router.get("/{connector_id}/runs")
async def connector_runs(
    connector_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    runs = await sync_engine.latest_runs(
        db,
        connector_instance_id=instance.id,
        tenant_id=principal.tenant_id,
        limit=max(1, min(limit, 100)),
    )
    return {"runs": [sync_engine.run_to_dict(run) for run in runs]}


@router.get("/{connector_id}/resources")
async def connector_resources(
    connector_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    resources = await list_resources(
        db, connector_instance_id=instance.id, tenant_id=principal.tenant_id
    )
    return {"resources": [_resource_out(resource) for resource in resources]}


# ---------------------------------------------------------------------------
# User mapping
# ---------------------------------------------------------------------------


@router.get("/{connector_id}/mappings")
async def connector_mappings(
    connector_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    mappings = await list_mappings(
        db, connector_instance_id=instance.id, tenant_id=principal.tenant_id
    )
    return {
        "mappings": [
            {
                "id": item.id,
                "principal_id": item.principal_id,
                "external_user_id": item.external_user_id,
                "status": item.status,
                "created_at": item.created_at,
                "revoked_at": item.revoked_at,
            }
            for item in mappings
        ]
    }


@router.post("/{connector_id}/mappings", status_code=201)
async def create_connector_mapping(
    connector_id: str,
    payload: MappingCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    try:
        mapping = await create_mapping(
            db,
            connector_instance_id=instance.id,
            tenant_id=principal.tenant_id,
            principal_id=payload.principal_id,
            external_user_id=payload.external_user_id,
            actor=principal.principal_id,
        )
    except ConnectorError as exc:
        await db.rollback()
        raise _http(exc) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "mapping_conflict", "message": "mapping is ambiguous or already exists"},
        ) from exc
    await db.commit()
    return {
        "id": mapping.id,
        "principal_id": mapping.principal_id,
        "external_user_id": mapping.external_user_id,
        "status": mapping.status,
    }


@router.delete("/{connector_id}/mappings/{mapping_id}")
async def delete_connector_mapping(
    connector_id: str,
    mapping_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATIONS_ADMIN)),
) -> dict:
    instance = await _instance(db, connector_id, principal)
    result = await db.execute(
        select(WorkspaceUserMapping).where(
            WorkspaceUserMapping.id == mapping_id,
            WorkspaceUserMapping.connector_instance_id == instance.id,
            WorkspaceUserMapping.tenant_id == principal.tenant_id,
        )
    )
    mapping = result.scalars().first()
    if mapping is None:
        raise HTTPException(
            status_code=404, detail={"code": "mapping_not_found", "message": "mapping not found"}
        )
    await revoke_mapping(db, mapping=mapping, actor=principal.principal_id)
    await db.commit()
    return {"revoked": True, "id": mapping.id}
