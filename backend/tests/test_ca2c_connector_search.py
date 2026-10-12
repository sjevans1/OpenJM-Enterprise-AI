"""CA2-C: connector search completeness.

The defect this package fixes: `_connector_search` read an arbitrary 200-row window of the tenant's
connector resources and only then tested each row's title and external id, so a resource whose
metadata matched but which sat beyond position 200 was permanently invisible. Measured here.

Fixture-only, in-process, no network and no model. The vector engine is stubbed as elsewhere in the
suite; connector search does not use it.

The tests call the tool's implementation function directly, which owns the response contract, and
separately assert that the tool is still registered under its published name.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import func, select

from test_vs7_connector_security import (  # noqa: F401
    PRINCIPAL_A,
    TENANT_A,
    TENANT_B,
    _instance,
    _make_instance,
    _mapping,
    _run_initial,
    _seed,
    connector,
    knowledge,
    provider,
)

from app.core.context import reset_principal, set_principal
from app.core.identity import Principal
from app.core.permissions import permissions_for_role
from app.models import Document, ExternalResource
from app.services.actions.connector_tools import (
    CONNECTOR_TOOL_NAMES,
    MAX_MATCHES,
    MATCH_SCAN_LIMIT,
    _connector_search,
)


def request_context(principal_id: str = PRINCIPAL_A, tenant_id: str = TENANT_A):
    import contextlib

    @contextlib.contextmanager
    def _cm():
        principal = Principal(
            principal_id=principal_id,
            tenant_id=tenant_id,
            subject="ca2c-subject",
            role="owner",
            membership_id="ca2c-membership",
            auth_method="local-dev",
            permissions=permissions_for_role("owner"),
        )
        set_principal(principal)
        try:
            yield principal
        finally:
            reset_principal()

    return _cm()


async def seed_titled(maker, provider_, n: int, *, name: str, body_marker_at: int | None = None) -> str:
    """Seed n connector resources, each with a distinctive, searchable title.

    Titles are authored here rather than taken from ingest defaults so a test can state exactly
    which resource must match.
    """
    await _seed(maker)
    instance_id = await _make_instance(maker, TENANT_A, name=name)
    for i in range(n):
        external_id = f"m{i:03d}"
        body = f"informational body text {i}"
        if body_marker_at is not None and i == body_marker_at:
            body = f"{body} bodyrelevancetoken"
        provider_.put(external_id, text=body, revision="1")
        provider_.access[("u1", external_id)] = True
    async with maker() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance, limit=n + 50)
        await _mapping(db, instance_id, PRINCIPAL_A, "u1")
        rows = (
            await db.execute(
                select(ExternalResource).where(
                    ExternalResource.connector_instance_id == instance_id,
                    ExternalResource.tenant_id == TENANT_A,
                )
            )
        ).scalars().all()
        by_external = {row.external_id: row for row in rows}
        for i in range(n):
            row = by_external.get(f"m{i:03d}")
            if row is not None:
                row.title = f"quarterly-report-{i:03d}"
        await db.commit()
    return instance_id


async def rows_before(maker, instance_id: str, external_id: str) -> int:
    """How many resources in the same scope sort before this one under the search's own order."""
    async with maker() as db:
        return (
            await db.execute(
                select(func.count(ExternalResource.id)).where(
                    ExternalResource.connector_instance_id == instance_id,
                    ExternalResource.tenant_id == TENANT_A,
                    ExternalResource.external_id < external_id,
                )
            )
        ).scalar_one()


async def search(maker, *, query: str, connector_id: str | None = None) -> dict:
    arguments: dict = {"query": query}
    if connector_id:
        arguments["connector_id"] = connector_id
    async with maker() as db:
        return await _connector_search(db, arguments)


# ============================================================ the defect itself


async def test_the_tool_is_still_registered_under_its_published_name():
    assert "connector.search" in CONNECTOR_TOOL_NAMES


async def test_a_matching_resource_beyond_position_200_is_found(
    file_db, connector, provider, knowledge
):
    """The defect. With a 200 row window applied before matching, this resource is invisible."""
    instance_id = await seed_titled(file_db, provider, 300, name="c2c-beyond")
    rank = await rows_before(file_db, instance_id, "m280")
    assert rank >= 200, "the matching resource must genuinely sit beyond a 200 row window"

    with request_context():
        result = await search(file_db, query="quarterly-report-280")

    assert result["ok"] is True
    returned = {match["external_id"] for match in result["matches"]}
    assert "m280" in returned, (
        "a resource matching beyond position 200 was not returned; the match predicate must be "
        "applied before the candidate window"
    )
    assert len(returned) == 1, "only the matching resource should be returned"


