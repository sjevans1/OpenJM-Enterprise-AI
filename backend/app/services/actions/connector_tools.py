"""Connector operations as governed VS6 tools.

Connector operations reach agent execution through the accepted VS6 registry and
runtime. There is no second execution engine: these are ordinary
:class:`~app.services.actions.registry.ToolSpec` registrations whose handlers are
invoked by ``runtime._run_step``, which means they inherit the whole VS6
security path for free, plan-time permission checks, the permissions
fingerprint, per-step re-validation immediately before execution, approval
consumption for writes, idempotency keys, budgets, timeouts and audit.

What these tools add on top of VS6 is the *connector* half of the authorization
question. VS6 answers "may this principal run this tool". A connector tool must
also answer "may this principal receive this external evidence", and that answer
is derived from the provider at execution time rather than from anything cached.
Both must be yes.

There is deliberately no generic connector tool. ``connector.invoke`` accepts an
operation *name* which must be declared by the connector type's registry spec and
must be a write operation; it cannot name an arbitrary URL, method or payload,
because no such argument exists. Read operations are served by the explicit read
tools, so an external mutation can never be smuggled through a read tool.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_principal
from app.core.permissions import Permission
from app.models import ConnectorInstance, ExternalResource, EXTERNAL_STATE_ACTIVE
from app.services.actions.registry import (
    OperationClass,
    ParameterSpec,
    RiskLevel,
    ToolRegistry,
    ToolSpec,
)
from app.services.connectors import sync as sync_engine
from app.services.connectors.authorization import authorize_resource
from app.services.connectors.base import ConnectorError, redact
from app.services.connectors.ingest import load_resource
from app.services.connectors.service import (
    ConnectorLockedError,
    build_context,
    get_implementation,
    resolve_instance,
    resolve_operation,
    spec_for,
)


def _refuse(category: str, detail: str = "") -> dict:
    return {"ok": False, "failure_category": category, "detail": redact(detail, limit=300)}


def _connector_public(instance: ConnectorInstance) -> dict:
    """Connector metadata safe to hand to a model. Never a config secret."""
    return {
        "connector_id": instance.id,
        "name": instance.name,
        "connector_type": instance.connector_type,
        "connector_version": instance.connector_version,
        "enabled": instance.enabled,
        "status": instance.status,
        "health_status": instance.health_status,
        "last_successful_sync_at": (
            instance.last_successful_sync_at.isoformat()
            if instance.last_successful_sync_at
            else None
        ),
        "last_failure_category": instance.last_failure_category,
    }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _connector_list(db: AsyncSession, arguments: dict) -> dict:
    principal = current_principal()
    result = await db.execute(
        select(ConnectorInstance)
        .where(ConnectorInstance.tenant_id == principal.tenant_id)
        .order_by(ConnectorInstance.name)
    )
    instances = list(result.scalars().all())
    return {
        "ok": True,
        "connectors": [_connector_public(instance) for instance in instances],
    }


async def _connector_status(db: AsyncSession, arguments: dict) -> dict:
    principal = current_principal()
    connector_id = str(arguments.get("connector_id") or "")
    try:
        instance = await resolve_instance(
            db, tenant_id=principal.tenant_id, connector_instance_id=connector_id
        )
    except ConnectorError as exc:
        return _refuse(exc.code)

    runs = await sync_engine.latest_runs(
        db, connector_instance_id=instance.id, tenant_id=principal.tenant_id, limit=5
    )
    payload = _connector_public(instance)
    payload["recent_runs"] = [sync_engine.run_to_dict(run) for run in runs]
    return {"ok": True, "connector": payload}


async def _connector_search(db: AsyncSession, arguments: dict) -> dict:
    """Search cached connector resources, revalidating current authorization.

    The search runs over connector-owned resources only. Every candidate is put
    through the current-authorization gate before it is returned, so a resource
    the mapped user can no longer see is not merely filtered from the output, it
    is quarantined on the way past.
    """
    principal = current_principal()
    query = str(arguments.get("query") or "").strip().lower()
    if not query:
        return _refuse("missing_argument", "a query is required")

    statement = select(ExternalResource).where(
        ExternalResource.tenant_id == principal.tenant_id,
        ExternalResource.lifecycle_state == EXTERNAL_STATE_ACTIVE,
    )
    connector_id = arguments.get("connector_id")
    if connector_id:
        statement = statement.where(ExternalResource.connector_instance_id == str(connector_id))
    result = await db.execute(statement.limit(200))
    candidates = list(result.scalars().all())

    matches = []
    denied = 0
    for resource in candidates:
        title = (resource.title or "").lower()
        if query not in title and query not in (resource.external_id or "").lower():
            continue
        outcome = await authorize_resource(
            db,
            tenant_id=principal.tenant_id,
            connector_instance_id=resource.connector_instance_id,
            resource=resource,
            principal_id=principal.principal_id,
        )
        if not outcome.allowed:
            denied += 1
            continue
        matches.append(
            {
                "connector_id": resource.connector_instance_id,
                "external_id": resource.external_id,
                "resource_type": resource.resource_type,
                "title": resource.title,
                "external_revision": resource.external_revision,
                "document_id": resource.document_id,
                "provenance": sync_engine.provenance_for(resource),
            }
        )
        if len(matches) >= 25:
            break

    return {
        "ok": True,
        "query": query,
        "matches": matches,
        "denied_count": denied,
        "note": "Only resources whose current user authorization was proven are returned.",
    }


async def _connector_fetch(db: AsyncSession, arguments: dict) -> dict:
    principal = current_principal()
    connector_id = str(arguments.get("connector_id") or "")
    external_id = str(arguments.get("external_id") or "")
    if not connector_id or not external_id:
        return _refuse("missing_argument", "connector_id and external_id are required")

    resource = await load_resource(
        db,
        connector_instance_id=connector_id,
        tenant_id=principal.tenant_id,
        external_id=external_id,
    )
    if resource is None:
        return _refuse("resource_unknown")

    outcome = await authorize_resource(
        db,
        tenant_id=principal.tenant_id,
        connector_instance_id=connector_id,
        resource=resource,
        principal_id=principal.principal_id,
    )
    if not outcome.allowed:
        # The reason is a safe category, never provider text.
        return _refuse(outcome.reason)

    return {
        "ok": True,
        "resource": {
            "external_id": resource.external_id,
            "resource_type": resource.resource_type,
            "title": resource.title,
            "external_revision": resource.external_revision,
            "document_id": resource.document_id,
            "provenance": sync_engine.provenance_for(resource),
        },
    }


async def _connector_sync(db: AsyncSession, arguments: dict) -> dict:
    """Trigger a bounded synchronization. Reads externally, mutates no external system."""
    principal = current_principal()
    connector_id = str(arguments.get("connector_id") or "")
    run_type = str(arguments.get("run_type") or "incremental")
    if run_type not in {"initial", "incremental", "reconcile"}:
        return _refuse("unsupported_run_type")
    try:
        instance = await resolve_instance(
            db, tenant_id=principal.tenant_id, connector_instance_id=connector_id
        )
    except ConnectorError as exc:
        return _refuse(exc.code)

    if not instance.enabled:
        # A disabled connector performs no external work, whatever the plan said.
        return _refuse("connector_disabled")

    run = await sync_engine.run_sync(
        db, instance=instance, actor=f"principal:{principal.principal_id}", run_type=run_type
    )
    return {"ok": True, "run": sync_engine.run_to_dict(run)}


async def _connector_invoke(db: AsyncSession, arguments: dict) -> dict:
    """Invoke a declared connector WRITE operation, approval already consumed.

    VS6 has already required and consumed an approval before this handler runs,
    and it re-validated the principal's permissions and the plan fingerprint
    immediately beforehand. This handler adds the connector-side revalidation:
    the instance must still belong to the caller's tenant, still be enabled, and
    still have a live credential, and the named operation must still be a
    declared write operation of the connector type.
    """
    principal = current_principal()
    connector_id = str(arguments.get("connector_id") or "")
    operation = str(arguments.get("operation") or "")
    raw_arguments = arguments.get("arguments_json")

    try:
        instance = await resolve_instance(
            db, tenant_id=principal.tenant_id, connector_instance_id=connector_id
        )
    except ConnectorError as exc:
        return _refuse(exc.code)

    if not instance.enabled:
        return _refuse("connector_disabled")

    try:
        spec, declared = resolve_operation(instance, operation)
    except ConnectorError as exc:
        # Includes the unregistered_operation refusal: resolve_operation
        # normalises the core registry error into a ConnectorError so the safe
        # category survives into the run record instead of degrading to a
        # generic tool_error inside the runtime.
        return _refuse(exc.code)

    if not declared.is_write:
        # Reads have their own tools. Sending a read through the approval-gated
        # mutation path would blur the read/write distinction that VS6 exists to
        # preserve, so it is refused rather than quietly allowed.
        return _refuse("read_operation_requires_read_tool")

    payload: dict = {}
    if raw_arguments:
        try:
            parsed = json.loads(raw_arguments)
        except (TypeError, ValueError):
            return _refuse("bad_arguments_json")
        if not isinstance(parsed, dict):
            return _refuse("bad_arguments_json")
        payload = parsed

    try:
        ctx = await build_context(db, instance)
    except ConnectorLockedError as exc:
        # A credential revoked or a connector disabled between planning and
        # execution lands here and refuses.
        return _refuse(exc.code)

    try:
        connector = get_implementation(spec.type_id, spec.version)
        result = await connector.execute_operation(ctx, operation, payload)
    except ConnectorError as exc:
        return _refuse(exc.code, exc.detail or "")
    except Exception as exc:  # noqa: BLE001 - a provider client must not crash the runtime
        return _refuse("tool_error", str(exc))

    return {"ok": True, "operation": operation, "result": result}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_connector_tools(registry: ToolRegistry) -> None:
    """Register the connector tool surface into the governed VS6 registry."""
    registry.register(
        ToolSpec(
            name="connector.list",
            description="List this tenant's configured connectors and their health.",
            operation_class=OperationClass.READ,
            risk_level=RiskLevel.LOW,
            required_permissions=frozenset({Permission.CONNECTOR_READ.value}),
            timeout_seconds=10,
        ),
        _connector_list,
    )
    registry.register(
        ToolSpec(
            name="connector.status",
            description=(
                "Show one connector's status and its most recent synchronization runs. "
                "Exposes operational state only, never credential material."
            ),
            operation_class=OperationClass.READ,
            risk_level=RiskLevel.LOW,
            required_permissions=frozenset({Permission.CONNECTOR_READ.value}),
            parameters=(ParameterSpec("connector_id", str, max_length=64),),
            timeout_seconds=10,
        ),
        _connector_status,
    )
    registry.register(
        ToolSpec(
            name="connector.search",
            description=(
                "Search resources synchronized from this tenant's connectors. Every "
                "result is revalidated against the provider for the calling user's "
                "current access, so revoked access cannot be served from a cache."
            ),
            operation_class=OperationClass.READ,
            risk_level=RiskLevel.MEDIUM,
            required_permissions=frozenset({Permission.CONNECTOR_READ.value}),
            parameters=(
                ParameterSpec("query", str, max_length=200),
                ParameterSpec("connector_id", str, required=False, max_length=64),
            ),
            timeout_seconds=20,
        ),
        _connector_search,
    )
    registry.register(
        ToolSpec(
            name="connector.fetch",
            description=(
                "Read one synchronized external resource by id, subject to the calling "
                "user's current authorization at the provider."
            ),
            operation_class=OperationClass.READ,
            risk_level=RiskLevel.MEDIUM,
            required_permissions=frozenset({Permission.CONNECTOR_READ.value}),
            parameters=(
                ParameterSpec("connector_id", str, max_length=64),
                ParameterSpec("external_id", str, max_length=255),
            ),
            timeout_seconds=20,
        ),
        _connector_fetch,
    )
    registry.register(
        ToolSpec(
            name="connector.sync",
            description=(
                "Run a bounded synchronization or reconciliation for one connector. "
                "Reads from the external system and refreshes local evidence. No "
                "external system is modified."
            ),
            operation_class=OperationClass.READ,
            risk_level=RiskLevel.MEDIUM,
            required_permissions=frozenset({Permission.CONNECTOR_WRITE.value}),
            parameters=(
                ParameterSpec("connector_id", str, max_length=64),
                ParameterSpec("run_type", str, required=False, max_length=16),
            ),
            timeout_seconds=60,
        ),
        _connector_sync,
    )
    registry.register(
        ToolSpec(
            name="connector.invoke",
            description=(
                "Invoke a declared write operation on a connector. External mutation: "
                "requires explicit human approval, and the operation must be one the "
                "connector type declares. Arbitrary calls are not expressible."
            ),
            operation_class=OperationClass.WRITE,
            risk_level=RiskLevel.HIGH,
            required_permissions=frozenset({Permission.CONNECTOR_WRITE.value}),
            parameters=(
                ParameterSpec("connector_id", str, max_length=64),
                ParameterSpec("operation", str, max_length=120),
                ParameterSpec("arguments_json", str, required=False, max_length=2000),
            ),
            timeout_seconds=30,
            requires_approval=True,
            reversible=False,
        ),
        _connector_invoke,
    )


CONNECTOR_TOOL_NAMES = (
    "connector.list",
    "connector.status",
    "connector.search",
    "connector.fetch",
    "connector.sync",
    "connector.invoke",
)


__all__ = ["CONNECTOR_TOOL_NAMES", "register_connector_tools"]
