"""Trusted identity resolution for OpenJM Enterprise AI.

Every protected operation funnels through :func:`authenticate` (or the FastAPI
dependency that wraps it). This module is the only place that turns a
credential into a :class:`~app.core.identity.Principal`, and it always re-reads
the current membership from the application database.

Consequences that the acceptance suite depends on:

* Changing a role, revoking a membership or disabling an account takes effect on
  the *next request*, with no token or cache to expire.
* A token is never trusted for a tenant it does not already have an active
  membership for; an ambiguous tenant is denied rather than guessed.
* Nothing here can be short-circuited by a stale cached document, report or
  vector, because authorization is resolved per request from the database.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.identity import (
    AuthenticationError,
    AuthMethod,
    AuthorizationError,
    Principal,
    TenantScopeError,
    build_permissions,
)
from app.core.tenancy import (
    LEGACY_PRINCIPAL_ID,
    LEGACY_PRINCIPAL_SUBJECT,
    LEGACY_TENANT_ID,
    LEGACY_TENANT_NAME,
    LEGACY_TENANT_SLUG,
)
from app.models import (
    AuditRecord,
    AuthSession,
    PrincipalAccount,
    Tenant,
    TenantMembership,
)
from app.services.access_governance import (
    load_principal_access,
    platform_capabilities_for,
)
from app.services.oidc import OIDCValidationError, oidc_client

settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a stored datetime.

    SQLite returns naive datetimes; the application always wrote UTC. Treat a
    naive value as UTC so expiry comparisons are correct on both backends.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fingerprint_items(items) -> str:
    payload = json.dumps(sorted(str(item) for item in items), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


async def ensure_local_identity(db: AsyncSession) -> None:
    """Provision the local development tenant, principal and owner membership.

    Idempotent. This is what makes ``auth_mode='dev'`` a real, server-derived
    identity context rather than a bare settings string: the request still
    resolves a tenant, a principal and a role from the database.
    """
    tenant = await db.get(Tenant, LEGACY_TENANT_ID)
    if tenant is None:
        tenant = Tenant(
            id=LEGACY_TENANT_ID,
            slug=LEGACY_TENANT_SLUG,
            name=LEGACY_TENANT_NAME,
            status="active",
        )
        db.add(tenant)
        await db.flush()

    account = await db.get(PrincipalAccount, LEGACY_PRINCIPAL_ID)
    if account is None:
        account = PrincipalAccount(
            id=LEGACY_PRINCIPAL_ID,
            subject=LEGACY_PRINCIPAL_SUBJECT,
            issuer="openjm-local",
            display_name="Local administrator",
            status="active",
        )
        db.add(account)
        await db.flush()

    membership = (
        await db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == LEGACY_TENANT_ID,
                TenantMembership.principal_id == LEGACY_PRINCIPAL_ID,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        db.add(
            TenantMembership(
                tenant_id=LEGACY_TENANT_ID,
                principal_id=LEGACY_PRINCIPAL_ID,
                role="owner",
                status="active",
                version=1,
            )
        )
    await db.commit()


async def create_tenant(
    db: AsyncSession, *, slug: str, name: str, tenant_id: str | None = None
) -> Tenant:
    tenant = Tenant(
        id=tenant_id or secrets.token_hex(18),
        slug=slug,
        name=name,
        status="active",
    )
    db.add(tenant)
    await db.flush()
    return tenant


async def get_or_create_principal(
    db: AsyncSession,
    *,
    subject: str,
    issuer: str | None = None,
    email: str | None = None,
    display_name: str | None = None,
    principal_id: str | None = None,
) -> PrincipalAccount:
    """Resolve an external subject to a local account.

    An account may be auto-created for a validated subject, but a *membership*
    is never auto-created: without an active membership the request is refused
    downstream. This keeps "valid token" from meaning "authorized user".
    """
    account = (
        await db.execute(
            select(PrincipalAccount).where(PrincipalAccount.subject == subject)
        )
    ).scalar_one_or_none()
    if account is not None:
        changed = False
        if email and account.email != email:
            account.email = email
            changed = True
        if display_name and account.display_name != display_name:
            account.display_name = display_name
            changed = True
        if changed:
            await db.flush()
        return account

    account = PrincipalAccount(
        id=principal_id or secrets.token_hex(18),
        subject=subject,
        issuer=issuer,
        email=email,
        display_name=display_name,
        status="active",
    )
    db.add(account)
    await db.flush()
    return account


async def add_membership(
    db: AsyncSession,
    *,
    tenant_id: str,
    principal_id: str,
    role: str,
) -> TenantMembership:
    existing = (
        await db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.principal_id == principal_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.role = role
        existing.status = "active"
        existing.revoked_at = None
        existing.version = (existing.version or 0) + 1
        await db.flush()
        return existing
    membership = TenantMembership(
        tenant_id=tenant_id,
        principal_id=principal_id,
        role=role,
        status="active",
        version=1,
    )
    db.add(membership)
    await db.flush()
    return membership


async def set_membership_role(
    db: AsyncSession, *, tenant_id: str, principal_id: str, role: str
) -> TenantMembership | None:
    membership = (
        await db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.principal_id == principal_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        return None
    membership.role = role
    membership.version = (membership.version or 0) + 1
    await db.flush()
    return membership


async def revoke_membership(
    db: AsyncSession, *, tenant_id: str, principal_id: str
) -> int:
    """Revoke a membership and every session bound to it.

    Returns the number of memberships changed. Any live session for that
    principal in that tenant is revoked in the same transaction so a
    revocation cannot be walked back by an in-flight token.
    """
    membership = (
        await db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.principal_id == principal_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        return 0
    membership.status = "revoked"
    membership.revoked_at = utcnow()
    membership.version = (membership.version or 0) + 1

    sessions = (
        (
            await db.execute(
                select(AuthSession).where(
                    AuthSession.principal_id == principal_id,
                    AuthSession.tenant_id == tenant_id,
                    AuthSession.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for session in sessions:
        session.revoked_at = utcnow()
    await db.flush()
    return 1


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


async def _membership_or_deny(
    db: AsyncSession, *, principal_id: str, tenant_id: str | None
) -> TenantMembership:
    query = select(TenantMembership).where(
        TenantMembership.principal_id == principal_id,
        TenantMembership.status == "active",
    )
    if tenant_id is not None:
        query = query.where(TenantMembership.tenant_id == tenant_id)
    rows = (await db.execute(query)).scalars().all()

    if not rows:
        raise TenantScopeError(
            "Principal has no active membership for the requested tenant",
            code="no_active_membership",
        )
    if tenant_id is None:
        if len(rows) > 1:
            # Ambiguous: never guess a tenant for a multi-tenant principal.
            raise TenantScopeError(
                "Principal belongs to multiple tenants; an explicit tenant is required",
                code="ambiguous_tenant",
            )
        return rows[0]
    return rows[0]


async def _principal_for(
    db: AsyncSession,
    *,
    account: PrincipalAccount,
    membership: TenantMembership,
    auth_method: str,
) -> Principal:
    tenant = await db.get(Tenant, membership.tenant_id)
    if tenant is None or tenant.status != "active":
        raise TenantScopeError("Tenant is not active", code="tenant_inactive")
    if account.status != "active":
        raise AuthorizationError("Principal account is disabled", code="account_disabled")

    # BV1-A: resolve the principal's data-side scopes and platform capabilities
    # from current rows, on every request. This is what makes a group or steward
    # revocation, or a platform-capability change, effective immediately with no
    # token or cache to expire.
    access = await load_principal_access(
        db, principal_id=account.id, tenant_id=membership.tenant_id
    )
    platform_capabilities = await platform_capabilities_for(db, principal_id=account.id)

    return Principal(
        principal_id=account.id,
        tenant_id=membership.tenant_id,
        subject=account.subject,
        role=membership.role,
        membership_id=membership.id,
        auth_method=auth_method,
        email=account.email,
        display_name=account.display_name,
        permissions=build_permissions(membership.role),
        department_ids=access.department_ids,
        group_ids=access.group_ids,
        steward_scopes=access.steward_scopes,
        platform_capabilities=frozenset(c.value for c in platform_capabilities),
        support_scopes=access.support_scopes,
    )


async def resolve_tenant_id(db: AsyncSession, tenant_hint: str | None) -> str | None:
    """Map a tenant claim (id or slug) to an OpenJM tenant id."""
    if not tenant_hint:
        return None
    tenant = await db.get(Tenant, tenant_hint)
    if tenant is not None:
        return tenant.id
    tenant = (
        await db.execute(select(Tenant).where(Tenant.slug == tenant_hint))
    ).scalar_one_or_none()
    if tenant is None:
        raise TenantScopeError("Unknown tenant", code="unknown_tenant")
    return tenant.id


async def principal_for_account(
    db: AsyncSession,
    account: PrincipalAccount,
    *,
    tenant_id: str | None,
    auth_method: str,
) -> Principal:
    membership = await _membership_or_deny(
        db, principal_id=account.id, tenant_id=tenant_id
    )
    return await _principal_for(
        db, account=account, membership=membership, auth_method=auth_method
    )


async def resolve_session(db: AsyncSession, raw_token: str) -> Principal:
    stored = (
        await db.execute(
            select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token))
        )
    ).scalar_one_or_none()
    if stored is None:
        raise AuthenticationError("Session token is not recognised", code="unknown_session")
    if stored.revoked_at is not None:
        raise AuthenticationError("Session has been revoked", code="session_revoked")
    expires_at = _as_utc(stored.expires_at)
    if expires_at is None or expires_at <= utcnow():
        raise AuthenticationError("Session has expired", code="session_expired")

    account = await db.get(PrincipalAccount, stored.principal_id)
    if account is None:
        raise AuthenticationError("Session principal no longer exists", code="unknown_principal")
    membership = await _membership_or_deny(
        db, principal_id=account.id, tenant_id=stored.tenant_id
    )
    return await _principal_for(
        db, account=account, membership=membership, auth_method=AuthMethod.SESSION.value
    )


async def create_session(
    db: AsyncSession,
    *,
    principal: Principal,
    ttl_seconds: int | None = None,
    auth_method: str | None = None,
) -> str:
    ttl = ttl_seconds or settings.session_ttl_seconds
    raw = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            token_hash=hash_token(raw),
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
            auth_method=auth_method or principal.auth_method,
            issued_at=utcnow(),
            expires_at=utcnow() + timedelta(seconds=ttl),
        )
    )
    await db.commit()
    return raw


async def revoke_session(db: AsyncSession, raw_token: str) -> int:
    stored = (
        await db.execute(
            select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token))
        )
    ).scalar_one_or_none()
    if stored is None:
        return 0
    if stored.revoked_at is None:
        stored.revoked_at = utcnow()
        await db.flush()
    return 1


async def resolve_oidc(db: AsyncSession, token: str) -> Principal:
    """Validate an OIDC JWT and resolve the local principal it maps to."""
    claims = await oidc_client.validate(token)
    identity = oidc_client.claims_to_identity(claims)
    account = await get_or_create_principal(
        db,
        subject=identity["subject"],
        issuer=identity["issuer"],
        email=identity["email"],
        display_name=identity["display_name"],
    )
    tenant_id = await resolve_tenant_id(db, identity["tenant_hint"])
    if tenant_id is None and settings.tenant_resolution != "single_membership":
        raise TenantScopeError(
            "Token does not identify a tenant", code="missing_tenant_claim"
        )
    return await principal_for_account(
        db, account, tenant_id=tenant_id, auth_method=AuthMethod.OIDC.value
    )


def local_development_principal() -> Principal:
    """The server-configured development principal.

    This is *not* a client-supplied identity and it is unavailable unless the
    deployment explicitly runs ``auth_mode='dev'`` with
    ``auth_allow_dev_mode``. It exists so local development and the offline test
    suite work without a database round-trip, and so a partially provisioned
    local database still resolves a real (tenant, principal, role) tuple rather
    than falling back to "anonymous".
    """
    return Principal(
        principal_id=LEGACY_PRINCIPAL_ID,
        tenant_id=LEGACY_TENANT_ID,
        subject=LEGACY_PRINCIPAL_SUBJECT,
        role="owner",
        membership_id="local-membership",
        auth_method=AuthMethod.LOCAL_DEV.value,
        display_name="Local administrator",
        permissions=build_permissions("owner"),
    )


async def authenticate(
    db: AsyncSession, *, bearer: str | None, dev_principal: bool = False
) -> Principal:
    """Resolve the trusted principal for one request. Fails closed."""
    if dev_principal:
        if not settings.auth_allow_dev_mode or settings.auth_mode != "dev":
            raise AuthenticationError(
                "Development identity is disabled on this deployment",
                code="dev_identity_disabled",
            )
        account = await db.get(PrincipalAccount, LEGACY_PRINCIPAL_ID)
        if account is None:
            # Local development against an unprovisioned database: resolve the
            # configured local identity directly. Production (auth_mode='oidc')
            # never reaches this branch.
            return local_development_principal()
        return await principal_for_account(
            db, account, tenant_id=LEGACY_TENANT_ID, auth_method=AuthMethod.LOCAL_DEV.value
        )

    if not bearer:
        raise AuthenticationError("A bearer token is required", code="missing_credential")

    # An OpenJM-native session token is opaque and shorter than a JWT; try it
    # first so a session never depends on the provider being reachable.
    if bearer.count(".") != 2:
        return await resolve_session(db, bearer)
    return await resolve_oidc(db, bearer)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


async def record_audit(
    db: AsyncSession,
    *,
    principal: Principal | None,
    action: str,
    decision: str,
    tenant_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    reason: str | None = None,
    metadata: dict | None = None,
) -> AuditRecord:
    record = AuditRecord(
        tenant_id=tenant_id or (principal.tenant_id if principal else "unknown"),
        principal_id=principal.principal_id if principal else None,
        role=principal.role if principal else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        decision=decision,
        reason=(reason or "")[:240] or None,
        auth_method=principal.auth_method if principal else None,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    db.add(record)
    await db.flush()
    return record


__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "OIDCValidationError",
    "add_membership",
    "authenticate",
    "create_session",
    "create_tenant",
    "ensure_local_identity",
    "fingerprint_items",
    "get_or_create_principal",
    "hash_token",
    "principal_for_account",
    "record_audit",
    "resolve_oidc",
    "resolve_session",
    "resolve_tenant_id",
    "revoke_membership",
    "revoke_session",
    "set_membership_role",
    "utcnow",
]
