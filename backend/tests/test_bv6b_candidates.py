"""BV6-B authoritative report candidates (RED -> GREEN).

Narrow opt-in contract, stacked on BV6-A curation:

* the tenant policy flag defaults OFF and flag-off retrieval is byte-identical
  to the frozen pre-change fingerprint;
* only ``authoritative`` reports qualify; ``approved`` and ``featured`` never do;
* source authorization is revalidated on every consideration (revoked source,
  lost group, cross-tenant, disabled source all skip the candidate);
* a candidate never introduces a source the requesting principal cannot
  independently access, and never reorders or replaces ordinary document
  evidence;
* no title/snippet leaks for anything the principal cannot access;
* a contribution is recorded in the execution trace.

Everything runs against a real file-backed SQLite database and the real service
layer. No model, vector store or governed SQL is reached.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.context import reset_principal, set_principal
from app.core.identity import Principal
from app.core.permissions import permissions_for_role
from app.db import Base
from app.models import (
    Conversation,
    Document,
    ExecutionTrace,
    Message,
    ReportDefinitionVersion,
    SavedReport,
    Tenant,
)
from app.schemas import Evidence
from app.services import tools as tools_module
from app.services.document_policy import DocumentAccess
from app.services.tools import KnowledgeSearchTool, ToolContext, ToolRegistry
from app.core.config import get_settings
from app.services import report_candidates as candidates

from sqlalchemy import select

import bv6b_flag_off_probe

TENANT_A = "tnt-bv6b-a"
TENANT_B = "tnt-bv6b-b"
OWNER_A = "bv6b-owner-a"
STAFF_A = "bv6b-staff-a"
OWNER_B = "bv6b-owner-b"

GOLDEN = Path(__file__).parent / "fixtures" / "bv6b_flag_off_golden.json"


def _principal(principal_id: str, tenant_id: str, role: str = "owner") -> Principal:
    return Principal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        subject=f"sub:{principal_id}",
        role=role,
        membership_id=f"m:{tenant_id}:{principal_id}",
        auth_method="local-dev",
        permissions=permissions_for_role(role),
    )


def _access(principal: Principal) -> DocumentAccess:
    return DocumentAccess(
        tenant_id=principal.tenant_id,
        principal_id=principal.principal_id,
        owner_key=principal.user_id,
    )


def _enable(monkeypatch, *, enabled: bool = True) -> None:
    """Patch the flag on the modules that read it (tools + candidates)."""
    patched = get_settings().model_copy(
        update={"report_authoritative_candidates_enabled": enabled}
    )
    monkeypatch.setattr(tools_module, "settings", patched)
    monkeypatch.setattr(candidates, "settings", patched)


def _document(db, *, tenant_id, user_id, name, classification="internal", groups=None, minute=0):
    document = Document(
        tenant_id=tenant_id,
        user_id=user_id,
        original_name=name,
        stored_path=f"/tmp/{name}",
        mime_type="text/plain",
        size_bytes=10,
        status="ready",
        indexed=True,
        classification=classification,
        tenant_visible=True,
        allowed_group_ids_json=json.dumps(groups) if groups else None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(minute=minute),
    )
    db.add(document)
    return document


async def _report(db, *, tenant_id, user_id, documents, title, curation_state, minute=0, question=None):
    conv = Conversation(tenant_id=tenant_id, user_id=user_id, title="Conv")
    db.add(conv)
    await db.flush()
    evidence = [
        {"source_type": "document", "source_id": d.id, "title": d.original_name, "passage": "p"}
        for d in documents
    ]
    assistant = Message(
        conversation_id=conv.id,
        role="assistant",
        content="answer",
        execution_class="knowledge",
        requested_mode="knowledge",
        evidence_json=json.dumps(evidence),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(minute=minute),
    )
    db.add(assistant)
    await db.flush()
    report = SavedReport(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conv.id,
        message_id=assistant.id,
        title=title,
        answer_text="answer",
        evidence_json=json.dumps(evidence),
        execution_class="knowledge",
        requested_mode="knowledge",
        source_count=len(evidence),
        snapshot_as_of=assistant.created_at,
        curation_state=curation_state,
    )
    db.add(report)
    await db.flush()
    if question is not None:
        # The report's own stored definition text; part of the relevance input.
        db.add(
            ReportDefinitionVersion(
                tenant_id=tenant_id,
                user_id=user_id,
                report_id=report.id,
                version=1,
                question_text=question,
                requested_mode="knowledge",
                pinned_document_ids_json=json.dumps([d.id for d in documents]),
                pinned_source_tables_json=json.dumps({}),
            )
        )
        await db.flush()
    return report


def _stable(evidence):
    """Volatile-field-free view of evidence for byte comparisons."""
    return [
        {
            "source_type": item.source_type,
            "source_id": item.source_id,
            "title": item.title,
            "passage": item.passage,
            "score": item.score,
            "provenance": item.provenance,
        }
        for item in evidence
    ]


async def _fake_retrieve(query, refs, **kwargs):
    del query, kwargs
    return [
        Evidence(
            source_type="document",
            source_id=document_id,
            title=title,
            passage=f"passage for {document_id}",
            provenance={"retrieval_role": "primary", "duplicate_count": 1},
        )
        for document_id, title in refs
    ]


async def _seed(db, *, curation_state="authoritative", include_foreign=False):
    db.add_all(
        [
            Tenant(id=TENANT_A, slug="bv6b-a", name="A", status="active"),
            Tenant(id=TENANT_B, slug="bv6b-b", name="B", status="active"),
        ]
    )
    await db.flush()
    doc_a = _document(db, tenant_id=TENANT_A, user_id=OWNER_A, name="alpha.txt", minute=0)
    doc_b = _document(db, tenant_id=TENANT_A, user_id=OWNER_A, name="beta.txt", minute=1)
    await db.flush()
    report = await _report(
        db,
        tenant_id=TENANT_A,
        user_id=OWNER_A,
        documents=[doc_a],
        title="Canonical launch procedure",
        curation_state=curation_state,
    )
    foreign = None
    if include_foreign:
        foreign_doc = _document(db, tenant_id=TENANT_B, user_id=OWNER_B, name="foreign.txt")
        await db.flush()
        foreign = await _report(
            db,
            tenant_id=TENANT_B,
            user_id=OWNER_B,
            documents=[foreign_doc],
            title="FOREIGN SECRET REPORT",
            curation_state="authoritative",
        )
    await db.commit()
    return {"doc_a": doc_a, "doc_b": doc_b, "report": report, "foreign": foreign}


async def _run_tool(db, principal, *, query="what is the canonical launch procedure"):
    registry = ToolRegistry()
    registry.register(KnowledgeSearchTool())
    original = tools_module.knowledge_engine.retrieve
    tools_module.knowledge_engine.retrieve = _fake_retrieve  # type: ignore[assignment]
    set_principal(principal)
    try:
        return await registry.execute(
            "knowledge.search",
            ToolContext(
                user_id=principal.user_id,
                permissions=frozenset({"knowledge.read"}),
                request_id="req-bv6b",
                route="knowledge",
                requested_mode="knowledge",
                db=db,
                access=_access(principal),
            ),
            {"query": query},
        )
    finally:
        reset_principal()
        tools_module.knowledge_engine.retrieve = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Flag off is byte-equivalent to the frozen pre-change fingerprint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_is_byte_equivalent_to_pre_change_golden():
    golden = json.loads(GOLDEN.read_text())
    assert await bv6b_flag_off_probe.retrieval_fingerprint() == golden


@pytest.mark.asyncio
async def test_flag_off_contributes_zero_candidates(file_db, monkeypatch):
    _enable(monkeypatch, enabled=False)
    async with file_db() as db:
        await _seed(db)
        result = await _run_tool(db, _principal(OWNER_A, TENANT_A))
    assert all(item.source_type != "report" for item in result.evidence)
    assert "report_candidates" not in result.trace_metadata
    assert "report_candidate_count" not in result.output


# ---------------------------------------------------------------------------
# Only authoritative qualifies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_authoritative_contributes_a_candidate(file_db, monkeypatch):
    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db, curation_state="authoritative")
        result = await _run_tool(db, _principal(OWNER_A, TENANT_A))
    candidates_out = [item for item in result.evidence if item.source_type == "report"]
    assert [item.source_id for item in candidates_out] == [world["report"].id]
    assert candidates_out[0].title == "Canonical launch procedure"
    # Ordinary document evidence is preserved and comes FIRST (additive only).
    assert [item.source_id for item in result.evidence[:2]] == [
        world["doc_b"].id,
        world["doc_a"].id,
    ]


@pytest.mark.asyncio
async def test_approved_alone_does_not_qualify(file_db, monkeypatch):
    _enable(monkeypatch)
    async with file_db() as db:
        await _seed(db, curation_state="approved")
        result = await _run_tool(db, _principal(OWNER_A, TENANT_A))
    assert all(item.source_type != "report" for item in result.evidence)


@pytest.mark.asyncio
async def test_featured_alone_does_not_qualify(file_db, monkeypatch):
    # ``featured`` is presentation only and never overwrites curation_state.
    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db, curation_state="approved")
        world["report"].featured = True
        await db.commit()
        result = await _run_tool(db, _principal(OWNER_A, TENANT_A))
    assert all(item.source_type != "report" for item in result.evidence)


# ---------------------------------------------------------------------------
# Revalidation on every consideration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoked_source_blocks_candidacy(file_db, monkeypatch):
    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db)
        # Revoke: the pinned document is mid-deletion and no longer retrievable.
        world["doc_a"].indexed = False
        world["doc_a"].status = "deleting"
        await db.commit()
        result = await _run_tool(db, _principal(OWNER_A, TENANT_A))
    assert all(item.source_type != "report" for item in result.evidence)
    assert all("Canonical launch procedure" != item.title for item in result.evidence)


@pytest.mark.asyncio
async def test_principal_without_source_access_gets_no_candidate_and_no_leak(
    file_db, monkeypatch
):
    _enable(monkeypatch)
    async with file_db() as db:
        # Pin a confidential document only visible to a group STAFF_A lacks.
        secret = _document(
            db,
            tenant_id=TENANT_A,
            user_id=OWNER_A,
            name="secret.txt",
            classification="confidential",
            groups=["hr-secret"],
        )
        await db.flush()
        await _report(
            db,
            tenant_id=TENANT_A,
            user_id=OWNER_A,
            documents=[secret],
            title="SECRET REPORT TITLE",
            curation_state="authoritative",
        )
        await db.commit()
        result = await _run_tool(db, _principal(STAFF_A, TENANT_A))
    assert all(item.source_type != "report" for item in result.evidence)
    assert all(item.title != "SECRET REPORT TITLE" for item in result.evidence)
    assert "secret.txt" not in json.dumps(
        [item.model_dump(mode="json") for item in result.evidence]
    )


@pytest.mark.asyncio
async def test_cross_tenant_report_never_contributes(file_db, monkeypatch):
    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db, include_foreign=True)
        result = await _run_tool(db, _principal(OWNER_A, TENANT_A))
    foreign_id = world["foreign"].id
    assert all(item.source_id != foreign_id for item in result.evidence)
    assert all("FOREIGN SECRET REPORT" != item.title for item in result.evidence)
    assert "FOREIGN SECRET REPORT" not in json.dumps(
        [item.model_dump(mode="json") for item in result.evidence]
    )


@pytest.mark.asyncio
async def test_candidate_never_introduces_a_source_outside_the_authorized_set(
    file_db, monkeypatch
):
    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db)
        result = await _run_tool(db, _principal(OWNER_A, TENANT_A))
    unauthorized_doc_ids = {world["doc_a"].id, world["doc_b"].id}
    candidate = next(item for item in result.evidence if item.source_type == "report")
    pinned = set(candidate.provenance["pinned_document_ids"])
    # The candidate only references sources the principal is authorized to read.
    assert pinned.issubset(unauthorized_doc_ids)
    assert candidate.provenance["candidate_kind"] == "authoritative_report"
    assert candidate.score is None


# ---------------------------------------------------------------------------
# Ordinary retrieval is intact (additive only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ordinary_document_evidence_is_unchanged_when_flag_on(
    file_db, monkeypatch
):
    async with file_db() as db:
        await _seed(db)
        _enable(monkeypatch, enabled=False)
        off = await _run_tool(db, _principal(OWNER_A, TENANT_A))
        _enable(monkeypatch, enabled=True)
        on = await _run_tool(db, _principal(OWNER_A, TENANT_A))

    ordinary_fingerprint = _stable(off.evidence)
    on_prefix = _stable(on.evidence[: len(off.evidence)])
    assert on_prefix == ordinary_fingerprint
    extra = on.evidence[len(off.evidence):]
    assert extra and all(item.source_type == "report" for item in extra)


# ---------------------------------------------------------------------------
# Contribution is traceable / auditable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contribution_is_recorded_in_the_execution_trace(file_db, monkeypatch):
    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db)
        result = await _run_tool(db, _principal(OWNER_A, TENANT_A))
        trace = (
            await db.execute(
                select(ExecutionTrace).where(ExecutionTrace.request_id == "req-bv6b")
            )
        ).scalar_one()
    metadata = json.loads(trace.metadata_json)
    records = metadata["report_candidates"]
    assert records == [
        {
            "report_id": world["report"].id,
            "title": "Canonical launch procedure",
            "candidate_kind": "authoritative_report",
            "policy_version": get_settings().report_authoritative_candidates_policy_version,
            "pinned_source_count": 1,
        }
    ]


# ---------------------------------------------------------------------------
# Scoped report runs are untouched (report_scope narrows, never widens)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_report_run_adds_no_candidate(file_db, monkeypatch):
    from app.services.report_scope import ReportSourceScope

    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db)
        principal = _principal(OWNER_A, TENANT_A)
        registry = ToolRegistry()
        registry.register(KnowledgeSearchTool())
        original = tools_module.knowledge_engine.retrieve
        tools_module.knowledge_engine.retrieve = _fake_retrieve  # type: ignore[assignment]
        set_principal(principal)
        try:
            result = await registry.execute(
                "knowledge.search",
                ToolContext(
                    user_id=principal.user_id,
                    permissions=frozenset({"knowledge.read"}),
                    request_id="req-scoped",
                    route="knowledge",
                    requested_mode="knowledge",
                    db=db,
                    access=_access(principal),
                    report_scope=ReportSourceScope.from_pins([world["doc_a"].id], {}),
                ),
                {"query": "canonical launch procedure"},
            )
        finally:
            reset_principal()
            tools_module.knowledge_engine.retrieve = original  # type: ignore[assignment]
    assert all(item.source_type != "report" for item in result.evidence)


# ---------------------------------------------------------------------------
# Deterministic query-relevance gate (authority is not relevance)
# ---------------------------------------------------------------------------


def test_tokenizer_is_deterministic_and_bounded():
    from app.services import report_candidates as rc

    # Lowercased, split on non-alphanumeric runs, short tokens and stopwords
    # dropped, order preserved, duplicates removed.
    assert rc.significant_tokens("The CANONICAL launch, procedure!! the canonical") == (
        "canonical",
        "launch",
        "procedure",
    )
    # Fail closed on absent / non-string / signal-free input.
    assert rc.significant_tokens("") == ()
    assert rc.significant_tokens(None) == ()
    assert rc.significant_tokens("a an is by the") == ()
    # Deterministic and idempotent.
    assert rc.significant_tokens("canonical launch") == rc.significant_tokens(
        "canonical launch"
    )


def test_relevance_decision_is_boolean_and_fails_closed():
    from app.services import report_candidates as rc

    # Unparseable / absent query, or text-free report, is never relevant.
    assert rc.report_text_relevant_to_query("", "Canonical launch", min_overlap=1) is False
    assert rc.report_text_relevant_to_query(None, "Canonical launch", min_overlap=1) is False
    assert rc.report_text_relevant_to_query("canonical launch", None, min_overlap=1) is False
    assert rc.report_text_relevant_to_query("canonical launch", "42", min_overlap=1) is False
    # Unrelated report text is not relevant; overlapping text is.
    assert (
        rc.report_text_relevant_to_query(
            "canonical launch", "quarterly finance reconciliation", min_overlap=1
        )
        is False
    )
    assert (
        rc.report_text_relevant_to_query(
            "canonical launch", "Canonical launch procedure", min_overlap=1
        )
        is True
    )
    # min_overlap < 1 falls closed to 1 (never "always relevant").
    assert (
        rc.report_text_relevant_to_query(
            "canonical launch", "quarterly finance reconciliation", min_overlap=0
        )
        is False
    )


@pytest.mark.asyncio
async def test_unrelated_authoritative_report_is_not_a_candidate(file_db, monkeypatch):
    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db)  # authoritative "Canonical launch procedure"
        unrelated = await _report(
            db,
            tenant_id=TENANT_A,
            user_id=OWNER_A,
            documents=[world["doc_b"]],
            title="Quarterly financial reconciliation",
            curation_state="authoritative",
            minute=5,
        )
        await db.commit()
        result = await _run_tool(
            db, _principal(OWNER_A, TENANT_A), query="what is the canonical launch procedure"
        )
    report_ids = [item.source_id for item in result.evidence if item.source_type == "report"]
    # The relevant report is a candidate; the unrelated authoritative one is not,
    # even though its pinned source is fully authorized.
    assert world["report"].id in report_ids
    assert unrelated.id not in report_ids


@pytest.mark.asyncio
async def test_stored_definition_question_is_used_for_relevance(file_db, monkeypatch):
    _enable(monkeypatch)
    async with file_db() as db:
        # The seeded canonical report is only ``approved`` (never a candidate).
        world = await _seed(db, curation_state="approved")
        # Title shares NO significant token with the query; the stored
        # definition question does. If question text were ignored this would
        # fail closed.
        report = await _report(
            db,
            tenant_id=TENANT_A,
            user_id=OWNER_A,
            documents=[world["doc_a"]],
            title="Snapshot 42",
            curation_state="authoritative",
            minute=7,
            question="How do I perform the canonical launch procedure?",
        )
        await db.commit()
        result = await _run_tool(
            db, _principal(OWNER_A, TENANT_A), query="canonical launch procedure"
        )
    report_ids = [item.source_id for item in result.evidence if item.source_type == "report"]
    assert report_ids == [report.id]


@pytest.mark.asyncio
async def test_matching_but_no_longer_authorized_report_is_not_returned(
    file_db, monkeypatch
):
    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db)  # authoritative, matches the query
        # Revoke the pinned source: even a relevant report fails closed.
        world["doc_a"].indexed = False
        world["doc_a"].status = "deleting"
        await db.commit()
        result = await _run_tool(
            db, _principal(OWNER_A, TENANT_A), query="canonical launch procedure"
        )
    assert all(item.source_type != "report" for item in result.evidence)


@pytest.mark.asyncio
async def test_non_authoritative_matching_report_is_not_returned(file_db, monkeypatch):
    _enable(monkeypatch)
    async with file_db() as db:
        await _seed(db, curation_state="approved")
        result = await _run_tool(
            db, _principal(OWNER_A, TENANT_A), query="canonical launch procedure"
        )
    assert all(item.source_type != "report" for item in result.evidence)


@pytest.mark.asyncio
async def test_report_with_no_significant_text_fails_closed(file_db, monkeypatch):
    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db, curation_state="approved")
        await _report(
            db,
            tenant_id=TENANT_A,
            user_id=OWNER_A,
            documents=[world["doc_a"]],
            title="42",  # no significant token, no definition question
            curation_state="authoritative",
            minute=8,
        )
        await db.commit()
        result = await _run_tool(
            db, _principal(OWNER_A, TENANT_A), query="canonical launch procedure"
        )
    assert all(item.source_type != "report" for item in result.evidence)


@pytest.mark.asyncio
async def test_candidate_count_remains_bounded(file_db, monkeypatch):
    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db)
        for index in range(7):
            await _report(
                db,
                tenant_id=TENANT_A,
                user_id=OWNER_A,
                documents=[world["doc_b"]],
                title=f"Canonical launch procedure {index}",
                curation_state="authoritative",
                minute=10 + index,
            )
        await db.commit()
        # 8 matching authoritative reports, cap at 3.
        monkeypatch.setattr(
            candidates,
            "settings",
            get_settings().model_copy(
                update={
                    "report_authoritative_candidates_enabled": True,
                    "report_authoritative_candidates_max": 3,
                }
            ),
        )
        result = await _run_tool(
            db, _principal(OWNER_A, TENANT_A), query="canonical launch procedure"
        )
    report_items = [item for item in result.evidence if item.source_type == "report"]
    assert len(report_items) == 3


@pytest.mark.asyncio
async def test_candidate_cannot_displace_authorized_primary_evidence(
    file_db, monkeypatch
):
    _enable(monkeypatch)
    async with file_db() as db:
        world = await _seed(db)
        result = await _run_tool(
            db, _principal(OWNER_A, TENANT_A), query="canonical launch procedure"
        )
    # Every ordinary document evidence item keeps its primary retrieval role and
    # sits before any appended candidate.
    first_report = next(
        index
        for index, item in enumerate(result.evidence)
        if item.source_type == "report"
    )
    for item in result.evidence[:first_report]:
        assert item.provenance.get("retrieval_role") == "primary"
    assert world["doc_a"].id in {item.source_id for item in result.evidence}
    assert world["doc_b"].id in {item.source_id for item in result.evidence}
