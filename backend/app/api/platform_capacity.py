"""INF1-C capacity telemetry API (application/platform layer).

Two authority planes share this module but never each other's data:

* the **platform operator** plane (``/platform/capacity``) requires an explicit
  platform capability. ``METADATA_READ`` may read bounded aggregate fleet and
  capacity metadata; ``INFERENCE_ADMIN`` may additionally pin a metric, record a
  sample and build/import an aggregate export. A tenant ``admin``/``owner`` holds
  no platform capability and is refused here.
* the **client administrator** plane (``/client/capacity``) requires the tenant
  ``tenant:admin`` permission and is scoped to the caller's own tenant by a SQL
  predicate. It returns only the tenant's own approved-service and usage
  summary: no fleet metadata, no cost or wholesale figure, and no other tenant.

The export surface is opt-in. While the export policy is disabled a build
request is refused (409) before any database read or socket, so a disabled
export performs zero outbound network attempts.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require, require_platform
from app.core.config import get_settings
from app.core.identity import AuthorizationError, Principal
from app.core.inference import ExportPolicyDisabled, TelemetryError
from app.core.permissions import Permission
from app.core.platform import PlatformCapability
from app.db import get_db
from app.models import InferenceMetricDefinition, PlatformTelemetryExport
from app.services.inference import serializer
from app.services.inference import telemetry

settings = get_settings()

platform_router = APIRouter(prefix="/platform/capacity", tags=["platform-capacity"])
client_router = APIRouter(prefix="/client/capacity", tags=["client-capacity"])
router = APIRouter()
router.include_router(platform_router)
router.include_router(client_router)

_PLATFORM_READ = require_platform(PlatformCapability.METADATA_READ)
_PLATFORM_ADMIN = require_platform(PlatformCapability.INFERENCE_ADMIN)
_TENANT_ADMIN = require(Permission.TENANT_ADMIN)


def _export_policy() -> serializer.ExportPolicy:
    """The effective export policy. Defaults to OFF with no side effects."""
    return serializer.ExportPolicy(
        enabled=bool(settings.telemetry_export_enabled),
        telemetry_policy_revision=settings.telemetry_policy_revision or None,
        signing_key_id=settings.telemetry_signing_key_id or None,
    )


def _http_for(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, ExportPolicyDisabled):
        # Refused, not failed: the export capability is switched off.
        return HTTPException(status_code=409, detail="aggregate telemetry export is disabled")
    if isinstance(exc, TelemetryError):
        return HTTPException(status_code=400, detail="Invalid telemetry value")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail="Invalid capacity telemetry request")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MetricDefinitionIn(BaseModel):
    metric_key: str
    metric_scope: str
    subject_kind: str
    value_kind: str
    unit: str
    origin: str
    gpu_method: str | None = None
    definition_revision: int = Field(default=1, ge=1)
    description: str | None = None


class MetricDefinitionOut(BaseModel):
    id: str
    metric_key: str
    definition_revision: int
    metric_scope: str
    subject_kind: str
    value_kind: str
    unit: str
    origin: str
    gpu_method: str | None
    cardinality_class: str
    status: str


class SampleIn(BaseModel):
    metric_key: str
    subject_kind: str
    origin: str
    window_start: datetime
    window_end: datetime
    value_int: int | None = None
    metric_revision: int | None = None
    subject_ref: str | None = None
    method: str = "unknown"
    quality: str = "unknown"
    tenant_id: str | None = None
    pool_id: str | None = None
    deployment_id: str | None = None
    attempt_id: str | None = None
    currency: str | None = None
    sequence: int = Field(default=0, ge=0)


class ExportIn(BaseModel):
    installation_id: str
    commercial_tenant_ref: str
    sequence: int = Field(ge=1)
    window_start: datetime
    window_end: datetime
    tenant_id: str
    export_id: str | None = None
    correction_of: str | None = None


# ---------------------------------------------------------------------------
# Platform operator plane
# ---------------------------------------------------------------------------


@platform_router.get("/metrics")
async def list_metrics(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_PLATFORM_READ),
):
    """The bounded catalogue of pinned metric definitions."""
    rows = (
        await db.execute(
            select(InferenceMetricDefinition).order_by(
                InferenceMetricDefinition.metric_key,
                InferenceMetricDefinition.definition_revision,
            )
        )
    ).scalars().all()
    return {
        "schema_version": telemetry.SCHEMA_VERSION,
        "metrics": [
            MetricDefinitionOut(
                id=row.id,
                metric_key=row.metric_key,
                definition_revision=row.definition_revision,
                metric_scope=row.metric_scope,
                subject_kind=row.subject_kind,
                value_kind=row.value_kind,
                unit=row.unit,
                origin=row.origin,
                gpu_method=row.gpu_method,
                cardinality_class=row.cardinality_class,
                status=row.status,
            ).model_dump()
            for row in rows
        ],
    }


@platform_router.post("/metrics", status_code=201)
async def define_metric(
    payload: MetricDefinitionIn,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_PLATFORM_ADMIN),
):
    """Pin one bounded-cardinality metric definition."""
    try:
        row = await telemetry.define_metric(
            db,
            metric_key=payload.metric_key,
            metric_scope=payload.metric_scope,
            subject_kind=payload.subject_kind,
            value_kind=payload.value_kind,
            unit=payload.unit,
            origin=payload.origin,
            gpu_method=payload.gpu_method,
            definition_revision=payload.definition_revision,
            description=payload.description,
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc
    return MetricDefinitionOut(
        id=row.id,
        metric_key=row.metric_key,
        definition_revision=row.definition_revision,
        metric_scope=row.metric_scope,
        subject_kind=row.subject_kind,
        value_kind=row.value_kind,
        unit=row.unit,
        origin=row.origin,
        gpu_method=row.gpu_method,
        cardinality_class=row.cardinality_class,
        status=row.status,
    )


@platform_router.post("/samples", status_code=201)
async def record_sample(
    payload: SampleIn,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_PLATFORM_ADMIN),
):
    """Record one pinned metric sample (idempotent on subject + window)."""
    try:
        row = await telemetry.record_sample(db, **payload.model_dump())
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc
    return {
        "id": row.id,
        "metric_key": row.metric_key,
        "metric_revision": row.metric_revision,
        "subject_kind": row.subject_kind,
        "subject_ref": row.subject_ref,
        "value_int": row.value_int,
        "counter_reset": row.counter_reset,
    }


@platform_router.get("/pools/{pool_id}/window")
async def pool_window(
    pool_id: str,
    window_start: datetime = Query(...),
    window_end: datetime = Query(...),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_PLATFORM_READ),
):
    """Pool-window capacity accounting with explicit idle/unattributed time."""
    try:
        return await telemetry.pool_window_accounting(
            db, pool_id=pool_id, window_start=window_start, window_end=window_end
        )
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc


@platform_router.get("/fleet")
async def fleet(
    window_start: datetime | None = Query(default=None),
    window_end: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_PLATFORM_READ),
):
    """Authorized fleet metadata and integer-minor-unit cost metadata."""
    try:
        return await telemetry.fleet_metadata(
            db, window_start=window_start, window_end=window_end
        )
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc


@platform_router.get("/reconciliation")
async def reconciliation(
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_PLATFORM_READ),
):
    """Reconcile one tenant's M1 totals, M2 attribution and telemetry samples."""
    try:
        return await telemetry.reconcile_usage(db, tenant_id=tenant_id)
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc


