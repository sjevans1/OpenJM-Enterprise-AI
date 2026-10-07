"""OpenJM platform control-plane API (BV3-A).

Every route is guarded by an explicit ``platform:*`` capability, never by a
tenant role. Tenant ``admin``/``owner`` therefore has zero authority here, and a
metadata operator can read control-plane metadata without any ability to read
customer content (no route in this module returns document, source or report
content).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_platform
from app.core.config import get_settings
from app.core.identity import AuthorizationError, Principal
from app.core.platform import PlatformCapability, normalize_platform_capabilities
from app.db import get_db
from app.services import access_governance
from app.services import platform_admin
from app import version as version_module

router = APIRouter(prefix="/platform", tags=["platform"])
settings = get_settings()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TenantOut(BaseModel):
    id: str
    slug: str
    name: str
    status: str
    created_at: datetime
    member_count: int


class TenantCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)


class MembershipProvisionRequest(BaseModel):
    model_config = {"extra": "forbid"}

    subject: str = Field(min_length=1, max_length=255)
    role: str = Field(pattern="^(owner|admin)$")
    email: str | None = Field(default=None, max_length=320)
    display_name: str | None = Field(default=None, max_length=240)
    issuer: str | None = Field(default=None, max_length=255)


class MembershipProvisionOut(BaseModel):
    tenant_id: str
    principal_id: str
    subject: str
    role: str


class OperatorOut(BaseModel):
    principal_id: str
    capability: str
    status: str
    created_at: datetime
    expires_at: datetime | None = None


class OperatorGrantRequest(BaseModel):
    model_config = {"extra": "forbid"}

    principal_id: str = Field(min_length=1, max_length=64)
    capabilities: list[str] = Field(min_length=1)


class SupportGrantRequest(BaseModel):
    model_config = {"extra": "forbid"}

    principal_id: str = Field(min_length=1, max_length=64)
    scope: str = Field(pattern="^(metadata|content)$")
    expires_at: datetime | None = None


class SupportDelegationOut(BaseModel):
    tenant_id: str
    principal_id: str
    scope: str
    status: str
    created_at: datetime
    expires_at: datetime | None = None


class PlatformStatusOut(BaseModel):
    product: str
    version: str
    release_train: str
    release_id: str | None
    profile: str
    auth_mode: str


class PlatformIdentityStatusOut(BaseModel):
    auth_mode: str
    oidc_configured: bool
    issuer_configured: bool
    client_id_configured: bool
    tests_allowed: bool


class PlatformModelStatusOut(BaseModel):
    provider_mode: str
    model_name: str
    fallback_policy: str
    base_url_configured: bool
    credential_configured: bool


def _http_for(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail="Invalid platform request")


# ---------------------------------------------------------------------------
# Deployment metadata (no secrets)
# ---------------------------------------------------------------------------


@router.get("/status", response_model=PlatformStatusOut)
async def platform_status(
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
):
    return PlatformStatusOut(**version_module.build_info(settings), auth_mode=settings.auth_mode)


@router.get("/identity", response_model=PlatformIdentityStatusOut)
async def platform_identity_status(
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
):
    """Identity configuration state, deliberately without secret values."""
    return PlatformIdentityStatusOut(
        auth_mode=settings.auth_mode,
        oidc_configured=bool(settings.oidc_issuer and settings.oidc_client_id),
        issuer_configured=bool(settings.oidc_issuer),
        client_id_configured=bool(settings.oidc_client_id),
        tests_allowed=False,
    )


@router.get("/model", response_model=PlatformModelStatusOut)
async def platform_model_status(
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
):
    """Model-provider state, without the endpoint URL or credential value."""
    return PlatformModelStatusOut(
        provider_mode=settings.model_provider_mode or "local",
        model_name=settings.model_name,
        fallback_policy=settings.model_provider_fallback,
        base_url_configured=bool(settings.model_base_url),
        credential_configured=bool(settings.model_api_key),
    )


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
):
    return [TenantOut(**vars(item)) for item in await platform_admin.list_tenants(db, actor=principal)]


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
async def get_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
):
    item = await platform_admin.get_tenant(db, actor=principal, tenant_id=tenant_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return TenantOut(**vars(item))


@router.post("/tenants", response_model=TenantOut)
async def create_tenant(
    payload: TenantCreateRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.TENANTS_ADMIN)),
):
    try:
        item, _created = await platform_admin.ensure_tenant(
            db, actor=principal, slug=payload.slug, name=payload.name
        )
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc
    await db.commit()
    return TenantOut(**vars(item))


@router.post("/tenants/{tenant_id}/suspend", response_model=TenantOut)
async def suspend_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.TENANTS_ADMIN)),
):
    return await _set_status(db, principal, tenant_id, "suspended")


@router.post("/tenants/{tenant_id}/reactivate", response_model=TenantOut)
async def reactivate_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.TENANTS_ADMIN)),
):
    return await _set_status(db, principal, tenant_id, "active")


async def _set_status(db, principal, tenant_id, status):
    item = await platform_admin.set_tenant_status(
        db, actor=principal, tenant_id=tenant_id, status=status
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    await db.commit()
    return TenantOut(**vars(item))


@router.post("/tenants/{tenant_id}/memberships", response_model=MembershipProvisionOut)
async def provision_membership(
    tenant_id: str,
    payload: MembershipProvisionRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.TENANTS_ADMIN)),
):
    try:
        result = await platform_admin.provision_membership(
            db,
            actor=principal,
            tenant_id=tenant_id,
            subject=payload.subject,
            role=payload.role,
            email=payload.email,
            display_name=payload.display_name,
            issuer=payload.issuer,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_for(exc) from exc
    await db.commit()
    return MembershipProvisionOut(**result)


# ---------------------------------------------------------------------------
# Platform operators
# ---------------------------------------------------------------------------


@router.get("/operators", response_model=list[OperatorOut])
async def list_operators(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATORS_ADMIN)),
):
    rows = await access_governance.list_platform_operators(db)
    return [OperatorOut(**vars(row)) for row in rows]


@router.post("/operators", response_model=list[OperatorOut])
async def grant_operator(
    payload: OperatorGrantRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATORS_ADMIN)),
):
    try:
        capabilities = normalize_platform_capabilities(payload.capabilities)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = await access_governance.grant_platform_operator(
        db,
        principal_id=payload.principal_id,
        capabilities=capabilities,
        granted_by=principal.principal_id,
    )
    await db.commit()
    return [OperatorOut(**vars(row)) for row in rows]


@router.delete("/operators/{principal_id}")
async def revoke_operator(
    principal_id: str,
    capability: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATORS_ADMIN)),
):
    try:
        revoked = await access_governance.revoke_platform_operator(
            db, principal_id=principal_id, capability=capability
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return {"revoked": revoked}


# ---------------------------------------------------------------------------
# Support delegations
# ---------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/support", response_model=list[SupportDelegationOut])
async def list_support(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
):
    rows = await access_governance.list_support_delegations(db, tenant_id=tenant_id)
    return [SupportDelegationOut(**vars(row)) for row in rows]


@router.post("/tenants/{tenant_id}/support", response_model=SupportDelegationOut)
async def grant_support(
    tenant_id: str,
    payload: SupportGrantRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATORS_ADMIN)),
):
    try:
        row = await access_governance.grant_support_delegation(
            db,
            actor=principal,
            tenant_id=tenant_id,
            principal_id=payload.principal_id,
            scope=payload.scope,
            expires_at=payload.expires_at,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_for(exc) from exc
    await db.commit()
    return SupportDelegationOut(**vars(row))


@router.delete("/tenants/{tenant_id}/support/{principal_id}")
async def revoke_support(
    tenant_id: str,
    principal_id: str,
    scope: str = Query(pattern="^(metadata|content)$"),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.OPERATORS_ADMIN)),
):
    try:
        revoked = await access_governance.revoke_support_delegation(
            db, actor=principal, tenant_id=tenant_id, principal_id=principal_id, scope=scope
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_for(exc) from exc
    await db.commit()
    return {"revoked": revoked}
