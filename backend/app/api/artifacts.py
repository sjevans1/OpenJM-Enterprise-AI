"""BV5-A: downloadable Chat artifacts.

General Chat can persist a real work product (HTML / Markdown / TXT / CSV)
instead of dumping file source into the transcript. Every request is owner- and
tenant-scoped through the trusted principal. Downloads are ALWAYS served as an
attachment and an HTML artifact is never rendered inline in the application
origin (it also carries a restrictive CSP).

This surface is deliberately separate from the Governed Saved Report surfaces
(``/api/reports``): a Chat artifact is not approved and not authoritative, and
it grants no execution authority. Nothing here executes content, runs SQL, calls
a model, or reaches the network; artifact content is inert data written to the
controlled store and read back as bytes.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require
from app.core.context import current_principal
from app.core.identity import Permission, Principal
from app.db import get_db
from app.models import ChatArtifact, Conversation, Message, now_utc
from app.schemas import (
    ChatArtifactDetail,
    ChatArtifactOut,
    CreateChatArtifactRequest,
)
from app.services import artifacts as artifacts_service
from app.services.artifacts import (
    ArtifactError,
    content_sha256,
    storage as _storage,
)

router = APIRouter(prefix="/artifacts", tags=["chat-artifacts"])

_HTML_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
_BASE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


def _http(exc: ArtifactError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc) or "Artifact error")


def _out(artifact: ChatArtifact) -> ChatArtifactOut:
    return ChatArtifactOut(
        id=artifact.id,
        title=artifact.title,
        filename=artifact.filename,
        mime_type=artifact.mime_type,
        artifact_format=artifact.artifact_format,
        size_bytes=artifact.size_bytes,
        state=artifact.state,
        is_evidence_backed=artifact.is_evidence_backed,
        conversation_id=artifact.conversation_id,
        message_id=artifact.message_id,
        created_at=artifact.created_at,
    )


def _detail(artifact: ChatArtifact) -> ChatArtifactDetail:
    try:
        provenance = (
            json.loads(artifact.provenance_json) if artifact.provenance_json else {}
        )
    except (TypeError, ValueError):
        provenance = {}
    return ChatArtifactDetail(**_out(artifact).model_dump(), provenance=provenance)


def _owned(db: AsyncSession, artifact_id: str) -> select:
    principal = current_principal()
    return select(ChatArtifact).where(
        ChatArtifact.id == artifact_id,
        ChatArtifact.user_id == principal.user_id,
        ChatArtifact.tenant_id == principal.tenant_id,
    )


async def _owned_artifact(db: AsyncSession, artifact_id: str) -> ChatArtifact | None:
    return (await db.execute(_owned(db, artifact_id))).scalars().first()


async def _validate_linkage(
    db: AsyncSession,
    principal: Principal,
    conversation_id: str | None,
    message_id: str | None,
) -> None:
    """Refuse a conversation/message link the caller does not own.

    Linkage is derived from the trusted principal, never trusted from the
    request: a caller cannot attach an artifact to another owner's conversation.
    """
    conversation = None
    if conversation_id is not None:
        conversation = (
            await db.execute(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == principal.user_id,
                    Conversation.tenant_id == principal.tenant_id,
                )
            )
        ).scalars().first()
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    if message_id is not None:
        message = (
            await db.execute(select(Message).where(Message.id == message_id))
        ).scalars().first()
        if message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        owner = (
            await db.execute(
                select(Conversation).where(
                    Conversation.id == message.conversation_id,
                    Conversation.user_id == principal.user_id,
                    Conversation.tenant_id == principal.tenant_id,
                )
            )
        ).scalars().first()
        if owner is None:
            raise HTTPException(status_code=404, detail="Message not found")
        if conversation is not None and message.conversation_id != conversation.id:
            raise HTTPException(
                status_code=422, detail="Message does not belong to the conversation"
            )


@router.post("", response_model=ChatArtifactDetail)
async def create_artifact(
    payload: CreateChatArtifactRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.CHAT_USE)),
) -> ChatArtifactDetail:
    """Persist one downloadable Chat work product owned by the caller."""
    try:
        mime = artifacts_service.ensure_allowed(payload.format)
        filename = artifacts_service.safe_filename(payload.title, payload.format)
        data = artifacts_service.validate_content(payload.format, payload.content)
    except ArtifactError as exc:
        raise _http(exc) from None

    await _validate_linkage(db, principal, payload.conversation_id, payload.message_id)

    key = artifacts_service.new_storage_key()
    _storage().write(key, data)
    is_backed, provenance_json = artifacts_service.provenance_payload(payload.evidence)

    artifact = ChatArtifact(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
        title=payload.title.strip()[:240],
        filename=filename,
        mime_type=mime,
        artifact_format=payload.format,
        size_bytes=len(data),
        storage_key=key,
        sha256=content_sha256(data),
        state="active",
        is_evidence_backed=is_backed,
        provenance_json=provenance_json,
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return _detail(artifact)


@router.get("", response_model=list[ChatArtifactOut])
async def list_artifacts(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.CHAT_USE)),
) -> list[ChatArtifactOut]:
    """List the caller's active artifacts, newest first."""
    rows = (
        await db.execute(
            select(ChatArtifact)
            .where(
                ChatArtifact.user_id == principal.user_id,
                ChatArtifact.tenant_id == principal.tenant_id,
                ChatArtifact.state == "active",
            )
            .order_by(ChatArtifact.created_at.desc())
        )
    ).scalars().all()
    return [_out(row) for row in rows]


@router.get("/{artifact_id}", response_model=ChatArtifactDetail)
async def get_artifact(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.CHAT_USE)),
) -> ChatArtifactDetail:
    artifact = await _owned_artifact(db, artifact_id)
    if artifact is None or artifact.state != "active":
        raise HTTPException(status_code=404, detail="Artifact not found")
    return _detail(artifact)


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.CHAT_USE)),
) -> Response:
    """Download an owned, active artifact as an attachment.

    The stored filename is re-validated and the bytes are read only through the
    controlled storage abstraction (opaque key, no caller path). HTML is served
    as an attachment with a restrictive CSP, never inline in the app origin.
    """
    artifact = await _owned_artifact(db, artifact_id)
    if artifact is None or artifact.state != "active":
        raise HTTPException(status_code=404, detail="Artifact not found")
    try:
        filename = artifacts_service.validate_stored_filename(artifact.filename)
    except ArtifactError:
        raise HTTPException(status_code=404, detail="Artifact not found") from None
    try:
        data = _storage().read(artifact.storage_key)
    except ArtifactError as exc:
        raise _http(exc) from None

    headers = dict(_BASE_HEADERS)
    # Always an attachment: never rendered inline in the application origin.
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    if artifact.mime_type == "text/html":
        headers["Content-Security-Policy"] = _HTML_POLICY
    return Response(content=data, media_type=artifact.mime_type, headers=headers)


@router.delete("/{artifact_id}", response_model=ChatArtifactOut)
async def delete_artifact(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.CHAT_USE)),
) -> ChatArtifactOut:
    """Retire an owned artifact. A deleted artifact can never be downloaded."""
    artifact = await _owned_artifact(db, artifact_id)
    if artifact is None or artifact.state != "active":
        raise HTTPException(status_code=404, detail="Artifact not found")
    artifact.state = "deleted"
    artifact.deleted_at = now_utc()
    await db.commit()
    await db.refresh(artifact)
    return _out(artifact)
