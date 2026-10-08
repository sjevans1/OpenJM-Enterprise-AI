"""M3 platform-plane entitlement metadata API (#46 M3).

Cross-tenant, aggregate-only commercial metadata for explicit OpenJM platform
operators, guarded by ``platform:metadata:read``. A tenant ``owner``/``admin``
holds no platform capability and is refused here, so client administration can
never become a cross-tenant enumeration.

The payload is aggregate metadata only: counts and totals. It returns no tenant
identifier, no prompt, no response, no per-tenant row and no credentials. This
module is separate from ``app/api/platform.py`` on purpose: M3 adds its own
bounded route rather than editing the shared platform module.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_platform
from app.core.identity import AuthorizationError, Principal
from app.core.platform import PlatformCapability
from app.db import get_db
from app.services import entitlements

router = APIRouter(prefix="/platform/entitlements", tags=["platform-entitlements"])


def _http_for(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail="Invalid platform entitlement request")


@router.get("/metadata")
async def entitlement_metadata(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_platform(PlatformCapability.METADATA_READ)),
):
    """Aggregate entitlement metadata across tenants. No customer content."""
    try:
        return await entitlements.platform_entitlement_metadata(db)
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc
