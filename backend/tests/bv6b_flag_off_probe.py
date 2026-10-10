"""Deterministic retrieval fingerprint for the BV6-B byte-equivalence proof.

This module deliberately imports ONLY pre-BV6-B retrieval surfaces. The SAME
file is executed at the pre-change base revision (to record a golden) and at
the BV6-B head (to prove flag-off behaviour is byte-identical). It must never
import the BV6-B feature module, so it stays runnable on both revisions.

Run directly to print the fingerprint JSON:
    PYTHONPATH=$PWD python tests/bv6b_flag_off_probe.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.context import reset_principal, set_principal
from app.core.identity import Principal
from app.core.permissions import permissions_for_role
from app.db import Base
from app.models import Conversation, Document, Message, SavedReport, Tenant
from app.schemas import Evidence
from app.services import tools as tools_module
from app.services.document_policy import DocumentAccess
from app.services.tools import KnowledgeSearchTool, ToolContext, ToolRegistry

TENANT_ID = "tnt-bv6b-probe"
PRINCIPAL_ID = "bv6b-probe-principal"
USER_ID = "bv6b-probe-user"


def _principal() -> Principal:
    return Principal(
        principal_id=PRINCIPAL_ID,
        tenant_id=TENANT_ID,
        subject=f"sub:{PRINCIPAL_ID}",
        role="owner",
        membership_id=f"m:{TENANT_ID}:{PRINCIPAL_ID}",
        auth_method="local-dev",
        permissions=permissions_for_role("owner"),
    )


def _access() -> DocumentAccess:
    return DocumentAccess(
        tenant_id=TENANT_ID,
        principal_id=PRINCIPAL_ID,
        owner_key=USER_ID,
    )


async def _fake_retrieve(query, refs, **kwargs):  # noqa: ANN001 - engine signature
    """Deterministic stand-in for the vector engine: one primary per ref."""
    del query, kwargs
    return [
        Evidence(
            source_type="document",
            source_id=document_id,
            title=title,
            passage=f"authorized passage for {document_id}",
            provenance={"retrieval_role": "primary", "duplicate_count": 1},
        )
        for document_id, title in refs
    ]


def _canon(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canon(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canon(item) for item in value]
    return value


def _fingerprint(evidence, output, trace_metadata) -> dict:
    """Canonical, volatile-field-free view of one retrieval result."""
    return {
        "evidence": [
            {
                "source_type": item.source_type,
                "source_id": item.source_id,
                "title": item.title,
                "passage": item.passage,
                "score": item.score,
                "provenance": _canon(item.provenance),
            }
            for item in evidence
        ],
        "output": _canon(output),
        "trace_metadata": _canon(trace_metadata),
    }


async def _seed(db) -> None:  # noqa: ANN001
    db.add(Tenant(id=TENANT_ID, slug="bv6b-probe", name="Probe", status="active"))
    await db.flush()
    for index, (name, doc_id) in enumerate(
        (("alpha.txt", "probe-doc-alpha"), ("beta.txt", "probe-doc-beta"))
    ):
        db.add(
            Document(
                id=doc_id,
                tenant_id=TENANT_ID,
                user_id=USER_ID,
                original_name=name,
                stored_path=f"/tmp/{name}",
                mime_type="text/plain",
                size_bytes=10,
                status="ready",
                indexed=True,
                classification="internal",
                tenant_visible=True,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(
                    minute=index
                ),
            )
        )
    await db.flush()

    # A plausible governed snapshot used for the trails below. It pins one
    # authorized document. Its content is never read unless the retrieval
    # policy opts in.
    conv = Conversation(tenant_id=TENANT_ID, user_id=USER_ID, title="Probe")
    db.add(conv)
    await db.flush()
    evidence_json = json.dumps(
        [
            {
                "source_type": "document",
                "source_id": "probe-doc-alpha",
                "title": "alpha.txt",
                "passage": "pinned passage",
            }
        ]
    )
    assistant = Message(
        conversation_id=conv.id,
        role="assistant",
        content="Pinned answer",
        execution_class="knowledge",
        requested_mode="knowledge",
        evidence_json=evidence_json,
    )
    db.add(assistant)
    await db.flush()
    db.add(
        SavedReport(
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            conversation_id=conv.id,
            message_id=assistant.id,
            title="Canonical launch procedure",
            answer_text="Pinned answer",
            evidence_json=evidence_json,
            execution_class="knowledge",
            requested_mode="knowledge",
            source_count=1,
            snapshot_as_of=assistant.created_at,
            curation_state="authoritative",
        )
    )
    await db.commit()


async def retrieval_fingerprint() -> dict:
    """Build the fixed scenario and return the canonical retrieval view."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    original = tools_module.knowledge_engine.retrieve
    tools_module.knowledge_engine.retrieve = _fake_retrieve  # type: ignore[assignment]
    try:
        async with maker() as db:
            await _seed(db)
            principal = _principal()
            set_principal(principal)
            try:
                registry = ToolRegistry()
                registry.register(KnowledgeSearchTool())
                result = await registry.execute(
                    "knowledge.search",
                    ToolContext(
                        user_id=USER_ID,
                        permissions=frozenset({"knowledge.read"}),
                        request_id="probe-request",
                        route="knowledge",
                        requested_mode="knowledge",
                        db=db,
                        access=_access(),
                    ),
                    {"query": "what is the canonical launch procedure"},
                )
            finally:
                reset_principal()
        return _fingerprint(result.evidence, result.output, result.trace_metadata)
    finally:
        tools_module.knowledge_engine.retrieve = original  # type: ignore[assignment]
        await engine.dispose()


if __name__ == "__main__":
    import asyncio

    print(json.dumps(asyncio.run(retrieval_fingerprint()), sort_keys=True, indent=2))
