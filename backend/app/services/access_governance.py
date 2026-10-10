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

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.governance import (
    DEFAULT_SOURCE_CLASSIFICATION,
    StewardScopeType,
    is_known_classification,
    is_known_steward_scope_type,
)
from app.core.identity import AuthorizationError, Principal
from app.core.permissions import Permission
from app.core.platform import (
    PlatformCapability,
    is_known_platform_capability,
    normalize_platform_capabilities,
)
from app.models import (
    AccessGroup,
    DataSource,
    DataSteward,
    Department,
    Document,
    GroupMembership,
    PlatformOperator,
    PrincipalAccount,
    SupportDelegation,
    Tenant,
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
    # Active, unexpired OpenJM support delegations for this tenant.
    support_scopes: frozenset[str] = frozenset()


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
        # A department only counts while BOTH the group and the department are
        # active: archiving the department removes it from resolution even when
        # an active group still points at it.
        department_ids = frozenset(
            dept
            for dept in (
                await db.execute(
                    select(AccessGroup.department_id)
                    .join(Department, Department.id == AccessGroup.department_id)
                    .where(
                        AccessGroup.tenant_id == tenant_id,
                        AccessGroup.id.in_(group_ids),
                        AccessGroup.status == "active",
                        AccessGroup.department_id.is_not(None),
                        Department.tenant_id == tenant_id,
                        Department.status == "active",
                    )
                )
            )
            .scalars()
            .all()
            if dept is not None
        )

    steward_scopes = await _effective_steward_scopes(
        db, principal_id=principal_id, tenant_id=tenant_id
    )

    return PrincipalAccess(
        department_ids=department_ids,
        group_ids=group_ids,
        steward_scopes=steward_scopes,
        support_scopes=await _active_support_scopes(
            db, principal_id=principal_id, tenant_id=tenant_id
        ),
    )


async def _active_support_scopes(
    db: AsyncSession, *, principal_id: str, tenant_id: str
) -> frozenset[str]:
    """Active, unexpired OpenJM support delegations for this principal+tenant."""
    moment = utcnow()
    rows = (
        await db.execute(
            select(SupportDelegation.scope, SupportDelegation.expires_at).where(
                SupportDelegation.tenant_id == tenant_id,
                SupportDelegation.principal_id == principal_id,
                SupportDelegation.status == "active",
            )
        )
    ).all()
    scopes: set[str] = set()
    for scope, expires_at in rows:
        expires = _as_utc(expires_at)
        if expires is not None and expires <= moment:
            continue
        scopes.add(str(scope))
    return frozenset(scopes)


async def _effective_steward_scopes(
    db: AsyncSession, *, principal_id: str, tenant_id: str
) -> frozenset[tuple[str, str]]:
    """Stewardship rows that are currently effective.

    A steward grant is preserved historically, but it only resolves while its
    scope is live. Archiving the governing department or group makes the grant
    ineffective on the next resolution; reactivating the scope makes the same
    preserved grant effective again, with no re-grant. A tenant-wide steward
    scope is governed by the active tenant membership that got us here.
    """
    rows = (
        await db.execute(
            select(DataSteward.scope_type, DataSteward.scope_id).where(
                DataSteward.tenant_id == tenant_id,
                DataSteward.principal_id == principal_id,
                DataSteward.status == "active",
            )
        )
    ).all()
    if not rows:
        return frozenset()

    wanted_departments = {
        str(scope_id)
        for scope_type, scope_id in rows
        if scope_type == StewardScopeType.DEPARTMENT.value
    }
    wanted_groups = {
        str(scope_id)
        for scope_type, scope_id in rows
        if scope_type == StewardScopeType.GROUP.value
    }

    active_department_ids: set[str] = set()
    if wanted_departments:
        active_department_ids = {
            str(row)
            for row in (
                await db.execute(
                    select(Department.id).where(
                        Department.tenant_id == tenant_id,
                        Department.id.in_(wanted_departments),
                        Department.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        }

    active_group_ids: set[str] = set()
    if wanted_groups:
        active_group_ids = {
            str(row)
            for row in (
                await db.execute(
                    select(AccessGroup.id).where(
                        AccessGroup.tenant_id == tenant_id,
                        AccessGroup.id.in_(wanted_groups),
                        AccessGroup.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        }

    effective: set[tuple[str, str]] = set()
    for scope_type, scope_id in rows:
        scope_type, scope_id = str(scope_type), str(scope_id)
        if scope_type == StewardScopeType.TENANT.value:
            if scope_id == tenant_id:
                effective.add((scope_type, scope_id))
        elif scope_type == StewardScopeType.DEPARTMENT.value:
            if scope_id in active_department_ids:
                effective.add((scope_type, scope_id))
        elif scope_type == StewardScopeType.GROUP.value:
            if scope_id in active_group_ids:
                effective.add((scope_type, scope_id))
    return frozenset(effective)


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
        department = await get_department(db, principal=principal, department_id=scope_id)
        if department is None:
            raise ValueError("Department does not belong to the active tenant")
        if department.status != "active":
            raise ValueError("Department is not active")
        return
    group = await get_group(db, principal=principal, group_id=scope_id)
    if group is None:
        raise ValueError("Group does not belong to the active tenant")
    if group.status != "active":
        raise ValueError("Group is not active")


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


LAST_TRUST_ROOT_MESSAGE = (
    "The final active operators:admin grant cannot be revoked; another active, "
    "unexpired operators:admin grant must exist first"
)


async def _assert_trust_root_survives(
    db: AsyncSession, *, rows_to_revoke: list[PlatformOperator]
) -> None:
    """Refuse a revocation that would remove the final effective trust root.

    ``operators:admin`` is the only capability that can grant or revoke platform
    operators, and bootstrap is deliberately one time and never a backdoor, so
    removing the last effective grant would lock the platform out permanently. A
    grant that is already revoked, or whose expiry has passed, is not authority
    and therefore does not satisfy this guard.
    """
    if not any(
        row.capability == PlatformCapability.OPERATORS_ADMIN.value
        for row in rows_to_revoke
    ):
        return

    revoking = {row.id for row in rows_to_revoke}
    moment = utcnow()
    rows = (
        await db.execute(
            select(PlatformOperator.id, PlatformOperator.expires_at).where(
                PlatformOperator.capability == PlatformCapability.OPERATORS_ADMIN.value,
                PlatformOperator.status == "active",
            )
        )
    ).all()
    for row_id, expires_at in rows:
        if row_id in revoking:
            continue
        expires = _as_utc(expires_at)
        if expires is not None and expires <= moment:
            continue
        return
    raise AuthorizationError(LAST_TRUST_ROOT_MESSAGE, code="last_platform_trust_root")


async def revoke_platform_operator(
    db: AsyncSession, *, principal_id: str, capability=None
) -> int:
    """Revoke one capability, or every capability when none is named.

    The final effective ``operators:admin`` trust root is protected: see
    :func:`_assert_trust_root_survives`.
    """
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
    await _assert_trust_root_survives(db, rows_to_revoke=list(rows))
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


# ---------------------------------------------------------------------------
# Document policy (BV1-B steward / admin mutation)
# ---------------------------------------------------------------------------


async def set_document_policy(
    db: AsyncSession,
    *,
    principal: Principal,
    document_id: str,
    classification: str,
    department_id: str | None = None,
    tenant_visible: bool | None = None,
    allowed_group_ids: list[str] | None = None,
) -> Document | None:
    """Set a Knowledge document's classification and policy.

    Authorized only for a tenant admin or a data steward whose scope covers the
    document (tenant-wide, the document's own department, or the department
    being assigned). Returns None for a document outside the actor's tenant, so
    a foreign document is never revealed and never mutated. Every change is
    audited.
    """
    if not is_known_classification(classification):
        raise ValueError(f"Unsupported classification: {classification!r}")

    document = (
        await db.execute(
            select(Document).where(
                Document.tenant_id == principal.tenant_id,
                Document.id == document_id,
            )
        )
    ).scalar_one_or_none()
    if document is None:
        return None

    if not principal.has(Permission.TENANT_ADMIN):
        covers = (
            StewardScopeType.TENANT.value,
            principal.tenant_id,
        ) in principal.steward_scopes
        if document.department_id and (
            StewardScopeType.DEPARTMENT.value,
            document.department_id,
        ) in principal.steward_scopes:
            covers = True
        if department_id and (
            StewardScopeType.DEPARTMENT.value,
            department_id,
        ) in principal.steward_scopes:
            covers = True
        if not covers:
            raise AuthorizationError(
                "Principal lacks tenant-admin or steward authority over this document",
                code="missing_scope",
            )

    if department_id is not None:
        if await get_department(db, principal=principal, department_id=department_id) is None:
            raise ValueError("Department does not belong to the active tenant")

    if allowed_group_ids:
        wanted = {str(group) for group in allowed_group_ids}
        found = set(
            (
                await db.execute(
                    select(AccessGroup.id).where(
                        AccessGroup.tenant_id == principal.tenant_id,
                        AccessGroup.id.in_(wanted),
                    )
                )
            )
            .scalars()
            .all()
        )
        if found != wanted:
            raise ValueError("Allowed group does not belong to the active tenant")

    audit_metadata: dict = {"classification": classification}
    document.classification = classification
    if department_id is not None:
        document.department_id = department_id
        audit_metadata["department_id"] = department_id
    if tenant_visible is not None:
        document.tenant_visible = bool(tenant_visible)
        audit_metadata["tenant_visible"] = bool(tenant_visible)
    if allowed_group_ids is not None:
        groups = sorted({str(group) for group in allowed_group_ids})
        document.allowed_group_ids_json = json.dumps(groups)
        audit_metadata["allowed_group_ids"] = groups
    await db.flush()

    await _audit(
        db,
        principal=principal,
        action="governance.document.policy",
        resource_type="document",
        resource_id=document.id,
        metadata=audit_metadata,
    )
    return document


# ---------------------------------------------------------------------------
# BV3-A: OpenJM support delegations
# ---------------------------------------------------------------------------

SUPPORT_SCOPES: tuple[str, ...] = ("metadata", "content")


def _require_operator(actor: Principal) -> None:
    actor.require_platform(PlatformCapability.OPERATORS_ADMIN)


async def list_support_delegations(
    db: AsyncSession, *, tenant_id: str | None = None, principal_id: str | None = None
) -> list[SupportDelegation]:
    query = select(SupportDelegation)
    if tenant_id is not None:
        query = query.where(SupportDelegation.tenant_id == tenant_id)
    if principal_id is not None:
        query = query.where(SupportDelegation.principal_id == principal_id)
    return list((await db.execute(query.order_by(SupportDelegation.created_at))).scalars().all())


async def grant_support_delegation(
    db: AsyncSession,
    *,
    actor: Principal,
    tenant_id: str,
    principal_id: str,
    scope: str,
    expires_at: datetime | None = None,
    classification_ceiling: str = DEFAULT_SOURCE_CLASSIFICATION,
    allowed_group_ids: list[str] | None = None,
    department_id: str | None = None,
) -> SupportDelegation:
    """Delegate tenant-scoped OpenJM support authority to one operator account.

    Only an operator with ``OPERATORS_ADMIN`` may delegate, and ``content``
    support additionally requires the actor to hold ``CONTENT_SUPPORT``: an
    authority cannot be delegated by someone who does not hold it. The delegation
    is scoped to one tenant, auditable, revocable and optionally time-bounded.

    A ``content`` delegation is *bounded* by ``classification_ceiling`` (the most
    classified source it may reach), ``allowed_group_ids`` and ``department_id``.
    A support read against it is permitted only when a document satisfies both the
    delegation's bounds and the tenant's classification/source policy, so a
    delegation never replaces or widens classification or source authorization.
    """
    _require_operator(actor)
    if scope not in SUPPORT_SCOPES:
        raise ValueError(f"Unsupported support scope: {scope!r}")
    if scope == "content" and not actor.has_platform(PlatformCapability.CONTENT_SUPPORT):
        raise AuthorizationError(
            "Content support cannot be delegated without holding that capability",
            code="missing_platform_capability",
        )
    if expires_at is not None:
        expiry = _as_utc(expires_at)
        if expiry is not None and expiry <= utcnow():
            raise ValueError("Support delegation expiry must be in the future")

    if not is_known_classification(classification_ceiling):
        raise ValueError(
            f"Unsupported classification ceiling: {classification_ceiling!r}"
        )

    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError("Unknown tenant")
    account = await db.get(PrincipalAccount, principal_id)
    if account is None:
        raise ValueError("Unknown principal account")

    if department_id is not None:
        department = (
            await db.execute(
                select(Department.id).where(
                    Department.tenant_id == tenant_id,
                    Department.id == department_id,
                )
            )
        ).scalar_one_or_none()
        if department is None:
            raise ValueError("Department does not belong to the target tenant")

    groups = sorted({str(group) for group in (allowed_group_ids or []) if str(group)})
    if groups:
        found = set(
            (
                await db.execute(
                    select(AccessGroup.id).where(
                        AccessGroup.tenant_id == tenant_id,
                        AccessGroup.id.in_(groups),
                    )
                )
            )
            .scalars()
            .all()
        )
        if found != set(groups):
            raise ValueError("Allowed group does not belong to the target tenant")

    existing = (
        await db.execute(
            select(SupportDelegation).where(
                SupportDelegation.tenant_id == tenant_id,
                SupportDelegation.principal_id == principal_id,
                SupportDelegation.scope == scope,
            )
        )
    ).scalar_one_or_none()
    bounded = {
        "classification_ceiling": classification_ceiling,
        "allowed_group_ids_json": json.dumps(groups),
        "department_id": department_id,
    }
    if existing is not None:
        existing.status = "active"
        existing.revoked_at = None
        existing.expires_at = expires_at
        existing.granted_by = actor.principal_id
        for field, value in bounded.items():
            setattr(existing, field, value)
        await db.flush()
        delegation = existing
    else:
        delegation = SupportDelegation(
            tenant_id=tenant_id,
            principal_id=principal_id,
            scope=scope,
            status="active",
            granted_by=actor.principal_id,
            expires_at=expires_at,
            **bounded,
        )
        db.add(delegation)
        await db.flush()

    await _audit(
        db,
        principal=actor,
        action="platform.support.grant",
        resource_type="support_delegation",
        resource_id=f"{tenant_id}:{principal_id}:{scope}",
        metadata={
            "scope": scope,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "classification_ceiling": classification_ceiling,
            "allowed_group_ids": groups,
            "department_id": department_id,
        },
    )
    return delegation


async def revoke_support_delegation(
    db: AsyncSession, *, actor: Principal, tenant_id: str, principal_id: str, scope: str
) -> int:
    _require_operator(actor)
    if scope not in SUPPORT_SCOPES:
        raise ValueError(f"Unsupported support scope: {scope!r}")
    delegation = (
        await db.execute(
            select(SupportDelegation).where(
                SupportDelegation.tenant_id == tenant_id,
                SupportDelegation.principal_id == principal_id,
                SupportDelegation.scope == scope,
                SupportDelegation.status == "active",
            )
        )
    ).scalar_one_or_none()
    if delegation is None:
        return 0
    delegation.status = "revoked"
    delegation.revoked_at = utcnow()
    await db.flush()
    await _audit(
        db,
        principal=actor,
        action="platform.support.revoke",
        resource_type="support_delegation",
        resource_id=f"{tenant_id}:{principal_id}:{scope}",
        metadata={"scope": scope},
    )
    return 1


async def set_data_source_policy(
    db: AsyncSession,
    *,
    principal: Principal,
    source_id: str,
    classification: str,
    department_id: str | None = None,
    tenant_visible: bool | None = None,
    allowed_group_ids: list[str] | None = None,
) -> DataSource | None:
    """Set a structured Data source's classification and policy.

    Same authorization and audit discipline as :func:`set_document_policy`. A
    source outside the actor's tenant is neither revealed nor mutated.
    """
    if not is_known_classification(classification):
        raise ValueError(f"Unsupported classification: {classification!r}")

    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.tenant_id == principal.tenant_id,
                DataSource.id == source_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        return None

    if not principal.has(Permission.TENANT_ADMIN):
        covers = (
            StewardScopeType.TENANT.value,
            principal.tenant_id,
        ) in principal.steward_scopes
        if source.department_id and (
            StewardScopeType.DEPARTMENT.value,
            source.department_id,
        ) in principal.steward_scopes:
            covers = True
        if department_id and (
            StewardScopeType.DEPARTMENT.value,
            department_id,
        ) in principal.steward_scopes:
            covers = True
        if not covers:
            raise AuthorizationError(
                "Principal lacks tenant-admin or steward authority over this source",
                code="missing_scope",
            )

    if department_id is not None:
        if await get_department(db, principal=principal, department_id=department_id) is None:
            raise ValueError("Department does not belong to the active tenant")

    if allowed_group_ids:
        wanted = {str(group) for group in allowed_group_ids}
        found = set(
            (
                await db.execute(
                    select(AccessGroup.id).where(
                        AccessGroup.tenant_id == principal.tenant_id,
                        AccessGroup.id.in_(wanted),
                    )
                )
            )
            .scalars()
            .all()
        )
        if found != wanted:
            raise ValueError("Allowed group does not belong to the active tenant")

    audit_metadata: dict = {"classification": classification}
    source.classification = classification
    if department_id is not None:
        source.department_id = department_id
        audit_metadata["department_id"] = department_id
    if tenant_visible is not None:
        source.tenant_visible = bool(tenant_visible)
        audit_metadata["tenant_visible"] = bool(tenant_visible)
    if allowed_group_ids is not None:
        groups = sorted({str(group) for group in allowed_group_ids})
        source.allowed_group_ids_json = json.dumps(groups)
        audit_metadata["allowed_group_ids"] = groups
    await db.flush()

    await _audit(
        db,
        principal=principal,
        action="governance.data_source.policy",
        resource_type="data_source",
        resource_id=source.id,
        metadata=audit_metadata,
    )
    return source
