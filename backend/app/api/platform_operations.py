"""OPS1 OpenJM Control Center API (platform operator plane).

Every route here is guarded by an explicit ``platform:*`` capability, never by a
tenant role and never by the monitoring ``ops_token``. The browser therefore
never needs the ops token: the console reuses the same internal readiness,
usage and audit implementations server-side, behind platform capabilities.

Capability used by this module:

* ``platform:metadata:read`` for every read-only operator surface.

Tenant mutations, operator grants/revocations and support delegations stay in
``app.api.platform`` under ``platform:tenants:admin`` / ``platform:operators:admin``;
this module adds no mutation of its own, so a metadata observer can never obtain
write authority by reaching a new route.

Nothing in this module returns customer content, a message body, a document
passage, a model prompt or answer, a connector credential or a secret.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_platform
from app.core.config import get_settings
from app.core.identity import Principal
from app.core.platform import PlatformCapability
from app.db import get_db
from app.models import Tenant
from app.services import operator_console, usage_aggregation
from app.services import readiness as readiness_service
from app import version as version_module

router = APIRouter(prefix="/platform", tags=["platform-operations"])

settings = get_settings()


# ---------------------------------------------------------------------------
# Overview / health / releases
# ---------------------------------------------------------------------------


@router.get("/operations/overview")
async def operations_overview(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
) -> dict:
    """Live operator overview: release identity, health, model and findings.

    Reuses the shared readiness builders, so this view and ``/api/ready/detail``
    report the same component state. Contains no secret and no customer content.
    """
    components = await readiness_service.readiness_components(db)
    tenants = (await db.execute(select(func.count()).select_from(Tenant))).scalar_one()
    suspended = (
        await db.execute(
            select(func.count()).select_from(Tenant).where(Tenant.status == "suspended")
        )
    ).scalar_one()
    security = await operator_console.security_summary(db)

    model_component = components.get("model_provider", {})
    return {
        "release": version_module.build_info(settings),
        "auth_mode": settings.auth_mode,
        "profile": settings.deployment_profile,
        "ready": readiness_service.overall_ready(components),
        "components": components,
        "model": {
            "provider_mode": settings.model_provider_mode or "local",
            "model_name": settings.model_name,
            "fallback_policy": settings.model_provider_fallback,
            "configured": bool(settings.model_base_url or settings.model_provider_mode),
            "health": model_component.get("status"),
        },
        "fleet": {
            "tenants": int(tenants),
            "suspended": int(suspended),
            "operators": security["operator_count"],
            "active_content_delegations": security["active_content_delegations"],
        },
        "alerts": operator_console.derive_alerts(components, suspended_tenants=int(suspended)),
    }


@router.get("/operations/health")
async def operations_health(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
) -> dict:
    """The shared per-component readiness report, for the operator plane."""
    components = await readiness_service.readiness_components(db)
    return {
        "ready": readiness_service.overall_ready(components),
        "profile": settings.deployment_profile,
        "components": components,
    }


@router.get("/operations/releases")
async def operations_releases(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
) -> dict:
    """Release/deployment health for the current deployment.

    Modelled as a single-item deployment list so a future fleet can extend the
    shape without inventing deployments that do not exist today.
    """
    components = await readiness_service.readiness_components(db)
    migrations = components.get("migrations", {})
    info = version_module.build_info(settings)
    deployment = {
        "release_id": info.get("release_id"),
        "release_train": info.get("release_train"),
        "version": info.get("version"),
        "profile": settings.deployment_profile,
        "migration_revision": migrations.get("revision"),
        "migration_head": migrations.get("head"),
        "migration_drift": bool(migrations.get("drift")),
        "migration_status": migrations.get("status"),
        "model_name": settings.model_name,
    }
    return {
        "release": info,
        "deployments": [deployment],
        "deployment_count": 1,
    }


# ---------------------------------------------------------------------------
# Fleet / usage
# ---------------------------------------------------------------------------


@router.get("/operations/tenants")
async def operations_tenants(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
) -> dict:
    """Platform fleet view: tenant metadata joined to aggregate usage."""
    return await operator_console.tenant_fleet(db, limit=limit, offset=offset)


@router.get("/operations/usage")
async def operations_usage(
    limit: int = Query(default=usage_aggregation.DEFAULT_TENANTS_PAGE, ge=1),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
) -> dict:
    """Cross-tenant aggregate usage metadata (reuses the M2 aggregation layer)."""
    return await usage_aggregation.aggregate_by_tenant(db, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Audit explorer
# ---------------------------------------------------------------------------


@router.get("/audit")
async def audit_explorer(
    tenant_id: str | None = Query(default=None),
    principal_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    family: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    audit_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=operator_console.DEFAULT_AUDIT_PAGE, ge=1),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
) -> dict:
    """Bounded, filtered, paginated view of the persisted authoritative audit trail.

    The page size is capped server-side at ``MAX_AUDIT_PAGE`` regardless of the
    requested ``limit``. The free-form audit metadata is projected to a bounded
    allow-list, so a raw payload is never dumped to an operator screen.
    """
    return await operator_console.search_audit(
        db,
        tenant_id=tenant_id,
        principal_id=principal_id,
        action=action,
        family=family,
        decision=decision,
        resource_type=resource_type,
        resource_id=resource_id,
        audit_id=audit_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.get("/audit/summary")
async def audit_summary(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
) -> dict:
    """Recent-activity summary across the audit event families."""
    return await operator_console.audit_summary(db)


# ---------------------------------------------------------------------------
# Security / diagnostics
# ---------------------------------------------------------------------------


@router.get("/security")
async def security_operations(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
) -> dict:
    """Security operations summary built from safe metadata only.

    Names capabilities, scopes and counts. A platform operator without
    ``platform:content:support`` remains unable to read customer content; this
    route grants nothing and exposes nothing beyond metadata.
    """
    return await operator_console.security_summary(db)


@router.get("/diagnostics")
async def diagnostics(
    correlation_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    tool_name: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    limit: int = Query(default=operator_console.DEFAULT_TRACE_PAGE, ge=1),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
) -> dict:
    """Bounded operational telemetry: readiness, safe traces and failure categories.

    Traces are projected field-by-field. Plan/executed SQL, the input hash and
    free-form trace metadata are never returned, because a SQL string can carry a
    customer value. The operational-log integration status is reported rather than
    inventing an unsafe raw-log table.
    """
    return await operator_console.diagnostics(
        db,
        correlation_id=correlation_id,
        status=status,
        tool_name=tool_name,
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
    )