async def test_matching_narrows_rather_than_widening(file_db, connector, provider, knowledge):
    instance_id = await seed_titled(file_db, provider, 40, name="c2c-narrow")
    with request_context():
        result = await search(file_db, query="quarterly-report-007")
    assert [match["external_id"] for match in result["matches"]] == ["m007"]
    assert result["matches_complete"] is True


async def test_external_id_matching_still_works(file_db, connector, provider, knowledge):
    instance_id = await seed_titled(file_db, provider, 10, name="c2c-extid")
    with request_context():
        result = await search(file_db, query="m003")
    assert "m003" in {match["external_id"] for match in result["matches"]}


async def test_search_matches_metadata_and_does_not_hard_filter_on_body(
    file_db, connector, provider, knowledge
):
    """A body-only match is not this tool's contract, and must not be silently claimed as one.

    The predicate is metadata only. Body relevance belongs to the Knowledge path, and pushing a
    body predicate into this query would hard-filter resources whose relevance appears only in their
    content.
    """
    instance_id = await seed_titled(
        file_db, provider, 10, name="c2c-body", body_marker_at=4
    )
    with request_context():
        result = await search(file_db, query="bodyrelevancetoken")
    assert result["ok"] is True
    assert result["matches"] == [], "connector search matches metadata, not document bodies"
    assert result["matches_complete"] is True


# ============================================================ security preserved


async def test_tenant_isolation_is_preserved(file_db, connector, provider, knowledge):
    instance_id = await seed_titled(file_db, provider, 20, name="c2c-tenant")
    with request_context(tenant_id=TENANT_B):
        result = await search(file_db, query="quarterly-report-003")
    assert result["matches"] == [], "another tenant's resources must never be searchable"


async def test_a_revoked_resource_is_excluded_and_counted_not_returned(
    file_db, connector, provider, knowledge
):
    instance_id = await seed_titled(file_db, provider, 20, name="c2c-revoked")
    provider.access[("u1", "m005")] = False
    with request_context():
        result = await search(file_db, query="quarterly-report-005")
    assert result["matches"] == [], "a revoked resource must not be returned"
    assert result["denied_count"] == 1, "a revocation is a denial, and is counted as one"


async def test_wildcards_in_the_query_are_treated_literally(
    file_db, connector, provider, knowledge
):
    """An unescaped % would act as a wildcard and widen the match beyond what was asked for."""
    instance_id = await seed_titled(file_db, provider, 30, name="c2c-wildcard")
    with request_context():
        result = await search(file_db, query="quarterly-report-0%")
    assert result["matches"] == [], "a literal % must not match everything"


# Each pair below is a case where Python's str.lower() accepts the match and a database
# case-folding predicate does not. They are the concrete counterexamples that make "narrow in SQL"
# unsound, and each one would have silently dropped an authorized resource before the Python matcher
# ever saw it. None of them can pass by a coincidence of an upper()/lower() round trip.
UNICODE_CASE_PAIRS = [
    pytest.param("ẞ", "ß", id="capital-sharp-s-vs-sharp-s"),
    pytest.param("İ", "i̇", id="dotted-capital-i-vs-i-combining-dot"),
    pytest.param("ǅ", "ǆ", id="titlecase-dz-vs-lowercase-dz"),
]


@pytest.mark.parametrize(("title", "query"), UNICODE_CASE_PAIRS)
async def test_case_folded_non_ascii_metadata_beyond_200_is_matched(
    file_db, connector, provider, knowledge, title, query
):
    """A case-folded non-ASCII match must be found, not dropped and not reported complete-empty."""
    instance_id = await seed_titled(file_db, provider, 300, name=f"c2c-fold-{ord(title)}")
    async with file_db() as db:
        row = (
            await db.execute(
                select(ExternalResource).where(
                    ExternalResource.connector_instance_id == instance_id,
                    ExternalResource.external_id == "m280",
                )
            )
        ).scalars().first()
        row.title = title
        await db.commit()

    before = await rows_before(file_db, instance_id, "m280")
    assert before > 200, (
        "the target must sit beyond the old 200-row window, otherwise this proves nothing"
    )

    with request_context():
        result = await search(file_db, query=query)

    assert "m280" in {match["external_id"] for match in result["matches"]}, (
        f"a resource titled {title!r} must match the query {query!r} because Python's case folding "
        "accepts it; no database-side predicate may drop it, and an empty result must not be "
        "reported as complete"
    )
    assert result["matches_complete"] is True


async def test_an_ascii_query_still_needs_python_case_folding(
    file_db, connector, provider, knowledge
):
    """Even a pure-ASCII query is unsafe to narrow in SQL.

    Python lowercases 'İ' to 'i̇', which CONTAINS an ASCII 'i'. So the query 'i' must match a resource
    titled 'İ', while the database's lower(title) leaves 'İ' unfolded and would not find it. This is
    why the narrowing was removed outright rather than retained for ASCII.
    """
    instance_id = await seed_titled(file_db, provider, 300, name="c2c-ascii-fold")
    async with file_db() as db:
        row = (
            await db.execute(
                select(ExternalResource).where(
                    ExternalResource.connector_instance_id == instance_id,
                    ExternalResource.external_id == "m280",
                )
            )
        ).scalars().first()
        row.title = "İ"
        await db.commit()

    with request_context():
        result = await search(file_db, query="i")

    assert "m280" in {match["external_id"] for match in result["matches"]}, (
        "the ASCII query 'i' is accepted by Python against a title of 'İ'; a database lower() does "
        "not fold it, so SQL narrowing would have dropped this authorized resource"
    )


async def test_a_non_ascii_match_beyond_the_scan_bound_is_reported_incomplete(
    file_db, connector, provider, knowledge, monkeypatch
):
    """The bound must never be presented as a complete absence, including for non-ASCII matches."""
    from app.services.actions import connector_tools as module

    instance_id = await seed_titled(file_db, provider, 40, name="c2c-fold-bound")
    async with file_db() as db:
        row = (
            await db.execute(
                select(ExternalResource).where(
                    ExternalResource.connector_instance_id == instance_id,
                    ExternalResource.external_id == "m039",
                )
            )
        ).scalars().first()
        row.title = "ẞ"
        await db.commit()

    monkeypatch.setattr(module, "MATCH_SCAN_LIMIT", 5)
    with request_context():
        result = await search(file_db, query="ß")

    assert result["matches"] == []
    assert result["matches_complete"] is False, (
        "a match outside the scan bound must be reported as incomplete examination, never as a "
        "complete empty result"
    )


async def test_matching_does_not_become_a_match_everything_filter(
    file_db, connector, provider, knowledge
):
    """Removing the narrowing must not make the tool return resources that do not match."""
    instance_id = await seed_titled(file_db, provider, 40, name="c2c-not-everything")
    with request_context():
        result = await search(file_db, query="quarterly-report-007")
    assert [match["external_id"] for match in result["matches"]] == ["m007"]


# ============================================================ truthful completeness


async def test_a_truncated_scan_with_no_matches_does_not_claim_matches_exist(
    file_db, connector, provider, knowledge
):
    """Truncation proves unchecked resources, not that further matches exist.

    More than MATCH_SCAN_LIMIT resources, none of which matches: the search is incomplete, but
    asserting that further matching resources exist would be a claim about rows never read.
    """
    instance_id = await seed_titled(file_db, provider, MATCH_SCAN_LIMIT + 1, name="c2c-no-matches")
    assert await rows_before(file_db, instance_id, f"m{MATCH_SCAN_LIMIT:03d}") == MATCH_SCAN_LIMIT

    with request_context():
        result = await search(file_db, query="nothing-matches-this-token")

    assert result["matches"] == []
    assert result["matches_complete"] is False, (
        "a truncated scan must still be reported as incomplete"
    )
    assert "may be incomplete" in result["note"]
    assert "More matching resources exist" not in result["note"], (
        "the note must not assert that unchecked resources match; it can only say the search may be "
        "incomplete"
    )
    assert "narrow the query" in result["note"]


async def test_matches_complete_is_false_when_the_match_cap_is_reached(
    file_db, connector, provider, knowledge
):
    """More authorized matches than the cap can list must not be presented as complete."""
    instance_id = await seed_titled(file_db, provider, 60, name="c2c-cap")
    with request_context():
        result = await search(file_db, query="quarterly-report-")
    assert len(result["matches"]) == MAX_MATCHES
    assert result["matches_complete"] is False
    assert "narrow the query" in result["note"]


