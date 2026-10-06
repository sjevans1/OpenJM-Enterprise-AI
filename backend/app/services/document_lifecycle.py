"""Document lifecycle with cross-process coordination (#6).

The sequential lifecycle was already accepted; this module hardens it for
concurrent and cross-process use without redesigning the normal path.

Coordination primitive
----------------------
``document_leases`` holds one row per document. Acquire is a conditional
UPDATE (steal an expired lease, or refresh your own) falling back to a first
INSERT. Both SQLite and PostgreSQL evaluate that atomically, so two processes
cannot both hold the same document. In-process locks are explicitly not
enough: ingestion and deletion can be served by different workers.

Correctness properties this module provides
-------------------------------------------
* A document is retrievable only when ``lifecycle_state='ready'``, ``indexed``
  is true and ``deleted_at`` is null. A partially ingested document is never
  visible.
* Ingestion publishes through a compare-and-swap on ``ingest_token``. If the
  document was deleted (or re-ingested) meanwhile, the stale attempt cannot
  commit and never resurrects a deleted document.
* Deletion clears the ingest token first, so an in-flight ingestion always
  loses the race deterministically.
* An expired lease is reclaimable, so a crashed process cannot deadlock a
  document; ``recover_stale`` returns stuck work to a terminal state without
  executing anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.tenancy import (
    DOC_STATE_DELETED,
    DOC_STATE_DELETING,
    DOC_STATE_FAILED,
    DOC_STATE_INDEXING,
    DOC_STATE_PENDING,
    DOC_STATE_READY,
)
from app.models import Document, DocumentLease

settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def retrievable_filter():
    """The single authoritative definition of "this document may be read".

    Every retrieval, citation and report-pinning path must apply this, so a
    partially ingested, failed or deleted document can never surface as
    evidence.
    """
    return and_(
        Document.lifecycle_state == DOC_STATE_READY,
        Document.indexed.is_(True),
        Document.deleted_at.is_(None),
    )


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------


async def acquire_lease(
    db: AsyncSession,
    document_id: str,
    *,
    purpose: str,
    ttl_seconds: int | None = None,
    holder: str | None = None,
) -> str | None:
    """Acquire the cross-process lease, or return None if another holds it."""
    token = holder or str(uuid4())
    ttl = ttl_seconds or settings.document_lease_seconds
    now = utcnow()
    expires = now + timedelta(seconds=ttl)

    result = await db.execute(
        update(DocumentLease)
        .where(
            DocumentLease.document_id == document_id,
            or_(
                DocumentLease.expires_at <= now,
                DocumentLease.holder_token == token,
            ),
        )
        .values(
            holder_token=token,
            acquired_at=now,
            expires_at=expires,
            purpose=purpose,
        )
    )
    if result.rowcount == 1:
        await db.commit()
        return token

    try:
        db.add(
            DocumentLease(
                document_id=document_id,
                holder_token=token,
                acquired_at=now,
                expires_at=expires,
                purpose=purpose,
            )
        )
        await db.commit()
        return token
    except IntegrityError:
        # Another process created the row first: it holds the lease.
        await db.rollback()
        return None


async def release_lease(db: AsyncSession, document_id: str, token: str) -> None:
    """Release a lease only if this holder still owns it."""
    await db.execute(
        update(DocumentLease)
        .where(
            DocumentLease.document_id == document_id,
            DocumentLease.holder_token == token,
        )
        .values(expires_at=utcnow(), purpose=None)
    )
    await db.commit()


async def lease_state(db: AsyncSession, document_id: str) -> DocumentLease | None:
    return (
        await db.execute(
            select(DocumentLease).where(DocumentLease.document_id == document_id)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


async def mark_pending(db: AsyncSession, document: Document) -> Document:
    """Initial state for a freshly uploaded document."""
    document.lifecycle_state = DOC_STATE_PENDING
    document.status = "indexing"
    document.indexed = False
    document.lifecycle_version = (document.lifecycle_version or 0) + 1
    await db.flush()
    return document


async def begin_ingest(db: AsyncSession, document_id: str) -> tuple[str, int] | None:
    """Move a document into ``indexing`` and return its attempt token.

    Returns ``(token, version)`` or None when the document is already deleted
    or deleted concurrently. The conditional UPDATE is the guard: a document
    that left a committable state cannot be re-entered.
    """
    token = str(uuid4())
    result = await db.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.deleted_at.is_(None),
        )
        .values(
            lifecycle_state=DOC_STATE_INDEXING,
            status="indexing",
            indexed=False,
            ingest_token=token,
            lifecycle_version=Document.lifecycle_version + 1,
        )
    )
    if result.rowcount != 1:
        await db.rollback()
        return None
    version = (
        await db.execute(select(Document.lifecycle_version).where(Document.id == document_id))
    ).scalar_one()
    await db.commit()
    return token, int(version)


async def commit_ingest(db: AsyncSession, document_id: str, token: str) -> bool:
    """Publish an ingestion attempt, but only if it is still the owner.

    The CAS on ``ingest_token`` is what makes delete-vs-ingest deterministic:
    deletion clears the token, so a stale attempt fails here and must clean up
    its vectors instead of publishing.
    """
    now = utcnow()
    result = await db.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.ingest_token == token,
            Document.lifecycle_state == DOC_STATE_INDEXING,
            Document.deleted_at.is_(None),
        )
        .values(
            lifecycle_state=DOC_STATE_READY,
            status="ready",
            indexed=True,
            indexed_at=now,
            ingest_token=None,
            lifecycle_version=Document.lifecycle_version + 1,
        )
    )
    await db.commit()
    return result.rowcount == 1


async def fail_ingest(
    db: AsyncSession, document_id: str, token: str, *, error: str | None = None
) -> bool:
    """Mark a failed attempt; only the owning attempt may do so."""
    result = await db.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.ingest_token == token,
            Document.lifecycle_state == DOC_STATE_INDEXING,
        )
        .values(
            lifecycle_state=DOC_STATE_FAILED,
            status="error",
            indexed=False,
            ingest_token=None,
            lifecycle_version=Document.lifecycle_version + 1,
        )
    )
    await db.commit()
    return result.rowcount == 1


async def begin_delete(db: AsyncSession, document_id: str) -> bool:
    """Claim a document for deletion, invalidating any in-flight ingestion.

    Clearing ``ingest_token`` is deliberate: from this instant the concurrent
    ingestion attempt can no longer commit.
    """
    result = await db.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.deleted_at.is_(None),
        )
        .values(
            lifecycle_state=DOC_STATE_DELETING,
            ingest_token=None,
            lifecycle_version=Document.lifecycle_version + 1,
        )
    )
    await db.commit()
    return result.rowcount == 1


async def finish_delete(db: AsyncSession, document_id: str) -> bool:
    """Mark the vectors gone and the document terminal."""
    now = utcnow()
    result = await db.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.lifecycle_state == DOC_STATE_DELETING,
        )
        .values(
            lifecycle_state=DOC_STATE_DELETED,
            status="deleted",
            indexed=False,
            deleted_at=now,
            lifecycle_version=Document.lifecycle_version + 1,
        )
    )
    await db.commit()
    return result.rowcount == 1


async def recover_stale(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Return stuck ``indexing`` documents to a terminal state after a crash.

    A document whose lease has expired cannot be being ingested any more: the
    holder would have had to renew it. It is marked ``failed`` (never ``ready``)
    and its stale attempt token cleared, so a late writer cannot publish.
    Nothing is executed and no vector is touched here.

    Returns the number of documents reclaimed.
    """
    moment = now or utcnow()
    stale = (
        (
            await db.execute(
                select(Document.id).where(
                    Document.lifecycle_state == DOC_STATE_INDEXING,
                    Document.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    reclaimed = 0
    for document_id in stale:
        lease = await lease_state(db, document_id)
        if lease is not None:
            expires = lease.expires_at
            if expires.tzinfo is None:
                # SQLite returns naive datetimes; the application always wrote UTC.
                expires = expires.replace(tzinfo=timezone.utc)
            if expires > moment:
                continue
        result = await db.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.lifecycle_state == DOC_STATE_INDEXING,
            )
            .values(
                lifecycle_state=DOC_STATE_FAILED,
                status="error",
                indexed=False,
                ingest_token=None,
                lifecycle_version=Document.lifecycle_version + 1,
            )
        )
        reclaimed += result.rowcount or 0
    await db.commit()
    return reclaimed


async def finalize_stuck_deletions(db: AsyncSession) -> int:
    """Complete deletions interrupted between ``deleting`` and ``deleted``."""
    stuck = (
        (
            await db.execute(
                select(Document.id).where(Document.lifecycle_state == DOC_STATE_DELETING)
            )
        )
        .scalars()
        .all()
    )
    completed = 0
    for document_id in stuck:
        if await finish_delete(db, document_id):
            completed += 1
    return completed
