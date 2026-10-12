"""Current authorization over cached authorization.

This is the rule the whole connector programme exists to enforce:

    a service credential being able to read a resource does NOT mean the
    end user is authorized to receive its evidence

Evidence is therefore never served on the strength of the connector's own
credential. Before cached connector content reaches a principal, this module
asks the provider whether *that principal's mapped external user* currently has
access, and it fails closed on every outcome that is not a proven yes:

* a missing or ambiguous user mapping denies;
* a revoked or superseded connector credential denies;
* a disabled or disconnected connector denies;
* a provider timeout, outage, rate limit or contradictory response denies;
* an inaccessible resource denies.

Denial is not merely a return value, and a denial never mutates shared content
either. A denial is enforced by refusing this request, and it stays enforced on
the next request because the provider is consulted again rather than because a
decision was cached.

The scope of a state change must match the scope of the failure. A
principal-scoped failure proves something about one reader and must change
nothing about the content, so one user's correct refusal cannot withdraw a
document from the rest of the tenant. Content-scoped conditions (the resource is
gone, its ACL is broken, its integrity is in doubt) are handled by explicit
lifecycle operations in :mod:`app.services.connectors.ingest`, never as a side
effect of an authorization decision.

The single exception is a connector that declares
``AuthorizationBehavior.CONNECTOR_SCOPED``, which states by construction that
its provider has no per-user permission model. Such content is treated as
tenant-wide and is never attributed to an individual user.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.connectors import AuthorizationBehavior
from app.models import (
    CONNECTOR_STATUS_ACTIVE,
    ConnectorInstance,
    EXTERNAL_STATE_ACTIVE,
    ExternalResource,
    WorkspaceUserMapping,
)
from app.services.connectors.base import ConnectorAuthUnavailable, ConnectorError
from app.services.connectors.service import (
    build_context,
    get_implementation,
    resolve_instance,
    spec_for,
)
from app.services.identity import utcnow

# Bound on how many candidate resources one retrieval request will revalidate
# against the provider. Keeps a tenant with a very large connector from turning
# a single question into an unbounded number of outbound calls.
MAX_AUTHORIZATION_CHECKS = 100


class DenialScope(str, Enum):
    """What a denial is evidence about.

    Authorization never changes content lifecycle state, so a denial carries its
    scope instead of acting on it. A caller that owns a lifecycle operation reads
    the scope and decides separately, and only content-scoped evidence can justify
    withdrawing content from everyone.
    """

    # This reader is not entitled. Says nothing about the content.
    PRINCIPAL = "principal"
    # Not provable for this request: timeout, outage, rate limit, contradictory
    # or unexpected provider failure. Fails closed for the request only.
    REQUEST = "request"
    # The connector instance or its credential cannot currently authorize anyone.
    CONNECTOR = "connector"
    # The content itself is gone, inconsistent, or already non-retrievable.
    CONTENT = "content"


@dataclass(frozen=True)
class AuthorizationOutcome:
    """The result of a current-authorization decision.

    Constructing an outcome never mutates shared state. There is deliberately no
    ``quarantined`` field: quarantine is an explicit lifecycle operation, not an
    authorization side effect.
    """

    allowed: bool
    reason: str
    scope: "DenialScope | None" = None
    detail: str = ""

    @staticmethod
    def deny(reason: str, scope: DenialScope, detail: str = "") -> "AuthorizationOutcome":
        return AuthorizationOutcome(allowed=False, reason=reason, scope=scope, detail=detail)

    @staticmethod
    def allow() -> "AuthorizationOutcome":
        return AuthorizationOutcome(allowed=True, reason="authorized")


# ---------------------------------------------------------------------------
# Explicit user mapping
# ---------------------------------------------------------------------------


async def resolve_mapping(
    db: AsyncSession,
    *,
    connector_instance_id: str,
    tenant_id: str,
    principal_id: str,
) -> WorkspaceUserMapping | None:
    """Return the active mapping for a principal, or None.

    The uniqueness constraints on ``workspace_user_mappings`` make an ambiguous
    mapping unrepresentable, so there is no "one of several" case to guess
    between. A revoked mapping does not resolve.
    """
    result = await db.execute(
        select(WorkspaceUserMapping).where(
            WorkspaceUserMapping.connector_instance_id == connector_instance_id,
            WorkspaceUserMapping.tenant_id == tenant_id,
            WorkspaceUserMapping.principal_id == principal_id,
            WorkspaceUserMapping.status == "active",
        )
    )
    return result.scalars().first()


async def create_mapping(
    db: AsyncSession,
    *,
    connector_instance_id: str,
    tenant_id: str,
    principal_id: str,
    external_user_id: str,
    actor: str,
) -> WorkspaceUserMapping:
    """Create an explicit mapping. Never inferred from a display name."""
    if not external_user_id or not str(external_user_id).strip():
        raise ConnectorError("external user id is required", code="mapping_invalid")
    mapping = WorkspaceUserMapping(
        tenant_id=tenant_id,
        connector_instance_id=connector_instance_id,
        principal_id=principal_id,
        external_user_id=str(external_user_id).strip(),
        status="active",
        created_by=actor,
    )
    db.add(mapping)
    await db.flush()
    from app.services.identity import record_audit

    await record_audit(
        db,
        principal=None,
        tenant_id=tenant_id,
        action="connector.user_mapping.create",
        decision="allow",
        resource_type="connector_instance",
        resource_id=connector_instance_id,
        metadata={"principal_id": principal_id, "actor": actor},
    )
    return mapping


async def revoke_mapping(
    db: AsyncSession, *, mapping: WorkspaceUserMapping, actor: str
) -> WorkspaceUserMapping:
    mapping.status = "revoked"
    mapping.revoked_at = utcnow()
    mapping.updated_at = utcnow()
    await db.flush()
    from app.services.identity import record_audit

    await record_audit(
        db,
        principal=None,
        tenant_id=mapping.tenant_id,
        action="connector.user_mapping.revoke",
        decision="allow",
        resource_type="connector_instance",
        resource_id=mapping.connector_instance_id,
        metadata={"principal_id": mapping.principal_id, "actor": actor},
    )
    return mapping


async def list_mappings(
    db: AsyncSession, *, connector_instance_id: str, tenant_id: str
) -> list[WorkspaceUserMapping]:
    result = await db.execute(
        select(WorkspaceUserMapping)
        .where(
            WorkspaceUserMapping.connector_instance_id == connector_instance_id,
            WorkspaceUserMapping.tenant_id == tenant_id,
        )
        .order_by(WorkspaceUserMapping.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Current authorization
# ---------------------------------------------------------------------------


async def authorize_resource(
    db: AsyncSession,
    *,
    tenant_id: str,
    connector_instance_id: str,
    resource: ExternalResource,
    principal_id: str,
) -> AuthorizationOutcome:
    """Decide whether ``principal_id`` may currently receive this evidence.

    This function never mutates content lifecycle state. Every failure mode
    returns a denial, there is no branch that upgrades a failure into an
    allowance, and there is no reliance on a previously cached decision: the
    provider is consulted on every request, which is what makes a denial durable
    without persisting anything principal-specific.

    An earlier revision quarantined the shared resource on every denial. That
    conflated a statement about one reader with a statement about the content, so
    one user's correct refusal removed the document for every other user in the
    tenant. Content-scoped conditions are handled by the explicit lifecycle
    operations in :mod:`app.services.connectors.ingest`, never here.
    """
    # A resource that is not currently active is not evidence at all. Reporting
    # that is not a mutation: the stored state already said so.
    if resource.lifecycle_state == "deleted":
        return AuthorizationOutcome.deny("resource_deleted", DenialScope.CONTENT)
    if resource.lifecycle_state != EXTERNAL_STATE_ACTIVE:
        return AuthorizationOutcome.deny("resource_quarantined", DenialScope.CONTENT)

    try:
        instance = await resolve_instance(
            db, tenant_id=tenant_id, connector_instance_id=connector_instance_id
        )
    except ConnectorError:
        return AuthorizationOutcome.deny("connector_not_found", DenialScope.CONNECTOR)

    # The tenant boundary is re-asserted here rather than trusted from the
    # caller, so a resource row that somehow carries a foreign tenant cannot be
    # authorized for this principal.
    if instance.tenant_id != resource.tenant_id or instance.id != resource.connector_instance_id:
        return AuthorizationOutcome.deny("resource_instance_mismatch", DenialScope.CONTENT)

    if not instance.enabled or instance.status != CONNECTOR_STATUS_ACTIVE:
        # The instance cannot authorize anyone right now. Withdrawing its
        # content is a connector-scoped lifecycle decision and is performed by
        # quarantine_instance_resources in :mod:`service`, not as a side effect
        # of asking about one principal.
        return AuthorizationOutcome.deny("connector_disabled", DenialScope.CONNECTOR)

    spec = spec_for(instance)

    if spec.authorization_behavior is AuthorizationBehavior.NONE:
        return AuthorizationOutcome.deny("authorization_unsupported", DenialScope.CONNECTOR)

    if spec.authorization_behavior is AuthorizationBehavior.CONNECTOR_SCOPED:
        # The provider has no per-user model. The content is tenant-wide and is
        # never attributed to an individual principal.
        return AuthorizationOutcome.allow()

    # PROVIDER_CURRENT_STATE: an explicit mapping and a live provider answer are
    # both required.
    mapping = await resolve_mapping(
        db,
        connector_instance_id=instance.id,
        tenant_id=tenant_id,
        principal_id=principal_id,
    )
    if mapping is None:
        return AuthorizationOutcome.deny("user_not_mapped", DenialScope.PRINCIPAL)

    try:
        ctx = await build_context(db, instance)
    except ConnectorError as exc:
        # Credential missing, revoked, unreadable, or the connector is not
        # enabled. All of these are refusals, never a fallback, and none of them
        # is evidence that the content is invalid.
        return AuthorizationOutcome.deny(
            exc.code, DenialScope.CONNECTOR, detail=exc.detail or ""
        )

    try:
        connector = get_implementation(spec.type_id, spec.version)
    except ConnectorError as exc:
        return AuthorizationOutcome.deny(exc.code, DenialScope.CONNECTOR)

    try:
        allowed = await connector.check_user_access(
            ctx, external_user_id=mapping.external_user_id, external_id=resource.external_id
        )
    except ConnectorAuthUnavailable as exc:
        # Timeout, outage, rate limit or contradictory provider state. Current
        # authorization cannot be proven for this request, so the evidence must
        # not be served. It must also not be withdrawn from anyone else: a third
        # party being briefly unreachable says nothing about the content.
        return AuthorizationOutcome.deny(
            exc.code or "authorization_unavailable",
            DenialScope.REQUEST,
            detail=exc.detail or "",
        )
    except ConnectorError as exc:
        return AuthorizationOutcome.deny(
            exc.code, DenialScope.REQUEST, detail=exc.detail or ""
        )
    except Exception as exc:  # noqa: BLE001 - an unexpected provider failure is a deny
        return AuthorizationOutcome.deny(
            "authorization_error", DenialScope.REQUEST, detail=str(exc)[:200]
        )

    if not allowed:
        return AuthorizationOutcome.deny("access_revoked", DenialScope.PRINCIPAL)

    return AuthorizationOutcome.allow()


# ---------------------------------------------------------------------------
# Retrieval integration
# ---------------------------------------------------------------------------


async def authorized_connector_document_ids(
    db: AsyncSession, *, tenant_id: str, principal_id: str, limit: int = MAX_AUTHORIZATION_CHECKS
) -> tuple[set[str], dict[str, str]]:
    """Return the connector documents this principal may currently use.

    Returns ``(document_ids, denials)``. Called from the retrieval paths so that
    connector evidence is admitted only through this gate. The bound keeps one
    question from turning into an unbounded number of provider calls.
    """
    instances = (
        await db.execute(
            select(ExternalResource)
            .join(
                ConnectorInstance,
                ConnectorInstance.id == ExternalResource.connector_instance_id,
            )
            .where(
                ExternalResource.tenant_id == tenant_id,
                ExternalResource.lifecycle_state == EXTERNAL_STATE_ACTIVE,
                ExternalResource.document_id.is_not(None),
                ConnectorInstance.tenant_id == tenant_id,
                ConnectorInstance.enabled.is_(True),
            )
            .limit(limit)
        )
    ).scalars().all()

    allowed_ids: set[str] = set()
    denials: dict[str, str] = {}
    for resource in instances:
        outcome = await authorize_resource(
            db,
            tenant_id=tenant_id,
            connector_instance_id=resource.connector_instance_id,
            resource=resource,
            principal_id=principal_id,
        )
        if outcome.allowed and resource.document_id:
            allowed_ids.add(resource.document_id)
        else:
            denials[resource.external_id] = outcome.reason
    return allowed_ids, denials


async def require_current_authorization(
    db: AsyncSession,
    *,
    tenant_id: str,
    connector_instance_id: str,
    external_id: str,
    principal_id: str,
) -> AuthorizationOutcome:
    """Gate for connector tool execution on one named resource."""
    from app.services.connectors.ingest import load_resource

    resource = await load_resource(
        db,
        connector_instance_id=connector_instance_id,
        tenant_id=tenant_id,
        external_id=external_id,
    )
    if resource is None:
        # Unknown, missing or foreign resource. This is a denial about the
        # requested resource, not about the principal and not about the
        # connector: it must return a structured refusal rather than raise, and
        # it must not mutate anything (there is nothing to mutate). The scope is
        # CONTENT, consistent with resource_deleted and resource_instance_mismatch.
        return AuthorizationOutcome.deny("resource_unknown", DenialScope.CONTENT)
    return await authorize_resource(
        db,
        tenant_id=tenant_id,
        connector_instance_id=connector_instance_id,
        resource=resource,
        principal_id=principal_id,
    )


def connector_resource_reference(connector_instance_id: str, external_id: str) -> str:
    """Canonical reference for a connector resource on a non-connector row.

    Encoded as JSON rather than joined with a separator, because provider
    resource ids are opaque and may legitimately contain any punctuation. A
    delimiter scheme would be ambiguous for some provider, and an ambiguous
    reference here would mean an authorization check against the wrong resource.
    """
    return json.dumps(
        {"connector_id": connector_instance_id, "external_id": external_id},
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_connector_resource_reference(value: str) -> tuple[str, str] | None:
    """Decode a reference, returning None when it is not well formed.

    A caller must fail closed on None rather than guessing at the resource.
    """
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    connector_id = payload.get("connector_id")
    external_id = payload.get("external_id")
    if not isinstance(connector_id, str) or not isinstance(external_id, str):
        return None
    if not connector_id or not external_id:
        return None
    return connector_id, external_id


async def authorized_connector_document_ids_for_context(db: AsyncSession) -> set[str]:
    """Resolve the request principal and return the connector documents it may use.

    This is the seam the retrieval paths call. It fails closed at every step: no
    request context, no principal, no mapped user, a revoked credential, a
    disabled connector or an unprovable provider answer all yield an empty set,
    so connector evidence simply does not appear rather than appearing on the
    strength of a cached decision.
    """
    from app.core.context import current_principal_or_none

    principal = current_principal_or_none()
    if principal is None:
        return set()
    allowed, _denials = await authorized_connector_document_ids(
        db, tenant_id=principal.tenant_id, principal_id=principal.principal_id
    )
    return allowed


__all__ = [
    "AuthorizationOutcome",
    "DenialScope",
    "MAX_AUTHORIZATION_CHECKS",
    "authorize_resource",
    "authorized_connector_document_ids",
    "authorized_connector_document_ids_for_context",
    "connector_resource_reference",
    "create_mapping",
    "list_mappings",
    "parse_connector_resource_reference",
    "require_current_authorization",
    "resolve_mapping",
    "revoke_mapping",
]
