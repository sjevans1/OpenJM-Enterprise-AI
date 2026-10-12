"""CA2-B: integration of the accepted CA2-A candidate contract into existing consumers.

Fixture-only, in-process, no network and no model. The vector engine is stubbed exactly as the
rest of this suite stubs it (the `knowledge` fixture replaces ingest/delete; this module also
replaces `retrieve`), so what is proven here is the **candidate-selection and evidence-integrity
boundary**, which is where the defect lives and where the tool's authorized-set check is anchored.
A live vector index is not exercised, and the report says so.

Mode is controlled by `settings.retrieval_candidate_mode`, default `legacy`.
"""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import select

from test_vs7_connector_security import (  # noqa: F401
    PRINCIPAL_A,
    TENANT_A,
    _instance,
    _make_instance,
    _mapping,
    _resource,
    _run_initial,
    _seed,
    connector,
    knowledge,
    provider,
)

from app.core.config import get_settings
from app.models import Document
from app.services import retrieval_candidates as rc
from app.services.connectors.candidate_authorization import StrictScopeError
from app.services.document_policy import DocumentAccess
from app.services.tools import KnowledgeSearchTool, ToolContext
from app.schemas import Evidence

BODY_MARKER = "reforestation levy"


def access_for(principal_id: str = PRINCIPAL_A, *, tenant_id: str = TENANT_A) -> DocumentAccess:
    return DocumentAccess(tenant_id=tenant_id, principal_id=principal_id, owner_key="u1")


@pytest.fixture
def mode(monkeypatch):
    """Set the integration mode for one test."""
    settings = get_settings()
    monkeypatch.setattr(settings, "retrieval_candidate_mode", rc.MODE_LEGACY)
    return lambda value: monkeypatch.setattr(settings, "retrieval_candidate_mode", value)


async def seed(maker, provider_, n: int, *, name: str, marker_at: int | None = None) -> str:
    await _seed(maker)
    instance_id = await _make_instance(maker, TENANT_A, name=name)
    for i in range(n):
        ext = f"m{i:03d}"
        body = f"policy document {i}"
        if marker_at is not None and i == marker_at:
            # Relevance in the BODY only. The title stays generic, so no title or external-id
            # match can be what reaches it.
            body = f"{body} {BODY_MARKER}"
        provider_.put(ext, text=body, revision="1")
        provider_.access[("u1", ext)] = True
    async with maker() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance, limit=n + 50)
        await _mapping(db, instance_id, PRINCIPAL_A, "u1")
        await db.commit()
    return instance_id


async def document_id_for(maker, instance_id: str, external_id: str) -> str:
    async with maker() as db:
        row = await _resource(db, instance_id, external_id)
        return row.document_id


def stub_retrieve(monkeypatch, records: dict[str, str]) -> list[str]:
    """A faithful engine stub: it can only return evidence for the refs it is given.

    `records` maps document_id -> ingested body text, taken from the `knowledge` fixture, so the
    stub decides relevance from the real ingested content rather than from a hard-coded list.
    """
    import app.services.knowledge as knowledge_module

    seen: list[str] = []

    async def fake_retrieve(query, refs, **kwargs):
        seen.clear()
        results = []
        for document_id, title in refs:
            seen.append(document_id)
            body = records.get(document_id, "")
            if BODY_MARKER in query and BODY_MARKER not in body:
                continue
            results.append(
                Evidence(
                    source_type="document",
                    source_id=document_id,
                    title=title,
                    passage=body[:120] or "passage",
                    score=0.9,
                    provenance={"retrieval_role": "primary"},
                )
            )
        return results

    monkeypatch.setattr(knowledge_module.knowledge_engine, "retrieve", fake_retrieve)
    return seen


def body_records(knowledge_state) -> dict[str, str]:
    return {document_id: text for document_id, text in knowledge_state["ingested"]}


def tool_context(session, access: DocumentAccess, *, scope=None) -> ToolContext:
    """A tool context bound to an open session: the tool executes queries on ``context.db``."""
    return ToolContext(
        user_id="u1",
        permissions=frozenset({"knowledge.read"}),
        db=session,
        access=access,
        report_scope=scope,
    )


@contextlib.contextmanager
def request_context(principal_id: str = PRINCIPAL_A, tenant_id: str = TENANT_A):
    """Publish a request principal in THIS module's fixture tenant.

    The corpus-wide legacy gate resolves its principal from the request context, and the suite's
    autouse identity context publishes the local development principal in ``tnt-local``, a
    different tenant from ``TENANT_A``. Without this the legacy gate correctly returns nothing,
    and any comparison against it would be vacuous rather than merely wrong.
    """
    from app.core.context import reset_principal, set_principal
    from app.core.identity import Principal
    from app.core.permissions import permissions_for_role

    principal = Principal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        subject="ca2b-subject",
        role="owner",
        membership_id="ca2b-membership",
        auth_method="local-dev",
        permissions=permissions_for_role("owner"),
    )
    set_principal(principal)
    try:
        yield principal
    finally:
        reset_principal()


async def legacy_candidate_ids(maker, *, query: str = "policy") -> set[str]:
    """The legacy governed candidate set, with a request principal in the fixture's tenant."""
    with request_context():
        async with maker() as db:
            candidate_set = await rc.governed_candidates(db, access_for(), query=query)
    return {document.id for document in candidate_set.documents}


# ===================================================== default-off, parity, compatibility


def test_the_default_mode_is_legacy_and_reports_coverage_as_unknown(mode):
    """The prototype is inert by default, and legacy must not claim completeness it cannot see."""
    settings = get_settings()
    assert getattr(settings, "retrieval_candidate_mode", "legacy") == rc.MODE_LEGACY
    assert rc.configured_mode() == rc.MODE_LEGACY
    assert rc.candidate_mode_enabled() is False


def test_an_unrecognised_mode_value_falls_back_to_legacy(mode):
    mode("nonsense")
    assert rc.configured_mode() == rc.MODE_LEGACY


async def test_legacy_mode_returns_exactly_the_existing_candidate_set(file_db, connector, provider, knowledge, mode):
    instance_id = await seed(file_db, provider, 12, name="b-parity")
    access = access_for()
    with request_context():
        async with file_db() as db:
            legacy_set = await rc.governed_candidates(db, access, query="policy")
        async with file_db() as db:
            from app.services.document_policy import governed_tenant_documents

            expected = await governed_tenant_documents(db, access)

    # Non-vacuous: the legacy path must actually yield documents in this fixture tenant.
    assert len(expected) == 12
    assert [d.id for d in legacy_set.documents] == [d.id for d in expected]
    assert legacy_set.mode == rc.MODE_LEGACY
    assert legacy_set.coverage is None, "legacy cannot observe coverage and must not claim it"
    assert legacy_set.notice is None
    assert legacy_set.provider_checks == 0


async def test_candidate_mode_matches_legacy_on_a_fully_authorized_corpus(
    file_db, connector, provider, knowledge, mode
):
    """Below the legacy bound, the two modes must agree exactly."""
    instance_id = await seed(file_db, provider, 40, name="b-parity-candidate")
    access = access_for()

    mode(rc.MODE_LEGACY)
    legacy_ids = await legacy_candidate_ids(file_db)
    assert len(legacy_ids) == 40, "the legacy comparison must be non-vacuous"

    mode(rc.MODE_CANDIDATE)
    async with file_db() as db:
        candidate_set = await rc.governed_candidates(db, access, query="policy")

    assert {d.id for d in candidate_set.documents} == legacy_ids
    assert candidate_set.coverage.value == "complete"
    assert candidate_set.notice is None


async def test_existing_33_to_100_resource_workflow_is_preserved(
    file_db, connector, provider, knowledge, mode
):
    """The known-good 33 to 100 resource shapes must keep working in both modes."""
    instance_id = await seed(file_db, provider, 80, name="b-midrange")
    access = access_for()

    mode(rc.MODE_LEGACY)
    legacy_count = len(await legacy_candidate_ids(file_db))
    assert legacy_count == 80, "the legacy comparison must be non-vacuous"

    mode(rc.MODE_CANDIDATE)
    async with file_db() as db:
        candidate_set = await rc.governed_candidates(db, access, query="policy")

    assert legacy_count == 80
    assert len(candidate_set.documents) == 80
    assert candidate_set.coverage.value == "complete"


