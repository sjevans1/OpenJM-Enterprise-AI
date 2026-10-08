"""M2 platform-plane usage metadata API (#46 M2).

Cross-tenant usage METADATA for explicit OpenJM platform operators, guarded by
``platform:metadata:read``. A tenant ``owner``/``admin`` holds no platform
capability and is refused here, so client administration can never become a
cross-tenant enumeration.

The payload is deliberately aggregate-only: per-tenant attempt/token counts and
per-source coverage. It returns no message text, no per-message identifiers, no
request ids and no credentials. Reading usage metadata is a separate concern
from exporting telemetry upstream to OpenJM; this module performs no outbound
call of any kind.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_platform
from app.core.identity import AuthorizationError, Principal
from app.core.platform import PlatformCapability
from app.db import get_db
from app.models import ModelUsageEvent
from app.services import usage_aggregation

router = APIRouter(prefix="/platform/usage", tags=["platform-usage"])


def _http_for(exc: Exception) -> HTTPException:
    """Map domain errors onto HTTP, matching the platform plane's convention."""
    if isinstance(exc, AuthorizationError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail="Invalid platform usage request")


@router.get("/tenants")
async def tenant_usage_metadata(
    limit: int = Query(default=usage_aggregation.DEFAULT_TENANTS_PAGE, ge=1),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
):
    """Per-tenant aggregate usage metadata, paginated with a bounded page size."""
    try:
        return await usage_aggregation.aggregate_by_tenant(db, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc


@router.get("/tenants/{tenant_id}")
async def one_tenant_usage_metadata(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
):
    """Aggregate usage metadata for one tenant; no content and no row identifiers."""
    try:
        aggregate = await usage_aggregation.aggregate_usage(
            db, tenant_id=tenant_id, period="total"
        )
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc

    has_usage = (
        await db.execute(
            select(func.count())
            .select_from(ModelUsageEvent)
            .where(ModelUsageEvent.tenant_id == tenant_id)
        )
    ).scalar_one()
    if not has_usage:
        raise HTTPException(status_code=404, detail="No usage for tenant")

    return {
        "generated_at": aggregate["generated_at"],
        "schema_version": aggregate["schema_version"],
        "attribution": aggregate["attribution"],
        "tenant_id": tenant_id,
        "usage": aggregate["totals"],
        "coverage": aggregate["coverage"],
    }
