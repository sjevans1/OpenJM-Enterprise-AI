"""VS5 authentication endpoints.

Three ways to become an authenticated principal:

1. Send a provider-issued OIDC JWT directly as ``Authorization: Bearer <jwt>``
   (validated on every request, no session involved).
2. Complete the authorization-code flow via ``/auth/oidc/callback``, which mints
   an OpenJM-native session token.
3. Exchange a provider token for an OpenJM session via ``/auth/token/exchange``.

In every case the resulting principal comes from the application database: a
valid token proves *who* the caller is, never *what* they may do.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal
from app.core.config import get_settings
from app.core.identity import AuthenticationError, IdentityError, Principal
from app.db import get_db
from app.services import identity as identity_service
from app.services.oidc import oidc_client

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


class TokenExchangeRequest(BaseModel):
    token: str = Field(min_length=8, max_length=8192)


class SessionResponse(BaseModel):
    token: str
    token_type: str = "Bearer"
    expires_in: int
    principal_id: str
    tenant_id: str
    role: str
    permissions: list[str]


class CallbackRequest(BaseModel):
    code: str = Field(min_length=1, max_length=8192)
    code_verifier: str | None = Field(default=None, max_length=256)
    redirect_uri: str | None = Field(default=None, max_length=1024)


class PrincipalOut(BaseModel):
    principal_id: str
    tenant_id: str
    subject: str
    role: str
    auth_method: str
    email: str | None = None
    display_name: str | None = None
    permissions: list[str]


@router.get("/config")
async def auth_config():
    """Public discovery for the UI: how this deployment authenticates.

    Nothing secret is exposed, and no principal is created by reading this. The
    authorization endpoint is read from the provider's discovery document so the
    SPA does not have to hardcode provider topology; it is absent rather than
    failing when the provider cannot be reached.
    """
    authorization_endpoint: str | None = None
    if oidc_client.configured:
        try:
            document = await oidc_client.discovery()
            value = document.get("authorization_endpoint")
            authorization_endpoint = value if isinstance(value, str) else None
        except Exception:  # noqa: BLE001 - discovery is best effort for the UI
            authorization_endpoint = None
    return {
        "auth_mode": settings.auth_mode,
        "oidc_configured": oidc_client.configured,
        "authorization_endpoint": authorization_endpoint,
        "issuer": settings.oidc_issuer or None,
        "client_id": settings.oidc_client_id or None,
        "tenant_header": "X-OpenJM-Tenant",
    }


@router.get("/me", response_model=PrincipalOut)
async def whoami(principal: Principal = Depends(get_principal)):
    return PrincipalOut(
        principal_id=principal.principal_id,
        tenant_id=principal.tenant_id,
        subject=principal.subject,
        role=principal.role,
        auth_method=principal.auth_method,
        email=principal.email,
        display_name=principal.display_name,
        permissions=sorted(principal.permission_strings()),
    )


def _session_response(principal: Principal, token: str) -> SessionResponse:
    return SessionResponse(
        token=token,
        expires_in=settings.session_ttl_seconds,
        principal_id=principal.principal_id,
        tenant_id=principal.tenant_id,
        role=principal.role,
        permissions=sorted(principal.permission_strings()),
    )


@router.post("/token/exchange", response_model=SessionResponse)
async def exchange_token(
    payload: TokenExchangeRequest, db: AsyncSession = Depends(get_db)
):
    """Exchange a validated provider token for an OpenJM session."""
    try:
        principal = await identity_service.resolve_oidc(db, payload.token)
    except IdentityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    token = await identity_service.create_session(db, principal=principal)
    await identity_service.record_audit(
        db,
        principal=principal,
        action="auth.session.issued",
        decision="allow",
        metadata={"method": "token_exchange"},
    )
    await db.commit()
    return _session_response(principal, token)


@router.post("/oidc/callback", response_model=SessionResponse)
async def oidc_callback(payload: CallbackRequest, db: AsyncSession = Depends(get_db)):
    """Complete the authorization-code flow and mint an OpenJM session."""
    if not settings.oidc_client_id or not settings.oidc_discovery_url:
        raise HTTPException(
            status_code=503,
            detail="OIDC login is not configured on this deployment",
        )
    document = await oidc_client.discovery()
    token_endpoint = document.get("token_endpoint")
    if not token_endpoint:
        raise HTTPException(status_code=503, detail="OIDC provider has no token endpoint")

    data = {
        "grant_type": "authorization_code",
        "code": payload.code,
        "client_id": settings.oidc_client_id,
        "redirect_uri": payload.redirect_uri or settings.oidc_redirect_uri,
    }
    if payload.code_verifier:
        data["code_verifier"] = payload.code_verifier
    if settings.oidc_client_secret:
        data["client_secret"] = settings.oidc_client_secret

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(token_endpoint, data=data)
            response.raise_for_status()
            tokens = response.json()
    except Exception as exc:  # noqa: BLE001 - never leak provider error detail
        raise HTTPException(status_code=401, detail="OIDC code exchange failed") from exc

    id_token = tokens.get("id_token") or tokens.get("access_token")
    if not id_token:
        raise HTTPException(status_code=401, detail="OIDC provider returned no token")
    try:
        principal = await identity_service.resolve_oidc(db, id_token)
    except IdentityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    session = await identity_service.create_session(db, principal=principal)
    await identity_service.record_audit(
        db,
        principal=principal,
        action="auth.session.issued",
        decision="allow",
        metadata={"method": "authorization_code"},
    )
    await db.commit()
    return _session_response(principal, session)


@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    """Revoke the presented OpenJM session token.

    Always returns 204 so a caller cannot use this endpoint to probe whether a
    token exists.
    """
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip() and value.count(".") != 2:
        await identity_service.revoke_session(db, value.strip())
        await db.commit()
    return {"logged_out": True}
