"""Authorization and information-governance foundation (BV1-A).

This module owns the client-side governance model the rest of the Hardening &
Business Value Realization phase builds on:

* departments and groups inside one tenant;
* group membership, by principal id (never by display name or email string);
* delegated data-steward capability, scoped to a tenant, department or group;
* explicit platform-operator capabilities, on a separate axis from tenant roles.

Design rules, matching the accepted VS5 identity layer:

* **Tenant-scoped, fail closed.** Every read and write is narrowed by the
  actor's active tenant. A row that belongs to another tenant is never returned,
  mutated or revealed; a not-found resource is reported as absent, not as a
  foreign resource.
* **Re-read per request, no cache.** :func:`load_principal_access` and
  :func:`platform_capabilities_for` read current rows, so a revocation takes
  effect on the next authorized operation with nothing to expire.
* **Audited.** Every mutation writes an :class:`~app.models.AuditRecord` in the
  actor's tenant.
* **Explicit input validation.** Unknown scope types, unknown platform
  capabilities reverting a grant, members who are not active tenant members, and
  duplicate slugs are hard errors, never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.governance import StewardScopeType, is_known_steward_scope_type
from app.core.identity import AuthorizationError, Principal
from app.core.permissions import Permission
from app.core.platform import (
    PlatformCapability,
    is_known_platform_capability,
    normalize_platform_capabilities,
)
from app.models import (
    AccessGroup,
    DataSteward,
    Department,
    GroupMembership,
    PlatformOperator,
    TenantMembership,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a stored datetime (SQLite returns naive UTC values)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _audit(
    db: AsyncSession,
    *,
    principal: Principal | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    # Imported lazily so this module can be imported by ``app.services.identity``
    # without a module-level import cycle.
    from app.services.identity import record_audit

    await record_audit(
        db,
        principal=principal,
        action=action,
        decision="allow",
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
    )


def _require_tenant_admin(principal: Principal) -> None:
    if not principal.has(Permission.TENANT_ADMIN):
        raise AuthorizationError(
            "Tenant administration permission is required for this operation",
            code="missing_permission",
        )


# ---------------------------------------------------------------------------
# Resolution (consumed by the identity layer on every request)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrincipalAccess:
    """A principal's effective data-side scopes inside one tenant."""

    department_ids: frozenset[str] = frozenset()
    group_ids: frozenset[str] = frozenset()
    steward_scopes: frozenset[tuple[str, str]] = frozenset()


