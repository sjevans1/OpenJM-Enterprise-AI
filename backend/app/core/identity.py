"""Trusted principal and tenant context.

VS5 replaces the previous trusted-development ``dev_user_id`` assumption with a
server-derived identity context. Nothing in this module trusts a client-supplied
identifier: a :class:`Principal` is only ever constructed by the server after a
token/session has been validated **and** the current tenant membership has been
re-read from the application database.

Two invariants matter for the whole programme:

1. **Tokens authenticate, the database authorizes.** A validated token carries a
   subject and a tenant hint. It never carries roles or permissions, because a
   token issued before a revocation would then keep granting access. Roles are
   resolved from :class:`~app.models.TenantMembership` on every request.
2. **Fail closed.** Missing, malformed, expired, ambiguous or revoked identity
   resolves to an error, never to a default principal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.permissions import Permission, permissions_for_role
from app.core.platform import PlatformCapability
from app.core.tenancy import LEGACY_TENANT_ID


class IdentityError(Exception):
    """Base class for identity/authorization failures."""

    status_code = 401
    code = "identity_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class AuthenticationError(IdentityError):
    """No usable credential, or a credential that failed validation."""

    status_code = 401
    code = "unauthenticated"


class AuthorizationError(IdentityError):
    """An authenticated principal lacks a required capability or tenant scope."""

    status_code = 403
    code = "forbidden"


class TenantScopeError(AuthorizationError):
    """The principal is not an active member of the requested tenant."""

    status_code = 403
    code = "tenant_forbidden"


class AuthMethod(str, Enum):
    OIDC = "oidc"
    SESSION = "session"
    LOCAL_DEV = "local-dev"


@dataclass(frozen=True)
class Principal:
    """A server-derived identity context for exactly one request.

    ``principal_id`` is the OpenJM user identifier that owns rows in the
    existing VS1-VS4 tables (the former ``user_id``). ``tenant_id`` is the
    active tenant for this request and is part of every ownership predicate.
    """

    principal_id: str
    tenant_id: str
    subject: str
    role: str
    membership_id: str
    auth_method: str
    email: str | None = None
    display_name: str | None = None
    permissions: frozenset[Permission] = field(default_factory=frozenset)
    # BV1-A access context, resolved from the database on every request. These
    # are data-side scopes (which governed information the principal may reach)
    # and are kept separate from ``permissions`` (what the principal may do).
    department_ids: frozenset[str] = field(default_factory=frozenset)
    group_ids: frozenset[str] = field(default_factory=frozenset)
    steward_scopes: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    # Platform (control-plane) capabilities, always an explicit grant and never
    # derived from the tenant role.
    platform_capabilities: frozenset[str] = field(default_factory=frozenset)
    # Active, unexpired OpenJM support delegations for this tenant
    # ("metadata", "content"). Content support never arrives implicitly.
    support_scopes: frozenset[str] = field(default_factory=frozenset)

    @property
    def user_id(self) -> str:
        """The ownership key written to every VS1-VS4 ``user_id`` column.

        It embeds the tenant, so an ownership predicate is tenant-correct
        *by construction* on every existing query, including the ones this
        programme did not have to touch. Two memberships of the same principal
        in different tenants therefore produce different keys and can never
        address each other's rows.

        The legacy local tenant keeps the bare principal id so that rows written
        by earlier single-tenant deployments remain addressable by their
        original owner after the upgrade.

        The explicit ``tenant_id`` column is still written on every row and is
        the primary, readable boundary; this key is the defense-in-depth layer.
        """
        if self.tenant_id == LEGACY_TENANT_ID:
            return self.principal_id
        return f"{self.tenant_id}:{self.principal_id}"

    def has(self, permission: Permission | str) -> bool:
        value = permission.value if isinstance(permission, Permission) else str(permission)
        return value in {p.value for p in self.permissions}

    def has_all(self, permissions) -> bool:
        return all(self.has(p) for p in permissions)

    def require(self, permission: Permission | str) -> None:
        if not self.has(permission):
            name = permission.value if isinstance(permission, Permission) else str(permission)
            raise AuthorizationError(
                f"Principal {self.principal_id} lacks required permission '{name}'",
                code="missing_permission",
            )

    def require_all(self, permissions) -> None:
        for permission in permissions:
            self.require(permission)

    def assert_tenant(self, tenant_id: str | None) -> None:
        """Reject any attempt to operate outside the principal's active tenant."""
        if tenant_id is None:
            return
        if str(tenant_id) != self.tenant_id:
            raise TenantScopeError(
                "Requested tenant does not match the authenticated tenant context",
                code="tenant_mismatch",
            )

    def permission_strings(self) -> frozenset[str]:
        return frozenset(p.value for p in self.permissions)

    # ------------------------------------------------------------------
    # BV1-A: data-scope and platform-capability helpers
    # ------------------------------------------------------------------

    def in_group(self, group_id: str) -> bool:
        return str(group_id) in self.group_ids

    def in_department(self, department_id: str) -> bool:
        return str(department_id) in self.department_ids

    def is_steward_for(self, scope_type: str, scope_id: str) -> bool:
        return (str(scope_type), str(scope_id)) in self.steward_scopes

    def is_steward_anywhere(self) -> bool:
        return bool(self.steward_scopes)

    def has_platform(self, capability: PlatformCapability | str) -> bool:
        value = (
            capability.value
            if isinstance(capability, PlatformCapability)
            else str(capability)
        )
        return value in self.platform_capabilities

    def require_platform(self, *capabilities: PlatformCapability | str) -> None:
        """Fail closed unless every requested platform capability is granted.

        Tenant role permissions are deliberately not consulted: a tenant owner
        without an explicit platform grant holds no platform authority.
        """
        for capability in capabilities:
            if not self.has_platform(capability):
                name = (
                    capability.value
                    if isinstance(capability, PlatformCapability)
                    else str(capability)
                )
                raise AuthorizationError(
                    f"Principal {self.principal_id} lacks platform capability '{name}'",
                    code="missing_platform_capability",
                )

    def platform_capability_strings(self) -> frozenset[str]:
        return frozenset(self.platform_capabilities)

    def has_support_scope(self, scope: str) -> bool:
        """An active OpenJM support delegation for this tenant and scope."""
        return str(scope) in self.support_scopes


def build_permissions(role: str | None) -> frozenset[Permission]:
    return permissions_for_role(role)
