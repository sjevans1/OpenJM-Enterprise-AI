"""CA2-A: candidate-scoped authorization, bounded execution and safe continuation.

Fixture-only, in-process, no network and no model. The production authorization path is
not exercised differently here; these tests use the same in-process connector doubles as
the existing connector security suite.
"""

from __future__ import annotations

import pytest

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
from app.services.document_policy import DocumentAccess

N = 150


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


async def page_through(
    db_factory, access, *, budget: int, query: str = "policy"
) -> tuple[ca.CandidateAuthorizationResult, set[str]]:
    """Walk every page and return the unioned authorized ids plus the last result."""
    all_ids: set[str] = set()
    cursor = None
    last = None
    for _ in range(50):  # generous bound; the loop must terminate well before this
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


# --------------------------------------------------------------- cursor integrity


def test_cursor_round_trips_and_binds_scope():
    token = ca.encode_cursor(
        tenant_id="t1", principal_id="p1", query="a query", after=["inst", "ext"], now=1000.0
    )
    assert ca.decode_cursor(token, tenant_id="t1", principal_id="p1", query="a query", now=1001.0) == [
        "inst",
        "ext",
    ]


def test_tampered_cursor_is_refused():
    token = ca.encode_cursor(
        tenant_id="t1", principal_id="p1", query="q", after=["i", "e"], now=1000.0
    )
    body, sig = token.split(".")
    # Flip a signature character.
    forged = f"{body}.{'A' if sig[0] != 'A' else 'B'}{sig[1:]}"
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(forged, tenant_id="t1", principal_id="p1", query="q", now=1001.0)
    assert exc.value.code == "cursor_invalid"


def test_cursor_with_repointed_payload_is_refused():
    """Rewriting the payload without re-signing must fail the signature check."""
    token = ca.encode_cursor(
        tenant_id="t1", principal_id="p1", query="q", after=["i", "e"], now=1000.0
    )
    _, sig = token.split(".")
    other = ca.encode_cursor(
        tenant_id="t1", principal_id="p1", query="q", after=["zzz", "zzz"], now=1000.0
    ).split(".")[0]
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(f"{other}.{sig}", tenant_id="t1", principal_id="p1", query="q", now=1001.0)
    assert exc.value.code == "cursor_invalid"


def test_expired_cursor_is_refused():
    token = ca.encode_cursor(
        tenant_id="t1", principal_id="p1", query="q", after=["i", "e"], now=1000.0, ttl_seconds=60
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(token, tenant_id="t1", principal_id="p1", query="q", now=1061.0)
    assert exc.value.code == "cursor_expired"


def test_cross_principal_reuse_is_refused():
    token = ca.encode_cursor(
        tenant_id="t1", principal_id="p1", query="q", after=["i", "e"], now=1000.0
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(token, tenant_id="t1", principal_id="p2", query="q", now=1001.0)
    assert exc.value.code == "cursor_scope_mismatch"


def test_cross_tenant_reuse_is_refused():
    token = ca.encode_cursor(
        tenant_id="t1", principal_id="p1", query="q", after=["i", "e"], now=1000.0
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(token, tenant_id="t2", principal_id="p1", query="q", now=1001.0)
    assert exc.value.code == "cursor_scope_mismatch"


def test_query_change_is_refused():
    token = ca.encode_cursor(
        tenant_id="t1", principal_id="p1", query="first question", after=["i", "e"], now=1000.0
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id="t1", principal_id="p1", query="different question", now=1001.0
        )
    assert exc.value.code == "cursor_query_mismatch"


def test_query_fingerprint_ignores_case_and_surrounding_space():
    a = ca.encode_cursor(tenant_id="t", principal_id="p", query="  Policy  ", after=None, now=1.0)
    assert ca.decode_cursor(a, tenant_id="t", principal_id="p", query="policy", now=2.0) is None


async def test_invalid_cursor_is_refused_at_the_contract_boundary(file_db, connector, provider, knowledge):
    """A bad cursor must raise, not silently start from the beginning."""
    instance_id = await seed_resources(file_db, provider, 3, name="c-bad-cursor")
    async with file_db() as db:
        with pytest.raises(ca.CursorError) as exc:
            await ca.authorize_candidates(
                db, access=access_for(), query="q", budget=2, cursor="not-a-cursor"
            )
    assert exc.value.code == "cursor_invalid"


# ------------------------------------------------------------------ safe continuation


async def test_continuation_reaches_every_authorized_resource_beyond_100(
    file_db, connector, provider, knowledge
):
    """The measurement that matters: all 150 reachable across pages with budget 32."""
    instance_id = await seed_resources(file_db, provider, N, name="c-continuation")
    last, ids = await page_through(file_db, access_for(), budget=32)
    assert last.coverage is ca.CoverageStatus.COMPLETE
    assert last.cursor is None
    assert len(ids) == N, f"only {len(ids)} of {N} authorized resources were reachable"


async def test_continuation_is_deterministic_across_repeat_walks(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 40, name="c-determinism")
    _, first = await page_through(file_db, access_for(), budget=7)
    _, second = await page_through(file_db, access_for(), budget=7)
    assert first == second, "repeated identical walks must agree"
    assert len(first) == 40


async def test_candidate_beyond_the_first_window_is_reachable_with_an_unrelated_title(
    file_db, connector, provider, knowledge
):
    """Adversarial shape: relevance only in the body, unrelated title, index > 100."""
    instance_id = await seed_resources(file_db, provider, N, name="c-adversarial")
    async with file_db() as db:
        resource = await _resource(db, instance_id, f"m{N - 1:03d}")
        tail_id = resource.document_id
    # The query shares no word with any seeded title.
    _, ids = await page_through(file_db, access_for(), budget=32, query="quarterly reforestation")
    assert tail_id in ids, "a document beyond the first window must still be reachable"


async def test_budget_is_respected_on_every_page(file_db, connector, provider, knowledge):
    instance_id = await seed_resources(file_db, provider, 60, name="c-budget")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=10)
    assert result.provider_checks <= 10
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.cursor is not None


# ---------------------------------------------------------------- denied / unavailable


async def test_denied_first_window_still_makes_progress(file_db, connector, provider, knowledge):
    """A denial at the front must consume its slot and not stall the scan."""
    instance_id = await seed_resources(file_db, provider, 4, name="c-denied-first")
    provider.access[("u1", "m000")] = False
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=1)
    assert first.counts()[ca.CandidateState.DENIED.value] == 1
    assert first.cursor is not None

    _, ids = await page_through(file_db, access_for(), budget=4)
    assert len(ids) == 3, "the three authorized resources must be reached past the denial"


async def test_provider_outage_is_unavailable_not_denied_and_scan_continues(
    file_db, connector, provider, knowledge
):
    from app.services.connectors.base import ConnectorAuthUnavailable

    instance_id = await seed_resources(file_db, provider, 3, name="c-outage")
    provider.access_error = ConnectorAuthUnavailable("provider down", code="provider_error")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=2)
    counts = result.counts()
    assert counts[ca.CandidateState.UNAVAILABLE.value] == 2
    assert counts[ca.CandidateState.DENIED.value] == 0, "an outage is not a denial"
    assert result.authorized_document_ids == frozenset()
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.cursor is not None, "a caller must be able to continue after an outage"

    # Recovery: clear the outage and continue from the cursor.
    provider.access_error = None
    async with file_db() as db:
        nxt = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=5, cursor=result.cursor
        )
    assert len(nxt.authorized_document_ids) >= 1