async def load_principal_access(
    db: AsyncSession, *, principal_id: str, tenant_id: str
) -> PrincipalAccess:
    """Resolve the principal's active groups, their departments, and stewardships.

    Departments are derived from the principal's active groups: a principal is
    "in" a department when they hold an active membership of an active group
    that belongs to that department.
    """
    group_ids = frozenset(
        (
            await db.execute(
                select(GroupMembership.group_id)
                .join(AccessGroup, AccessGroup.id == GroupMembership.group_id)
                .where(
                    GroupMembership.tenant_id == tenant_id,
                    GroupMembership.principal_id == principal_id,
                    GroupMembership.status == "active",
                    AccessGroup.tenant_id == tenant_id,
                    AccessGroup.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )

    department_ids: frozenset[str] = frozenset()
    if group_ids:
        department_ids = frozenset(
            dept
            for dept in (
                await db.execute(
                    select(AccessGroup.department_id).where(
                        AccessGroup.tenant_id == tenant_id,
                        AccessGroup.id.in_(group_ids),
                        AccessGroup.status == "active",
                        AccessGroup.department_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
            if dept is not None
        )

    steward_scopes = frozenset(
        (str(row[0]), str(row[1]))
        for row in (
            await db.execute(
                select(DataSteward.scope_type, DataSteward.scope_id).where(
                    DataSteward.tenant_id == tenant_id,
                    DataSteward.principal_id == principal_id,
                    DataSteward.status == "active",
                )
            )
        ).all()
    )

    return PrincipalAccess(
        department_ids=department_ids, group_ids=group_ids, steward_scopes=steward_scopes
    )


async def platform_capabilities_for(
    db: AsyncSession, *, principal_id: str, now: datetime | None = None
) -> frozenset[PlatformCapability]:
    """Resolve the principal's currently effective platform capabilities.

    An expired or revoked grant contributes nothing. Unknown capability strings
    stored in the row (for example from a future release) are ignored rather than
    trusted.
    """
    moment = now or utcnow()
    rows = (
        await db.execute(
            select(PlatformOperator.capability, PlatformOperator.expires_at).where(
                PlatformOperator.principal_id == principal_id,
                PlatformOperator.status == "active",
            )
        )
    ).all()

    capabilities: set[PlatformCapability] = set()
    for capability, expires_at in rows:
        if not is_known_platform_capability(capability):
            continue
        expires = _as_utc(expires_at)
        if expires is not None and expires <= moment:
            continue
        capabilities.add(PlatformCapability(capability))
    return frozenset(capabilities)


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


async def get_department(
    db: AsyncSession, *, principal: Principal, department_id: str
) -> Department | None:
    return (
        await db.execute(
            select(Department).where(
                Department.tenant_id == principal.tenant_id,
                Department.id == department_id,
            )
        )
    ).scalar_one_or_none()


async def list_departments(db: AsyncSession, *, principal: Principal) -> list[Department]:
    return list(
        (
            await db.execute(
                select(Department)
                .where(Department.tenant_id == principal.tenant_id)
                .order_by(Department.slug)
            )
        )
        .scalars()
        .all()
    )


async def create_department(
    db: AsyncSession, *, principal: Principal, slug: str, name: str
) -> Department:
    _require_tenant_admin(principal)
    existing = (
        await db.execute(
            select(Department).where(
                Department.tenant_id == principal.tenant_id, Department.slug == slug
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"Department slug already exists in this tenant: {slug!r}")

    department = Department(
        tenant_id=principal.tenant_id, slug=slug, name=name, status="active"
    )
    db.add(department)
    await db.flush()
    await _audit(
        db,
        principal=principal,
        action="governance.department.create",
        resource_type="department",
        resource_id=department.id,
        metadata={"slug": slug},
    )
    return department


async def set_department_status(
    db: AsyncSession, *, principal: Principal, department_id: str, status: str
) -> Department | None:
    _require_tenant_admin(principal)
    if status not in ("active", "archived"):
        raise ValueError(f"Unsupported department status: {status!r}")
    department = await get_department(db, principal=principal, department_id=department_id)
    if department is None:
        return None
    department.status = status
    await db.flush()
    await _audit(
        db,
        principal=principal,
        action="governance.department.status",
        resource_type="department",
        resource_id=department.id,
        metadata={"status": status},
    )
    return department


# ---------------------------------------------------------------------------
# Groups and membership
# ---------------------------------------------------------------------------


async def get_group(
    db: AsyncSession, *, principal: Principal, group_id: str
) -> AccessGroup | None:
    return (
        await db.execute(
            select(AccessGroup).where(
                AccessGroup.tenant_id == principal.tenant_id,
                AccessGroup.id == group_id,
            )
        )
    ).scalar_one_or_none()


async def list_groups(db: AsyncSession, *, principal: Principal) -> list[AccessGroup]:
    return list(
        (
            await db.execute(
                select(AccessGroup)
                .where(AccessGroup.tenant_id == principal.tenant_id)
                .order_by(AccessGroup.slug)
            )
        )
        .scalars()
        .all()
    )


async def create_group(
    db: AsyncSession,
    *,
    principal: Principal,
    slug: str,
    name: str,
    department_id: str | None = None,
) -> AccessGroup:
    _require_tenant_admin(principal)
    if department_id is not None:
        department = await get_department(
            db, principal=principal, department_id=department_id
        )
        if department is None:
            raise ValueError("Department does not belong to the active tenant")

    existing = (
        await db.execute(
            select(AccessGroup).where(
                AccessGroup.tenant_id == principal.tenant_id, AccessGroup.slug == slug
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"Group slug already exists in this tenant: {slug!r}")

    group = AccessGroup(
        tenant_id=principal.tenant_id,
        department_id=department_id,
        slug=slug,
        name=name,
        status="active",
    )
    db.add(group)
    await db.flush()
    await _audit(
        db,
        principal=principal,
        action="governance.group.create",
        resource_type="group",
        resource_id=group.id,
        metadata={"slug": slug, "department_id": department_id},
    )
    return group


async def set_group_status(
    db: AsyncSession, *, principal: Principal, group_id: str, status: str
) -> AccessGroup | None:
    _require_tenant_admin(principal)
    if status not in ("active", "archived"):
        raise ValueError(f"Unsupported group status: {status!r}")
    group = await get_group(db, principal=principal, group_id=group_id)
    if group is None:
        return None
    group.status = status
    await db.flush()
    await _audit(
        db,
        principal=principal,
        action="governance.group.status",
        resource_type="group",
        resource_id=group.id,
        metadata={"status": status},
    )
    return group


async def list_group_members(
    db: AsyncSession, *, principal: Principal, group_id: str
) -> list[GroupMembership]:
    group = await get_group(db, principal=principal, group_id=group_id)
    if group is None:
        return []
    return list(
        (
            await db.execute(
                select(GroupMembership).where(
                    GroupMembership.tenant_id == principal.tenant_id,
                    GroupMembership.group_id == group_id,
                    GroupMembership.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )


async def _active_tenant_member_exists(
    db: AsyncSession, *, tenant_id: str, principal_id: str
) -> bool:
    membership = (
        await db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.principal_id == principal_id,
                TenantMembership.status == "active",
            )
        )
    ).scalar_one_or_none()
    return membership is not None


async def add_group_member(
    db: AsyncSession, *, principal: Principal, group_id: str, member_principal_id: str
) -> GroupMembership | None:
    """Add one tenant member to one group. Returns None for a foreign group.

    The member must already hold an active membership in the actor's tenant, so a
    foreign principal can never be pulled into a group by id.
    """
    _require_tenant_admin(principal)
    group = await get_group(db, principal=principal, group_id=group_id)
    if group is None:
        return None
    if not await _active_tenant_member_exists(
        db, tenant_id=principal.tenant_id, principal_id=member_principal_id
    ):
        raise ValueError("Principal is not an active member of this tenant")

    existing = (
        await db.execute(
            select(GroupMembership).where(
                GroupMembership.tenant_id == principal.tenant_id,
                GroupMembership.group_id == group_id,
                GroupMembership.principal_id == member_principal_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status == "active":
            return existing
        existing.status = "active"
        existing.revoked_at = None
        await db.flush()
        membership = existing
    else:
        membership = GroupMembership(
            tenant_id=principal.tenant_id,
            group_id=group_id,
            principal_id=member_principal_id,
            status="active",
        )
        db.add(membership)
        await db.flush()

    await _audit(
        db,
        principal=principal,
        action="governance.group.member.add",
        resource_type="group",
        resource_id=group_id,
        metadata={"member_principal_id": member_principal_id},
    )
    return membership


async def remove_group_member(
    db: AsyncSession, *, principal: Principal, group_id: str, member_principal_id: str
) -> int:
    """Revoke a group membership. Returns the number of rows changed.

    Narrowed to an active row so a repeated removal is a no-op (returns 0) rather
    than an error.
    """
    _require_tenant_admin(principal)
    group = await get_group(db, principal=principal, group_id=group_id)
    if group is None:
        return 0
    membership = (
        await db.execute(
            select(GroupMembership).where(
                GroupMembership.tenant_id == principal.tenant_id,
                GroupMembership.group_id == group_id,
                GroupMembership.principal_id == member_principal_id,
                GroupMembership.status == "active",
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        return 0
    membership.status = "revoked"
    membership.revoked_at = utcnow()
    await db.flush()
    await _audit(
        db,
        principal=principal,
        action="governance.group.member.remove",
        resource_type="group",
        resource_id=group_id,
        metadata={"member_principal_id": member_principal_id},
    )
    return 1


# ---------------------------------------------------------------------------
# Data stewards
# ---------------------------------------------------------------------------


async def _validate_steward_scope(
    db: AsyncSession, *, principal: Principal, scope_type: str, scope_id: str
) -> None:
    if not is_known_steward_scope_type(scope_type):
        raise ValueError(f"Unsupported steward scope type: {scope_type!r}")
    if scope_type == StewardScopeType.TENANT.value:
        if scope_id != principal.tenant_id:
            raise ValueError("Tenant-scoped stewardship must reference the active tenant")
        return
    if scope_type == StewardScopeType.DEPARTMENT.value:
        if await get_department(db, principal=principal, department_id=scope_id) is None:
            raise ValueError("Department does not belong to the active tenant")
        return
    if await get_group(db, principal=principal, group_id=scope_id) is None:
        raise ValueError("Group does not belong to the active tenant")


async def list_stewards(db: AsyncSession, *, principal: Principal) -> list[DataSteward]:
    return list(
        (
            await db.execute(
                select(DataSteward).where(DataSteward.tenant_id == principal.tenant_id)
            )
        )
        .scalars()
        .all()
    )


async def grant_steward(
    db: AsyncSession,
    *,
    principal: Principal,
    steward_principal_id: str,
    scope_type: str,
    scope_id: str,
) -> DataSteward:
    _require_tenant_admin(principal)
    await _validate_steward_scope(
        db, principal=principal, scope_type=scope_type, scope_id=scope_id
    )
    if not await _active_tenant_member_exists(
        db, tenant_id=principal.tenant_id, principal_id=steward_principal_id
    ):
        raise ValueError("Principal is not an active member of this tenant")

    existing = (
        await db.execute(
            select(DataSteward).where(
                DataSteward.tenant_id == principal.tenant_id,
                DataSteward.principal_id == steward_principal_id,
                DataSteward.scope_type == scope_type,
                DataSteward.scope_id == scope_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.status = "active"
        existing.revoked_at = None
        existing.granted_by = principal.principal_id
        await db.flush()
        steward = existing
    else:
        steward = DataSteward(
            tenant_id=principal.tenant_id,
            principal_id=steward_principal_id,
            scope_type=scope_type,
            scope_id=scope_id,
            status="active",
            granted_by=principal.principal_id,
        )
        db.add(steward)
        await db.flush()

    await _audit(
        db,
        principal=principal,
        action="governance.steward.grant",
        resource_type="steward_scope",
        resource_id=f"{scope_type}:{scope_id}",
        metadata={"steward_principal_id": steward_principal_id},
    )
    return steward


async def revoke_steward(
    db: AsyncSession,
    *,
    principal: Principal,
    steward_principal_id: str,
    scope_type: str,
    scope_id: str,
) -> int:
    _require_tenant_admin(principal)
    steward = (
        await db.execute(
            select(DataSteward).where(
                DataSteward.tenant_id == principal.tenant_id,
                DataSteward.principal_id == steward_principal_id,
                DataSteward.scope_type == scope_type,
                DataSteward.scope_id == scope_id,
                DataSteward.status == "active",
            )
        )
    ).scalar_one_or_none()
    if steward is None:
        return 0
    steward.status = "revoked"
    steward.revoked_at = utcnow()
    await db.flush()
    await _audit(
        db,
        principal=principal,
        action="governance.steward.revoke",
        resource_type="steward_scope",
        resource_id=f"{scope_type}:{scope_id}",
        metadata={"steward_principal_id": steward_principal_id},
    )
    return 1


# ---------------------------------------------------------------------------
# Platform operators
# ---------------------------------------------------------------------------


async def list_platform_operators(
    db: AsyncSession, *, principal_id: str | None = None
) -> list[PlatformOperator]:
    query = select(PlatformOperator)
    if principal_id is not None:
        query = query.where(PlatformOperator.principal_id == principal_id)
    return list((await db.execute(query.order_by(PlatformOperator.created_at))).scalars().all())


async def grant_platform_operator(
    db: AsyncSession,
    *,
    principal_id: str,
    capabilities,
    granted_by: str | None = None,
    expires_at: datetime | None = None,
) -> list[PlatformOperator]:
    """Grant explicit platform capabilities to one principal account.

    This is the low-level provisioning primitive used by operator tooling and the
    BV3 control plane; the route layer guards it with the ``OPERATORS_ADMIN``
    capability. Unknown capability strings are rejected rather than dropped.
    """
    normalized = normalize_platform_capabilities(capabilities)
    if not normalized:
        raise ValueError("At least one platform capability is required")

    grants: list[PlatformOperator] = []
    for capability in sorted(normalized, key=lambda c: c.value):
        existing = (
            await db.execute(
                select(PlatformOperator).where(
                    PlatformOperator.principal_id == principal_id,
                    PlatformOperator.capability == capability.value,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.status = "active"
            existing.revoked_at = None
            existing.expires_at = expires_at
            existing.granted_by = granted_by
            await db.flush()
            grants.append(existing)
        else:
            grant = PlatformOperator(
                principal_id=principal_id,
                capability=capability.value,
                status="active",
                granted_by=granted_by,
                expires_at=expires_at,
            )
            db.add(grant)
            await db.flush()
            grants.append(grant)

    await _audit(
        db,
        principal=None,
        action="platform.operator.grant",
        resource_type="platform_operator",
        resource_id=principal_id,
        metadata={
            "capabilities": sorted(capability.value for capability in normalized),
            "granted_by": granted_by,
        },
    )
    return grants


async def revoke_platform_operator(
    db: AsyncSession, *, principal_id: str, capability=None
) -> int:
    """Revoke one capability, or every capability when none is named."""
    query = select(PlatformOperator).where(
        PlatformOperator.principal_id == principal_id,
        PlatformOperator.status == "active",
    )
    if capability is not None:
        value = capability.value if isinstance(capability, PlatformCapability) else str(capability)
        if not is_known_platform_capability(value):
            raise ValueError(f"Unknown platform capability: {value!r}")
        query = query.where(PlatformOperator.capability == value)

    rows = (await db.execute(query)).scalars().all()
    for row in rows:
        row.status = "revoked"
        row.revoked_at = utcnow()
    await db.flush()
    if rows:
        await _audit(
            db,
            principal=None,
            action="platform.operator.revoke",
            resource_type="platform_operator",
            resource_id=principal_id,
            metadata={
                "capabilities": sorted(row.capability for row in rows),
            },
        )
    return len(rows)
