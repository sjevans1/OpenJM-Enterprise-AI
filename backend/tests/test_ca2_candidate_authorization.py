"""CA2-A: candidate authorization. Traversal, exact scope, revalidation.

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
from sqlalchemy import delete, func, select

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

from app.models import ConnectorInstance, Document, ExternalResource
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
    for _ in range(60):
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


async def legacy_digest_inputs(db, access) -> tuple:
    """The pre-B1 digest basis: count plus key extrema. Blind to a balanced middle swap."""
    return (
        await db.execute(
            select(
                func.count(ExternalResource.id),
                func.min(ExternalResource.external_id),
                func.max(ExternalResource.external_id),
                func.min(ExternalResource.connector_instance_id),
                func.max(ExternalResource.connector_instance_id),
            )
            .select_from(ExternalResource)
            .join(ConnectorInstance, ConnectorInstance.id == ExternalResource.connector_instance_id)
            .where(ExternalResource.tenant_id == access.tenant_id)
        )
    ).one()


# =============================================================== cursor integrity


def test_cursor_round_trips_and_binds_scope():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="a query",
        after=["inst", "ns", "ext"], now=1000.0,
    )
    payload = ca.decode_cursor(
        token, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="a query", now=1001.0
    )
    assert payload["k"] == ["inst", "ns", "ext"]
    assert payload["t"] == TENANT_ID


def test_a_two_element_cursor_position_is_rejected():
    """A truncated identity cannot address a namespaced candidate uniquely."""
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=None, now=1000.0
    )
    body = ca._cursor_cipher().encrypt(
        json.dumps({**json.loads(ca._cursor_cipher().decrypt(token.encode())), "k": ["i", "e"]}).encode()
    ).decode()
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            body, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", now=1001.0
        )
    assert exc.value.code == "cursor_invalid"


def test_cursor_does_not_disclose_tenant_principal_or_resource_identities():
    external_id = "resource-external-identifier-000042"
    token = ca.encode_cursor(
        tenant_id=TENANT_ID,
        principal_id=PRINCIPAL_ID,
        query="a very distinctive query string",
        after=["instance-identifier-abc", "namespace-identifier-def", external_id],
        now=1000.0,
    )
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    for secret in (TENANT_ID, PRINCIPAL_ID, "a very distinctive query string", external_id):
        assert secret.encode() not in raw, f"cursor leaked {secret!r}"
        assert secret not in token, f"cursor leaked {secret!r} in its encoded form"


def test_cursor_is_not_a_readable_json_payload():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "n", "e"], now=1000.0
    )
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    with pytest.raises(Exception):
        json.loads(raw.decode())


def test_tampered_cursor_is_refused():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "n", "e"], now=1000.0
    )
    forged = token[:-6] + ("AAAAAA" if not token.endswith("AAAAAA") else "BBBBBB")
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            forged, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", now=1001.0
        )
    assert exc.value.code == "cursor_invalid"


def test_cursor_sealed_with_a_foreign_key_is_refused():
    foreign = Fernet(Fernet.generate_key())
    payload = {
        "v": ca.CURSOR_VERSION,
        "t": TENANT_ID,
        "p": PRINCIPAL_ID,
        "q": "irrelevant",
        "s": "irrelevant",
        "d": "",
        "u": False,
        "k": ["i", "n", "e"],
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
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "n", "e"],
        now=1000.0, ttl_seconds=60,
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", now=1061.0
        )
    assert exc.value.code == "cursor_expired"


def test_cross_principal_reuse_is_refused():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "n", "e"], now=1000.0
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id=TENANT_ID, principal_id="99999999-9999-9999-9999-999999999999",
            query="q", now=1001.0,
        )
    assert exc.value.code == "cursor_scope_mismatch"


def test_cross_tenant_reuse_is_refused():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "n", "e"], now=1000.0
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id="33333333-3333-3333-3333-333333333333", principal_id=PRINCIPAL_ID,
            query="q", now=1001.0,
        )
    assert exc.value.code == "cursor_scope_mismatch"


def test_query_change_is_refused():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="first question", after=["i", "n", "e"],
        now=1000.0,
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="different question",
            now=1001.0,
        )
    assert exc.value.code == "cursor_query_mismatch"


def test_candidate_scope_change_is_refused():
    token = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "n", "e"],
        connector_instance_ids=["instance-a", "instance-b"], now=1000.0,
    )
    with pytest.raises(ca.CursorError) as exc:
        ca.decode_cursor(
            token, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q",
            connector_instance_ids=["instance-a"], now=1001.0,
        )
    assert exc.value.code == "cursor_scope_mismatch"

    reordered = ca.encode_cursor(
        tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q", after=["i", "n", "e"],
        connector_instance_ids=["instance-b", "instance-a"], now=1000.0,
    )
    payload = ca.decode_cursor(
        reordered, tenant_id=TENANT_ID, principal_id=PRINCIPAL_ID, query="q",
        connector_instance_ids=["instance-a", "instance-b"], now=1001.0,
    )
    assert payload["k"] == ["i", "n", "e"]


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
    await seed_resources(file_db, provider, 3, name="c-bad-cursor")
    async with file_db() as db:
        with pytest.raises(ca.CursorError) as exc:
            await ca.authorize_candidates(
                db, access=access_for(), query="q", budget=2, cursor="not-a-cursor"
            )
    assert exc.value.code == "cursor_invalid"


# ============================================ B1: corpus-change detection


async def test_middle_replacement_is_invisible_to_the_legacy_digest_but_not_the_new_one(
    file_db, connector, provider, knowledge
):
    """B1 before/after. Replacement preserving count and extrema defeats the old digest."""
    instance_id = await seed_resources(file_db, provider, 6, name="c-b1-middle")
    access = access_for()

    async with file_db() as db:
        before_legacy = await legacy_digest_inputs(db, access)
        before_digest, _ = await ca._corpus_digest(db, access, None)

    # Balanced middle replacement: remove one middle candidate, add another that does not
    # move either extreme, leaving the count identical.
    provider.put("m003x", text="replacement document", revision="1")
    provider.access[("u1", "m003x")] = True
    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance, limit=100)
        victim = (
            await db.execute(
                select(ExternalResource).where(
                    ExternalResource.connector_instance_id == instance_id,
                    ExternalResource.external_id == "m003",
                )
            )
        ).scalars().first()
        assert victim is not None
        await db.execute(delete(ExternalResource).where(ExternalResource.id == victim.id))
        await db.commit()

    async with file_db() as db:
        after_legacy = await legacy_digest_inputs(db, access)
        after_digest, _ = await ca._corpus_digest(db, access, None)

    # BEFORE: the old basis cannot see the change at all.
    assert after_legacy == before_legacy, (
        "the legacy count/extrema basis was expected to be blind to a balanced middle swap"
    )
    # AFTER: the new watermark-based digest detects it.
    assert after_digest != before_digest, "the new digest must detect a middle replacement"


async def test_balanced_middle_replacement_forces_partial_and_blocks_authoritative(
    file_db, connector, provider, knowledge
):
    """B1 at contract level: a change between pages must never yield authoritative output."""
    instance_id = await seed_resources(file_db, provider, 6, name="c-b1-contract")
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=2)
    assert first.cursor is not None

    provider.put("m003x", text="replacement document", revision="1")
    provider.access[("u1", "m003x")] = True
    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance, limit=100)
        victim = (
            await db.execute(
                select(ExternalResource).where(
                    ExternalResource.connector_instance_id == instance_id,
                    ExternalResource.external_id == "m003",
                )
            )
        ).scalars().first()
        await db.execute(delete(ExternalResource).where(ExternalResource.id == victim.id))
        await db.commit()

    async with file_db() as db:
        second = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=1000, cursor=first.cursor
        )
    assert second.coverage is ca.CoverageStatus.PARTIAL
    assert second.coverage_reason == ca.REASON_CORPUS_CHANGED
    assert second.unresolved is True
    assert second.authoritative is False


async def test_candidate_addition_is_detected(file_db, connector, provider, knowledge):
    instance_id = await seed_resources(file_db, provider, 6, name="c-b1-add")
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=3)
    provider.put("m900", text="late document", revision="1")
    provider.access[("u1", "m900")] = True
    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance, limit=100)
        await db.commit()
    async with file_db() as db:
        second = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=1000, cursor=first.cursor
        )
    assert second.coverage_reason == ca.REASON_CORPUS_CHANGED
    assert second.coverage is ca.CoverageStatus.PARTIAL


async def test_candidate_removal_is_detected(file_db, connector, provider, knowledge):
    instance_id = await seed_resources(file_db, provider, 6, name="c-b1-remove")
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=3)
    async with file_db() as db:
        victim = (
            await db.execute(
                select(ExternalResource).where(
                    ExternalResource.connector_instance_id == instance_id,
                    ExternalResource.external_id == "m005",
                )
            )
        ).scalars().first()
        await db.execute(delete(ExternalResource).where(ExternalResource.id == victim.id))
        await db.commit()
    async with file_db() as db:
        second = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=1000, cursor=first.cursor
        )
    assert second.coverage_reason == ca.REASON_CORPUS_CHANGED
    assert second.coverage is ca.CoverageStatus.PARTIAL


async def test_document_level_policy_change_is_a_stated_limit_with_a_safe_path(
    file_db, connector, provider, knowledge
):
    """B1 limit, stated rather than hidden.

    ``Document`` has no ``updated_at``, so a reclassification cannot move the digest. The
    contract therefore still refuses to call the resumed walk authoritative, and
    consumption-time revalidation excludes the now-ineligible document.
    """
    instance_id = await seed_resources(file_db, provider, 5, name="c-b1-policy")
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=2)
    async with file_db() as db:
        row = await _resource(db, instance_id, "m004")
        collected = row.document_id
        doc = (
            await db.execute(select(Document).where(Document.id == collected))
        ).scalars().first()
        doc.classification = "highly_restricted"
        doc.tenant_visible = False
        await db.commit()
    async with file_db() as db:
        second = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=1000, cursor=first.cursor
        )
    # The documented limit: a Document edit does not move the resource-row watermark, so the
    # digest reports no change and the page count reason stands.
    assert second.coverage_reason in (ca.REASON_BUDGET, "")
    # A resumed walk is never authoritative regardless, which is the safety property.
    assert second.snapshot_consistent is False
    assert second.authoritative is False
    # And the safe path excludes the now-ineligible document.
    async with file_db() as db:
        revalidated = await ca.revalidate_document_ids(
            db, access=access_for(), document_ids=[collected]
        )
    assert collected not in ca.currently_authorized(revalidated)


async def test_an_unchanged_digest_is_not_treated_as_proof_of_an_unchanged_corpus(
    file_db, connector, provider, knowledge
):
    """B1: a resumed traversal is never authoritative, whatever the digest says."""
    await seed_resources(file_db, provider, 10, name="c-b1-not-proof")
    first = None
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=3)
    assert first.cursor is not None
    async with file_db() as db:
        second = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=3, cursor=first.cursor
        )
    # No change occurred, so the digest matched and the reason is the mechanical one.
    assert second.coverage_reason == ca.REASON_BUDGET
    # But a resumed traversal still cannot claim snapshot consistency or authority.
    assert second.snapshot_consistent is False
    assert second.authoritative is False


# ============================================ B2: exact candidate authorization


async def test_exact_scope_is_unaffected_by_hundreds_of_unrelated_resources(
    file_db, connector, provider, knowledge
):
    """B2: 65 requested sources among 300 unrelated resources."""
    instance_id = await seed_resources(file_db, provider, 365, name="c-b2-isolation")
    requested = [ca.CandidateRef(instance_id, f"m{i:03d}") for i in range(65)]

    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), resource_refs=requested, budget=65
        )
    assert result.coverage is ca.CoverageStatus.COMPLETE
    assert result.provider_checks == 65, (
        f"unrelated resources consumed budget: {result.provider_checks} checks for 65 refs"
    )
    assert result.rows_fetched <= 65, f"fetched {result.rows_fetched} rows for 65 identities"
    assert len(result.authorized_document_ids) == 65
    assert result.authoritative is True


async def test_exact_scope_reports_incomplete_when_the_budget_cannot_cover_the_request(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 20, name="c-b2-budget")
    requested = [ca.CandidateRef(instance_id, f"m{i:03d}") for i in range(20)]
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), resource_refs=requested, budget=5
        )
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.coverage_reason == ca.REASON_BUDGET
    assert result.cursor is None, "exact scope does not paginate"
    assert result.authoritative is False
    assert ca.currently_authorized(result) == frozenset()


async def test_exact_scope_rejects_unresolved_and_cross_tenant_references_safely(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 4, name="c-b2-refs")
    refs = [
        ca.CandidateRef(instance_id, "m000"),
        ca.CandidateRef(instance_id, "does-not-exist"),
        ca.CandidateRef("instance-from-another-tenant", "m001"),
    ]
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), resource_refs=refs, budget=10
        )
    by_ext = {d.external_id: d for d in result.decisions}
    assert by_ext["m000"].state is ca.CandidateState.AUTHORIZED
    # Both failures give the same answer, so nothing is disclosed about the other tenant.
    assert by_ext["does-not-exist"].state is ca.CandidateState.UNAVAILABLE
    assert by_ext["does-not-exist"].reason == ca.REASON_REFERENCE_UNRESOLVED
    assert by_ext["m001"].state is ca.CandidateState.UNAVAILABLE
    assert by_ext["m001"].reason == ca.REASON_REFERENCE_UNRESOLVED
    # Unresolved identities cost no provider budget: only m000 was checked.
    assert result.provider_checks == 1
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.authoritative is False


async def test_exact_scope_does_not_leak_across_tenants(file_db, connector, provider, knowledge):
    instance_id = await seed_resources(file_db, provider, 3, name="c-b2-tenant")
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db,
            access=access_for(tenant_id=TENANT_B),
            resource_refs=[ca.CandidateRef(instance_id, "m000")],
            budget=5,
        )
    assert result.decisions[0].state is ca.CandidateState.UNAVAILABLE
    assert result.decisions[0].document_id is None
    assert result.authorized_document_ids == frozenset()
    assert result.provider_checks == 0


def test_malformed_references_are_rejected():
    with pytest.raises(ValueError):
        ca.CandidateRef("", "m001")
    with pytest.raises(ValueError):
        ca.CandidateRef("instance", "   ")
    with pytest.raises(ValueError):
        ca.CandidateRef(None, "m001")  # type: ignore[arg-type]


async def test_exact_scope_requires_some_identity(file_db, connector, provider, knowledge):
    async with file_db() as db:
        with pytest.raises(ValueError):
            await ca.authorize_exact_candidates(db, access=access_for())
        with pytest.raises(ValueError):
            await ca.authorize_exact_candidates(
                db, access=access_for(), resource_refs=[ca.CandidateRef("i", "e")], budget=0
            )
        # An empty request is a caller bug for a pinned scope, not a satisfied one.
        with pytest.raises(ValueError):
            await ca.authorize_exact_candidates(db, access=access_for(), resource_refs=[])
        with pytest.raises(ValueError):
            await ca.authorize_exact_candidates(db, access=access_for(), document_ids=[])


async def test_exact_scope_deduplicates_repeated_identities(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 3, name="c-b2-dedupe")
    refs = [ca.CandidateRef(instance_id, "m000")] * 4
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), resource_refs=refs, budget=10
        )
    assert result.provider_checks == 1, "a repeated identity must not cost repeated checks"
    assert result.coverage is ca.CoverageStatus.COMPLETE


async def test_exact_scope_applies_local_policy_before_provider_budget(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 3, name="c-b2-policy")
    async with file_db() as db:
        row = await _resource(db, instance_id, "m001")
        doc = (
            await db.execute(select(Document).where(Document.id == row.document_id))
        ).scalars().first()
        doc.classification = "highly_restricted"
        doc.tenant_visible = False
        await db.commit()
    refs = [ca.CandidateRef(instance_id, f"m{i:03d}") for i in range(3)]
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), resource_refs=refs, budget=10
        )
    assert result.counts()[ca.CandidateState.LOCAL_POLICY_EXCLUDED.value] == 1
    assert result.provider_checks == 2, "a locally excluded source must not cost a check"
    assert result.coverage is ca.CoverageStatus.COMPLETE


async def test_exact_scope_resolves_by_document_id(file_db, connector, provider, knowledge):
    instance_id = await seed_resources(file_db, provider, 3, name="c-b2-docid")
    async with file_db() as db:
        row = await _resource(db, instance_id, "m002")
        target = row.document_id
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), document_ids=[target], budget=5
        )
    assert result.coverage is ca.CoverageStatus.COMPLETE
    assert result.authorized_document_ids == frozenset({target})
    assert result.provider_checks == 1


async def test_exact_scope_preserves_the_paginated_catalog_operation(
    file_db, connector, provider, knowledge
):
    """B2: exact scope is additive; traversal with a cursor still works."""
    instance_id = await seed_resources(file_db, provider, 12, name="c-b2-both")
    async with file_db() as db:
        traversed = await ca.authorize_candidates(db, access=access_for(), query="q", budget=5)
    assert traversed.cursor is not None, "traversal still paginates"
    async with file_db() as db:
        exact = await ca.authorize_exact_candidates(
            db,
            access=access_for(),
            resource_refs=[ca.CandidateRef(instance_id, "m011")],
            budget=5,
        )
    assert exact.cursor is None, "exact scope does not paginate"
    assert exact.coverage is ca.CoverageStatus.COMPLETE


async def test_exact_scope_does_not_mutate_shared_content(file_db, connector, provider, knowledge):
    instance_id = await seed_resources(file_db, provider, 3, name="c-b2-nomutate")
    provider.access[("u1", "m000")] = False
    async with file_db() as db:
        await ca.authorize_exact_candidates(
            db,
            access=access_for(),
            resource_refs=[ca.CandidateRef(instance_id, "m000")],
            budget=5,
        )
        await db.commit()
    async with file_db() as db:
        row = await _resource(db, instance_id, "m000")
    assert row.lifecycle_state == "active", "CA1 non-mutation must hold for exact scope"


# ============================================ B3: stale cross-page authorization


async def test_revocation_cannot_survive_into_an_authoritative_output(
    file_db, connector, provider, knowledge
):
    """B3 before/after. Page one authorizes A; A is revoked; later pages complete."""
    instance_id = await seed_resources(file_db, provider, 6, name="c-b3-stale")

    # 1. Page one authorizes resource A (m000) alone.
    async with file_db() as db:
        page_one = await ca.authorize_candidates(db, access=access_for(), query="q", budget=1)
    first_id = next(iter(page_one.authorized_document_ids))
    with_revoked = set(page_one.authorized_document_ids)
    assert len(with_revoked) == 1

    # 2. The provider revokes access to A.
    provider.access[("u1", "m000")] = False

    # 3. Later pages complete successfully.
    cursor = page_one.cursor
    while cursor is not None:
        async with file_db() as db:
            nxt = await ca.authorize_candidates(
                db, access=access_for(), query="q", budget=10, cursor=cursor
            )
        with_revoked |= set(nxt.authorized_document_ids)
        cursor = nxt.cursor

    # 4. The naive union that a caller would build across pages still contains A.
    assert first_id in with_revoked, "the naive cross-page union is expected to retain A"

    # The contract must not let that set be treated as authoritative current authorization.
    async with file_db() as db:
        revalidated = await ca.revalidate_document_ids(
            db, access=access_for(), document_ids=list(with_revoked)
        )
    current = ca.currently_authorized(revalidated)
    assert first_id not in current, "a revoked authorization survived into the output"
    assert current == with_revoked - {first_id}
    assert current != with_revoked

    # And the authoritative accessor must refuse outright rather than emit the subset: a pinned
    # report that requested every one of these sources must not silently drop the revoked one.
    with pytest.raises(ca.StrictScopeError) as exc:
        ca.require_strict_scope(revalidated)
    assert exc.value.reason == ca.REASON_STRICT_DENIED


async def test_a_resumable_traversal_is_never_authoritative(
    file_db, connector, provider, knowledge
):
    await seed_resources(file_db, provider, 40, name="c-b3-trav")
    last, ids = await walk(file_db, access_for(), budget=7)
    assert len(ids) == 40
    assert last.coverage is ca.CoverageStatus.COMPLETE
    assert last.snapshot_consistent is False
    assert last.authoritative is False, "a multi-page walk must not be authoritative"


async def test_a_single_page_traversal_is_snapshot_consistent(
    file_db, connector, provider, knowledge
):
    await seed_resources(file_db, provider, 5, name="c-b3-single")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=10)
    assert result.coverage is ca.CoverageStatus.COMPLETE
    assert result.snapshot_consistent is True
    assert result.authoritative is True


async def test_an_unresolved_single_page_is_not_authoritative(
    file_db, connector, provider, knowledge
):
    from app.services.connectors.base import ConnectorAuthUnavailable

    await seed_resources(file_db, provider, 3, name="c-b3-unres")
    provider.access_error = ConnectorAuthUnavailable("provider down", code="provider_error")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=10)
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.authoritative is False
    assert ca.currently_authorized(result) == frozenset()


async def test_revalidation_returns_nothing_when_it_cannot_be_trusted(
    file_db, connector, provider, knowledge
):
    from app.services.connectors.base import ConnectorAuthUnavailable

    instance_id = await seed_resources(file_db, provider, 4, name="c-b3-untrusted")
    async with file_db() as db:
        row = await _resource(db, instance_id, "m000")
        target = row.document_id
    provider.access_error = ConnectorAuthUnavailable("provider down", code="provider_error")
    async with file_db() as db:
        result = await ca.revalidate_document_ids(
            db, access=access_for(), document_ids=[target]
        )
    assert result.authoritative is False
    assert ca.currently_authorized(result) == frozenset()


async def test_revalidation_does_not_mutate_content(file_db, connector, provider, knowledge):
    instance_id = await seed_resources(file_db, provider, 3, name="c-b3-nomutate")
    provider.access[("u1", "m001")] = False
    async with file_db() as db:
        row = await _resource(db, instance_id, "m001")
        target = row.document_id
    async with file_db() as db:
        await ca.revalidate_document_ids(db, access=access_for(), document_ids=[target])
        await db.commit()
    async with file_db() as db:
        row = await _resource(db, instance_id, "m001")
    assert row.lifecycle_state == "active"
    assert row.permission_state != "revoked"


# ====================================== DENIED versus UNAVAILABLE, and consistency


def test_denial_scope_maps_totally_onto_candidate_states():
    """The two vocabularies are pinned together, so they cannot drift apart."""
    assert ca._state_for(AuthorizationOutcome.allow())[0] is ca.CandidateState.AUTHORIZED
    assert (
        ca._state_for(AuthorizationOutcome.deny("access_revoked", DenialScope.PRINCIPAL))[0]
        is ca.CandidateState.DENIED
    )
    for scope in (DenialScope.REQUEST, DenialScope.CONNECTOR, DenialScope.CONTENT):
        state, _ = ca._state_for(AuthorizationOutcome.deny("some_reason", scope))
        assert state is ca.CandidateState.UNAVAILABLE, f"{scope} must be unavailable"


async def test_a_missing_provider_mapping_is_denied_and_is_documented_as_local(
    file_db, connector, provider, knowledge
):
    """DENIED includes definitive local refusal, not only a direct provider answer."""
    await seed_resources(file_db, provider, 2, name="c-unmapped")
    async with file_db() as db:
        result = await ca.authorize_candidates(
            db, access=access_for(PRINCIPAL_C), query="q", budget=5
        )
    assert result.counts()[ca.CandidateState.DENIED.value] == 2
    assert result.authorized_document_ids == frozenset()
    assert "mapping" in (ca._state_for.__doc__ or "").lower(), (
        "the DENIED documentation must not imply every denial came from the provider"
    )


# ================================================ traversal behaviour, preserved


async def test_continuation_reaches_every_authorized_resource_beyond_100(
    file_db, connector, provider, knowledge
):
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
    assert first == second
    assert len(first) == 40


async def test_candidate_beyond_the_first_window_is_reachable_with_an_unrelated_title(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, N, name="c-adversarial")
    async with file_db() as db:
        resource = await _resource(db, instance_id, f"m{N - 1:03d}")
        tail_id = resource.document_id
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


async def test_scan_bound_is_enforced_in_sql_and_does_not_materialise_the_corpus(
    file_db, connector, provider, knowledge
):
    await seed_resources(file_db, provider, N, name="c-sql-bound")
    async with file_db() as db:
        result = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=1000, max_scan_rows=20
        )
    assert result.rows_fetched <= 21, f"fetched {result.rows_fetched} rows for a 20-row bound"
    assert result.rows_fetched < N, "the corpus must not be materialised"
    assert result.coverage_reason == ca.REASON_SCAN_BOUND
    assert result.cursor is not None


async def test_a_small_scan_window_never_skips_a_candidate(
    file_db, connector, provider, knowledge
):
    """A budget-proportional window changes the page count, never the reachability."""
    await seed_resources(file_db, provider, N, name="c-window-progress")
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
    assert len(seen) == N, f"only {len(seen)} of {N} reached with a 15-row window"
    assert pages >= 10


async def test_local_policy_excluded_candidates_do_not_consume_budget_or_skip_the_walk(
    file_db, connector, provider, knowledge
):
    """Locally ineligible rows are handled, not skipped, and cost no provider check."""
    instance_id = await seed_resources(file_db, provider, 6, name="c-window-eligible")
    async with file_db() as db:
        for ext in ("m000", "m001", "m002", "m003"):
            row = await _resource(db, instance_id, ext)
            doc = (
                await db.execute(select(Document).where(Document.id == row.document_id))
            ).scalars().first()
            doc.classification = "highly_restricted"
            doc.tenant_visible = False
        await db.commit()

    # Budget 1 per page and a window of 2, so the walk must cross the ineligible prefix
    # without skipping the eligible rows behind it.
    seen: set[str] = set()
    cursor = None
    for _ in range(50):
        async with file_db() as db:
            result = await ca.authorize_candidates(
                db,
                access=access_for(),
                query="q",
                budget=1,
                cursor=cursor,
                max_scan_rows=2,
                connector_instance_ids=[instance_id],
            )
        seen |= result.authorized_document_ids
        cursor = result.cursor
        if cursor is None:
            break
    assert len(seen) == 2, f"expected the two eligible resources, reached {len(seen)}"


async def test_unresolved_failure_on_an_earlier_page_cannot_later_claim_complete(
    file_db, connector, provider, knowledge
):
    from app.services.connectors.base import ConnectorAuthUnavailable

    await seed_resources(file_db, provider, 3, name="c-sticky")
    provider.access_error = ConnectorAuthUnavailable("provider down", code="provider_error")
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=2)
    assert first.unresolved is True
    provider.access_error = None
    async with file_db() as db:
        second = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=10, cursor=first.cursor
        )
    assert second.coverage is ca.CoverageStatus.PARTIAL
    assert second.coverage_reason == ca.REASON_UNAVAILABLE
    assert second.unresolved is True


async def test_outage_makes_coverage_partial_even_when_the_walk_completes(
    file_db, connector, provider, knowledge
):
    from app.services.connectors.base import ConnectorAuthUnavailable

    await seed_resources(file_db, provider, 3, name="c-outage-complete")
    provider.access_error = ConnectorAuthUnavailable("provider down", code="provider_error")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=10)
    assert result.examined == 3
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.coverage_reason == ca.REASON_UNAVAILABLE
    assert result.counts()[ca.CandidateState.UNAVAILABLE.value] == 3
    assert result.counts()[ca.CandidateState.DENIED.value] == 0, "an outage is not a denial"


async def test_denied_first_window_still_makes_progress(file_db, connector, provider, knowledge):
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
    assert result.counts()[ca.CandidateState.UNAVAILABLE.value] == 2
    assert result.authorized_document_ids == frozenset()
    assert result.cursor is not None
    provider.access_error = None
    async with file_db() as db:
        nxt = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=5, cursor=result.cursor
        )
    assert len(nxt.authorized_document_ids) >= 1


async def test_revocation_ahead_of_the_cursor_is_denied_on_the_next_page(
    file_db, connector, provider, knowledge
):
    await seed_resources(file_db, provider, 6, name="c-revoke-ahead")
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=2)
    authorized_first = {d.external_id for d in first.decisions if d.document_id}
    assert authorized_first == {"m000", "m001"}
    provider.access[("u1", "m003")] = False
    async with file_db() as db:
        second = await ca.authorize_candidates(
            db, access=access_for(), query="q", budget=2, cursor=first.cursor
        )
    by_ext = {d.external_id: d for d in second.decisions}
    assert "m003" in by_ext, "the next page must actually examine m003"
    assert by_ext["m003"].state is ca.CandidateState.DENIED
    assert "m003" not in second.authorized_document_ids


async def test_revocation_of_a_previously_authorized_resource_is_honoured_immediately(
    file_db, connector, provider, knowledge
):
    await seed_resources(file_db, provider, 5, name="c-revoke-cached")
    _, before = await walk(file_db, access_for(), budget=2)
    assert len(before) == 5
    provider.access[("u1", "m002")] = False
    _, after = await walk(file_db, access_for(), budget=2)
    assert len(after) == 4, "the revoked resource must not survive a revalidation"
    assert after < before


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
    summary = result.user_facing_summary()
    assert set(summary) == {"coverage", "partial", "reason"}
    assert not any(
        isinstance(v, int) and not isinstance(v, bool) for v in summary.values()
    ), "the user-facing summary must not carry counts"


async def test_authorization_traversal_is_independent_of_query_relevance(
    file_db, connector, provider, knowledge
):
    """Traversal is not retrieval: relevance must not change what is authorized."""
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
    for name in ("score", "relevance", "rank", "similarity", "confidence", "vector"):
        assert not hasattr(result, name)
        assert not hasattr(result.decisions[0], name)


async def test_tenant_isolation_is_preserved(file_db, connector, provider, knowledge):
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


# ================= strict exact-scope semantics: decisions versus authorization


async def test_partially_revoked_pinned_scope_cannot_silently_exclude_the_revoked_source(
    file_db, connector, provider, knowledge
):
    """Correction 1: a completed decision pass is not a satisfied pinned scope.

    Every source is decided, so coverage is COMPLETE. Three sources are revoked, so the pinned
    requirement is NOT met. Treating the two as the same thing is how an authoritative output
    would silently emit 9 of the 12 requested sources.
    """
    instance_id = await seed_resources(file_db, provider, 12, name="c-strict-revoked")
    refs = [ca.CandidateRef(instance_id, f"m{i:03d}") for i in range(12)]
    for ext in ("m002", "m005", "m009"):
        provider.access[("u1", ext)] = False

    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), resource_refs=refs, budget=12
        )

    # Every requested source received a decision.
    assert len(result.decisions) == 12
    assert result.coverage is ca.CoverageStatus.COMPLETE
    assert result.scope_kind is ca.ScopeKind.EXACT

    # But the pinned scope is not satisfied, and the reason names the cause.
    assert result.strict_scope_satisfied is False
    assert result.strict_exclusion_reason == ca.REASON_STRICT_DENIED

    # The permissive accessor would have returned 9 and dropped 3 without saying so.
    assert len(ca.currently_authorized(result)) == 9

    # The authoritative accessor refuses instead of emitting the subset.
    with pytest.raises(ca.StrictScopeError) as exc:
        ca.require_strict_scope(result)
    assert exc.value.reason == ca.REASON_STRICT_DENIED


async def test_a_fully_authorized_pinned_scope_is_strict(file_db, connector, provider, knowledge):
    instance_id = await seed_resources(file_db, provider, 12, name="c-strict-ok")
    refs = [ca.CandidateRef(instance_id, f"m{i:03d}") for i in range(12)]
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), resource_refs=refs, budget=12
        )
    assert result.strict_scope_satisfied is True
    assert result.strict_exclusion_reason == ""
    assert len(ca.require_strict_scope(result)) == 12


async def test_strict_scope_fails_on_a_locally_restricted_source(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 6, name="c-strict-restricted")
    async with file_db() as db:
        row = await _resource(db, instance_id, "m003")
        doc = (
            await db.execute(select(Document).where(Document.id == row.document_id))
        ).scalars().first()
        doc.classification = "highly_restricted"
        doc.tenant_visible = False
        await db.commit()
    refs = [ca.CandidateRef(instance_id, f"m{i:03d}") for i in range(6)]
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), resource_refs=refs, budget=6
        )
    assert result.coverage is ca.CoverageStatus.COMPLETE
    assert result.strict_scope_satisfied is False
    assert result.strict_exclusion_reason == ca.REASON_STRICT_LOCAL_POLICY
    with pytest.raises(ca.StrictScopeError) as exc:
        ca.require_strict_scope(result)
    assert exc.value.reason == ca.REASON_STRICT_LOCAL_POLICY


async def test_strict_scope_fails_on_an_unresolved_reference(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 4, name="c-strict-missing")
    refs = [
        ca.CandidateRef(instance_id, "m000"),
        ca.CandidateRef(instance_id, "not-a-real-source"),
    ]
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), resource_refs=refs, budget=4
        )
    assert result.strict_scope_satisfied is False
    assert result.strict_exclusion_reason == ca.REASON_STRICT_UNAVAILABLE
    with pytest.raises(ca.StrictScopeError) as exc:
        ca.require_strict_scope(result)
    assert exc.value.reason == ca.REASON_STRICT_UNAVAILABLE


async def test_strict_scope_fails_when_the_budget_leaves_sources_undecided(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 10, name="c-strict-budget")
    refs = [ca.CandidateRef(instance_id, f"m{i:03d}") for i in range(10)]
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), resource_refs=refs, budget=4
        )
    assert result.strict_scope_satisfied is False
    assert result.strict_exclusion_reason == ca.REASON_STRICT_BUDGET
    with pytest.raises(ca.StrictScopeError):
        ca.require_strict_scope(result)


# ================= correction 2: finite exact-source provider budgets


async def test_an_omitted_budget_is_finite_and_covers_the_existing_report_shapes(
    file_db, connector, provider, knowledge
):
    """65 to 100 source reports must keep working with no explicit budget."""
    instance_id = await seed_resources(file_db, provider, 100, name="c-budget-default")
    refs = [ca.CandidateRef(instance_id, f"m{i:03d}") for i in range(100)]
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(db, access=access_for(), resource_refs=refs)

    assert ca.DEFAULT_EXACT_BUDGET >= 100, "the default must cover a 100 source report"
    assert ca.DEFAULT_EXACT_BUDGET < 10_000, "the default must be finite, not effectively open"
    assert result.provider_checks == 100
    assert result.provider_checks <= ca.DEFAULT_EXACT_BUDGET
    assert result.coverage is ca.CoverageStatus.COMPLETE
    assert result.strict_scope_satisfied is True


async def test_an_oversized_exact_budget_is_refused_rather_than_clamped(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 3, name="c-budget-oversize")
    refs = [ca.CandidateRef(instance_id, "m000")]
    async with file_db() as db:
        with pytest.raises(ValueError):
            await ca.authorize_exact_candidates(
                db, access=access_for(), resource_refs=refs, budget=ca.MAX_EXACT_BUDGET + 1
            )
        with pytest.raises(ValueError):
            await ca.authorize_exact_candidates(
                db, access=access_for(), resource_refs=refs, budget=1_000_000
            )


async def test_a_request_larger_than_the_default_budget_stays_bounded_and_not_strict(
    file_db, connector, provider, knowledge
):
    """Correction 2: provider calls stay bounded even when the request is larger."""
    total = ca.DEFAULT_EXACT_BUDGET + 40
    instance_id = await seed_resources(file_db, provider, total, name="c-budget-bounded")
    refs = [ca.CandidateRef(instance_id, f"m{i:03d}") for i in range(total)]

    async with file_db() as db:
        result = await ca.authorize_exact_candidates(db, access=access_for(), resource_refs=refs)

    assert result.provider_checks == ca.DEFAULT_EXACT_BUDGET, "provider calls must be bounded"
    assert result.provider_checks < total
    assert result.coverage is ca.CoverageStatus.PARTIAL
    assert result.strict_scope_satisfied is False
    assert result.strict_exclusion_reason == ca.REASON_STRICT_BUDGET
    with pytest.raises(ca.StrictScopeError):
        ca.require_strict_scope(result)


# ================= correction 3: traversal is never strict pinned-report authorization


async def test_a_single_page_traversal_is_never_strict(file_db, connector, provider, knowledge):
    """A completed traversal, even in one page, is not pinned-report authorization."""
    await seed_resources(file_db, provider, 5, name="c-strict-traversal")
    async with file_db() as db:
        result = await ca.authorize_candidates(db, access=access_for(), query="q", budget=10)

    # Complete, and authoritative at the traversal level.
    assert result.coverage is ca.CoverageStatus.COMPLETE
    assert result.authoritative is True
    assert result.snapshot_consistent is True

    # But never strict, because it has no requested source set to cover.
    assert result.scope_kind is ca.ScopeKind.TRAVERSAL
    assert result.strict_scope_satisfied is False
    assert result.strict_exclusion_reason == ca.REASON_STRICT_NOT_APPLICABLE

    with pytest.raises(ca.StrictScopeError) as exc:
        ca.require_strict_scope(result)
    assert exc.value.reason == ca.REASON_STRICT_NOT_APPLICABLE


async def test_a_resumable_traversal_is_also_never_strict(
    file_db, connector, provider, knowledge
):
    await seed_resources(file_db, provider, 12, name="c-strict-traversal-resumable")
    async with file_db() as db:
        first = await ca.authorize_candidates(db, access=access_for(), query="q", budget=5)
    assert first.scope_kind is ca.ScopeKind.TRAVERSAL
    assert first.strict_scope_satisfied is False
    with pytest.raises(ca.StrictScopeError):
        ca.require_strict_scope(first)


# ============= candidate identity: resource_namespace is part of the schema uniqueness key


class Twin:
    """A row retagged so it shares an external_id with another row in a different namespace."""

    def __init__(self, row_id: str, document_id: str, original_namespace: str, namespace: str):
        self.row_id = row_id
        self.document_id = document_id
        self.original_namespace = original_namespace
        self.namespace = namespace


async def make_namespaced_twin(
    db_factory, instance_id: str, *, source_ext: str, namespace: str, external_id: str
) -> Twin:
    """Retag one ingested row into another namespace with a chosen external_id.

    ``uq_external_resource_identity`` is
    ``(tenant_id, connector_instance_id, resource_namespace, external_id)``, so retagging a row
    is a legal collision and creates exactly the case the identity model must keep distinct: two
    rows in one connector instance that share an ``external_id`` and differ only by namespace.
    """
    async with db_factory() as db:
        row = (
            await db.execute(
                select(ExternalResource).where(
                    ExternalResource.connector_instance_id == instance_id,
                    ExternalResource.external_id == source_ext,
                )
            )
        ).scalars().first()
        assert row is not None
        original_namespace = row.resource_namespace
        twin = Twin(row.id, row.document_id, original_namespace, namespace)
        row.resource_namespace = namespace
        row.external_id = external_id
        await db.commit()
    return twin


async def test_two_namespaces_sharing_an_external_id_are_distinct_in_a_traversal(
    file_db, connector, provider, knowledge
):
    """The defect: keying on (instance, external_id) collapses two distinct rows into one."""
    instance_id = await seed_resources(file_db, provider, 2, name="c-ns-distinct")
    twin = await make_namespaced_twin(
        file_db, instance_id, source_ext="m001", namespace="ca2-other-namespace",
        external_id="m000",
    )

    last, ids = await walk(file_db, access_for(), budget=10)

    # Both rows are candidates and both documents are reachable.
    assert len(ids) == 2, f"expected both namespaced rows, reached {len(ids)} of 2"
    assert twin.document_id in ids

    # And the result does not collapse them into a single decision.
    twins = [d for d in last.decisions if d.external_id == "m000"]
    assert len(twins) == 2, "two namespaced rows must produce two decisions"
    assert {d.resource_namespace for d in twins} == {twin.original_namespace, twin.namespace}
    assert len({d.document_id for d in twins}) == 2, "each twin keeps its own document"


async def test_namespaced_twins_survive_a_continuation_page_boundary(
    file_db, connector, provider, knowledge
):
    """A keyset on (instance, external_id) alone skips the second twin at the boundary."""
    instance_id = await seed_resources(file_db, provider, 2, name="c-ns-boundary")
    twin = await make_namespaced_twin(
        file_db, instance_id, source_ext="m001", namespace="ca2-other-namespace",
        external_id="m000",
    )

    seen_identities: set[tuple[str, str, str]] = set()
    seen_documents: set[str] = set()
    cursor = None
    pages = 0
    for _ in range(20):
        async with file_db() as db:
            result = await ca.authorize_candidates(
                db, access=access_for(), query="q", budget=1, cursor=cursor
            )
        for decision in result.decisions:
            seen_identities.add(
                (decision.connector_instance_id, decision.resource_namespace, decision.external_id)
            )
        seen_documents |= set(result.authorized_document_ids)
        cursor = result.cursor
        pages += 1
        if cursor is None:
            break

    assert pages >= 2, "the page boundary must actually be crossed"
    assert len(seen_documents) == 2, f"reached {len(seen_documents)} of 2 namespaced rows"
    assert twin.document_id in seen_documents
    assert len(seen_identities) == 2, "a twin was duplicated or lost across the boundary"


async def test_a_bare_reference_matching_two_namespaces_fails_closed(
    file_db, connector, provider, knowledge
):
    """An identity the caller did not disambiguate must not resolve to an arbitrary namespace."""
    instance_id = await seed_resources(file_db, provider, 2, name="c-ns-ambiguous")
    await make_namespaced_twin(
        file_db, instance_id, source_ext="m001", namespace="ca2-other-namespace",
        external_id="m000",
    )

    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db,
            access=access_for(),
            resource_refs=[ca.CandidateRef(instance_id, "m000")],
            budget=5,
        )

    assert result.provider_checks == 0, "an ambiguous identity must not spend provider budget"
    assert len(result.decisions) == 1, "the requested identity still receives a decision"
    assert result.decisions[0].state is ca.CandidateState.UNAVAILABLE
    assert result.decisions[0].reason == ca.REASON_REFERENCE_AMBIGUOUS
    assert result.decisions[0].document_id is None
    assert result.authorized_document_ids == frozenset()
    assert result.strict_scope_satisfied is False
    assert result.strict_exclusion_reason == ca.REASON_STRICT_AMBIGUOUS
    with pytest.raises(ca.StrictScopeError) as exc:
        ca.require_strict_scope(result)
    assert exc.value.reason == ca.REASON_STRICT_AMBIGUOUS


async def test_a_namespaced_reference_resolves_exactly_one_row(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_resources(file_db, provider, 2, name="c-ns-exact")
    twin = await make_namespaced_twin(
        file_db, instance_id, source_ext="m001", namespace="ca2-other-namespace",
        external_id="m000",
    )

    async with file_db() as db:
        for_ns = await ca.authorize_exact_candidates(
            db,
            access=access_for(),
            resource_refs=[
                ca.CandidateRef(instance_id, "m000", resource_namespace=twin.namespace)
            ],
            budget=5,
        )
    assert for_ns.provider_checks == 1
    assert for_ns.decisions[0].state is ca.CandidateState.AUTHORIZED
    assert for_ns.decisions[0].resource_namespace == twin.namespace
    assert for_ns.decisions[0].document_id == twin.document_id
    assert for_ns.strict_scope_satisfied is True

    async with file_db() as db:
        for_original = await ca.authorize_exact_candidates(
            db,
            access=access_for(),
            resource_refs=[
                ca.CandidateRef(instance_id, "m000", resource_namespace=twin.original_namespace)
            ],
            budget=5,
        )
    assert for_original.provider_checks == 1
    assert for_original.decisions[0].resource_namespace == twin.original_namespace
    assert for_original.decisions[0].document_id not in (None, twin.document_id)

    # Both namespaced references together stay distinct and each is checked once.
    async with file_db() as db:
        both = await ca.authorize_exact_candidates(
            db,
            access=access_for(),
            resource_refs=[
                ca.CandidateRef(instance_id, "m000", resource_namespace=twin.namespace),
                ca.CandidateRef(
                    instance_id, "m000", resource_namespace=twin.original_namespace
                ),
            ],
            budget=5,
        )
    assert both.provider_checks == 2, "two distinct rows cost two checks"
    assert len(both.authorized_document_ids) == 2


async def test_the_same_resource_by_reference_and_document_id_costs_one_provider_check(
    file_db, connector, provider, knowledge
):
    """Reference and document id naming one row must not double the provider work."""
    instance_id = await seed_resources(file_db, provider, 3, name="c-row-dedupe")
    async with file_db() as db:
        row = await _resource(db, instance_id, "m001")
        target_document = row.document_id
        namespace = row.resource_namespace

    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db,
            access=access_for(),
            resource_refs=[
                ca.CandidateRef(instance_id, "m001", resource_namespace=namespace)
            ],
            document_ids=[target_document],
            budget=5,
        )

    assert result.provider_checks == 1, "the same row must be authorized only once"
    assert len(result.decisions) == 2, "but every requested identity still gets a decision"
    assert all(d.state is ca.CandidateState.AUTHORIZED for d in result.decisions), [
        (d.external_id, d.resource_namespace, d.state.value, d.reason) for d in result.decisions
    ]
    assert result.authorized_document_ids == frozenset({target_document})
    assert result.strict_scope_satisfied is True


async def test_exact_input_size_is_bounded(file_db, connector, provider, knowledge):
    instance_id = await seed_resources(file_db, provider, 3, name="c-input-bound")

    too_many = [ca.CandidateRef(instance_id, f"x{i}") for i in range(ca.MAX_EXACT_INPUTS + 1)]
    async with file_db() as db:
        with pytest.raises(ValueError):
            await ca.authorize_exact_candidates(
                db, access=access_for(), resource_refs=too_many, budget=5
            )

    # Exactly at the bound is accepted, and unresolved identities cost no provider budget.
    at_bound = [ca.CandidateRef(instance_id, f"y{i}") for i in range(ca.MAX_EXACT_INPUTS)]
    async with file_db() as db:
        result = await ca.authorize_exact_candidates(
            db, access=access_for(), resource_refs=at_bound, budget=5
        )
    assert result.provider_checks == 0
    assert result.strict_scope_satisfied is False
    assert ca.MAX_EXACT_INPUTS <= ca.MAX_EXACT_RESOLVED_ROWS
