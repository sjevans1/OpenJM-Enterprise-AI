"""INF1 inference registry API (OpenJM platform plane).

Bounded operator operations for model releases, runtime profiles, deployments,
tenant bindings and redacted routing-decision reads.

Authorization is the platform axis, never a tenant role: mutations require
``platform:inference:admin`` and reads require ``platform:metadata:read``. A
tenant owner holds neither. Lists are paginated, tenant level decision history
requires an explicit tenant filter, and private endpoint or credential
references are never returned in any response.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_platform
from app.core.identity import AuthorizationError, Principal
from app.core.inference import DeploymentLifecycle, validate_rahkia_alias
from app.core.platform import PlatformCapability
from app.db import get_db
from app.models import (
    InferenceDeployment,
    InferenceModelRelease,
    InferenceRoutingDecision,
    InferenceRuntimeProfile,
)
from app.services.inference import registry as registry_service

router = APIRouter(prefix="/platform/inference", tags=["platform-inference"])

_READ = require_platform(PlatformCapability.METADATA_READ)
_ADMIN = require_platform(PlatformCapability.INFERENCE_ADMIN)

MAX_PAGE = 200


class ModelReleaseCreate(BaseModel):
    model_config = {"extra": "forbid"}

    rahkia_alias: str = Field(min_length=1, max_length=120)
    artifact_ref: str | None = Field(default=None, max_length=500)
    tokenizer_ref: str | None = Field(default=None, max_length=500)
    tokenizer_revision: str | None = Field(default=None, max_length=120)
    chat_template_revision: str | None = Field(default=None, max_length=120)
    capabilities: list[str] = Field(default_factory=list)
    max_context_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    lineage_ref: str | None = Field(default=None, max_length=500)
    commercial_use_ref: str | None = Field(default=None, max_length=240)
    evaluation_ref: str | None = Field(default=None, max_length=240)
    release_version: int = Field(default=1, ge=1)


class RuntimeProfileCreate(BaseModel):
    model_config = {"extra": "forbid"}

    adapter_kind: str = Field(min_length=1, max_length=64)
    adapter_version: str = Field(min_length=1, max_length=64)
    engine_kind: str = Field(min_length=1, max_length=64)
    engine_version: str = Field(min_length=1, max_length=120)
    capabilities: list[str] = Field(default_factory=list)
    usage_method: str = Field(default="unknown")
    cache_policy: str = Field(default="none", max_length=32)
    qualification_ref: str | None = Field(default=None, max_length=240)
    image_ref: str | None = Field(default=None, max_length=500)
    cancellation_supported: bool = False
    profile_version: int = Field(default=1, ge=1)


class DeploymentCreate(BaseModel):
    model_config = {"extra": "forbid"}

    deployment_id: str = Field(min_length=1, max_length=36)
    model_release_id: str = Field(min_length=1, max_length=36)
    runtime_profile_id: str = Field(min_length=1, max_length=36)
    inference_mode: str = Field(min_length=1, max_length=32)
    owner_tenant_id: str | None = None
    site_id: str | None = Field(default=None, max_length=64)
    installation_id: str | None = Field(default=None, max_length=64)
    residency_domain: str | None = Field(default=None, max_length=64)
    endpoint_ref: str | None = Field(default=None, max_length=240)
    credential_ref: str | None = Field(default=None, max_length=240)
    lifecycle: str = Field(default=DeploymentLifecycle.DISABLED.value)
    connectivity_mode: str = Field(default="connected", max_length=32)
    max_context_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    max_request_bytes: int | None = Field(default=None, gt=0)
    security_qualification_ref: str | None = Field(default=None, max_length=240)


class LifecycleUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    lifecycle: str = Field(min_length=1, max_length=32)
    expected_revision: int = Field(ge=1)


class HealthRecord(BaseModel):
    model_config = {"extra": "forbid"}

    status: str = Field(pattern="^(healthy|degraded|unknown)$")
    deployment_revision: int = Field(ge=1)
    model_present: bool | None = None
    failure_code: str | None = Field(default=None, max_length=64)
    ttl_seconds: int = Field(default=120, ge=1, le=3600)


class BindingUpsert(BaseModel):
    model_config = {"extra": "forbid"}

    tenant_id: str = Field(min_length=1, max_length=64)
    rahkia_alias: str = Field(min_length=1, max_length=120)
    allowed_model_release_ids: list[str] = Field(default_factory=list)
    allowed_deployment_ids: list[str] = Field(default_factory=list)
    allowed_sites: list[str] = Field(default_factory=list)
    allowed_modes: list[str] = Field(default_factory=list)
    allowed_data_classes: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    priority: list[str] = Field(default_factory=list)
    allow_hosted: bool = False
    fallback_policy: str = Field(default="none", max_length=32)
    expected_revision: int | None = Field(default=None, ge=1)


def _http_for(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail="Invalid inference registry request")


def _model_out(row: InferenceModelRelease) -> dict:
    return {
        "id": row.id,
        "rahkia_alias": row.rahkia_alias,
        "release_version": row.release_version,
        "capabilities": _loads(row.capabilities_json),
        "max_context_tokens": row.max_context_tokens,
        "max_output_tokens": row.max_output_tokens,
        "tokenizer_revision": row.tokenizer_revision,
        "chat_template_revision": row.chat_template_revision,
        "status": row.status,
        "created_at": row.created_at,
    }


def _runtime_out(row: InferenceRuntimeProfile) -> dict:
    return {
        "id": row.id,
        "adapter_kind": row.adapter_kind,
        "adapter_version": row.adapter_version,
        "engine_kind": row.engine_kind,
        "engine_version": row.engine_version,
        "profile_version": row.profile_version,
        "capabilities": _loads(row.capabilities_json),
        "usage_method": row.usage_method,
        "cancellation_supported": row.cancellation_supported,
        "status": row.status,
    }


def _deployment_out(row: InferenceDeployment) -> dict:
    # endpoint_ref and credential_ref are deliberately absent: private endpoint
    # and credential provisioning stays in trusted operator configuration.
    return {
        "id": row.id,
        "deployment_id": row.deployment_id,
        "revision": row.revision,
        "model_release_id": row.model_release_id,
        "runtime_profile_id": row.runtime_profile_id,
        "inference_mode": row.inference_mode,
        "owner_tenant_id": row.owner_tenant_id,
        "site_id": row.site_id,
        "installation_id": row.installation_id,
        "lifecycle": row.lifecycle,
        "connectivity_mode": row.connectivity_mode,
        "max_context_tokens": row.max_context_tokens,
        "max_output_tokens": row.max_output_tokens,
        "max_request_bytes": row.max_request_bytes,
        "security_qualification_ref": row.security_qualification_ref,
    }


def _binding_out(row) -> dict:
    return {
        "id": row.id,
        "binding_id": row.binding_id,
        "revision": row.revision,
        "tenant_id": row.tenant_id,
        "rahkia_alias": row.rahkia_alias,
        "allowed_model_release_ids": _loads(row.allowed_model_release_ids_json),
        "allowed_deployment_ids": _loads(row.allowed_deployment_ids_json),
        "allowed_sites": _loads(row.allowed_sites_json),
        "allowed_modes": _loads(row.allowed_modes_json),
        "allowed_data_classes": _loads(row.allowed_data_classes_json),
        "required_capabilities": _loads(row.required_capabilities_json),
        "priority": _loads(row.priority_json),
        "allow_hosted": row.allow_hosted,
        "fallback_policy": row.fallback_policy,
        "status": row.status,
    }


def _loads(raw: str | None) -> list[str]:
    import json

    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


@router.get("/models")
async def list_models(
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_READ),
):
    rows = (
        await db.execute(
            select(InferenceModelRelease).order_by(InferenceModelRelease.rahkia_alias).limit(limit)
        )
    ).scalars().all()
    return {"models": [_model_out(row) for row in rows]}


@router.post("/models", status_code=201)
async def create_model(
    payload: ModelReleaseCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    try:
        row = await registry_service.create_model_release(
            db,
            actor=principal,
            rahkia_alias=payload.rahkia_alias,
            artifact_ref=payload.artifact_ref,
            tokenizer_ref=payload.tokenizer_ref,
            tokenizer_revision=payload.tokenizer_revision,
            chat_template_revision=payload.chat_template_revision,
            capabilities=payload.capabilities,
            max_context_tokens=payload.max_context_tokens,
            max_output_tokens=payload.max_output_tokens,
            lineage_ref=payload.lineage_ref,
            commercial_use_ref=payload.commercial_use_ref,
            evaluation_ref=payload.evaluation_ref,
            release_version=payload.release_version,
        )
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc
    await db.commit()
    return _model_out(row)


@router.get("/runtimes")
async def list_runtimes(
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_READ),
):
    rows = (
        await db.execute(
            select(InferenceRuntimeProfile).order_by(InferenceRuntimeProfile.id).limit(limit)
        )
    ).scalars().all()
    return {"runtimes": [_runtime_out(row) for row in rows]}


@router.post("/runtimes", status_code=201)
async def create_runtime(
    payload: RuntimeProfileCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    try:
        row = await registry_service.create_runtime_profile(
            db,
            actor=principal,
            adapter_kind=payload.adapter_kind,
            adapter_version=payload.adapter_version,
            engine_kind=payload.engine_kind,
            engine_version=payload.engine_version,
            capabilities=payload.capabilities,
            usage_method=payload.usage_method,
            cache_policy=payload.cache_policy,
            qualification_ref=payload.qualification_ref,
            image_ref=payload.image_ref,
            cancellation_supported=payload.cancellation_supported,
            profile_version=payload.profile_version,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_for(exc) from exc
    await db.commit()
    return _runtime_out(row)


@router.get("/deployments")
async def list_deployments(
    site_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_READ),
):
    rows = await registry_service.list_deployments(db, site_id=site_id, limit=limit)
    return {"deployments": [_deployment_out(row) for row in rows]}


@router.post("/deployments", status_code=201)
async def create_deployment(
    payload: DeploymentCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    try:
        row = await registry_service.create_deployment(
            db,
            actor=principal,
            deployment_id=payload.deployment_id,
            model_release_id=payload.model_release_id,
            runtime_profile_id=payload.runtime_profile_id,
            inference_mode=payload.inference_mode,
            owner_tenant_id=payload.owner_tenant_id,
            site_id=payload.site_id,
            installation_id=payload.installation_id,
            residency_domain=payload.residency_domain,
            endpoint_ref=payload.endpoint_ref,
            credential_ref=payload.credential_ref,
            lifecycle=payload.lifecycle,
            connectivity_mode=payload.connectivity_mode,
            max_context_tokens=payload.max_context_tokens,
            max_output_tokens=payload.max_output_tokens,
            max_request_bytes=payload.max_request_bytes,
            security_qualification_ref=payload.security_qualification_ref,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_for(exc) from exc
    await db.commit()
    return _deployment_out(row)


@router.post("/deployments/{deployment_id}/lifecycle")
async def update_lifecycle(
    deployment_id: str,
    payload: LifecycleUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    try:
        row = await registry_service.set_deployment_lifecycle(
            db,
            actor=principal,
            deployment_id=deployment_id,
            lifecycle=payload.lifecycle,
            expected_revision=payload.expected_revision,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_for(exc) from exc
    await db.commit()
    return _deployment_out(row)


@router.post("/deployments/{deployment_id}/health", status_code=201)
async def record_health(
    deployment_id: str,
    payload: HealthRecord,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    try:
        row = await registry_service.record_health_observation(
            db,
            deployment_id=deployment_id,
            deployment_revision=payload.deployment_revision,
            status=payload.status,
            model_present=payload.model_present,
            failure_code=payload.failure_code,
            ttl_seconds=payload.ttl_seconds,
            actor=principal,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_for(exc) from exc
    await db.commit()
    return {"id": row.id, "status": row.status, "expires_at": row.expires_at}


@router.get("/bindings")
async def list_bindings(
    tenant_id: str = Query(min_length=1, max_length=64),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_READ),
):
    """Tenant bindings for one explicitly named tenant.

    The tenant filter is required, so this surface can never be used to sweep
    the registry looking for tenants.
    """
    rows = await registry_service.list_bindings(db, tenant_id=tenant_id)
    return {"bindings": [_binding_out(row) for row in rows]}


@router.put("/bindings")
async def upsert_binding(
    payload: BindingUpsert,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    try:
        row = await registry_service.upsert_tenant_binding(
            db,
            actor=principal,
            tenant_id=payload.tenant_id,
            rahkia_alias=payload.rahkia_alias,
            allowed_model_release_ids=payload.allowed_model_release_ids,
            allowed_deployment_ids=payload.allowed_deployment_ids,
            allowed_sites=payload.allowed_sites,
            allowed_modes=payload.allowed_modes,
            allowed_data_classes=payload.allowed_data_classes,
            required_capabilities=payload.required_capabilities,
            priority=payload.priority,
            allow_hosted=payload.allow_hosted,
            fallback_policy=payload.fallback_policy,
            expected_revision=payload.expected_revision,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_for(exc) from exc
    await db.commit()
    return _binding_out(row)


@router.post("/bindings/revoke")
async def revoke_binding(
    tenant_id: str = Query(min_length=1, max_length=64),
    rahkia_alias: str = Query(min_length=1, max_length=120),
    expected_revision: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    try:
        validate_rahkia_alias(rahkia_alias)
        row = await registry_service.revoke_tenant_binding(
            db,
            actor=principal,
            tenant_id=tenant_id,
            rahkia_alias=rahkia_alias,
            expected_revision=expected_revision,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_for(exc) from exc
    await db.commit()
    return _binding_out(row)


@router.get("/routing-decisions")
async def list_routing_decisions(
    tenant_id: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_READ),
):
    """Redacted decision history for one tenant.

    The record is content free by construction: no endpoint, credential, prompt
    hash or another tenant's resource identifier exists in the table.
    """
    rows = (
        await db.execute(
            select(InferenceRoutingDecision)
            .where(InferenceRoutingDecision.tenant_id == tenant_id)
            .order_by(InferenceRoutingDecision.decided_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {
        "decisions": [
            {
                "id": row.id,
                "attempt_id": row.attempt_id,
                "reason": row.reason,
                "deployment_id": row.deployment_id,
                "deployment_revision": row.deployment_revision,
                "model_release_id": row.model_release_id,
                "runtime_profile_id": row.runtime_profile_id,
                "binding_revision": row.binding_revision,
                "decided_at": row.decided_at.isoformat() if isinstance(row.decided_at, datetime) else row.decided_at,
            }
            for row in rows
        ]
    }