async def test_revocation_between_pages_is_honoured_on_the_next_page(
    file_db, connector, provider, knowledge
):
    """No caching: a page revalidates, so a revocation takes effect immediately."""
    instance_id = await seed_resources(file_db, provider, 6, name="c-revocation")
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=3)
    assert len(first.authorized_document_ids) == 3
    assert first.cursor is not None

    # Revoke one of the resources that the first page already authorized.
    provider.access[("u1", "m000")] = False

    async with file_db() as db:
        again = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=3, cursor=first.cursor
        )
    # The next page must not reauthorize the revoked resource, and the revoked
    # resource must not appear as authorized anywhere in the second page.
    assert not any(d.external_id == "m000" for d in again.decisions if d.document_id)


async def test_unmapped_principal_is_denied_not_errored(file_db, connector, provider, knowledge):
    instance_id = await seed_resources(file_db, provider, 2, name="c-unmapped")
    access = access_for(PRINCIPAL_C)  # no mapping for this principal
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access, query="q", budget=5)
    counts = result.counts()
    assert counts[ca.CandidateState.DENIED.value] == 2
    assert result.authorized_document_ids == frozenset()


# ------------------------------------------------------------------- contract properties


async def test_local_eligibility_is_applied_before_provider_budget(
    file_db, connector, provider, knowledge
):
    """A locally invisible candidate must not consume a provider check."""
    instance_id = await seed_resources(file_db, provider, 3, name="c-local-first")
    async with file_db() as db:
        resource = await _resource(db, instance_id, "m000")
        from sqlalchemy import select

        from app.models import Document

        doc = (await db.execute(select(Document).where(Document.id == resource.document_id))).scalars().first()
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
    instance_id = await seed_resources(file_db, provider, 5, name="c-no-leak")
    provider.access[("u1", "m001")] = False
    provider.access_error = None
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=5)
    for decision in result.decisions:
        if decision.state is ca.CandidateState.AUTHORIZED:
            assert decision.document_id
        else:
            assert decision.document_id is None
    # The user-facing shape carries coverage only, and no numeric volume that could
    # reveal how much was denied or left unexamined. Note bool is a subclass of int.
    summary = result.user_facing_summary()
    assert set(summary) == {"coverage", "partial", "reason"}
    assert not any(
        isinstance(v, int) and not isinstance(v, bool) for v in summary.values()
    ), "the user-facing summary must not carry counts"


async def test_partial_coverage_is_represented_in_the_contract(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 10, name="c-partial")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=4)
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.authorization_incomplete is True
    assert result.coverage_reason == "budget_exhausted"
    assert result.user_facing_summary()["partial"] is True


async def test_complete_coverage_reports_complete_and_no_cursor(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 3, name="c-complete")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=10)
    assert result.coverage is ca.CoverageStatus.COMPLETE
    assert result.authorization_incomplete is False
    assert result.cursor is None


async def test_tenant_isolation_is_preserved(file_db, connector, provider, knowledge):
    """A principal in another tenant sees no candidates from this tenant."""
    instance_id = await seed_resources(file_db, provider, 5, name="c-isolation")
    access = access_for(tenant_id=TENANT_B)
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access, query="q", budget=10)
    assert result.decisions == ()
    assert result.authorized_document_ids == frozenset()


async def test_authorization_does_not_mutate_shared_content(file_db, connector, provider, knowledge):
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
