"""#6 document lifecycle concurrency acceptance.

Concurrency is modelled with real, independent sessions (and one real second
engine connection) against the same file-backed database, so the compare-and-swap
paths execute against the database rather than an in-process lock.
"""

import asyncio

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import (
    DOC_STATE_DELETED,
    DOC_STATE_FAILED,
    DOC_STATE_INDEXING,
    DOC_STATE_READY,
)
from app.db import enable_sqlite_foreign_keys
from app.models import Document
from app.services import document_lifecycle as lifecycle


def _doc(document_id="doc-1", state=DOC_STATE_INDEXING, indexed=False, **kw):
    return Document(
        id=document_id,
        tenant_id="tnt-local",
        user_id="local-admin",
        original_name="policy.txt",
        stored_path="/tmp/does-not-exist",
        size_bytes=10,
        status="indexing",
        indexed=indexed,
        lifecycle_state=state,
        lifecycle_version=1,
        **kw,
    )


@pytest.fixture
async def file_db_two(tmp_path):
    """Two sessionmakers over one file database, each with its own connection."""
    path = tmp_path / "concurrency.db"
    engines = []
    makers = []
    from app.db import Base

    for _ in range(2):
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 10}
        )
        event.listen(engine.sync_engine, "connect", enable_sqlite_foreign_keys)
        engines.append(engine)
    async with engines[0].begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    for engine in engines:
        makers.append(async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield makers
    finally:
        for engine in engines:
            await engine.dispose()


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


async def test_partially_ingested_document_is_never_visible(file_db):
    async with file_db() as db:
        db.add(_doc(state=DOC_STATE_INDEXING))
        await db.commit()
        rows = (
            await db.execute(select(Document).where(*lifecycle.retrievable_filter()))
        ).scalars().all()
    assert rows == []


async def test_ready_document_is_visible(file_db):
    async with file_db() as db:
        db.add(_doc(state=DOC_STATE_READY, indexed=True))
        await db.commit()
        rows = (
            await db.execute(select(Document).where(*lifecycle.retrievable_filter()))
        ).scalars().all()
    assert [r.id for r in rows] == ["doc-1"]


async def test_deleted_document_is_never_retrievable(file_db):
    async with file_db() as db:
        doc = _doc(state=DOC_STATE_READY, indexed=True)
        db.add(doc)
        await db.commit()
        assert await lifecycle.begin_delete(db, "doc-1") is True
        assert await lifecycle.finish_delete(db, "doc-1") is True
        rows = (
            await db.execute(select(Document).where(*lifecycle.retrievable_filter()))
        ).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# Lease (cross-process mutual exclusion)
# ---------------------------------------------------------------------------


async def test_only_one_process_can_hold_a_lease(file_db_two):
    a, b = file_db_two
    async with a() as db_a:
        db_a.add(_doc(state=DOC_STATE_INDEXING))
        await db_a.commit()
    async with a() as db_a, b() as db_b:
        first = await lifecycle.acquire_lease(db_a, "doc-1", purpose="ingest", ttl_seconds=60)
        second = await lifecycle.acquire_lease(db_b, "doc-1", purpose="delete", ttl_seconds=60)
    assert first is not None
    assert second is None, "a second process must not acquire a held lease"


async def test_an_expired_lease_is_reclaimable(file_db_two):
    a, b = file_db_two
    async with a() as db_a:
        db_a.add(_doc(state=DOC_STATE_INDEXING))
        await db_a.commit()
        await lifecycle.acquire_lease(db_a, "doc-1", purpose="ingest", ttl_seconds=-1)
    async with b() as db_b:
        stolen = await lifecycle.acquire_lease(db_b, "doc-1", purpose="delete", ttl_seconds=60)
    assert stolen is not None, "a crashed holder must not deadlock the document"


# ---------------------------------------------------------------------------
# Delete vs ingest (deterministic resolution)
# ---------------------------------------------------------------------------


async def test_delete_during_ingestion_prevents_publish(file_db):
    """The ingestion attempt loses the race and cannot publish."""
    async with file_db() as db:
        db.add(_doc())
        await db.commit()
        started = await lifecycle.begin_ingest(db, "doc-1")
        assert started is not None
        token, _ = started

        # A concurrent deletion claims the document and clears the token.
        assert await lifecycle.begin_delete(db, "doc-1") is True
        assert await lifecycle.finish_delete(db, "doc-1") is True

        # The stale ingestion attempt is refused.
        assert await lifecycle.commit_ingest(db, "doc-1", token) is False

        document = await db.get(Document, "doc-1")
    assert document.lifecycle_state == DOC_STATE_DELETED
    assert document.indexed is False


async def test_concurrent_ingest_and_delete_resolves_to_deleted(file_db_two):
    """Two truly interleaved attempts: exactly one outcome, never both."""
    a, b = file_db_two
    async with a() as db_a:
        db_a.add(_doc())
        await db_a.commit()

    async def ingest():
        async with a() as db:
            started = await lifecycle.begin_ingest(db, "doc-1")
            if started is None:
                return "ingest_refused"
            await asyncio.sleep(0.05)
            ok = await lifecycle.commit_ingest(db, "doc-1", started[0])
            return "ingest_committed" if ok else "ingest_lost_race"

    async def delete():
        async with b() as db:
            await asyncio.sleep(0.01)
            if not await lifecycle.begin_delete(db, "doc-1"):
                return "delete_refused"
            await lifecycle.finish_delete(db, "doc-1")
            return "deleted"

    results = await asyncio.gather(ingest(), delete())
    async with a() as db:
        document = await db.get(Document, "doc-1")
    assert "deleted" in results
    assert document.lifecycle_state == DOC_STATE_DELETED
    assert document.indexed is False, "a deleted document must never be published"


@pytest.fixture
async def migrated_maker(tmp_path):
    """A sessionmaker over a real *migrated* database.

    The database-level guards are created by migrations, not by
    ``create_all``, so anything asserting on a trigger must run here.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.migrations_runner import adopt_and_upgrade

    path = tmp_path / "migrated.db"
    url = f"sqlite+aiosqlite:///{path}"
    adopt_and_upgrade(url)
    engine = create_async_engine(url, connect_args={"timeout": 10})
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_deleted_document_cannot_be_resurrected(migrated_maker):
    """The database guard refuses any transition out of the deleted state."""
    async with migrated_maker() as db:
        db.add(_doc(state=DOC_STATE_READY, indexed=True))
        await db.commit()
        await lifecycle.begin_delete(db, "doc-1")
        await lifecycle.finish_delete(db, "doc-1")

    async with migrated_maker() as db:
        document = await db.get(Document, "doc-1")
        document.lifecycle_state = DOC_STATE_READY
        document.indexed = True
        document.deleted_at = None
        with pytest.raises(Exception):
            await db.commit()
        await db.rollback()

    async with migrated_maker() as db:
        document = await db.get(Document, "doc-1")
    assert document.lifecycle_state == DOC_STATE_DELETED


# ---------------------------------------------------------------------------
# Retry and crash recovery
# ---------------------------------------------------------------------------


async def test_repeated_commit_is_not_idempotent_by_accident(file_db):
    """A second commit with the same token must not re-publish."""
    async with file_db() as db:
        db.add(_doc())
        await db.commit()
        token, _ = await lifecycle.begin_ingest(db, "doc-1")
        assert await lifecycle.commit_ingest(db, "doc-1", token) is True
        assert await lifecycle.commit_ingest(db, "doc-1", token) is False


async def test_crash_recovery_marks_stuck_ingestion_failed_and_never_ready(file_db):
    async with file_db() as db:
        db.add(_doc())
        await db.commit()
        token, _ = await lifecycle.begin_ingest(db, "doc-1")
        # Simulate a crash: no lease held for this document.
        reclaimed = await lifecycle.recover_stale(db)
        document = await db.get(Document, "doc-1")

    assert reclaimed == 1
    assert document.lifecycle_state == DOC_STATE_FAILED
    assert document.indexed is False
    assert document.ingest_token is None


async def test_crash_recovery_does_not_touch_a_live_lease(file_db):
    async with file_db() as db:
        db.add(_doc())
        await db.commit()
        await lifecycle.acquire_lease(db, "doc-1", purpose="ingest", ttl_seconds=300)
        reclaimed = await lifecycle.recover_stale(db)
        document = await db.get(Document, "doc-1")

    assert reclaimed == 0
    assert document.lifecycle_state == DOC_STATE_INDEXING


async def test_recovery_cannot_resurrect_a_deleted_document(file_db):
    async with file_db() as db:
        db.add(_doc(state=DOC_STATE_READY, indexed=True))
        await db.commit()
        await lifecycle.begin_delete(db, "doc-1")
        await lifecycle.finish_delete(db, "doc-1")
        await lifecycle.recover_stale(db)
        document = await db.get(Document, "doc-1")
    assert document.lifecycle_state == DOC_STATE_DELETED


async def test_stuck_deletion_is_completed_on_recovery(file_db):
    async with file_db() as db:
        db.add(_doc(state=DOC_STATE_READY, indexed=True))
        await db.commit()
        await lifecycle.begin_delete(db, "doc-1")  # crashes before finish_delete
        completed = await lifecycle.finalize_stuck_deletions(db)
        document = await db.get(Document, "doc-1")
    assert completed == 1
    assert document.lifecycle_state == DOC_STATE_DELETED
