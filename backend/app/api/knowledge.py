from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require
from app.core.config import get_settings
from app.core.identity import AuthorizationError, Permission, Principal
from app.core.tenancy import DOC_STATE_DELETED, DOC_STATE_PENDING
from app.db import get_db
from app.models import Document
from app.schemas import DocumentOut, IngestResponse
from app.services import access_governance
from app.services import document_lifecycle as lifecycle
from app.services import identity as identity_service
from app.services.document_policy import access_from_principal, visible_documents
from app.services.knowledge import KnowledgeEngineError, knowledge_engine


router = APIRouter(prefix="/knowledge", tags=["knowledge"])
settings = get_settings()

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".txt",
    ".md",
    ".html",
    ".htm",
}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        original_name=document.original_name,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        status=document.status,
        indexed=document.indexed,
        created_at=document.created_at,
        classification=document.classification,
        department_id=document.department_id,
        tenant_visible=document.tenant_visible,
    )


def _owned(principal: Principal):
    """Tenant-scoped ownership predicate for documents.

    Both halves are required: the tenant is the isolation boundary and the
    principal is the owner. Neither alone is sufficient.
    """
    return (
        Document.tenant_id == principal.tenant_id,
        Document.user_id == principal.user_id,
    )


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.KNOWLEDGE_READ)),
):
    result = await db.execute(
        select(Document)
        .where(*_owned(principal), Document.deleted_at.is_(None))
        .order_by(Document.created_at.desc())
    )
    # BV1-B: a document the caller is not authorized to use does not appear in
    # the catalog either, so the listing cannot leak its existence.
    documents = visible_documents(access_from_principal(principal), result.scalars().all())
    return [_document_out(document) for document in documents]


class DocumentPolicyUpdate(BaseModel):
    classification: Literal["public", "internal", "confidential", "highly_restricted"]
    department_id: str | None = Field(default=None, max_length=36)
    tenant_visible: bool | None = None
    allowed_group_ids: list[str] | None = Field(default=None, max_length=200)


@router.patch("/documents/{document_id}/policy", response_model=DocumentOut)
async def update_document_policy(
    document_id: str,
    payload: DocumentPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.KNOWLEDGE_WRITE)),
):
    """Set a governed document's classification/policy (steward or admin only).

    The permission gate admits any Knowledge writer; the service then requires a
    tenant admin or a steward whose scope covers the document, and audits every
    change.
    """
    try:
        document = await access_governance.set_document_policy(
            db, principal=principal, document_id=document_id, **payload.model_dump()
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await db.commit()
    return _document_out(document)


@router.post("/documents", response_model=IngestResponse)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.KNOWLEDGE_WRITE)),
):
    original_name = Path(file.filename or "upload").name
    extension = Path(original_name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {extension or 'unknown'}",
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 50 MB limit")

    document_id = str(uuid4())
    stored_name = f"{document_id}_{original_name}"
    stored_path = settings.upload_dir / stored_name
    stored_path.write_bytes(content)

    document = Document(
        id=document_id,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        original_name=original_name,
        stored_path=str(stored_path),
        mime_type=file.content_type,
        size_bytes=len(content),
        status="indexing",
        indexed=False,
    )
    # The document is created in a non-retrievable state: it only becomes
    # evidence after a successful, still-owned ingestion commit.
    document.lifecycle_state = DOC_STATE_PENDING
    document.lifecycle_version = 1
    db.add(document)
    await db.commit()

    lease = await lifecycle.acquire_lease(db, document_id, purpose="ingest")
    if lease is None:
        raise HTTPException(
            status_code=409,
            detail="Document is already being processed by another worker",
        )

    started = await lifecycle.begin_ingest(db, document_id)
    if started is None:
        await lifecycle.release_lease(db, document_id, lease)
        raise HTTPException(status_code=409, detail="Document is not ingestable")
    token, _version = started

    try:
        await knowledge_engine.ingest(document_id, stored_path, source_name=original_name)
    except KnowledgeEngineError as exc:
        await lifecycle.fail_ingest(db, document_id, token, error=str(exc))
        await lifecycle.release_lease(db, document_id, lease)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - never leave a half-open attempt
        await lifecycle.fail_ingest(db, document_id, token, error=str(exc))
        await lifecycle.release_lease(db, document_id, lease)
        raise

    committed = await lifecycle.commit_ingest(db, document_id, token)
    if not committed:
        # Deterministic delete-vs-ingest resolution: the document was deleted
        # (or superseded) while vectors were being written. The attempt must
        # not publish; clean up what it created and report the conflict.
        try:
            await knowledge_engine.delete(document_id)
        except KnowledgeEngineError:
            pass
        await lifecycle.release_lease(db, document_id, lease)
        raise HTTPException(
            status_code=409,
            detail="Document was removed while it was being ingested",
        )

    await lifecycle.release_lease(db, document_id, lease)
    await db.refresh(document)
    return IngestResponse(**_document_out(document).model_dump())


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.KNOWLEDGE_DELETE)),
):
    result = await db.execute(
        select(Document).where(Document.id == document_id, *_owned(principal))
    )
    document = result.scalars().first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Already deleted: idempotent, and never a second vector deletion.
    if document.lifecycle_state == DOC_STATE_DELETED:
        return {"deleted": True, "document_id": document_id, "already_deleted": True}

    lease = await lifecycle.acquire_lease(db, document_id, purpose="delete")
    if lease is None:
        raise HTTPException(
            status_code=409,
            detail="Document is being processed by another worker; retry",
        )

    # Invalidates any concurrent ingestion attempt before any vector work.
    claimed = await lifecycle.begin_delete(db, document_id)
    if not claimed:
        await lifecycle.release_lease(db, document_id, lease)
        raise HTTPException(status_code=409, detail="Document is not deletable")

    try:
        await knowledge_engine.delete(document_id)
    except KnowledgeEngineError as exc:
        await lifecycle.release_lease(db, document_id, lease)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    stored = Path(document.stored_path)
    if stored.exists():
        stored.unlink()

    await lifecycle.finish_delete(db, document_id)
    await lifecycle.release_lease(db, document_id, lease)

    await identity_service.record_audit(
        db,
        principal=principal,
        action="knowledge.document.delete",
        decision="allow",
        resource_type="document",
        resource_id=document_id,
    )
    await db.commit()
    return {"deleted": True, "document_id": document_id}
