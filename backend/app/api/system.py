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

import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.observability import METRICS
from app.db import get_db
from app.services import readiness as readiness_service
from app.version import PRODUCT_VERSION, build_info

router = APIRouter(tags=["system"])

settings = get_settings()


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def require_ops_access(request: Request) -> None:
    """Guard the detailed operational surface (metrics, readiness detail).

    A configured ``ops_token`` is required and compared in constant time.
    Without one the detailed endpoints are development-only: a production
    profile hides them (404) rather than exposing operational state to an
    unauthenticated caller. This is the trusted monitoring boundary; it stops an
    external probe from amplifying DB-wide counts or a remote model probe.
    """
    token = (settings.ops_token or "").strip()
    if token:
        supplied = _bearer(request) or ""
        if not secrets.compare_digest(supplied, token):
            raise HTTPException(
                status_code=401,
                detail="operational access requires a valid monitoring credential",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return
    if settings.is_production:
        raise HTTPException(status_code=404, detail="Not Found")


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





async def _public_readiness(db: AsyncSession) -> dict:
    """Minimal, cheap readiness: process + database reachability + storage.

    Deliberately avoids DB-wide counts, the migration runner and any remote
    model probe so an unauthenticated probe cannot amplify load.
    """
    database_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - reported, never raised
        database_ok = False
    try:
        storage_ok = settings.upload_dir.exists() and os.access(
            settings.upload_dir, os.W_OK
        )
    except OSError:  # pragma: no cover - platform dependent
        storage_ok = False
    ready = database_ok and storage_ok
    return {"ready": ready, "status": "ready" if ready else "degraded"}


@router.get("/ready")
async def readiness(db: AsyncSession = Depends(get_db)) -> dict:
    """Public readiness: a minimal, cheap go/no-go.

    The detailed per-component report (migrations, storage usage, model-provider
    probe, scheduler/connector/notification counts) is the trusted monitoring
    surface at ``/api/ready/detail``.
    """
    return await _public_readiness(db)


@router.get("/ready/detail")
async def readiness_detail(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_ops_access),
) -> dict:
    """Detailed readiness for trusted monitoring.

    Returns a per-component report. The overall ``ready`` flag reflects only the
    components required to serve core requests (database, migrations, storage);
    the model provider and scheduler are reported but do not gate readiness, so a
    provider outage is visible without taking the process out of rotation.

    The component builders live in ``app.services.readiness`` and are shared with
    the capability-gated operator console, so the machine probe and the console
    can never disagree about component health.
    """
    components = await readiness_service.readiness_components(db)
    return {
        "ready": readiness_service.overall_ready(components),
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
async def metrics(_: None = Depends(require_ops_access)) -> Response:
    """Prometheus-style metrics (trusted monitoring surface).

    Disabled deployments return 404; a production deployment without an
    ``ops_token`` hides the endpoint entirely.
    """
    if not settings.metrics_enabled:
        return Response(status_code=404, content="metrics disabled\n")
    return Response(content=METRICS.render(), media_type="text/plain; version=0.0.4")