async def test_matches_complete_is_false_when_the_scan_bound_is_hit(
    file_db, connector, provider, knowledge, monkeypatch
):
    from app.services.actions import connector_tools as module

    instance_id = await seed_titled(file_db, provider, 40, name="c2c-scan")
    monkeypatch.setattr(module, "MATCH_SCAN_LIMIT", 5)
    with request_context():
        result = await search(file_db, query="quarterly-report-")
    assert result["match_scan_limit"] == 5
    assert result["matches_complete"] is False, (
        "a truncated scan must report incomplete rather than claiming completeness"
    )
    assert len(result["matches"]) < 25


async def test_an_empty_result_under_a_truncated_scan_is_not_claimed_complete(
    file_db, connector, provider, knowledge, monkeypatch
):
    """Zero results must not be presented as proof that nothing matches."""
    from app.services.actions import connector_tools as module

    instance_id = await seed_titled(file_db, provider, 40, name="c2c-empty")
    monkeypatch.setattr(module, "MATCH_SCAN_LIMIT", 0)
    with request_context():
        result = await search(file_db, query="quarterly-report-")
    assert result["matches"] == []
    assert result["matches_complete"] is False, (
        "an empty result from a truncated scan must not imply that nothing matches"
    )


# ============================================================ measured cost


async def test_search_cost_is_bounded_and_reported(file_db, connector, provider, knowledge):
    """Provider calls, SQL statements and latency for one search over a 300 resource corpus.

    Reported rather than asserted against a threshold, because no agreed threshold exists for this
    tool. The point is that the cost is measured, bounded and visible.
    """
    from sqlalchemy import event

    instance_id = await seed_titled(file_db, provider, 300, name="c2c-cost")

    with request_context():
        async with file_db() as db:
            engine = db.bind.sync_engine
            statements = 0
            db_seconds = 0.0

            def _before(conn, cursor, statement, parameters, context, executemany):
                conn.info.setdefault("c2c_t0", []).append(time.perf_counter())

            def _after(conn, cursor, statement, parameters, context, executemany):
                started = conn.info["c2c_t0"].pop()
                nonlocal statements, db_seconds
                statements += 1
                db_seconds += time.perf_counter() - started

            event.listen(engine, "before_cursor_execute", _before)
            event.listen(engine, "after_cursor_execute", _after)
            try:
                provider.check_calls.clear()
                started = time.perf_counter()
                result = await _connector_search(db, {"query": "quarterly-report-280"})
                elapsed_ms = (time.perf_counter() - started) * 1000
                provider_calls = len(provider.check_calls)
            finally:
                event.remove(engine, "before_cursor_execute", _before)
                event.remove(engine, "after_cursor_execute", _after)

    print(
        "\n=== CA2-C SEARCH COST ==="
        f"\ncorpus_resources: 300"
        f"\nmatched: {len(result['matches'])}"
        f"\nprovider_calls: {provider_calls}"
        f"\nsql_statements: {statements}"
        f"\ndatabase_ms: {round(db_seconds * 1000, 2)}"
        f"\nwall_ms: {round(elapsed_ms, 2)}"
        f"\nmatch_scan_limit: {MATCH_SCAN_LIMIT}"
        f"\nmax_matches: {MAX_MATCHES}"
        "\n=== END ===\n"
    )

    # Bounded: one provider check per examined match, and only one match was examined here.
    assert provider_calls == 1, f"expected one authorization check, saw {provider_calls}"
    assert len(result["matches"]) == 1
    # Bounded SQL: a constant number of statements, not one per candidate row.
    assert statements <= 12, f"{statements} SQL statements for one search is not bounded"


async def test_search_does_not_mutate_shared_content(file_db, connector, provider, knowledge):
    """CA1 invariant: a denial encountered during search must not quarantine the resource."""
    instance_id = await seed_titled(file_db, provider, 10, name="c2c-nomutate")
    provider.access[("u1", "m002")] = False
    with request_context():
        await search(file_db, query="quarterly-report-002")
    async with file_db() as db:
        row = (
            await db.execute(
                select(ExternalResource).where(
                    ExternalResource.connector_instance_id == instance_id,
                    ExternalResource.external_id == "m002",
                )
            )
        ).scalars().first()
    assert row.lifecycle_state == "active", "a denial during search must not change lifecycle"
    assert row.permission_state != "revoked"
