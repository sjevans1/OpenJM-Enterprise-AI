"""Reusable readiness component inspection (VS8, shared by both planes).

The per-component readiness report was originally composed inside the system
router. OPS1 exposes the same report through the capability-gated platform
operator plane, so the builders live here and both callers import them. There is
one implementation of "is the database reachable", "is the schema at head" and
so on, so a machine probe and the operator console can never disagree.

Design rules preserved from the original surface:

* a failing dependency is reported, never raised, so a caller always gets a
  stable shape;
* no secret ever appears: components are built from counts, categories and
  configured (non-secret) identifiers only. The model base URL and any
  credential are never returned.
"""

from __future__ import annotations

import shutil
import time

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db import engine
from app.models import ConnectorInstance, Notification, Schedule, ScheduleRun

settings = get_settings()

# Components whose health determines overall readiness. The model provider and
# scheduler are reported but do not gate, so a provider outage is visible
# without taking the process out of rotation.
GATING_COMPONENTS: tuple[str, ...] = ("database", "migrations", "storage")

# Component status vocabulary shared with the operator console.
STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_DEGRADED = "degraded"
STATUS_ERROR = "error"
STATUS_DISABLED = "disabled"


async def database_component(db: AsyncSession) -> dict:
    started = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return {"status": STATUS_ERROR, "detail": type(exc).__name__}
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    pool = engine.pool
    pool_info: dict[str, object] = {"latency_ms": latency_ms}
    try:
        pool_info["checked_out"] = pool.checkedout()
        pool_info["size"] = pool.size()
    except (AttributeError, NotImplementedError):  # pragma: no cover - NullPool
        pass
    return {"status": STATUS_OK, **pool_info}


async def migration_component(db: AsyncSession) -> dict:
    from app.migrations_runner import current_revision, script_heads

    database_url = engine.url.render_as_string(hide_password=False)
    try:
        import asyncio

        revision = await asyncio.to_thread(current_revision, database_url)
        heads = await asyncio.to_thread(script_heads, database_url)
    except Exception as exc:  # noqa: BLE001
        return {"status": STATUS_ERROR, "detail": type(exc).__name__}
    status = STATUS_OK if revision in heads else STATUS_DEGRADED
    return {
        "status": status,
        "revision": revision,
        "head": heads[0] if heads else None,
        "drift": revision not in heads,
    }


def storage_component() -> dict:
    path = settings.upload_dir
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".openjm-writable-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        writable = True
    except OSError:
        writable = False
    component: dict[str, object] = {
        "status": STATUS_OK if writable else STATUS_ERROR,
        "writable": writable,
    }
    try:
        usage = shutil.disk_usage(path)
        free_mb = usage.free // (1024 * 1024)
        component["free_mb"] = free_mb
        if free_mb < 256:
            component["status"] = STATUS_WARNING
            component["detail"] = "low free space on the storage volume"
    except OSError:  # pragma: no cover - platform dependent
        pass
    return component


def knowledge_component() -> dict:
    if not settings.knowledge_enabled:
        return {"status": STATUS_DISABLED}
    writable = settings.vector_path.exists() and settings.vector_path.is_dir()
    return {
        "status": STATUS_OK if writable else STATUS_DEGRADED,
        "backend": "DB-GPT",
        "vector_writable": writable,
    }


async def model_provider_component() -> dict:
    """Provider health, separate from core liveness. Never leaks the credential."""
    from app.services import model_gateway as model_gateway_service

    gateway = model_gateway_service.OpenAICompatibleModelGateway()
    return await gateway.probe()


async def scheduler_component(db: AsyncSession) -> dict:
    from app.services import scheduler

    last_finished = (await db.execute(select(func.max(ScheduleRun.finished_at)))).scalar()
    active = (
        await db.execute(
            select(func.count()).select_from(Schedule).where(Schedule.enabled.is_(True))
        )
    ).scalar()
    return {
        "status": STATUS_OK,
        "enabled": settings.scheduler_enabled,
        "active_schedules": int(active or 0),
        "last_run_at": last_finished,
        "registered_operations": len(scheduler.registered_operations()),
    }


async def connector_component(db: AsyncSession) -> dict:
    rows = (
        await db.execute(
            select(ConnectorInstance.status, func.count()).group_by(ConnectorInstance.status)
        )
    ).all()
    by_status = {status: int(count) for status, count in rows}
    unhealthy = by_status.get("error", 0) + by_status.get("unhealthy", 0)
    return {
        "status": STATUS_OK if unhealthy == 0 else STATUS_DEGRADED,
        "instances": sum(by_status.values()),
        "by_status": by_status,
    }


async def notification_component(db: AsyncSession) -> dict:
    pending = (
        await db.execute(
            select(func.count()).select_from(Notification).where(Notification.status == "pending")
        )
    ).scalar()
    return {"status": STATUS_OK, "pending": int(pending or 0)}


async def readiness_components(db: AsyncSession) -> dict[str, dict]:
    """Build the full per-component readiness report (safe fields only)."""
    return {
        "database": await database_component(db),
        "migrations": await migration_component(db),
        "storage": storage_component(),
        "knowledge": knowledge_component(),
        "model_provider": await model_provider_component(),
        "scheduler": await scheduler_component(db),
        "connectors": await connector_component(db),
        "notifications": await notification_component(db),
    }


def overall_ready(components: dict[str, dict]) -> bool:
    """Readiness gated only by the components required to serve core requests."""
    return all(
        components.get(name, {}).get("status") in {STATUS_OK, STATUS_WARNING}
        for name in GATING_COMPONENTS
    )
