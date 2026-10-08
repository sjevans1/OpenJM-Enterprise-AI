"""Tenant-facing client administration (BV3-B).

The business-facing administration plane. It wraps the governance primitives
already accepted in BV1-A/BV1-C (departments, groups, group membership, steward
grants, source/document policy) and adds the membership lifecycle a client
administrator needs: invite, role change, revoke/reactivate, session revoke.

Authority is the tenant permission model, not a platform capability: every
mutation here requires ``tenant:admin`` (admin or owner). Read paths are
tenant-scoped by construction, so no route can enumerate another tenant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import AuthorizationError, Principal
from app.core.permissions import ROLE_OWNER, Permission, is_known_role
from app.models import AuthSession, PrincipalAccount, Tenant, TenantMembership
from app.services import identity as identity_service
from app.services import usage_aggregation
from app.services import usage_metering
from app.services.access_governance import load_principal_access
from app.services.identity import utcnow

# Whitelisted, non-secret tenant preferences. Anything else is refused, so this
# surface can never become a back door for arbitrary tenant configuration.
PREFERENCE_SPEC: dict[str, type] = {
    "default_report_timezone": str,
    "notify_on_run_failure": bool,
    "support_contact_email": str,
}


@dataclass(frozen=True)
class MemberRecord:
    principal_id: str
    subject: str
    role: str
    status: str
    email: str | None
    display_name: str | None
    department_ids: tuple[str, ...]
    group_ids: tuple[str, ...]


def _require_tenant_admin(principal: Principal) -> None:
    if not principal.has(Permission.TENANT_ADMIN):
        raise AuthorizationError(
            "Principal lacks tenant administration authority",
            code="missing_tenant_admin",
        )


async def _audit(
    db: AsyncSession,
    principal: Principal,
    action: str,
    resource_id: str | None,
    metadata: dict | None = None,
) -> None:
    await identity_service.record_audit(
        db,
        principal=principal,
        action=action,
        decision="allow",
        resource_type="membership",
        resource_id=resource_id,
        metadata=metadata,
    )


async def _member_record(
    db: AsyncSession, *, tenant_id: str, membership: TenantMembership
) -> MemberRecord:
    account = await db.get(PrincipalAccount, membership.principal_id)
    access = await load_principal_access(
        db, principal_id=membership.principal_id, tenant_id=tenant_id
    )
    return MemberRecord(
        principal_id=membership.principal_id,
        subject=account.subject if account else "",
        role=membership.role,
        status=membership.status,
        email=account.email if account else None,
        display_name=account.display_name if account else None,
        department_ids=tuple(sorted(access.department_ids)),
        group_ids=tuple(sorted(access.group_ids)),
    )


async def _active_owners(db: AsyncSession, *, tenant_id: str) -> list[TenantMembership]:
    return list(
        (
            await db.execute(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.role == ROLE_OWNER,
                    TenantMembership.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )


async def list_members(db: AsyncSession, *, principal: Principal) -> list[MemberRecord]:
    _require_tenant_admin(principal)
    rows = (
        await db.execute(
            select(TenantMembership)
            .where(TenantMembership.tenant_id == principal.tenant_id)
            .order_by(TenantMembership.created_at)
        )
    ).scalars().all()
    return [
        await _member_record(db, tenant_id=principal.tenant_id, membership=row)
        for row in rows
    ]


async def provision_member(
    db: AsyncSession,
    *,
    principal: Principal,
    subject: str,
    role: str,
    email: str | None = None,
    display_name: str | None = None,
    issuer: str | None = None,
) -> MemberRecord:
    """Invite/provision a tenant membership. Idempotent per subject.

    Granting ``owner`` requires the actor to be an owner: a client admin cannot
    promote a member (or themselves) above their own authority.
    """
    _require_tenant_admin(principal)
    if not is_known_role(role):
        raise ValueError(f"Unknown tenant role: {role!r}")
    if role == ROLE_OWNER and principal.role != ROLE_OWNER:
        raise AuthorizationError(
            "Only a tenant owner may grant the owner role",
            code="missing_tenant_owner",
        )
    if not subject.strip():
        raise ValueError("A subject is required")
    account = await identity_service.get_or_create_principal(
        db, subject=subject, issuer=issuer, email=email, display_name=display_name
    )
    membership = await identity_service.add_membership(
        db, tenant_id=principal.tenant_id, principal_id=account.id, role=role
    )
    await _audit(
        db,
        principal,
        "client_admin.member.provision",
        account.id,
        {"subject": subject, "role": role},
    )
    return await _member_record(
        db, tenant_id=principal.tenant_id, membership=membership
    )


async def change_role(
    db: AsyncSession, *, principal: Principal, member_principal_id: str, role: str
) -> MemberRecord | None:
    _require_tenant_admin(principal)
    if not is_known_role(role):
        raise ValueError(f"Unknown tenant role: {role!r}")
    if member_principal_id == principal.principal_id:
        raise AuthorizationError(
            "A principal cannot change their own role", code="self_mutation"
        )
    membership = await _membership(db, tenant_id=principal.tenant_id, principal_id=member_principal_id)
    if membership is None:
        return None
    if role == ROLE_OWNER and principal.role != ROLE_OWNER:
        raise AuthorizationError(
            "Only a tenant owner may grant the owner role", code="missing_tenant_owner"
        )
    if membership.role == ROLE_OWNER and role != ROLE_OWNER:
        owners = await _active_owners(db, tenant_id=principal.tenant_id)
        if len(owners) <= 1:
            raise ValueError("The last active owner cannot be demoted")
    updated = await identity_service.set_membership_role(
        db, tenant_id=principal.tenant_id, principal_id=member_principal_id, role=role
    )
    if updated is None:
        return None
    await _audit(
        db, principal, "client_admin.member.role", member_principal_id, {"role": role}
    )
    return await _member_record(db, tenant_id=principal.tenant_id, membership=updated)


async def _membership(
    db: AsyncSession, *, tenant_id: str, principal_id: str
) -> TenantMembership | None:
    return (
        await db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.principal_id == principal_id,
            )
        )
    ).scalar_one_or_none()


async def set_membership_status(
    db: AsyncSession, *, principal: Principal, member_principal_id: str, status: str
) -> MemberRecord | None:
    """Revoke or reactivate a membership. Effective on the member's next request."""
    _require_tenant_admin(principal)
    if status not in ("active", "revoked"):
        raise ValueError(f"Unsupported membership status: {status!r}")
    if member_principal_id == principal.principal_id:
        raise AuthorizationError(
            "A principal cannot revoke their own membership", code="self_mutation"
        )
    membership = await _membership(
        db, tenant_id=principal.tenant_id, principal_id=member_principal_id
    )
    if membership is None:
        return None
    if status == "revoked" and membership.role == ROLE_OWNER:
        owners = await _active_owners(db, tenant_id=principal.tenant_id)
        if len(owners) <= 1:
            raise ValueError("The last active owner cannot be revoked")
    if status == "revoked":
        changed = await identity_service.revoke_membership(
            db, tenant_id=principal.tenant_id, principal_id=member_principal_id
        )
        if not changed:
            return None
        refreshed = await _membership(
            db, tenant_id=principal.tenant_id, principal_id=member_principal_id
        )
        if refreshed is None:
            return None
        updated = refreshed
    else:
        membership.status = "active"
        await db.flush()
        updated = membership
    if updated is None:
        return None
    await _audit(
        db, principal, "client_admin.member.status", member_principal_id, {"status": status}
    )
    return await _member_record(db, tenant_id=principal.tenant_id, membership=updated)


