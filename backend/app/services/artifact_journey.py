"""BV5-C: the end-to-end Chat artifact journey.

This module is the server-side seam between a model answer and a real,
downloadable Chat artifact. It preserves the separation the programme requires:

* **The model proposes; the server decides.** The model may include exactly one
  bounded directive block in its answer. ``parse_artifact_directive`` extracts it
  strictly; anything malformed, or a format/name/size the server does not permit,
  fails closed. The model never chooses a storage path, a host filename, or runs
  any shell/network call.
* **The controlled artifact service does the work.** Creation is delegated to
  ``app.services.artifacts`` (opaque storage keys, allow-listed MIME, bounded
  size, tabular CSV). This module only adds persistence, ownership/linkage
  checks and metadata projection.
* **A Chat artifact stays distinct from a Governed Saved Report.** Its provenance
  is metadata only and is always labelled ``approved=False`` /
  ``authoritative=False`` (see ``artifacts.provenance_payload``).
* **Failures are bounded.** A bad directive or a refused format yields an
  ``ArtifactOutcome`` carrying a short, user-safe note; the conversation turn is
  never corrupted and no phantom card is produced.

The same ``persist_chat_artifact`` is used by the ``/api/artifacts`` route, the
Chat route and the governed ``artifact.create`` bounded action so all three
paths share one validation and one idempotency rule.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import Permission, Principal
from app.models import ChatArtifact, Conversation, Message
from app.schemas import ChatArtifactOut
from app.services.artifacts import (
    ArtifactError,
    content_sha256,
    ensure_storable,
    new_storage_key,
    provenance_payload,
    safe_filename,
    storage,
    validate_content,
)

# ---------------------------------------------------------------------------
# Bounded failure vocabulary
# ---------------------------------------------------------------------------


class ArtifactForbidden(ArtifactError):
    """The principal may not create artifacts."""

    status_code = 403


class ArtifactNotFound(ArtifactError):
    """A named conversation/message does not exist for this principal."""

    status_code = 404


class ArtifactLinkageError(ArtifactError):
    """A message does not belong to the conversation it is linked to."""

    status_code = 422


class ArtifactDirectiveError(Exception):
    """A model directive block was present but malformed."""


_MALFORMED_DIRECTIVE_MESSAGE = (
    "I could not attach the requested file because its description was "
    "malformed, so nothing was created."
)
_REFUSED_ARTIFACT_MESSAGE = (
    "I could not create the requested downloadable file, so no download is "
    "available."
)


def failure_category(exc: ArtifactError) -> str:
    """A bounded, log-safe category for an artifact failure (no internals)."""
    return getattr(exc, "code", type(exc).__name__.lower())


# ---------------------------------------------------------------------------
# Directive parsing (the model's only input surface)
# ---------------------------------------------------------------------------

# Exactly one fenced ``artifact`` block carrying a small JSON object. The body is
# inert data; nothing here templates, executes or fetches it. The closing fence
# must sit on its own line, so a fenced code block *inside* the content cannot
# terminate the directive early.
_DIRECTIVE_RE = re.compile(
    r"```artifact[ \t]*\r?\n(?P<body>.*?)\r?\n```", re.DOTALL | re.IGNORECASE
)


@dataclass(frozen=True)
class ArtifactDirective:
    title: str
    format: str
    content: str


def parse_artifact_directive(text: object) -> tuple[str, ArtifactDirective | None]:
    """Split a model answer into (visible text, optional artifact directive).

    Returns ``(text, None)`` when no directive is present. Raises
    ``ArtifactDirectiveError`` when a directive is present but malformed — the
    caller turns that into a bounded failure, never an exception to the user.

    The directive block is removed from the visible text so raw source never
    reaches the transcript.
    """
    if not isinstance(text, str) or "```artifact" not in text.lower():
        return (text if isinstance(text, str) else ""), None

    match = _DIRECTIVE_RE.search(text)
    if match is None:
        # An opener with no usable closer: refuse rather than guess.
        raise ArtifactDirectiveError("Unterminated artifact directive")

    cleaned = (text[: match.start()] + text[match.end():]).strip()
    try:
        # ``strict=False`` allows literal newlines inside the JSON string so a
        # multi-line file body need not be escaped by the model. Structure and
        # types are still validated below.
        payload = json.loads(match.group("body"), strict=False)
    except ValueError as exc:
        raise ArtifactDirectiveError("Artifact directive is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ArtifactDirectiveError("Artifact directive is not a JSON object")

    title = payload.get("title")
    fmt = payload.get("format")
    content = payload.get("content")
    if not isinstance(title, str) or not title.strip():
        raise ArtifactDirectiveError("Artifact directive has no title")
    if not isinstance(fmt, str) or not fmt.strip():
        raise ArtifactDirectiveError("Artifact directive has no format")
    if not isinstance(content, str) or not content:
        raise ArtifactDirectiveError("Artifact directive has no content")

    # Title is advisory: it is truncated and, later, slugged into a filename by
    # the server. It can never influence the storage path.
    return cleaned, ArtifactDirective(
        title=title.strip()[:200],
        format=fmt.strip().lower(),
        content=content,
    )


# Instruction appended to the system prompt so a model can produce a directive.
ARTIFACT_SYSTEM_INSTRUCTION = (
    "When the user asks you to produce a downloadable file or artifact (for "
    "example a document, report, table or data export), also emit exactly one "
    "fenced block after your explanation:\n"
    "```artifact\n"
    '{"title": "<short title>", "format": "html|markdown|text|csv", '
    '"content": "<the full file body>"}\n'
    "```\n"
    "The server validates this block and, if permitted, stores it and shows the "
    "user a download. The content is inert data and must never contain a "
    "filesystem path. Use format 'csv' only when the content is a genuine table."
)


# ---------------------------------------------------------------------------
# Ownership / linkage
# ---------------------------------------------------------------------------


async def validate_linkage(
    db: AsyncSession,
    principal: Principal,
    conversation_id: str | None,
    message_id: str | None,
) -> None:
    """Refuse a conversation/message link the caller does not own.

    Linkage is derived from the trusted principal, never trusted from the
    request: a caller cannot attach an artifact to another owner's conversation
    or message.
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
            raise ArtifactNotFound("Conversation not found")
    if message_id is not None:
        message = (
            await db.execute(select(Message).where(Message.id == message_id))
        ).scalars().first()
        if message is None:
            raise ArtifactNotFound("Message not found")
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
            raise ArtifactNotFound("Message not found")
        if conversation is not None and message.conversation_id != conversation.id:
            raise ArtifactLinkageError("Message does not belong to the conversation")


