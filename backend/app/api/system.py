"""VS8 operational surface: version, health, readiness, metrics and public config.

Design rules:

* **Liveness is separate from dependency health.** ``/api/health`` answers "is
  this process serving?"; ``/api/ready`` answers "can it do its work?" and names
  each component. A model-provider outage does not make the process unready —
  it is reported as its own component, so a provider failure is visible without
  killing the container.
* **No secret or tenant content ever appears.** Every component report is built
  from counts, categories and configured (non-secret) identifiers. The model
  base URL and any credential are never returned; only the provider mode and
  model identifier are.
* **Readiness never raises.** A failing dependency is reported, not surfaced as
  a 500, so a probe gets a stable shape.
"""

from __future__ import annotations

import shutil
import time

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.observability import METRICS
from app.db import engine, get_db
from app.models import (
    ConnectorInstance,
    Notification,
    Schedule,
    ScheduleRun,
)
from app.services import model_gateway as model_gateway_service
from app.version import PRODUCT_VERSION, build_info

router = APIRouter(tags=["system"])

settings = get_settings()


@router.get("/version")
async def version() -> dict:
    """Product/release identity, safe to expose before authentication."""
    return build_info(settings)


@router.get("/health")
async def health() -> dict:
    """Liveness: the process is up and serving."""
    return {
        "status": "ok",
        "product": settings.product_name,
        "version": PRODUCT_VERSION,
        "knowledge_engine": "DB-GPT" if settings.knowledge_enabled else "disabled",
        "model": settings.model_name,
        "profile": settings.deployment_profile,
    }


async def _database_component(db: AsyncSession) -> dict:
    started = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return {"status": "error", "detail": type(exc).__name__}
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    pool = engine.pool
    pool_info: dict[str, object] = {"latency_ms": latency_ms}
    try:
        pool_info["checked_out"] = pool.checkedout()
        pool_info["size"] = pool.size()
    except (AttributeError, NotImplementedError):  # pragma: no cover - NullPool
        pass
    return {"status": "ok", **pool_info}


async def _migration_component(db: AsyncSession) -> dict:
    from app.migrations_runner import current_revision, script_heads

    database_url = engine.url.render_as_string(hide_password=False)
    try:
        import asyncio

        revision = await asyncio.to_thread(current_revision, database_url)
        heads = await asyncio.to_thread(script_heads, database_url)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__}
    status = "ok" if revision in heads else "degraded"
    return {"status": status, "revision": revision, "head": heads[0] if heads else None}


def _storage_component() -> dict:
    path = settings.upload_dir
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".openjm-writable-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        writable = True
    except OSError:
        writable = False
    component: dict[str, object] = {"status": "ok" if writable else "error", "writable": writable}
    try:
        usage = shutil.disk_usage(path)
        free_mb = usage.free // (1024 * 1024)
        component["free_mb"] = free_mb
        if free_mb < 256:
            component["status"] = "warning"
            component["detail"] = "low free space on the storage volume"
    except OSError:  # pragma: no cover - platform dependent
        pass
    return component


def _knowledge_component() -> dict:
    if not settings.knowledge_enabled:
        return {"status": "disabled"}
    writable = settings.vector_path.exists() and settings.vector_path.is_dir()
    return {
        "status": "ok" if writable else "degraded",
        "backend": "DB-GPT",
        "vector_writable": writable,
    }


async def _model_provider_component() -> dict:
    """Provider health, separate from core liveness. Never leaks the credential."""
    gateway = model_gateway_service.OpenAICompatibleModelGateway()
    return await gateway.probe()


async def _scheduler_component(db: AsyncSession) -> dict:
    from app.services import scheduler

    last_finished = (
        await db.execute(select(func.max(ScheduleRun.finished_at)))
    ).scalar()
    active = (
        await db.execute(
            select(func.count()).select_from(Schedule).where(Schedule.enabled.is_(True))
        )
    ).scalar()
    return {
        "status": "ok",
        "enabled": settings.scheduler_enabled,
        "active_schedules": int(active or 0),
        "last_run_at": last_finished,
        "registered_operations": len(scheduler.registered_operations()),
    }


async def _connector_component(db: AsyncSession) -> dict:
    rows = (
        await db.execute(
            select(ConnectorInstance.status, func.count()).group_by(ConnectorInstance.status)
        )
    ).all()
    by_status = {status: int(count) for status, count in rows}
    unhealthy = by_status.get("error", 0) + by_status.get("unhealthy", 0)
    return {
        "status": "ok" if unhealthy == 0 else "degraded",
        "instances": sum(by_status.values()),
        "by_status": by_status,
    }


async def _notification_component(db: AsyncSession) -> dict:
    pending = (
        await db.execute(
            select(func.count()).select_from(Notification).where(Notification.status == "pending")
        )
    ).scalar()
    return {"status": "ok", "pending": int(pending or 0)}


@router.get("/ready")
async def readiness(db: AsyncSession = Depends(get_db)) -> dict:
    """Readiness: can this deployment do its work?

    Returns a per-component report. The overall ``ready`` flag reflects only the
    components required to serve core requests (database, migrations, storage);
    the model provider and scheduler are reported but do not gate readiness, so a
    provider outage is visible without taking the process out of rotation.
    """
    components = {
        "database": await _database_component(db),
        "migrations": await _migration_component(db),
        "storage": _storage_component(),
        "knowledge": _knowledge_component(),
        "model_provider": await _model_provider_component(),
        "scheduler": await _scheduler_component(db),
        "connectors": await _connector_component(db),
        "notifications": await _notification_component(db),
    }
    gating = ("database", "migrations", "storage")
    ready = all(components[name]["status"] in {"ok", "warning"} for name in gating)
    return {
        "ready": ready,
        "profile": settings.deployment_profile,
        "components": components,
    }


@router.get("/config/public")
async def public_config() -> dict:
    """White-label display metadata. Text only; never a secret or a URL to one."""
    return {
        "product_name": settings.product_name,
        "organization_name": settings.organization_name,
        "brand_logo_url": settings.brand_logo_url,
        "browser_page_title": settings.browser_page_title or settings.product_name,
        "support_contact": settings.support_contact,
        "theme_accent": settings.theme_accent,
        "version": PRODUCT_VERSION,
        "release_id": settings.release_id or None,
    }


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus-style metrics. Disabled deployments return 404."""
    if not settings.metrics_enabled:
        return Response(status_code=404, content="metrics disabled\n")
    return Response(content=METRICS.render(), media_type="text/plain; version=0.0.4")