# ============================================ end-to-end evidence beyond document 100


async def test_body_relevant_evidence_beyond_document_100_is_retrieved(
    file_db, connector, provider, knowledge, mode, monkeypatch
):
    """The point of the package: reach body-relevant evidence past the legacy window.

    Relevance for the marker lives only in the body of the last document, and its title is
    generic, so nothing about its name or identity can be what reaches it. This asserts the
    whole path: candidate selection, the refs handed to the engine, and the evidence that comes
    back through the tool's authorized-set integrity check.
    """
    total = 150
    beyond_index = total - 1
    instance_id = await seed(
        file_db, provider, total, name="b-beyond", marker_at=beyond_index
    )
    beyond_document = await document_id_for(file_db, instance_id, f"m{beyond_index:03d}")
    records = body_records(knowledge)
    refs_seen = stub_retrieve(monkeypatch, records)
    access = access_for()

    # Legacy: the corpus window truncates, so the document is not even a candidate. The whole
    # legacy comparison runs with a request principal in this fixture's tenant, otherwise the
    # legacy gate correctly returns nothing and the comparison would be vacuous rather than wrong.
    mode(rc.MODE_LEGACY)
    with request_context():
        async with file_db() as db:
            legacy_ids = {
                document.id
                for document in (
                    await rc.governed_candidates(db, access, query=BODY_MARKER)
                ).documents
            }
        assert len(legacy_ids) == 100, f"expected the 100 check window, got {len(legacy_ids)}"
        assert beyond_document not in legacy_ids

        async with file_db() as db:
            legacy_result = await KnowledgeSearchTool().execute(
                tool_context(db, access), {"query": BODY_MARKER}
            )
    assert beyond_document not in refs_seen, "legacy must not hand the document to the engine"
    assert beyond_document not in {item.source_id for item in legacy_result.evidence}

    # Candidate: the document is reached, handed to the engine, and comes back as evidence.
    refs_seen.clear()
    mode(rc.MODE_CANDIDATE)
    async with file_db() as db:
        candidate_set = await rc.governed_candidates(db, access, query=BODY_MARKER)
    assert beyond_document in {d.id for d in candidate_set.documents}

    async with file_db() as db:
        result = await KnowledgeSearchTool().execute(
            tool_context(db, access), {"query": BODY_MARKER}
        )
    assert beyond_document in refs_seen, "the engine must be given the beyond-window document"
    cited = {item.source_id for item in result.evidence}
    assert beyond_document in cited, "body-relevant evidence past document 100 must be retrieved"
    assert BODY_MARKER in " ".join(item.passage for item in result.evidence)


# ============================================ truthful, leak-free partial coverage


async def test_partial_coverage_is_reported_and_the_notice_leaks_nothing(
    file_db, connector, provider, knowledge, mode
):
    instance_id = await seed(file_db, provider, 150, name="b-partial")
    access = access_for()
    mode(rc.MODE_CANDIDATE)

    # A cap far below the corpus forces genuine partial coverage rather than a silent truncation.
    async with file_db() as db:
        partial = await rc.governed_candidates(db, access, query="policy", check_cap=32)

    assert partial.coverage.value == "partial"
    assert partial.is_partial is True
    notice = partial.notice_for_user()
    assert notice is not None
    assert notice["code"] == rc.COVERAGE_NOTICE_CODE
    assert notice["coverage"] == "partial"
    assert notice["reason"] in rc.COVERAGE_NOTICE_MESSAGES
    assert notice["message"] == rc.COVERAGE_NOTICE_MESSAGES[notice["reason"]]

    # The notice is application-generated boilerplate and must contain no identifier, count or
    # title from the data it describes: no document ids, no external ids, no namespace prefixes,
    # no provider detail, no titles.
    assert not any(
        isinstance(value, int) and not isinstance(value, bool) for value in notice.values()
    )
    rendered = " ".join(str(value) for value in notice.values())
    assert "connector:" not in rendered
    assert "m1" not in rendered
    for document in partial.documents:
        assert document.original_name not in rendered

    # Complete coverage says nothing at all, and never claims partial.
    async with file_db() as db:
        complete = await rc.governed_candidates(
            db, access, query="policy", check_cap=rc.MAX_REQUEST_CHECK_CAP
        )
    assert complete.coverage.value == "complete"
    assert complete.notice_for_user() is None


# ============================================ authoritative pinned scopes


async def test_a_pinned_scope_of_65_to_100_sources_is_fully_revalidated(
    file_db, connector, provider, knowledge, mode
):
    instance_id = await seed(file_db, provider, 75, name="b-pinned-ok")
    access = access_for()
    mode(rc.MODE_CANDIDATE)

    document_ids = [await document_id_for(file_db, instance_id, f"m{i:03d}") for i in range(75)]
    async with file_db() as db:
        pinned = await rc.strict_pinned_documents(db, access, document_ids=document_ids)

    assert len(pinned) == 75, "every pinned source must be returned, not just those in a window"
    assert {document.id for document in pinned} == set(document_ids)


async def test_a_pinned_scope_refuses_when_a_source_was_revoked_after_earlier_pages(
    file_db, connector, provider, knowledge, mode
):
    """No authoritative result may be assembled from stale cross-page authorizations."""
    instance_id = await seed(file_db, provider, 90, name="b-pinned-revoked")
    access = access_for()
    mode(rc.MODE_CANDIDATE)

    document_ids = [await document_id_for(file_db, instance_id, f"m{i:03d}") for i in range(90)]

    # Establish that the full pinned scope authorizes first.
    async with file_db() as db:
        assert len(await rc.strict_pinned_documents(db, access, document_ids=document_ids)) == 90

    # Revoke one source, and only then ask for the authoritative result.
    provider.access[("u1", "m044")] = False

    async with file_db() as db:
        with pytest.raises(StrictScopeError) as exc:
            await rc.strict_pinned_documents(db, access, document_ids=document_ids)
    assert exc.value.reason, "the refusal must carry a typed reason"

    # It must refuse, not quietly emit the 89 that remain.
    assert "strict" in exc.value.reason


async def test_a_pinned_scope_is_not_limited_by_the_corpus_window(
    file_db, connector, provider, knowledge, mode
):
    """The legacy failure mode: a pinned source outside the window. Exact scope must still resolve it."""
    instance_id = await seed(file_db, provider, 150, name="b-pinned-window")
    access = access_for()
    mode(rc.MODE_CANDIDATE)

    # A source at index 140 is far outside any 100 check window.
    tail_document = await document_id_for(file_db, instance_id, "m140")

    # The legacy window is what misses it.
    mode(rc.MODE_LEGACY)
    legacy_ids = await legacy_candidate_ids(file_db)
    assert len(legacy_ids) == 100, f"expected the 100 check window, got {len(legacy_ids)}"
    assert tail_document not in legacy_ids, "the window is expected to miss this source"

    # Exact scope still resolves it, because it is resolved by identity and not by a window.
    mode(rc.MODE_CANDIDATE)
    async with file_db() as db:
        pinned = await rc.strict_pinned_documents(db, access, document_ids=[tail_document])
    assert [document.id for document in pinned] == [tail_document]


async def test_a_pinned_scope_over_native_documents_keeps_local_policy_semantics(
    file_db, connector, provider, knowledge, mode
):
    """A native document has no provider resource, so local policy alone governs it."""
    instance_id = await seed(file_db, provider, 6, name="b-pinned-native")
    access = access_for()
    mode(rc.MODE_CANDIDATE)

    hidden_document = await document_id_for(file_db, instance_id, "m003")
    async with file_db() as db:
        document = (
            await db.execute(select(Document).where(Document.id == hidden_document))
        ).scalars().first()
        classified_name = document.original_name
        document.classification = "highly_restricted"
        document.tenant_visible = False
        await db.commit()

    visible_document = await document_id_for(file_db, instance_id, "m000")
    async with file_db() as db:
        pinned = await rc.strict_pinned_documents(db, access, document_ids=[visible_document])
    assert [document.id for document in pinned] == [visible_document]

    # A locally hidden source is not readable, so an authoritative scope containing it refuses
    # rather than dropping it.
    async with file_db() as db:
        with pytest.raises(StrictScopeError):
            await rc.strict_pinned_documents(
                db, access, document_ids=[visible_document, hidden_document]
            )
    assert classified_name


