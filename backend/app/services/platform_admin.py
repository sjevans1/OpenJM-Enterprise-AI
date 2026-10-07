"""OpenJM platform control-plane services (BV3-A).

Backs the platform-administration APIs with the authority axis already accepted
in BV1-A: a caller must hold an explicit ``platform:*`` capability, never a
tenant role. Every privileged mutation is audited, tenant creation and
membership provisioning are idempotent, and nothing here reads or returns
customer content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import Principal
from app.core.platform import PlatformCapability
from app.models import Tenant, TenantMembership
from app.services import identity as identity_service

TENANT_STATUSES: tuple[str, ...] = ("active", "suspended")
PROVISIONABLE_ROLES: tuple[str, ...] = ("owner", "admin")


@dataclass(frozen=True)
class TenantMetadata:
    """Control-plane view of a tenant. Never includes customer content."""

    id: str
    slug: str
    name: str
    status: str
    created_at: datetime
    member_count: int


def _require(actor: Principal, capability: PlatformCapability) -> None:
    actor.require_platform(capability)


async def _audit(
    db: AsyncSession,
    actor: Principal,
    action: str,
    resource_id: str | None,
    metadata: dict | None = None,
) -> None:
    await identity_service.record_audit(
        db,
        principal=actor,
        action=action,
        decision="allow",
        resource_type="tenant",
        resource_id=resource_id,
        metadata=metadata,
    )


async def _metadata_for(db: AsyncSession, tenant: Tenant) -> TenantMetadata:
    members = await db.scalar(
        select(func.count())
        .select_from(TenantMembership)
        .where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.status == "active",
        )
    )
    return TenantMetadata(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        status=tenant.status,
        created_at=tenant.created_at,
        member_count=int(members or 0),
    )


async def list_tenants(db: AsyncSession, *, actor: Principal) -> list[TenantMetadata]:
    _require(actor, PlatformCapability.METADATA_READ)
    rows = (await db.execute(select(Tenant).order_by(Tenant.created_at))).scalars().all()
    return [await _metadata_for(db, tenant) for tenant in rows]


async def get_tenant(
    db: AsyncSession, *, actor: Principal, tenant_id: str
) -> TenantMetadata | None:
    _require(actor, PlatformCapability.METADATA_READ)
    tenant = await db.get(Tenant, tenant_id)
    return await _metadata_for(db, tenant) if tenant is not None else None


async def ensure_tenant(
    db: AsyncSession,
    *,
    actor: Principal,
    slug: str,
    name: str,
    tenant_id: str | None = None,
) -> tuple[TenantMetadata, bool]:
    """Create a tenant, or return the existing one for the same slug.

    Idempotent by slug so a retried provisioning call cannot create a duplicate
    tenant. Returns ``(metadata, created)``.
    """
    _require(actor, PlatformCapability.TENANTS_ADMIN)
    if not slug.strip() or not name.strip():
        raise ValueError("Tenant slug and name are required")
    existing = (
        await db.execute(select(Tenant).where(Tenant.slug == slug))
    ).scalar_one_or_none()
    if existing is not None:
        await _audit(
            db, actor, "platform.tenant.ensure", existing.id, {"slug": slug, "idempotent": True}
        )
        return await _metadata_for(db, existing), False
    tenant = await identity_service.create_tenant(
        db, slug=slug, name=name, tenant_id=tenant_id
    )
    await _audit(db, actor, "platform.tenant.create", tenant.id, {"slug": slug})
    return await _metadata_for(db, tenant), True


async def set_tenant_status(
    db: AsyncSession, *, actor: Principal, tenant_id: str, status: str
) -> TenantMetadata | None:
    """Suspend or reactivate a tenant.

    Suspension takes effect on the tenant's next request: the accepted identity
    layer refuses a non-active tenant, so no session or cache has to expire.
    """
    _require(actor, PlatformCapability.TENANTS_ADMIN)
    if status not in TENANT_STATUSES:
        raise ValueError(f"Unsupported tenant status: {status!r}")
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        return None
    tenant.status = status
    await db.flush()
    await _audit(db, actor, "platform.tenant.status", tenant.id, {"status": status})
    return await _metadata_for(db, tenant)


async def provision_membership(
    db: AsyncSession,
    *,
    actor: Principal,
    tenant_id: str,
    subject: str,
    role: str,
    email: str | None = None,
    display_name: str | None = None,
    issuer: str | None = None,
) -> dict:
    """Provision a tenant's initial owner/admin membership. Idempotent per subject."""
    _require(actor, PlatformCapability.TENANTS_ADMIN)
    if role not in PROVISIONABLE_ROLES:
        raise ValueError(f"Only {PROVISIONABLE_ROLES} may be provisioned from the platform plane")
    if not subject.strip():
        raise ValueError("A subject is required")
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError("Unknown tenant")
    account = await identity_service.get_or_create_principal(
        db, subject=subject, issuer=issuer, email=email, display_name=display_name
    )
    membership = await identity_service.add_membership(
        db, tenant_id=tenant_id, principal_id=account.id, role=role
    )
    await _audit(
        db,
        actor,
        "platform.tenant.provision_membership",
        tenant_id,
        {"subject": subject, "role": role, "principal_id": account.id},
    )
    return {
        "tenant_id": tenant_id,
        "principal_id": account.id,
        "subject": subject,
        "role": membership.role,
    }
