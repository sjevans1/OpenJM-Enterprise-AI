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

Denial is not merely a return value. Where cached content exists, a denial
quarantines it, so a stale authorization decision cannot survive revocation even
if some other code path would have admitted the document.

The single exception is a connector that declares
``AuthorizationBehavior.CONNECTOR_SCOPED``, which states by construction that
its provider has no per-user permission model. Such content is treated as
tenant-wide and is never attributed to an individual user.
"""

from __future__ import annotations

from dataclasses import dataclass

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
from app.services.connectors.ingest import quarantine_resource
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


@dataclass(frozen=True)
class AuthorizationOutcome:
    """The result of a current-authorization decision."""

    allowed: bool
    reason: str
    detail: str = ""
    quarantined: bool = False

    @staticmethod
    def deny(reason: str, detail: str = "", *, quarantined: bool = False) -> "AuthorizationOutcome":
        return AuthorizationOutcome(
            allowed=False, reason=reason, detail=detail, quarantined=quarantined
        )

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
    quarantine_on_deny: bool = True,
) -> AuthorizationOutcome:
    """Decide whether ``principal_id`` may currently receive this evidence.

    Every failure mode returns a denial. There is no branch that upgrades a
    failure into an allowance, and there is no reliance on a previously cached
    decision.
    """
    # A resource that is not currently active is not evidence at all.
    if resource.lifecycle_state == "deleted":
        return AuthorizationOutcome.deny("resource_deleted")
    if resource.lifecycle_state != EXTERNAL_STATE_ACTIVE:
        return AuthorizationOutcome.deny("resource_quarantined")

    try:
        instance = await resolve_instance(
            db, tenant_id=tenant_id, connector_instance_id=connector_instance_id
        )
    except ConnectorError:
        return AuthorizationOutcome.deny("connector_not_found")

    # The tenant boundary is re-asserted here rather than trusted from the
    # caller, so a resource row that somehow carries a foreign tenant cannot be
    # authorized for this principal.
    if instance.tenant_id != resource.tenant_id or instance.id != resource.connector_instance_id:
        return AuthorizationOutcome.deny("resource_instance_mismatch")

    if not instance.enabled or instance.status != CONNECTOR_STATUS_ACTIVE:
        return await _deny(
            db,
            resource=resource,
            reason="connector_disabled",
            quarantine_on_deny=quarantine_on_deny,
        )

    spec = spec_for(instance)

    if spec.authorization_behavior is AuthorizationBehavior.NONE:
        return await _deny(
            db,
            resource=resource,
            reason="authorization_unsupported",
            quarantine_on_deny=quarantine_on_deny,
        )

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
        return await _deny(
            db,
            resource=resource,
            reason="user_not_mapped",
            quarantine_on_deny=quarantine_on_deny,
        )

    try:
        ctx = await build_context(db, instance)
    except ConnectorError as exc:
        # Credential missing, revoked, unreadable, or the connector is not
        # enabled. All of these are refusals, never a fallback.
        return await _deny(
            db,
            resource=resource,
            reason=exc.code,
            detail=exc.detail or "",
            quarantine_on_deny=quarantine_on_deny,
        )

    try:
        connector = get_implementation(spec.type_id, spec.version)
    except ConnectorError as exc:
        return await _deny(
            db, resource=resource, reason=exc.code, quarantine_on_deny=quarantine_on_deny
        )

    try:
        allowed = await connector.check_user_access(
            ctx, external_user_id=mapping.external_user_id, external_id=resource.external_id
        )
    except ConnectorAuthUnavailable as exc:
        # Timeout, outage, rate limit or contradictory provider state. Current
        # authorization cannot be proven, so the evidence must not be served.
        return await _deny(
            db,
            resource=resource,
            reason=exc.code or "authorization_unavailable",
            detail=exc.detail or "",
            quarantine_on_deny=quarantine_on_deny,
        )
    except ConnectorError as exc:
        return await _deny(
            db,
            resource=resource,
            reason=exc.code,
            detail=exc.detail or "",
            quarantine_on_deny=quarantine_on_deny,
        )
    except Exception as exc:  # noqa: BLE001 - an unexpected provider failure is a deny
        return await _deny(
            db,
            resource=resource,
            reason="authorization_error",
            detail=str(exc)[:200],
            quarantine_on_deny=quarantine_on_deny,
        )

    if not allowed:
        return await _deny(
            db,
            resource=resource,
            reason="access_revoked",
            quarantine_on_deny=quarantine_on_deny,
        )

    return AuthorizationOutcome.allow()


async def _deny(
    db: AsyncSession,
    *,
    resource: ExternalResource,
    reason: str,
    detail: str = "",
    quarantine_on_deny: bool,
) -> AuthorizationOutcome:
    """Deny, and quarantine the cached content so the denial is durable."""
    quarantined = False
    if quarantine_on_deny and resource.lifecycle_state == EXTERNAL_STATE_ACTIVE:
        await quarantine_resource(db, resource=resource, reason=reason)
        quarantined = True
    return AuthorizationOutcome.deny(reason, detail, quarantined=quarantined)


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
        return AuthorizationOutcome.deny("resource_unknown")
    return await authorize_resource(
        db,
        tenant_id=tenant_id,
        connector_instance_id=connector_instance_id,
        resource=resource,
        principal_id=principal_id,
    )


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
    "MAX_AUTHORIZATION_CHECKS",
    "authorize_resource",
    "authorized_connector_document_ids",
    "authorized_connector_document_ids_for_context",
    "create_mapping",
    "list_mappings",
    "require_current_authorization",
    "resolve_mapping",
    "revoke_mapping",
]
