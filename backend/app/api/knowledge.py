from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db import get_db
from app.models import Document
from app.schemas import DocumentOut, IngestResponse
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
    )


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document)
        .where(Document.user_id == settings.dev_user_id)
        .order_by(Document.created_at.desc())
    )
    return [_document_out(document) for document in result.scalars().all()]


@router.post("/documents", response_model=IngestResponse)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
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
        user_id=settings.dev_user_id,
        original_name=original_name,
        stored_path=str(stored_path),
        mime_type=file.content_type,
        size_bytes=len(content),
        status="indexing",
        indexed=False,
    )
    db.add(document)
    await db.commit()

    try:
        await knowledge_engine.ingest(document.id, stored_path)
    except KnowledgeEngineError as exc:
        document.status = "error"
        document.indexed = False
        await db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    document.status = "ready"
    document.indexed = True
    await db.commit()
    await db.refresh(document)
    return IngestResponse(**_document_out(document).model_dump())


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == settings.dev_user_id,
        )
    )
    document = result.scalars().first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        if document.indexed:
            await knowledge_engine.delete(document.id)
    except KnowledgeEngineError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    stored_path = Path(document.stored_path)
    if stored_path.exists():
        stored_path.unlink()

    await db.delete(document)
    await db.commit()
    return {"deleted": True, "document_id": document_id}