# ---------------------------------------------------------------------------
# Creation (the controlled service)
# ---------------------------------------------------------------------------


async def _existing_artifact(
    db: AsyncSession,
    principal: Principal,
    *,
    fmt: str,
    digest: str,
    conversation_id: str | None,
    message_id: str | None,
) -> ChatArtifact | None:
    """An idempotency probe: the same owner + linkage + format + content.

    A retried operation must not create a second artifact or a second blob.
    """
    return (
        await db.execute(
            select(ChatArtifact).where(
                ChatArtifact.tenant_id == principal.tenant_id,
                ChatArtifact.user_id == principal.user_id,
                ChatArtifact.artifact_format == fmt,
                ChatArtifact.sha256 == digest,
                ChatArtifact.state == "active",
                ChatArtifact.conversation_id == conversation_id,
                ChatArtifact.message_id == message_id,
            )
        )
    ).scalars().first()


async def persist_chat_artifact(
    db: AsyncSession,
    principal: Principal,
    *,
    title: str,
    fmt: str,
    content: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
    evidence: Sequence | Iterable = (),
) -> ChatArtifact:
    """Create (or return the identical existing) downloadable Chat artifact.

    The caller owns the transaction: this function flushes but never commits, so
    a Chat turn can stage the artifact within its own unit of work. Storage keys
    are opaque and server-generated; the filename is derived and normalised by
    the server, never taken from the caller.
    """
    if not principal.has(Permission.CHAT_USE):
        raise ArtifactForbidden("Artifact creation requires chat:use")

    # Server-side validation: allow-listed MIME, single-segment filename, bounded
    # size, tabular CSV. Any refusal here fails closed.
    mime = ensure_storable(fmt)
    filename = safe_filename(title, fmt)
    data = validate_content(fmt, content)

    await validate_linkage(db, principal, conversation_id, message_id)

    digest = content_sha256(data)
    existing = await _existing_artifact(
        db,
        principal,
        fmt=fmt,
        digest=digest,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    if existing is not None:
        return existing

    key = new_storage_key()
    storage().write(key, data)
    is_backed, provenance_json = provenance_payload(list(evidence))

    artifact = ChatArtifact(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        title=(title or "").strip()[:240] or "Artifact",
        filename=filename,
        mime_type=mime,
        artifact_format=fmt,
        size_bytes=len(data),
        storage_key=key,
        sha256=digest,
        state="active",
        is_evidence_backed=is_backed,
        provenance_json=provenance_json,
    )
    db.add(artifact)
    await db.flush()
    return artifact


# ---------------------------------------------------------------------------
# Chat journey
# ---------------------------------------------------------------------------


@dataclass
class ArtifactOutcome:
    """The result of evaluating one model answer for an artifact directive."""

    text: str
    artifact: ChatArtifact | None = None
    failure: str | None = None
    failure_message: str | None = None


async def maybe_create_artifact(
    db: AsyncSession,
    principal: Principal,
    *,
    text: str,
    conversation_id: str | None,
    message_id: str | None,
    evidence: Sequence | Iterable = (),
) -> ArtifactOutcome:
    """Inspect a model answer and, if permitted, create the Chat artifact.

    Never raises for a bad directive or a refused artifact: the turn continues
    with the directive stripped and a bounded note. A failure produces no
    artifact row and no phantom card.
    """
    try:
        cleaned, directive = parse_artifact_directive(text)
    except ArtifactDirectiveError:
        return ArtifactOutcome(
            text=text,
            failure="malformed_directive",
            failure_message=_MALFORMED_DIRECTIVE_MESSAGE,
        )
    if directive is None:
        return ArtifactOutcome(text=cleaned)

    try:
        artifact = await persist_chat_artifact(
            db,
            principal,
            title=directive.title,
            fmt=directive.format,
            content=directive.content,
            conversation_id=conversation_id,
            message_id=message_id,
            evidence=evidence,
        )
    except ArtifactError as exc:
        return ArtifactOutcome(
            text=cleaned,
            failure=failure_category(exc),
            failure_message=_REFUSED_ARTIFACT_MESSAGE,
        )
    return ArtifactOutcome(text=cleaned, artifact=artifact)


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def chat_artifact_out(artifact: ChatArtifact) -> ChatArtifactOut:
    """Project a stored artifact to its download-affordance metadata.

    Only metadata leaves the server: never the content, never base64, never the
    storage key.
    """
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


async def artifacts_by_message(
    db: AsyncSession,
    principal: Principal,
    conversation_id: str,
) -> dict[str, list[ChatArtifactOut]]:
    """Active artifacts for a conversation, grouped by their linked message.

    One query, owner- and tenant-scoped, so reopening a conversation never leaks
    another principal's artifact references.
    """
    rows = (
        await db.execute(
            select(ChatArtifact).where(
                ChatArtifact.tenant_id == principal.tenant_id,
                ChatArtifact.user_id == principal.user_id,
                ChatArtifact.conversation_id == conversation_id,
                ChatArtifact.state == "active",
            )
        )
    ).scalars().all()
    grouped: dict[str, list[ChatArtifactOut]] = defaultdict(list)
    for row in rows:
        if row.message_id:
            grouped[row.message_id].append(chat_artifact_out(row))
    return grouped