@platform_router.get("/correlations")
async def correlations(
    tenant_id: str = Query(...),
    window_start: datetime = Query(...),
    window_end: datetime = Query(...),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_PLATFORM_READ),
):
    """Attempt correlation against the INF1 attribution sidecar."""
    try:
        return await telemetry.correlate_attempts(
            db, tenant_id=tenant_id, window_start=window_start, window_end=window_end
        )
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc


@platform_router.get("/exports")
async def list_exports(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_PLATFORM_READ),
):
    """Recorded aggregate-only exports (metadata only, never a payload)."""
    rows = (
        await db.execute(
            select(PlatformTelemetryExport)
            .order_by(PlatformTelemetryExport.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {
        "schema_version": telemetry.SCHEMA_VERSION,
        "export_enabled": bool(settings.telemetry_export_enabled),
        "exports": [
            {
                "export_id": row.export_id,
                "installation_id": row.installation_id,
                "sequence": row.sequence,
                "schema_version": row.schema_version,
                "row_count": row.row_count,
                "payload_digest": row.payload_digest,
                "status": row.status,
            }
            for row in rows
        ],
    }


@platform_router.post("/exports", status_code=201)
async def build_export(
    payload: ExportIn,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_PLATFORM_ADMIN),
):
    """Build and record one aggregate-only export, or refuse when disabled."""
    try:
        built, record = await telemetry.build_export(
            db,
            policy=_export_policy(),
            installation_id=payload.installation_id,
            commercial_tenant_ref=payload.commercial_tenant_ref,
            sequence=payload.sequence,
            window_start=payload.window_start,
            window_end=payload.window_end,
            tenant_id=payload.tenant_id,
            export_id=payload.export_id,
            correction_of=payload.correction_of,
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc
    return {
        "export": {
            "export_id": record.export_id,
            "installation_id": record.installation_id,
            "sequence": record.sequence,
            "row_count": record.row_count,
            "payload_digest": record.payload_digest,
            "status": record.status,
        },
        "payload": built,
    }


@platform_router.post("/exports/import", status_code=201)
async def import_export(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_PLATFORM_ADMIN),
):
    """Validate and record one aggregate-only import (idempotent by export id)."""
    try:
        record = await telemetry.import_export(db, payload=payload)
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc
    return {
        "export_id": record.export_id,
        "installation_id": record.installation_id,
        "row_count": record.row_count,
        "payload_digest": record.payload_digest,
    }


# ---------------------------------------------------------------------------
# Client administrator plane
# ---------------------------------------------------------------------------


@client_router.get("/summary")
async def client_summary(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_TENANT_ADMIN),
):
    """The caller's own approved-service and usage summary, tenant-scoped only."""
    try:
        return await telemetry.own_service_summary(db, tenant_id=principal.tenant_id)
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc
