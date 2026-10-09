"""E1 cross-process lifecycle correctness (revisits Issue #6).

This module proves the cross-process lifecycle properties an API deployment with
more than one worker relies on, and pins the two gaps the audit closed:

* **Deletion-claim resurrection.** A document that one worker has already claimed
  for deletion (``lifecycle_state='deleting'``) must not be re-entered by a
  concurrent ingest attempt on another worker. The compare-and-swap that makes
  delete-vs-ingest deterministic only holds if a *later* ingest cannot step back
  into ``indexing`` after the delete claim, which would defeat the deletion and
  make ``finish_delete`` a no-op.

* **Stale availability during the delete window.** ``begin_delete`` used to leave
  the legacy ``status``/``indexed`` pair truthy until ``finish_delete`` ran, so
  every availability check that still gates on that legacy pair (the saved-report
  evidence revalidation and the legacy knowledge-search selection) kept treating a
  document as available while it was being deleted on another worker. Availability
  must be governed by the single authoritative predicate
  :func:`app.services.document_lifecycle.retrievable_filter`, and the lifecycle
  primitive must close the legacy pair at the instant the delete is claimed.

The remaining tests are proving tests: they exercise the *correct* cross-process
behaviour (revocation re-read per request, no cached authorization) so a future
regression that introduces a cache or a process-local memo fails here.
"""

import asyncio

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import (
    DOC_STATE_DELETED,
    DOC_STATE_DELETING,
    DOC_STATE_INDEXING,
    DOC_STATE_READY,
    LEGACY_PRINCIPAL_ID,
    LEGACY_TENANT_ID,
)
from app.db import Base, enable_sqlite_foreign_keys
from app.models import Document
from app.services import document_lifecycle as lifecycle


def _doc(document_id="doc-1", state=DOC_STATE_READY, indexed=True, **kw):
    return Document(
        id=document_id,
        tenant_id=LEGACY_TENANT_ID,
        user_id=LEGACY_PRINCIPAL_ID,
        original_name="policy.txt",
        stored_path="/tmp/does-not-exist",
        size_bytes=10,
        status="ready" if state == DOC_STATE_READY else "indexing",
        indexed=indexed,
        lifecycle_state=state,
        lifecycle_version=1,
        classification="internal",
        tenant_visible=True,
        **kw,
    )


@pytest.fixture
async def two_workers(tmp_path):
    """Two sessionmakers over one file database, each with its own connection.

    This is the cross-process model used throughout: two independent sessions
    (and two independent engines) against the same store, so nothing can pass on
    an in-process cache or lock.
    """
    path = tmp_path / "e1.db"
    engines = []
    makers = []
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
# Finding 1: deletion-claim resurrection
# ---------------------------------------------------------------------------


async def test_begin_ingest_cannot_resurrect_a_document_claimed_for_deletion(file_db):
    """A delete claim closes the document to a later ingest attempt.

    Sequence: a ready document is claimed for deletion (``begin_delete``), then a
    concurrent ingest attempt calls ``begin_ingest``. The ingest must be refused
    so the document stays ``deleting`` and ``finish_delete`` can still complete it;
    otherwise the ingest resurrects the document to ``indexing`` and the deletion
    is silently lost.
    """
    async with file_db() as db:
        db.add(_doc(state=DOC_STATE_READY, indexed=True))
        await db.commit()

        assert await lifecycle.begin_delete(db, "doc-1") is True

        # The document is claimed for deletion; a concurrent ingest must not
        # re-enter it.
        assert await lifecycle.begin_ingest(db, "doc-1") is None

        claimed = await db.get(Document, "doc-1")
        assert claimed.lifecycle_state == DOC_STATE_DELETING

        # The deletion still completes deterministically.
        assert await lifecycle.finish_delete(db, "doc-1") is True
        final = await db.get(Document, "doc-1")

    assert final.lifecycle_state == DOC_STATE_DELETED
    assert final.indexed is False


async def test_begin_ingest_refuses_an_already_deleted_document(file_db):
    """The terminal deleted state can never be re-entered by ingest."""
    async with file_db() as db:
        db.add(_doc(state=DOC_STATE_READY, indexed=True))
        await db.commit()
        await lifecycle.begin_delete(db, "doc-1")
        await lifecycle.finish_delete(db, "doc-1")

        assert await lifecycle.begin_ingest(db, "doc-1") is None
        document = await db.get(Document, "doc-1")

    assert document.lifecycle_state == DOC_STATE_DELETED


async def test_delete_claim_and_ingest_race_resolves_to_deleted_across_workers(
    two_workers,
):
    """Truly interleaved delete-claim and ingest: the document ends deleted.

    Worker A claims deletion first, then worker B attempts to begin an ingest.
    The ingest loses and the document is deleted; it is never resurrected into a
    retrievable state.
    """
    a, b = two_workers
    async with a() as db_a:
        db_a.add(_doc(state=DOC_STATE_READY, indexed=True))
        await db_a.commit()

    async def claim_delete():
        async with a() as db:
            await asyncio.sleep(0.01)
            claimed = await lifecycle.begin_delete(db, "doc-1")
            # Hold the claim briefly so the late ingest lands *inside* the
            # delete window (after the claim, before the finish).
            await asyncio.sleep(0.08)
            await lifecycle.finish_delete(db, "doc-1")
            return "deleted" if claimed else "delete_refused"

    async def late_ingest():
        async with b() as db:
            await asyncio.sleep(0.04)
            return await lifecycle.begin_ingest(db, "doc-1")

    deleted, started = await asyncio.gather(claim_delete(), late_ingest())
    assert deleted == "deleted"
    assert started is None, "a late ingest must not resurrect a claimed document"

    async with a() as db:
        document = await db.get(Document, "doc-1")
    assert document.lifecycle_state == DOC_STATE_DELETED
    assert document.indexed is False


# ---------------------------------------------------------------------------
# Finding 2: stale availability during the delete window
# ---------------------------------------------------------------------------


async def test_begin_delete_immediately_closes_the_legacy_availability_pair(file_db):
    """The delete claim must falsify the legacy ``status``/``indexed`` pair.

    Every availability check that has not yet been migrated to
    ``retrievable_filter`` gates on ``status == 'ready' AND indexed``. A document
    under deletion must not satisfy that pair, or a concurrent reader on another
    worker sees it as available.
    """
    async with file_db() as db:
        db.add(_doc(state=DOC_STATE_READY, indexed=True))
        await db.commit()
        assert await lifecycle.begin_delete(db, "doc-1") is True
        document = await db.get(Document, "doc-1")

    legacy_available = document.status == "ready" and document.indexed is True
    assert legacy_available is False, (
        "a document claimed for deletion must not look available to the legacy "
        "status/indexed predicate"
    )


async def test_reports_evidence_revalidation_rejects_a_document_mid_deletion(file_db):
    """Saved-report revalidation must not count a document being deleted.

    The report re-run path revalidates that every cited source is still usable.
    A document that another worker has claimed for deletion (``deleting``) is no
    longer a valid source and must fail revalidation.
    """
    from app.api.reports import _sources_available
    from app.schemas import Evidence

    async with file_db() as db:
        db.add(_doc(state=DOC_STATE_READY, indexed=True))
        await db.commit()
        # Concurrent worker claims the document for deletion.
        assert await lifecycle.begin_delete(db, "doc-1") is True

        evidence = [
            Evidence(
                source_type="document",
                source_id="doc-1",
                title="policy.txt",
                passage="sensitive passage",
            )
        ]
        available = await _sources_available(db, evidence)

    assert available is False, (
        "a document mid-deletion must not pass report evidence revalidation"
    )


async def test_knowledge_search_legacy_path_excludes_a_document_mid_deletion(
    file_db, monkeypatch
):
    """The legacy knowledge-search selection must not admit a deleting document."""
    from app.services.knowledge import knowledge_engine
    from app.services.tools import KnowledgeSearchTool, ToolContext

    async with file_db() as db:
        db.add(_doc(state=DOC_STATE_READY, indexed=True))
        await db.commit()
        assert await lifecycle.begin_delete(db, "doc-1") is True

    captured: dict = {}

    async def fake_retrieve(query, refs, **kwargs):
        captured["refs"] = [ref[0] for ref in refs]
        return []

    monkeypatch.setattr(knowledge_engine, "retrieve", fake_retrieve)

    async with file_db() as db:
        tool = KnowledgeSearchTool()
        await tool.execute(
            ToolContext(
                user_id=LEGACY_PRINCIPAL_ID,
                permissions=frozenset({"knowledge.read"}),
                db=db,
                access=None,
            ),
            {"query": "policy"},
        )

    assert captured["refs"] == [], (
        "a document mid-deletion must not be selected by the legacy search path"
    )


# ---------------------------------------------------------------------------
# Proving tests: revocation visibility across workers, no cache
# ---------------------------------------------------------------------------


async def test_revocation_is_observed_by_the_next_request_on_another_worker(
    two_workers, file_db
):
    """A group grant then revoke on one connection is seen by the next read on
    another connection, with nothing stale in between."""
    from app.models import AccessGroup, GroupMembership, Tenant
    from app.services import identity as identity_service
    from app.services.access_governance import load_principal_access

    tenant_id = "tnt-e1"
    admin_id = "e1-admin"
    member_id = "e1-member"

    a, b = two_workers
    async with a() as db:
        db.add(Tenant(id=tenant_id, slug="e1", name="E1", status="active"))
        await db.flush()
        await identity_service.get_or_create_principal(
            db, subject="sub:e1-admin", principal_id=admin_id
        )
        await identity_service.get_or_create_principal(
            db, subject="sub:e1-member", principal_id=member_id
        )
        await identity_service.add_membership(
            db, tenant_id=tenant_id, principal_id=member_id, role="viewer"
        )
        db.add(
            AccessGroup(id="grp-e1", tenant_id=tenant_id, slug="g", name="G", status="active")
        )
        await db.flush()
        db.add(
            GroupMembership(
                tenant_id=tenant_id, group_id="grp-e1", principal_id=member_id, status="active"
            )
        )
        await db.commit()

    # Worker B resolves the grant.
    async with b() as db:
        access = await load_principal_access(db, principal_id=member_id, tenant_id=tenant_id)
    assert access.group_ids == frozenset({"grp-e1"})

    # Worker A revokes the membership.
    async with a() as db:
        membership = (
            await db.execute(
                select(GroupMembership).where(GroupMembership.principal_id == member_id)
            )
        ).scalar_one()
        membership.status = "revoked"
        await db.commit()

    # The very next request on worker B sees the revocation; nothing is cached.
    async with b() as db:
        access = await load_principal_access(db, principal_id=member_id, tenant_id=tenant_id)
    assert access.group_ids == frozenset(), "a revocation must be visible on the next request"
