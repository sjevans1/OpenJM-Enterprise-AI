"""FastAPI dependencies for authenticated, tenant-scoped requests.

Every protected route depends on :func:`get_principal` or, more usually, on a
:func:`require` guard built from it. Authorization is therefore enforced in the
server layer for each operation, never only in the UI.

Tenant selection is explicit:

* the credential may carry a tenant (OIDC claim, or an OpenJM session bound to a
  tenant), and
* a caller may *request* a tenant with the ``X-OpenJM-Tenant`` header.

A requested tenant is only honoured when the principal holds an **active
membership** in it. The header can narrow scope, never widen it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.context import set_principal
from app.core.identity import (
    AuthenticationError,
    AuthorizationError,
    IdentityError,
    Permission,
    Principal,
)
from app.db import get_db
from app.services import identity as identity_service

settings = get_settings()

TENANT_HEADER = "X-OpenJM-Tenant"


def _www_authenticate(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _to_http(exc: IdentityError) -> HTTPException:
    if isinstance(exc, AuthenticationError):
        return _www_authenticate(exc.message)
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def get_principal(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_openjm_tenant: str | None = Header(default=None, alias=TENANT_HEADER),
) -> Principal:
    bearer = _bearer(request)
    dev = bearer is None and settings.auth_mode == "dev"
    try:
        principal = await identity_service.authenticate(
            db, bearer=bearer, dev_principal=dev
        )
        if x_openjm_tenant and x_openjm_tenant != principal.tenant_id:
            # Re-resolve against the requested tenant: this fails closed unless
            # the principal has an active membership there.
            tenant_id = await identity_service.resolve_tenant_id(db, x_openjm_tenant)
            account = await identity_service.get_or_create_principal(
                db,
                subject=principal.subject,
                principal_id=principal.principal_id,
                issuer=None,
            )
            principal = await identity_service.principal_for_account(
                db, account, tenant_id=tenant_id, auth_method=principal.auth_method
            )
    except IdentityError as exc:
        raise _to_http(exc) from exc

    # Publish the validated principal for the request so service-layer helpers
    # can apply ownership predicates without re-deriving the identity.
    set_principal(principal)
    return principal


def require(*permissions: Permission) -> Callable[..., Awaitable[Principal]]:
    """Guard a route on one or more permissions.

    Used as ``principal: Principal = Depends(require(Permission.DATA_READ))``.
    """

    async def _guard(
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ) -> Principal:
        try:
            principal.require_all(permissions)
        except AuthorizationError as exc:
            await identity_service.record_audit(
                db,
                principal=principal,
                action=f"authorize:{','.join(p.value for p in permissions)}",
                decision="deny",
                reason=exc.message,
            )
            await db.commit()
            raise _to_http(exc) from exc
        return principal

    return _guard


async def optional_principal(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Principal | None:
    """Resolve a principal when one is present, without failing the request."""
    try:
        return await get_principal(request, db=db, x_openjm_tenant=None)
    except HTTPException:
        return None