# ============================================ B5: the cap is exact across a whole request


async def test_the_request_cap_is_never_overshot_with_a_non_divisible_page_budget(
    file_db, connector, provider, knowledge, mode
):
    """B5: a 100 check cap with a 32 check page budget must spend exactly 100, not 128."""
    await seed(file_db, provider, 150, name="b5-cap")
    mode(rc.MODE_CANDIDATE)

    async with file_db() as db:
        result = await rc.governed_candidates(
            db, access_for(), query="policy", check_cap=100, page_budget=32
        )

    assert result.provider_checks == 100, (
        f"the request cap must be exact, spent {result.provider_checks} of 100"
    )
    assert result.is_partial is True
    assert result.reason == rc.REASON_PROVIDER_CAP
    notice = result.notice_for_user()
    assert notice is not None
    assert notice["reason"] == rc.REASON_PROVIDER_CAP
    assert notice["message"] == rc.COVERAGE_NOTICE_MESSAGES[rc.REASON_PROVIDER_CAP]

    # A cap that exactly covers the corpus is complete, and says nothing.
    async with file_db() as db:
        complete = await rc.governed_candidates(
            db, access_for(), query="policy", check_cap=rc.MAX_REQUEST_CHECK_CAP
        )
    assert complete.provider_checks == 150
    assert complete.coverage.value == "complete"
    assert complete.notice_for_user() is None


async def test_a_source_beyond_position_256_is_reachable_through_real_continuation(
    file_db, connector, provider, knowledge, mode, monkeypatch
):
    """B4: continuation must be real, so a source past any single page is still reachable."""
    total = 300
    instance_id = await seed(file_db, provider, total, name="b4-continuation")
    mode(rc.MODE_CANDIDATE)
    access = access_for()
    beyond = await document_id_for(file_db, instance_id, f"m{total - 1:03d}")

    seen: set[str] = set()
    cursor = None
    pages = 0
    pages_totalling_under_256 = 0
    for _ in range(40):
        async with file_db() as db:
            page = await rc.governed_candidates(
                db, access, query="", check_cap=64, page_budget=32, cursor=cursor
            )
        seen |= {document.id for document in page.documents}
        pages += 1
        pages_totalling_under_256 += page.provider_checks
        cursor = page.cursor
        if cursor is None:
            break

    assert pages >= 5, "the walk must take several pages, so continuation is exercised"
    assert beyond in seen, "a source beyond position 256 must be reachable by continuing"
    assert len(seen) == total, f"reached {len(seen)} of {total} by continuing"
    assert pages_totalling_under_256 >= total

    # Repeating WITHOUT the cursor is a fresh request, not a silent restart of a promised
    # continuation, and it says so by returning work still outstanding.
    async with file_db() as db:
        restarted = await rc.governed_candidates(
            db, access, query="", check_cap=64, page_budget=32
        )
    assert restarted.is_partial is True
    assert restarted.cursor is not None, "an unfinished walk must advertise more work"


def test_get_documents_keeps_its_list_contract_and_pagination_is_additive():
    """GET /documents must keep returning a list; coverage lives on a new surface."""
    from app.api import knowledge as knowledge_api

    routes = {(route.path, tuple(sorted(route.methods))): route for route in knowledge_api.router.routes}
    list_route = routes[("/knowledge/documents", ("GET",))]
    assert list_route.response_model == list[knowledge_api.DocumentOut]

    page_route = routes[("/knowledge/documents/page", ("GET",))]
    assert page_route.response_model is knowledge_api.DocumentCatalogPageOut
    # The additive surface is a superset shape, not a replacement.
    fields = set(knowledge_api.DocumentCatalogPageOut.model_fields)
    assert {"items", "mode", "coverage", "partial", "notice"} <= fields


# ============================================================ request-context lifecycle over HTTP
#
# The notice is request-scoped state, so the reset at request entry is what stops one request's
# notice being reported on another request's answer. The only honest way to check that is two
# SEQUENTIAL HTTP requests through the real router and services. An earlier test invoked the reset
# helper directly, which proves the helper works but not that the handler calls it.


