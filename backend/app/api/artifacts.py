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
from app.models import ChatArtifact, now_utc
from app.schemas import (
    ArtifactRenderFormat,
    ChatArtifactDetail,
    ChatArtifactOut,
    CreateChatArtifactRequest,
)
from app.services import artifact_journey as journey
from app.services import artifacts as artifacts_service
from app.services import artifacts_render as render_service
from app.services.artifacts import (
    ArtifactError,
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
    return journey.chat_artifact_out(artifact)


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


@router.post("", response_model=ChatArtifactDetail)
async def create_artifact(
    payload: CreateChatArtifactRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.CHAT_USE)),
) -> ChatArtifactDetail:
    """Persist one downloadable Chat work product owned by the caller.

    Validation, linkage checking and idempotency are shared with the Chat
    journey and the governed ``artifact.create`` action
    (``app.services.artifact_journey.persist_chat_artifact``).
    """
    try:
        artifact = await journey.persist_chat_artifact(
            db,
            principal,
            title=payload.title,
            fmt=payload.format,
            content=payload.content,
            conversation_id=payload.conversation_id,
            message_id=payload.message_id,
            evidence=payload.evidence,
        )
    except ArtifactError as exc:
        raise _http(exc) from None

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


@router.get("/{artifact_id}/render/{target}")
async def render_artifact(
    artifact_id: str,
    target: ArtifactRenderFormat,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.CHAT_USE)),
) -> Response:
    """Render an owned, active artifact to PDF or DOCX and return it as an attachment.

    BV5-B. Rendering is a pure transform of the artifact's already-authorized
    content and provenance: it never fetches a remote asset, never executes or
    templates content, and never writes to the store or the database. A render
    failure therefore leaves no phantom artifact behind — the caller gets a
    bounded error instead of a false success. The rendered bytes are
    size-checked against ``max_artifact_bytes`` before they are returned.

    Authorization is the same owner+tenant gate as ``/download`` and the result
    is served only as an attachment.
    """
    artifact = await _owned_artifact(db, artifact_id)
    if artifact is None or artifact.state != "active":
        raise HTTPException(status_code=404, detail="Artifact not found")
    try:
        data = _storage().read(artifact.storage_key)
    except ArtifactError as exc:
        raise _http(exc) from None

    provenance = None
    if artifact.provenance_json:
        try:
            provenance = json.loads(artifact.provenance_json)
        except (TypeError, ValueError):
            provenance = None

    try:
        rendered = render_service.render_artifact(
            content=data.decode("utf-8", errors="replace"),
            provenance=provenance,
            target=target,
            title=artifact.title,
        )
        filename = artifacts_service.safe_filename(artifact.title, target)
    except ArtifactError as exc:
        raise _http(exc) from None

    headers = dict(_BASE_HEADERS)
    # Always an attachment: never rendered inline in the application origin.
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    # No active content is expected, but keep the restrictive policy as defense.
    headers["Content-Security-Policy"] = _HTML_POLICY
    return Response(
        content=rendered,
        media_type=render_service.RENDER_MIME[target],
        headers=headers,
    )


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
