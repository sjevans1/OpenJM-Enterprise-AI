"""OpenJM-owned role and permission model.

This module is deliberately dependency-free (no FastAPI, no SQLAlchemy) so it
can be reasoned about, unit-tested and reused by services, migrations and the
API layer alike.

Design rules:

* Least privilege. A role only receives the permissions listed here.
* Explicit. Unknown roles resolve to *no* permissions, never to a default.
* Stable identifiers. Permission strings are part of the persisted audit
  record and must not be renamed casually.
"""

from __future__ import annotations

from enum import Enum


class Permission(str, Enum):
    """A single authorized capability, evaluated per request."""

    CHAT_USE = "chat:use"

    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_WRITE = "knowledge:write"
    KNOWLEDGE_DELETE = "knowledge:delete"

    DATA_READ = "data:read"
    DATA_WRITE = "data:write"

    REPORTS_READ = "reports:read"
    REPORTS_WRITE = "reports:write"
    REPORTS_RUN = "reports:run"
    REPORTS_EXPORT = "reports:export"

    TRACES_READ = "traces:read"
    AUDIT_READ = "audit:read"

    ACTIONS_READ = "actions:read"
    ACTIONS_PLAN = "actions:plan"
    ACTIONS_EXECUTE = "actions:execute"
    ACTIONS_APPROVE = "actions:approve"

    # VS7 connector / operational permissions. Reading connector state is an
    # operational visibility permission; configuring, enabling or rotating a
    # connector is a write; purging cached external state is the destructive
    # tier and is reserved for owners.
    CONNECTOR_READ = "connector:read"
    CONNECTOR_WRITE = "connector:write"
    CONNECTOR_ADMIN = "connector:admin"

    SCHEDULES_READ = "schedules:read"
    SCHEDULES_WRITE = "schedules:write"

    NOTIFICATIONS_READ = "notifications:read"
    NOTIFICATIONS_WRITE = "notifications:write"

    TENANT_ADMIN = "tenant:admin"


ROLE_VIEWER = "viewer"
ROLE_EDITOR = "editor"
ROLE_ADMIN = "admin"
ROLE_OWNER = "owner"

ROLES: tuple[str, ...] = (ROLE_VIEWER, ROLE_EDITOR, ROLE_ADMIN, ROLE_OWNER)

_VIEWER = frozenset(
    {
        Permission.CHAT_USE,
        Permission.KNOWLEDGE_READ,
        Permission.DATA_READ,
        Permission.REPORTS_READ,
        Permission.TRACES_READ,
        Permission.ACTIONS_READ,
    }
)

_EDITOR = _VIEWER | frozenset(
    {
        Permission.KNOWLEDGE_WRITE,
        Permission.DATA_WRITE,
        Permission.REPORTS_WRITE,
        Permission.REPORTS_RUN,
        Permission.REPORTS_EXPORT,
        Permission.ACTIONS_PLAN,
        Permission.ACTIONS_EXECUTE,
        # Operational visibility of connector and schedule state.
        Permission.CONNECTOR_READ,
        Permission.SCHEDULES_READ,
    }
)

_ADMIN = _EDITOR | frozenset(
    {
        Permission.KNOWLEDGE_DELETE,
        Permission.AUDIT_READ,
        Permission.ACTIONS_APPROVE,
        # Administering connectors, schedules and notification channels is a
        # tenant-level operation, not a per-user one.
        Permission.CONNECTOR_WRITE,
        Permission.SCHEDULES_WRITE,
        Permission.NOTIFICATIONS_READ,
        Permission.NOTIFICATIONS_WRITE,
        Permission.TENANT_ADMIN,
    }
)

_OWNER = _ADMIN | frozenset({p for p in Permission})


ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    ROLE_VIEWER: _VIEWER,
    ROLE_EDITOR: _EDITOR,
    ROLE_ADMIN: _ADMIN,
    ROLE_OWNER: _OWNER,
}


def is_known_role(role: str | None) -> bool:
    return bool(role) and role in ROLE_PERMISSIONS


def permissions_for_role(role: str | None) -> frozenset[Permission]:
    """Resolve a role to its permission set.

    An unknown, missing or malformed role yields the empty set. Callers must
    treat "no permissions" as deny, never as "no restrictions".
    """
    if not role:
        return frozenset()
    return ROLE_PERMISSIONS.get(role, frozenset())


def permission_strings_for_role(role: str | None) -> frozenset[str]:
    return frozenset(p.value for p in permissions_for_role(role))