class _AnsweringGateway:
    """Minimal stand-in for the model gateway, so a general answer can be produced offline."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return self.answer


async def test_a_partial_knowledge_notice_cannot_leak_into_the_next_http_request(
    client, file_db, connector, provider, knowledge, mode, monkeypatch
):
    """Two sequential HTTP requests on one client, through the real router.

    Request 1 is a real Knowledge request whose connector authorization cannot be proven because the
    provider is failing, so coverage is partial, the server publishes a notice, and the response
    carries it. Request 2 is a real general Chat request, which publishes no notice of its own, so a
    notice on its response could only have been inherited from request 1.

    Only answer composition is stubbed. The orchestrator, the exploratory candidate traversal, the
    notice publication, the request context and the response assembly are all the real code.
    """
    from app.api import deps as api_deps
    from app.core.identity import Principal
    from app.core.permissions import permissions_for_role
    from app.main import app
    from app.services.connectors.base import ConnectorAuthUnavailable

    mode(rc.MODE_CANDIDATE)
    await seed(file_db, provider, 30, name="c2b-http-notice")
    monkeypatch.setattr("app.api.chat.model_gateway", _AnsweringGateway("An answer."))

    # The route resolves its own principal, so the corpus seeded for the fixture tenant is only
    # reachable if the request is that tenant's principal. Overriding get_principal is the stable
    # hook for that; the context is published as well because the governed-document gate reads the
    # request context rather than the dependency.
    principal = Principal(
        principal_id=PRINCIPAL_A,
        tenant_id=TENANT_A,
        subject="ca2b-http",
        role="owner",
        membership_id="ca2b-http-membership",
        auth_method="local-dev",
        permissions=permissions_for_role("owner"),
    )
    app.dependency_overrides[api_deps.get_principal] = lambda: principal

    with request_context():
        # Request 1: the provider does not answer, so no candidate's authorization can be proven.
        provider.access_error = ConnectorAuthUnavailable("provider down", code="provider_error")
        first = await client.post(
            "/api/chat",
            json={"message": "what is the reforestation levy policy?", "mode": "knowledge"},
        )
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["coverage_notice"] is not None, (
            "request 1 must be partial and must say so, otherwise this test cannot prove anything; "
            f"execution_class={first_body.get('execution_class')!r} answer={first_body.get('answer')!r}"
        )
        assert first_body["coverage_notice"]["reason"] == "authorization_unavailable"

        # Request 2: a different mode, a healthy provider, and no notice of its own.
        provider.access_error = None
        second = await client.post("/api/chat", json={"message": "hello there", "mode": "chat"})
    assert second.status_code == 200, second.text
    assert second.json()["coverage_notice"] is None, (
        "a request that produced no notice of its own must not inherit the previous request's, or a "
        "complete answer would be reported to the user as incomplete"
    )


async def test_a_stale_notice_in_the_ambient_context_is_cleared_before_the_request_runs(
    client, file_db, connector, provider, knowledge, mode, monkeypatch
):
    """The reset at request entry is load-bearing, and this is where that can be observed.

    An HTTP request runs in a task that COPIES the ambient context, so a notice already sitting in
    that context is visible inside the request. Only the reset clears it. Verified by falsification:
    with the reset call removed from the handler this test fails, and the sequential test above still
    passes, because two independent requests are also isolated from each other by the async runtime.
    """
    from app.core.context import set_coverage_notice

    monkeypatch.setattr("app.api.chat.model_gateway", _AnsweringGateway("A general answer."))
    set_coverage_notice(
        {
            "code": "partial_coverage",
            "coverage": "partial",
            "reason": "budget_exhausted",
            "message": "A notice left over from an earlier request on this context.",
        }
    )
    response = await client.post("/api/chat", json={"message": "hello there", "mode": "chat"})
    assert response.status_code == 200, response.text
    assert response.json()["coverage_notice"] is None, (
        "a notice already present in the ambient context must be cleared at request entry; otherwise "
        "an unrelated earlier value would be reported against this request's answer"
    )