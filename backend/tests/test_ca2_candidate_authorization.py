"""CA2-A: candidate-scoped authorization, bounded execution and safe continuation.

Fixture-only, in-process, no network and no model. The production authorization path is not
exercised differently here; these tests use the same in-process connector doubles as the
existing connector security suite.

Test names state the behaviour, so a failure reads as the claim that broke.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.fernet import Fernet

from test_vs7_connector_security import (  # noqa: F401
    PRINCIPAL_A,
    PRINCIPAL_C,
    TENANT_A,
    TENANT_B,
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

from app.services.connectors import candidate_authorization as ca
from app.services.connectors.authorization import AuthorizationOutcome, DenialScope
from app.services.document_policy import DocumentAccess

N = 150

TENANT_ID = "11111111-1111-1111-1111-111111111111"
PRINCIPAL_ID = "22222222-2222-2222-2222-222222222222"


def access_for(principal_id: str = PRINCIPAL_A, *, tenant_id: str = TENANT_A) -> DocumentAccess:
    return DocumentAccess(tenant_id=tenant_id, principal_id=principal_id, owner_key="u1")


async def seed_resources(maker, provider_, n: int, *, name: str) -> str:
    await _seed(maker)
    instance_id = await _make_instance(maker, TENANT_A, name=name)
    for i in range(n):
        ext = f"m{i:03d}"
        provider_.put(ext, text=f"policy document {i}", revision="1")
        provider_.access[("u1", ext)] = True
    async with maker() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance, limit=n + 50)
        await _mapping(db, instance_id, PRINCIPAL_A, "u1")
        await db.commit()
    return instance_id


async def walk(
    db_factory, access, *, budget: int, query: str = "policy"
) -> tuple[ca.CandidateAuthorizationResult, set[str]]:
    """Page to the end and return the last result plus the union of authorized ids."""
    all_ids: set[str] = set()
    cursor = None
    last = None
    for _ in range(50):
        async with db_factory() as db:
            last = await ca.authorize_candidates(
                db, access=access, query=query, budget=budget, cursor=cursor
            )
        all_ids |= set(last.authorized_document_ids)
        cursor = last.cursor
        if cursor is None:
            break
    assert last is not None
    return last, all_ids


# =============================================================== finding 2: opacity


def test_cursor_round_trips_and_binds_scope():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="a query", after=["inst", "ext"],
        now=1000.0,
    )
    payload = ca.decode_cursor(
        token, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="a query", now=1001.0
    )
    assert payload["k"] == ["inst", "ext"]
    assert payload["t"] == TENANT_ID


def test_cursor_does_not_disclose_tenant_principal_or_resource_identities():
    """Finding 2: the token must be opaque, not a readable signed payload."""
    external_id = "resource-external-identifier-000042"
    token = ca.encode_cursor(
        tenant_id=TENANT_ID,
        principal_id=PRINCIPAL_ID,
        query="a very distinctive query string",
        after=["instance-identifier-abc", external_id],
        now=1000.0,
    )
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    for secret in (TENANT_ID, PRINCIPAL_ID, "/a very distinctive query string", external_id):
        assert secret.encode() not in raw, f"cursor leaked {secret!r}"
        assert secret not in token, f"cursor leaked {secret!r} in its encoded form"


def test_cursor_is_not_a_readable_json_payload():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "e"], now=1000.0
    )
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    with pytest.raises(Exception):
        json.loads(raw.decode())


def test_tampered_cursor_is_refused():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "e"], now=1000.0
    )
    forged = token[:-6] + ("AAAAAA" if not token.endswith("AAAAAA") else "BBBBBB")
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            forged, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", now=1001.0
        )
    assert exc.value.code == "cursor_invalid"


def test_cursor_sealed_with_a_foreign_key_is_refused():
    """Proves the token is bound to the server secret, not merely well-formed."""
    foreign = Fernet(Fernet.generate_key())
    payload = {
        "v": ca.CURSOR_VERSION,
        "t": TENANT_ID,
        "p": PRINCIPAL_ID,
        "q": "irrelevant",
        "s": "irrelevant",
        "d": "",
        "u": False,
        "k": ["i", "e"],
        "iat": 1000,
        "exp": 2000,
    }
    token = foreign.encrypt(json.dumps(payload).encode()).decode()
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", now=1001.0
        )
    assert exc.value.code == "cursor_invalid"


def test_expired_cursor_is_refused():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "e"],
        now=1000.0, ttl_seconds=60,
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", now=1061.0
        )
    assert exc.value.code == "cursor_expired"


def test_cross_principal_reuse_is_refused():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "e"], now=1000.0
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id=TENANT_ID, principal_id="99999999-9999-9999-9999-999999999999",
            query="q", now=1001.0,
        )
    assert exc.value.code == "cursor_scope_mismatch"


def test_cross_tenant_reuse_is_refused():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "e"], now=1000.0
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id="33333333-3333-3333-3333-333333333333", principal_id=PRINCIPAL_ID,
            query="q", now=1001.0,
        )
    assert exc.value.code == "cursor_scope_mismatch"


def test_query_change_is_refused():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="first question", after=["i", "e"],
        now=1000.0,
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="different question",
            now=1001.0,
        )
    assert exc.value.code == "cursor_query_mismatch"


def test_candidate_scope_change_is_refused():
    """Finding 4: continuation is bound to the complete candidate scope."""
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "e"],
        connector_instance_ids=["instance-a", "instance-b"], now=1000.0,
    )
    # Same principal and query, different scope: must not be honoured.
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q",
            connector_instance_ids=["instance-a"], now=1001.0,
        )
    assert exc.value.code == "cursor_scope_mismatch"

    # Order within the scope must not matter.
    reordered = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "e"],
        connector_instance_ids=["instance-b", "instance-a"], now=1000.0,
    )
    payload = ca.decode_cursor(
        reordered, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q",
        connector_instance_ids=["instance-a", "instance-b"], now=1001.0,
    )
    assert payload["k"] == ["i", "e"]


def test_query_fingerprint_ignores_case_and_surrounding_space():
    a = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="  Policy  ", after=None, now=1.0
    )
    assert ca.decode_cursor(
        a, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="policy", now=2.0
    )["k"] is None


async def test_invalid_cursor_is_refused_at_the_contract_boundary(
    file_db, connector, provider, knowledge
):
    """A bad cursor must raise, not silently start from the beginning."""
    await seed_resources(file_db, provider, 3, name="c-bad-cursor")
    async with file_db() as db:
        with pytest.raises(ca.CursorError) as exc:
            await ca.authorize_candidates(
                db, access=access_for(), query="q", budget=2, cursor="not-a-cursor"
            )
    assert exc.value.code == "cursor_invalid"


# ====================================== correction B: DENIED versus UNAVAILABLE


def test_denial_scope_maps_totally_onto_candidate_states():
    """The two vocabularies are pinned together, so they cannot drift apart.

    White-box on purpose: this is the reconciliation itself.
    """
    assert ca._state_for(AuthorizationOutcome.allow())[0] is ca.CandidateState.AUTHORIZED
    # Only a provider-proved denial for THIS principal is a denial.
    assert (
        ca._state_for(AuthorizationOutcome.deny("access_revoked", DenialScope.PRINCIPAL))[0]
        is ca.CandidateState.DENIED
    )
    # Everything else that fails to authorize is unavailable, never denied.
    for scope in (DenialScope.REQUEST, DenialScope.CONNECTOR, DenialScope.CONTENT):
        state, _ = ca._state_for(AuthorizationOutcome.deny("some_reason", scope))
        assert state is ca.CandidateState.UNAVAILABLE, f"{scope} must be unavailable"


# ================================================ finding 1: coverage honesty


async def test_outage_makes_coverage_partial_even_when_the_walk_completes(
    file_db, connector, provider, knowledge
):
    """A fully walked corpus with an outage is still not complete coverage."""
    from app.services.connectors.base import ConnectorAuthUnavailable

    await seed_resources(file_db, provider, 3, name="c-outage-complete")
    provider.access_error = ConnectorAuthUnavailable("provider down", code="provider_error")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=10)
    assert result.examined == 3, "the walk itself finished"
    assert result.coverage is ca.CoverageStatus.PARTIAL, "unavailable must not read as complete"
    assert result.coverage_reason == ca.REASON_UNAVAILABLE
    assert result.unresolved is True
    assert result.cursor is None
    assert result.authorization_incomplete is True
    assert result.counts()[ca.CandidateState.UNAVAILABLE.value] == 3


async def test_unresolved_failure_on_an_earlier_page_cannot_later_claim_complete(
    file_db, connector, provider, knowledge
):
    """Finding 1: the unresolved flag is sticky across pages."""
    from app.services.connectors.base import ConnectorAuthUnavailable

    await seed_resources(file_db, provider, 3, name="c-sticky")
    provider.access_error = ConnectorAuthUnavailable("provider down", code="provider_error")
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=2)
    assert first.unresolved is True
    assert first.cursor is not None

    # Provider recovers. The next page exhausts the corpus with no failures of its own.
    provider.access_error = None
    async with file_db() as db:
        second = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=10, cursor=first.cursor
        )
    assert second.examined >= 1
    assert second.coverage is ca.CoverageStatus.PARTIAL, (
        "a clean final page must not erase an earlier unresolved failure"
    )
    assert second.coverage_reason == ca.REASON_UNAVAILABLE
    assert second.unresolved is True


async def test_complete_is_claimed_only_when_fully_walked_and_unresolved_is_false(
    file_db, connector, provider, knowledge
):
    await seed_resources(file_db, provider, 3, name="c-complete-honest")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=10)
    assert result.coverage is ca.CoverageStatus.COMPLETE
    assert result.coverage_reason == ""
    assert result.unresolved is False
    assert result.cursor is None
    assert result.authorization_incomplete is False


# ================================================ finding 3: the SQL scan bound


async def test_scan_bound_is_enforced_in_sql_and_does_not_materialise_the_corpus(
    file_db, connector, provider, knowledge
):
    """Finding 3: a 150-resource corpus with a 20-row bound must fetch at most 21 rows."""
    await seed_resources(file_db, provider, N, name="c-sql-bound")
    async with file_db() as db:
        result = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=1000, max_scan_rows=20
        )
    assert result.rows_fetched <= 21, f"fetched {result.rows_fetched} rows for a 20-row bound"
    assert result.rows_fetched < N, "the corpus must not be materialised"
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.coverage_reason == ca.REASON_SCAN_BOUND
    assert result.cursor is not None, "the walk must be resumable"


async def test_scan_bound_walk_still_reaches_every_resource(
    file_db, connector, provider, knowledge
):
    """A small scan bound must not lose candidates, only spread them over more pages."""
    await seed_resources(file_db, provider, N, name="c-sql-bound-progress")
    access = access_for()
    seen: set[str] = set()
    cursor = None
    pages = 0
    for _ in range(200):
        async with file_db() as db:
            result = await ca.authorize_candidates(
                db, access=access, query="q", budget=1000, cursor=cursor, max_scan_rows=15
            )
        seen |= result.authorized_document_ids
        cursor = result.cursor
        pages += 1
        if cursor is None:
            break
    assert len(seen) == N, f"only {len(seen)} of {N} reached with a 15-row scan bound"
    assert pages >= 10


# ================================================ finding 4: candidate-set change


async def test_candidate_set_change_between_pages_invalidates_completeness(
    file_db, connector, provider, knowledge
):
    """Finding 4: a scan invalidated by a corpus change must never claim complete."""
    instance_id = await seed_resources(file_db, provider, 6, name="c-corpus-change")
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=3)
    assert first.cursor is not None

    # A new candidate appears in scope between the pages.
    provider.put("m900", text="late arriving document", revision="1")
    provider.access[("u1", "m900")] = True
    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance, limit=100)
        await db.commit()

    async with file_db() as db:
        second = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=1000, cursor=first.cursor
        )
    assert second.coverage is ca.CoverageStatus.PARTIAL, (
        "a walk invalidated by a corpus change must not claim complete coverage"
    )
    assert second.coverage_reason == ca.REASON_CORPUS_CHANGED
    assert second.unresolved is True


# ============================================== correction A: revocation, non-vacuous


async def test_revocation_ahead_of_the_cursor_is_denied_on_the_next_page(
    file_db, connector, provider, knowledge
):
    """The cursor has not reached this resource yet, so the next page must see the revocation."""
    await seed_resources(file_db, provider, 6, name="c-revoke-ahead")
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=2)
    assert first.cursor is not None
    authorized_first = {d.external_id for d in first.decisions if d.document_id}
    assert authorized_first == {"m000", "m001"}

    # Revoke a resource that lies AHEAD of the cursor. The old test revoked one already
    # passed, which the next page never revisits, making the assertion vacuous.
    provider.access[("u1", "m003")] = False

    async with file_db() as db:
        second = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=2, cursor=first.cursor
        )
    by_ext = {d.external_id: d for d in second.decisions}
    assert "m003" in by_ext, "the next page must actually examine m003"
    assert by_ext["m003"].state is ca.CandidateState.DENIED
    assert by_ext["m003"].document_id is None
    assert "m003" not in second.authorized_document_ids


async def test_revocation_of_a_previously_authorized_resource_is_honoured_immediately(
    file_db, connector, provider, knowledge
):
    """No cross-request caching: a fresh walk must drop a revoked resource."""
    await seed_resources(file_db, provider, 5, name="c-revoke-cached")
    _, before = await walk(file_db, access_for(), budget=2)
    assert len(before) == 5

    provider.access[("u1", "m002")] = False

    _, after = await walk(file_db, access_for(), budget=2)
    assert len(after) == 4, "the revoked resource must not survive a revalidation"
    assert len(after - before) == 0

    # And the granted set cannot be larger than the granted set before revocation.
    assert after < before


# ============================================================ continuation behaviour


async def test_continuation_reaches_every_authorized_resource_beyond_100(
    file_db, connector, provider, knowledge
):
    """The measurement that matters: all 150 reachable across pages with budget 32."""
    await seed_resources(file_db, provider, N, name="c-continuation")
    last, ids = await walk(file_db, access_for(), budget=32)
    assert len(ids) == N, f"only {len(ids)} of {N} authorized resources were reachable"
    assert last.coverage is ca.CoverageStatus.COMPLETE
    assert last.cursor is None


async def test_continuation_is_deterministic_across_repeat_walks(
    file_db, connector, provider, knowledge
):
    await seed_resources(file_db, provider, 40, name="c-determinism")
    _, first = await walk(file_db, access_for(), budget=7)
    _, second = await walk(file_db, access_for(), budget=7)
    assert first == second, "repeated identical walks must agree"
    assert len(first) == 40


async def test_candidate_beyond_the_first_window_is_reachable_with_an_unrelated_title(
    file_db, connector, provider, knowledge
):
    """Adversarial shape: relevance only in the body, unrelated title, index above 100."""
    instance_id = await seed_resources(file_db, provider, N, name="c-adversarial")
    async with file_db() as db:
        resource = await _resource(db, instance_id, f"m{N - 1:03d}")
        tail_id = resource.document_id
    # The query shares no word with any seeded title.
    _, ids = await walk(file_db, access_for(), budget=32, query="quarterly reforestation")
    assert tail_id in ids, "a document beyond the first window must still be reachable"


async def test_budget_is_respected_on_every_page(file_db, connector, provider, knowledge):
    await seed_resources(file_db, provider, 60, name="c-budget")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=10)
    assert result.provider_checks <= 10
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.coverage_reason == ca.REASON_BUDGET
    assert result.cursor is not None


# ================================================= correction C: not semantic retrieval


async def test_authorization_traversal_is_independent_of_query_relevance(
    file_db, connector, provider, knowledge
):
    """Correction C: traversal is not retrieval. Relevance must not change the result.

    A query matching every title and a query matching none must authorize exactly the same
    resources, because this module decides what is permitted, not what is relevant. If this
    test ever fails, an authorization walk has started behaving like a ranking.
    """
    await seed_resources(file_db, provider, 20, name="c-not-retrieval")
    _, matching = await walk(file_db, access_for(), budget=5, query="policy document")
    _, nonsense = await walk(file_db, access_for(), budget=5, query="zzz unrelated nonsense zzz")
    assert matching == nonsense
    assert len(matching) == 20


async def test_the_contract_exposes_no_relevance_or_score_concept(
    file_db, connector, provider, knowledge
):
    await seed_resources(file_db, provider, 4, name="c-no-score")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=4)
    forbidden = ("score", "relevance", "rank", "similarity", "confidence", "vector")
    for name in forbidden:
        assert not hasattr(result, name)
        assert not hasattr(result.decisions[0], name)


# ============================================================== remaining contract


async def test_denied_first_window_still_makes_progress(file_db, connector, provider, knowledge):
    """A denial at the front must consume its slot and not stall the scan."""
    await seed_resources(file_db, provider, 4, name="c-denied-first")
    provider.access[("u1", "m000")] = False
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=1)
    assert first.counts()[ca.CandidateState.DENIED.value] == 1
    assert first.cursor is not None

    _, ids = await walk(file_db, access_for(), budget=4)
    assert len(ids) == 3, "the three authorized resources must be reached past the denial"


async def test_provider_outage_is_unavailable_not_denied_and_scan_continues(
    file_db, connector, provider, knowledge
):
    from app.services.connectors.base import ConnectorAuthUnavailable

    await seed_resources(file_db, provider, 3, name="c-outage")
    provider.access_error = ConnectorAuthUnavailable("provider down", code="provider_error")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=2)
    counts = result.counts()
    assert counts[ca.CandidateState.UNAVAILABLE.value] == 2
    assert counts[ca.CandidateState.DENIED.value] == 0, "an outage is not a denial"
    assert result.authorized_document_ids == frozenset()
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.cursor is not None, "a caller must be able to continue after an outage"

    provider.access_error = None
    async with file_db() as db:
        nxt = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=5, cursor=result.cursor
        )
    assert len(nxt.authorized_document_ids) >= 1


async def test_unmapped_principal_is_denied_not_errored(file_db, connector, provider, knowledge):
    await seed_resources(file_db, provider, 2, name="c-unmapped")
    async with file_db() as db:
        result = await ca.authorize_candidates(
            db, access=access_for(PRINCIPAL_C), query="q", budget=5
        )
    assert result.counts()[ca.CandidateState.DENIED.value] == 2
    assert result.authorized_document_ids == frozenset()


async def test_local_eligibility_is_applied_before_provider_budget(
    file_db, connector, provider, knowledge
):
    """A locally invisible candidate must not consume a provider check."""
    from sqlalchemy import select

    from app.models import Document

    instance_id = await seed_resources(file_db, provider, 3, name="c-local-first")
    async with file_db() as db:
        resource = await _resource(db, instance_id, "m000")
        doc = (
            await db.execute(select(Document).where(Document.id == resource.document_id))
        ).scalars().first()
        doc.classification = "highly_restricted"
        doc.tenant_visible = False
        await db.commit()

    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=5)
    assert result.counts()[ca.CandidateState.LOCAL_POLICY_EXCLUDED.value] == 1
    assert result.provider_checks == 2, "the excluded candidate must not cost a provider check"


async def test_authorized_decisions_are_the_only_ones_carrying_a_document_id(
    file_db, connector, provider, knowledge
):
    await seed_resources(file_db, provider, 5, name="c-no-leak")
    provider.access[("u1", "m001")] = False
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=5)
    for decision in result.decisions:
        if decision.state is ca.CandidateState.AUTHORIZED:
            assert decision.document_id
        else:
            assert decision.document_id is None
    # The user-facing shape carries coverage only, and no numeric volume that could reveal
    # how much was denied or left unexamined. Note bool is a subclass of int.
    summary = result.user_facing_summary()
    assert set(summary) == {"coverage", "partial", "reason"}
    assert not any(
        isinstance(v, int) and not isinstance(v, bool) for v in summary.values()
    ), "the user-facing summary must not carry counts"


async def test_partial_coverage_is_represented_in_the_contract(
    file_db, connector, provider, knowledge
):
    await seed_resources(file_db, provider, 10, name="c-partial")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=4)
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.authorization_incomplete is True
    assert result.user_facing_summary()["partial"] is True


async def test_tenant_isolation_is_preserved(file_db, connector, provider, knowledge):
    """A principal in another tenant sees no candidates from this tenant."""
    await seed_resources(file_db, provider, 5, name="c-isolation")
    async with file_db() as db:
        result = await ca.authorize_candidates(
            db, access=access_for(tenant_id=TENANT_B), query="q", budget=10
        )
    assert result.decisions == ()
    assert result.authorized_document_ids == frozenset()
    assert result.examined == 0


async def test_authorization_does_not_mutate_shared_content(
    file_db, connector, provider, knowledge
):
    """CA1 semantics preserved: a denial must leave the resource active."""
    instance_id = await seed_resources(file_db, provider, 3, name="c-no-mutate")
    provider.access[("u1", "m000")] = False
    async with file_db() as db:
        await ca.authorize_candidates(db, access=access_for(), query="q", budget=3)
        await db.commit()
    async with file_db() as db:
        resource = await _resource(db, instance_id, "m000")
    assert resource.lifecycle_state == "active", "a principal denial must not quarantine"


async def test_budget_must_be_positive(file_db, connector, provider, knowledge):
    async with file_db() as db:
        with pytest.raises(ValueError):
            await ca.authorize_candidates(db, access=access_for(), query="q", budget=0)
        with pytest.raises(ValueError):
            await ca.authorize_candidates(
                db, access=access_for(), query="q", budget=1, max_scan_rows=0
            )