async def revoke_member_sessions(
    db: AsyncSession, *, principal: Principal, member_principal_id: str
) -> int:
    """Revoke every active session for one member of this tenant."""
    _require_tenant_admin(principal)
    if member_principal_id == principal.principal_id:
        raise AuthorizationError(
            "A principal cannot revoke their own sessions", code="self_mutation"
        )
    membership = await _membership(
        db, tenant_id=principal.tenant_id, principal_id=member_principal_id
    )
    if membership is None:
        return 0
    sessions = (
        await db.execute(
            select(AuthSession).where(
                AuthSession.principal_id == member_principal_id,
                AuthSession.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    revoked = 0
    for session in sessions:
        session.revoked_at = utcnow()
        revoked += 1
    await db.flush()
    await _audit(
        db, principal, "client_admin.session.revoke", member_principal_id, {"count": revoked}
    )
    return revoked


async def read_preferences(db: AsyncSession, *, principal: Principal) -> dict:
    _require_tenant_admin(principal)
    tenant = await db.get(Tenant, principal.tenant_id)
    if tenant is None:
        return {}
    try:
        stored = json.loads(tenant.settings_json or "{}")
    except (TypeError, ValueError):
        stored = {}
    return {key: stored.get(key) for key in PREFERENCE_SPEC}


async def update_preferences(
    db: AsyncSession, *, principal: Principal, changes: dict
) -> dict:
    """Update whitelisted tenant preferences; unknown keys or bad types are refused."""
    _require_tenant_admin(principal)
    tenant = await db.get(Tenant, principal.tenant_id)
    if tenant is None:
        raise ValueError("Unknown tenant")
    previous = await read_preferences(db, principal=principal)
    current = dict(previous)
    for key, value in (changes or {}).items():
        expected = PREFERENCE_SPEC.get(key)
        if expected is None:
            raise ValueError(f"Unsupported tenant preference: {key!r}")
        if value is None:
            current[key] = None
            continue
        if expected is bool and not isinstance(value, bool):
            raise ValueError(f"Preference {key!r} must be a boolean")
        if expected is str and not isinstance(value, str):
            raise ValueError(f"Preference {key!r} must be a string")
        current[key] = value
    tenant.settings_json = json.dumps(current, sort_keys=True)
    await db.flush()
    await _audit(
        db,
        principal,
        "client_admin.preferences.update",
        principal.tenant_id,
        {"keys": sorted((changes or {}).keys())},
    )
    return current


async def usage_summary(
    db: AsyncSession,
    *,
    principal: Principal,
    period: str = "day",
    start=None,
    end=None,
) -> dict:
    """Tenant-scoped usage totals plus M2 aggregates for the caller's tenant.

    The scalar ``usage`` totals object is kept for backward compatibility with
    existing consumers; the M2 ``aggregate`` carries the per-bucket breakdowns.
    This surface is scoped to the caller's tenant by a SQL predicate and can
    never total another tenant. Plan and entitlement values remain M3 work and
    stay ``None``.
    """
    _require_tenant_admin(principal)
    totals = await usage_metering.summarize_usage(db, tenant_id=principal.tenant_id)
    aggregate = await usage_aggregation.aggregate_usage(
        db, tenant_id=principal.tenant_id, period=period, start=start, end=end
    )
    return {
        "tenant_id": principal.tenant_id,
        "usage": totals,
        "aggregate": aggregate,
        # M3 (entitlements) has not landed; this is a placeholder so the admin
        # surface has a stable contract.
        "plan": None,
        "entitlements": None,
        "note": "Usage aggregation is M2; plan and entitlement data arrive with M3.",
    }
