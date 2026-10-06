"""Connector instance lifecycle and the credential boundary.

Two invariants live here, and everything else in VS7 depends on them:

1. **Tenant isolation.** Every read and write resolves the connector instance
   through a `tenant_id` predicate. A connector id belonging to another tenant
   is indistinguishable from one that does not exist, so it cannot be used,
   enumerated, or probed for existence.

2. **Credentials are references, never values.** The plaintext credential exists
   only inside the :class:`~app.services.connectors.base.ConnectorContext`
   handed to one operation. It is never returned by the API, never written to
   audit metadata, never placed in an error message, and a revoked credential
   resolves to a refusal rather than to a stale value.

Every mutating lifecycle operation writes an audit record. The API layer commits
only after the audit row is added, so an operator action that changes connector
state is never unattributable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.connectors import ConnectorRegistryError, connector_registry
from app.models import (
    CONNECTOR_STATUS_ACTIVE,
    CONNECTOR_STATUS_CONFIGURED,
    CONNECTOR_STATUS_DISCONNECTED,
    CONNECTOR_STATUS_DISABLED,
    CONNECTOR_STATUS_ERROR,
    ConnectorCredential,
    ConnectorInstance,
    ExternalResource,
)
from app.services.connectors.base import Connector, ConnectorContext, ConnectorError, redact
from app.services.credentials import CredentialVaultError, credential_vault
from app.services.identity import record_audit, utcnow


class ConnectorLockedError(ConnectorError):
    """A lifecycle operation was refused because the connector is not usable."""

    def __init__(self, message: str, *, code: str = "connector_unusable"):
        super().__init__(message, code=code)


SYSTEM_PRINCIPAL = "system:connector"


def resource_namespace(connector_instance_id: str) -> str:
    """The explicit namespace for every resource a connector instance owns.

    Namespacing is what guarantees a connector resource can never collide with
    an uploaded document, a structured data source, another connector, or
    another tenant: identity is (tenant, instance, namespace, external_id) and
    the namespace itself is derived from the instance id.
    """
    return f"connector:{connector_instance_id}"


# ---------------------------------------------------------------------------
# Connector implementations
#
# Registration is explicit, exactly like the connector registry. A module that
# is merely importable cannot introduce network access.
# ---------------------------------------------------------------------------

_IMPLEMENTATIONS: dict[str, Connector] = {}


def register_implementation(connector: Connector) -> None:
    key = f"{connector.type_id}@{connector.version}"
    existing = _IMPLEMENTATIONS.get(key)
    if existing is not None and type(existing) is not type(connector):
        raise ConnectorError(
            f"connector implementation {key} is already registered",
            code="duplicate_implementation",
        )
    _IMPLEMENTATIONS[key] = connector


def get_implementation(type_id: str, version: str) -> Connector:
    key = f"{type_id}@{version}"
    connector = _IMPLEMENTATIONS.get(key)
    if connector is None:
        raise ConnectorError(
            f"no implementation is registered for connector {key}",
            code="implementation_missing",
        )
    return connector


def registered_implementations() -> tuple[str, ...]:
    return tuple(sorted(_IMPLEMENTATIONS))


# ---------------------------------------------------------------------------
# Resolution (the tenant boundary)
# ---------------------------------------------------------------------------


async def resolve_instance(
    db: AsyncSession, *, tenant_id: str, connector_instance_id: str
) -> ConnectorInstance:
    """Load a connector instance inside one tenant, or refuse.

    A row belonging to a different tenant raises the same error as a row that
    does not exist, so a caller cannot use this to test whether a foreign
    connector id is real.
    """
    result = await db.execute(
        select(ConnectorInstance).where(
            ConnectorInstance.id == connector_instance_id,
            ConnectorInstance.tenant_id == tenant_id,
        )
    )
    instance = result.scalars().first()
    if instance is None:
        raise ConnectorError("connector instance not found", code="connector_not_found")
    return instance


def spec_for(instance: ConnectorInstance):
    """Resolve the declared spec. Unknown type/version is a hard refusal."""
    try:
        return connector_registry.get(instance.connector_type, instance.connector_version)
    except ConnectorRegistryError as exc:
        raise ConnectorError(str(exc), code="unknown_connector_type") from exc


def resolve_operation(instance: ConnectorInstance, operation: str):
    """Resolve a declared operation, normalising registry errors into ConnectorError.

    :class:`~app.core.connectors.ConnectorRegistryError` deliberately lives in the
    dependency-free core module and is therefore not a ``ConnectorError``. Every
    service-layer caller must still see one uniform refusal type, because a raw
    registry error escaping a tool handler gets recorded by the VS6 runtime as a
    generic ``tool_error`` instead of the specific ``unregistered_operation``
    category, which loses the safe audit attribution even though the call is
    still refused. This helper is the single place that conversion happens.
    """
    spec = spec_for(instance)
    try:
        return spec, spec.operation(operation)
    except ConnectorRegistryError as exc:
        raise ConnectorError(str(exc), code="unregistered_operation") from exc


async def resolve_credential(
    db: AsyncSession, instance: ConnectorInstance
) -> ConnectorCredential:
    """Return the *active* credential for an instance, or refuse.

    There is no fallback to a superseded credential and no fallback to a revoked
    one. Revocation is therefore fail-closed by construction rather than by
    convention: the row simply stops resolving.
    """
    if not instance.credential_id:
        raise ConnectorLockedError(
            "connector has no credential configured", code="credential_missing"
        )
    result = await db.execute(
        select(ConnectorCredential).where(
            ConnectorCredential.id == instance.credential_id,
            ConnectorCredential.tenant_id == instance.tenant_id,
            ConnectorCredential.connector_instance_id == instance.id,
            ConnectorCredential.status == "active",
        )
    )
    credential = result.scalars().first()
    if credential is None:
        # Either revoked, superseded without repointing, or foreign. All three
        # must behave identically to a caller.
        raise ConnectorLockedError(
            "connector credential is not active", code="credential_revoked"
        )
    return credential


def _decrypt_fields(credential: ConnectorCredential) -> dict[str, str]:
    try:
        payload = credential_vault.decrypt(credential.secret_ciphertext)
    except CredentialVaultError as exc:
        # Tampered or unreadable ciphertext is a refusal, never a fallback.
        raise ConnectorLockedError(
            "connector credential could not be read", code="credential_unreadable"
        ) from exc
    try:
        fields = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ConnectorLockedError(
            "connector credential is malformed", code="credential_malformed"
        ) from exc
    if not isinstance(fields, dict):
        raise ConnectorLockedError(
            "connector credential is malformed", code="credential_malformed"
        )
    return {str(key): str(value) for key, value in fields.items()}


async def build_context(db: AsyncSession, instance: ConnectorInstance) -> ConnectorContext:
    """Assemble the context for one operation, proving usability on the way."""
    if not instance.enabled or instance.status != CONNECTOR_STATUS_ACTIVE:
        raise ConnectorLockedError(
            "connector is not enabled", code="connector_disabled"
        )
    spec = spec_for(instance)
    credential = await resolve_credential(db, instance)
    config: dict = {}
    if instance.config_json:
        try:
            parsed = json.loads(instance.config_json)
            if isinstance(parsed, dict):
                config = parsed
        except (TypeError, ValueError):
            config = {}
    return ConnectorContext(
        tenant_id=instance.tenant_id,
        connector_instance_id=instance.id,
        connector_type=instance.connector_type,
        config=config,
        credential=_decrypt_fields(credential),
        base_url=config.get("base_url"),
        timeout_seconds=int(config.get("timeout_seconds") or spec.timeout_seconds),
    )


async def build_context_unchecked(
    db: AsyncSession, instance: ConnectorInstance
) -> ConnectorContext:
    """Assemble a context for a health check on a connector that is not enabled.

    Testing a connection is a configuration-time operation: an operator must be
    able to prove a credential works *before* enabling the connector. This never
    grants retrieval or execution, because every other entry point calls
    :func:`build_context`, which requires the enabled state.
    """
    spec = spec_for(instance)
    credential = await resolve_credential(db, instance)
    config: dict = {}
    if instance.config_json:
        try:
            parsed = json.loads(instance.config_json)
            if isinstance(parsed, dict):
                config = parsed
        except (TypeError, ValueError):
            config = {}
    return ConnectorContext(
        tenant_id=instance.tenant_id,
        connector_instance_id=instance.id,
        connector_type=instance.connector_type,
        config=config,
        credential=_decrypt_fields(credential),
        base_url=config.get("base_url"),
        timeout_seconds=int(config.get("timeout_seconds") or spec.timeout_seconds),
    )


# ---------------------------------------------------------------------------
# Lifecycle operations
# ---------------------------------------------------------------------------


@dataclass
class LifecycleResult:
    instance: ConnectorInstance
    detail: str = ""
    ok: bool = True


async def configure_instance(
    db: AsyncSession,
    *,
    tenant_id: str,
    actor: str,
    name: str,
    connector_type: str,
    version: str | None = None,
    config: dict | None = None,
    credential: dict | None = None,
    credential_label: str = "primary",
) -> ConnectorInstance:
    """Create a connector instance, its credential, and its audit trail.

    The instance starts ``configured`` and **disabled**. Enabling is a separate,
    separately-audited decision, which is what keeps an opt-in connector from
    becoming live merely because it was configured.
    """
    try:
        spec = connector_registry.get(connector_type, version)
    except ConnectorRegistryError as exc:
        raise ConnectorError(str(exc), code="unknown_connector_type") from exc

    instance = ConnectorInstance(
        tenant_id=tenant_id,
        name=name,
        connector_type=spec.type_id,
        connector_version=spec.version,
        display_name=spec.display_name,
        status=CONNECTOR_STATUS_CONFIGURED,
        enabled=False,
        config_json=json.dumps(config or {}),
        created_by=actor,
    )
    db.add(instance)
    await db.flush()

    if credential is not None:
        stored = ConnectorCredential(
            tenant_id=tenant_id,
            connector_instance_id=instance.id,
            label=credential_label,
            kind=spec.credential_kind,
            secret_ciphertext=credential_vault.encrypt(json.dumps(credential)),
            status="active",
            version=1,
            created_by=actor,
        )
        db.add(stored)
        await db.flush()
        instance.credential_id = stored.id

    await record_audit(
        db,
        principal=None,
        tenant_id=tenant_id,
        action="connector.configure",
        decision="allow",
        resource_type="connector_instance",
        resource_id=instance.id,
        metadata={"connector_type": spec.type_id, "version": spec.version, "actor": actor},
    )
    return instance


async def set_enabled(
    db: AsyncSession, *, instance: ConnectorInstance, enabled: bool, actor: str
) -> ConnectorInstance:
    """Enable or disable a connector. Disabling is the authorization switch."""
    if enabled:
        spec = spec_for(instance)
        # An enabled connector must be usable: a credential has to resolve, and
        # the implementation has to exist. Enabling something that cannot work
        # would produce a connector that fails later instead of now.
        if not instance.credential_id:
            raise ConnectorLockedError(
                "configure a credential before enabling this connector",
                code="credential_missing",
            )
        await resolve_credential(db, instance)
        get_implementation(spec.type_id, spec.version)
        instance.enabled = True
        instance.status = CONNECTOR_STATUS_ACTIVE
        decision = "allow"
        action = "connector.enable"
    else:
        instance.enabled = False
        instance.status = CONNECTOR_STATUS_DISABLED
        decision = "allow"
        action = "connector.disable"

    instance.updated_at = utcnow()
    await record_audit(
        db,
        principal=None,
        tenant_id=instance.tenant_id,
        action=action,
        decision=decision,
        resource_type="connector_instance",
        resource_id=instance.id,
        metadata={"actor": actor},
    )
    return instance


async def test_connection(
    db: AsyncSession, *, instance: ConnectorInstance, actor: str
) -> LifecycleResult:
    """Ask the provider whether the configuration and credential work."""
    spec = spec_for(instance)
    if not spec.supports_test_connection:
        raise ConnectorError(
            "this connector type does not support a connection test",
            code="test_connection_unsupported",
        )
    try:
        connector = get_implementation(spec.type_id, spec.version)
        ctx = await build_context_unchecked(db, instance)
        result = await connector.test_connection(ctx)
        ok = bool(result.get("ok"))
        detail = redact(result.get("detail", ""))
    except ConnectorError as exc:
        ok = False
        detail = redact(exc.detail or str(exc))
    except Exception as exc:  # noqa: BLE001 - a provider client must not crash the API
        ok = False
        detail = redact(str(exc))

    instance.health_status = "healthy" if ok else "unhealthy"
    instance.health_detail = detail or None
    if ok:
        instance.last_successful_connection_at = utcnow()
        instance.last_failure_category = None
    else:
        instance.last_failure_category = "connection_test_failed"
        instance.last_failure_at = utcnow()
    instance.updated_at = utcnow()

    await record_audit(
        db,
        principal=None,
        tenant_id=instance.tenant_id,
        action="connector.test_connection",
        decision="allow" if ok else "deny",
        resource_type="connector_instance",
        resource_id=instance.id,
        reason=None if ok else instance.last_failure_category,
        metadata={"actor": actor},
    )
    return LifecycleResult(instance=instance, detail=detail, ok=ok)


async def rotate_credential(
    db: AsyncSession,
    *,
    instance: ConnectorInstance,
    actor: str,
    credential: dict,
    label: str | None = None,
) -> ConnectorCredential:
    """Replace the active credential without recreating the connector.

    The previous row is superseded rather than deleted so the rotation is
    auditable, and the instance is repointed at the new row. Nothing about the
    connector instance identity, its cached resources, its cursors or its
    schedules changes.
    """
    spec = spec_for(instance)
    previous_id = instance.credential_id

    next_version = 1
    if previous_id:
        result = await db.execute(
            select(ConnectorCredential).where(ConnectorCredential.id == previous_id)
        )
        previous = result.scalars().first()
        if previous is not None:
            next_version = int(previous.version) + 1
            previous.status = "superseded"
            previous.rotated_at = utcnow()

    stored = ConnectorCredential(
        tenant_id=instance.tenant_id,
        connector_instance_id=instance.id,
        label=label or "primary",
        kind=spec.credential_kind,
        secret_ciphertext=credential_vault.encrypt(json.dumps(credential)),
        status="active",
        version=next_version,
        created_by=actor,
        rotated_at=utcnow(),
    )
    db.add(stored)
    await db.flush()
    instance.credential_id = stored.id
    instance.last_failure_category = None
    instance.updated_at = utcnow()

    await record_audit(
        db,
        principal=None,
        tenant_id=instance.tenant_id,
        action="connector.rotate_credential",
        decision="allow",
        resource_type="connector_instance",
        resource_id=instance.id,
        metadata={"actor": actor, "version": next_version},
    )
    return stored


async def revoke_credential(
    db: AsyncSession, *, instance: ConnectorInstance, actor: str
) -> int:
    """Revoke every credential of an instance.

    Returns the number revoked. The connector is disabled at the same time: a
    connector whose credential is gone must not keep serving cached evidence,
    so revocation is a state change and not merely a credential flag.
    """
    result = await db.execute(
        select(ConnectorCredential).where(
            ConnectorCredential.connector_instance_id == instance.id,
            ConnectorCredential.tenant_id == instance.tenant_id,
            ConnectorCredential.status != "revoked",
        )
    )
    credentials = list(result.scalars().all())
    for credential in credentials:
        credential.status = "revoked"
        credential.revoked_at = utcnow()
        credential.revoked_by = actor

    instance.enabled = False
    instance.status = CONNECTOR_STATUS_ERROR
    instance.health_status = "unhealthy"
    instance.last_failure_category = "credential_revoked"
    instance.last_failure_at = utcnow()
    instance.updated_at = utcnow()

    await record_audit(
        db,
        principal=None,
        tenant_id=instance.tenant_id,
        action="connector.revoke_credential",
        decision="allow",
        resource_type="connector_instance",
        resource_id=instance.id,
        metadata={"actor": actor, "revoked": len(credentials)},
    )
    return len(credentials)


async def disconnect(
    db: AsyncSession, *, instance: ConnectorInstance, actor: str, purge: bool = False
) -> int:
    """Disconnect a connector, quarantining (or purging) its cached content.

    Disconnecting always quarantines: cached external content stops being
    retrievable immediately. ``purge=True`` additionally removes the cached rows
    and their Knowledge documents, which is the destructive tier.
    """
    instance.enabled = False
    instance.status = CONNECTOR_STATUS_DISCONNECTED
    instance.health_status = "unknown"
    instance.updated_at = utcnow()

    quarantined = await quarantine_instance_resources(
        db, instance=instance, reason="connector_disconnected"
    )

    purged = 0
    if purge:
        purged = await purge_instance_resources(db, instance=instance)

    await record_audit(
        db,
        principal=None,
        tenant_id=instance.tenant_id,
        action="connector.disconnect",
        decision="allow",
        resource_type="connector_instance",
        resource_id=instance.id,
        metadata={"actor": actor, "quarantined": quarantined, "purged": purged},
    )
    return quarantined


async def list_instances(
    db: AsyncSession, *, tenant_id: str
) -> list[ConnectorInstance]:
    result = await db.execute(
        select(ConnectorInstance)
        .where(ConnectorInstance.tenant_id == tenant_id)
        .order_by(ConnectorInstance.name)
    )
    return list(result.scalars().all())


async def quarantine_instance_resources(
    db: AsyncSession, *, instance: ConnectorInstance, reason: str
) -> int:
    """Quarantine every active resource of an instance.

    Quarantine marks cached content as non-retrievable without deleting it, so
    reconciliation can restore it once authorization is provable again. This is
    a local import to avoid a cycle: the ingest module owns the Knowledge-side
    flip, and this module owns the connector-side bookkeeping.
    """
    from app.services.connectors.ingest import quarantine_resource

    result = await db.execute(
        select(ExternalResource).where(
            ExternalResource.connector_instance_id == instance.id,
            ExternalResource.tenant_id == instance.tenant_id,
            ExternalResource.lifecycle_state != "deleted",
        )
    )
    resources = list(result.scalars().all())
    for resource in resources:
        await quarantine_resource(db, resource=resource, reason=reason)
    return len(resources)


async def purge_instance_resources(db: AsyncSession, *, instance: ConnectorInstance) -> int:
    """Delete cached connector rows and their Knowledge documents."""
    from app.services.connectors.ingest import delete_resource_content

    result = await db.execute(
        select(ExternalResource).where(
            ExternalResource.connector_instance_id == instance.id,
            ExternalResource.tenant_id == instance.tenant_id,
        )
    )
    resources = list(result.scalars().all())
    for resource in resources:
        await delete_resource_content(db, resource=resource)
        await db.delete(resource)
    await db.flush()
    return len(resources)


__all__ = [
    "ConnectorLockedError",
    "LifecycleResult",
    "build_context",
    "build_context_unchecked",
    "configure_instance",
    "disconnect",
    "get_implementation",
    "list_instances",
    "purge_instance_resources",
    "quarantine_instance_resources",
    "register_implementation",
    "registered_implementations",
    "resolve_credential",
    "resolve_instance",
    "resolve_operation",
    "resource_namespace",
    "revoke_credential",
    "rotate_credential",
    "set_enabled",
    "spec_for",
    "test_connection",
]
