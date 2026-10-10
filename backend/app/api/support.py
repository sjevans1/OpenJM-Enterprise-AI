"""Delegated OpenJM support read API (Lane K).

Two read-only routes, each reached **only** through an explicit, tenant-scoped,
revocable support delegation:

* ``GET /support/tenants/{tenant_id}/content`` — the target tenant's retrievable
  governed documents, requiring a ``content``-scope delegation.
* ``GET /support/tenants/{tenant_id}/metadata`` — the target tenant's
  control metadata, requiring a ``metadata``-scope delegation.

There is intentionally no platform-capability guard here: platform-operator
status (even ``CONTENT_SUPPORT``) grants nothing by itself. The authority is the
delegation row, resolved per request, and every call is audited. These routes
carry no mutation surface.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_support_read
from app.db import get_db
from app.services import support_access
from app.services.support_access import SupportReadContext

router = APIRouter(prefix="/support", tags=["support"])


class SupportDocumentOut(BaseModel):
    id: str
    original_name: str
    mime_type: str | None = None
    size_bytes: int
    classification: str
    department_id: str | None = None
    tenant_visible: bool
    created_at: datetime


class SupportTenantMetadataOut(BaseModel):
    id: str
    slug: str
    name: str
    status: str


def _document_out(document) -> SupportDocumentOut:
    return SupportDocumentOut(
        id=document.id,
        original_name=document.original_name,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        classification=document.classification,
        department_id=document.department_id,
        tenant_visible=document.tenant_visible,
        created_at=document.created_at,
    )


@router.get(
    "/tenants/{tenant_id}/content", response_model=list[SupportDocumentOut]
)
async def read_support_content(
    tenant_id: str,
    context: SupportReadContext = Depends(
        require_support_read(support_access.SUPPORT_CONTENT_SCOPE)
    ),
    db: AsyncSession = Depends(get_db),
):
    documents = await support_access.read_tenant_content(db, context=context)
    return [_document_out(document) for document in documents]


@router.get(
    "/tenants/{tenant_id}/metadata", response_model=SupportTenantMetadataOut
)
async def read_support_metadata(
    tenant_id: str,
    context: SupportReadContext = Depends(
        require_support_read(support_access.SUPPORT_METADATA_SCOPE)
    ),
    db: AsyncSession = Depends(get_db),
):
    metadata = await support_access.read_tenant_metadata(db, context=context)
    return SupportTenantMetadataOut(**metadata)
