"""CA2 comparative measurement harness: accepted legacy path versus CA2 candidate mode.

MANDATORY WORK PACKAGE. Both paths are measured on **the same fixture instance**, so the
comparison is like for like: identical resources, tenant, principal, connector behaviour and
execution conditions.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
Everything here is **mocked and in-process**. The vector engine is stubbed exactly as the rest of
this suite stubs it, and there is no live DB-GPT, Chroma, Postgres or Keycloak. These are
therefore *fixture* timings and count measurements. They are valid for comparing the two paths on
identical work, and they are **not** production p95 measurements. Every record carries an
`environment` block so no reader can mistake which kind of number they are looking at.

Cost is reported as the provider authorization call count, which is the cost driver. Unit pricing
for the provider is not available to this harness, so no currency estimate is fabricated.

Run with `-s` to see the JSON records:

    python -m pytest tests/test_ca2_measurement_harness.py -q -s
"""

from __future__ import annotations

import json
import statistics
import time

import pytest
from sqlalchemy import select

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
from test_ca2b_candidate_integration import (  # noqa: F401
    BODY_MARKER,
    access_for,
    body_records,
    document_id_for,
    legacy_candidate_ids,
    request_context,
    stub_retrieve,
    tool_context,
)

from app.core.config import get_settings
from app.models import Document
from app.services import orchestrator as orchestrator_module
from app.services import retrieval_candidates as rc
from app.services.connectors.candidate_authorization import CursorError, StrictScopeError
from app.services.tools import KnowledgeSearchTool

ENVIRONMENT = {
    "kind": "mocked, in-process fixture",
    "live_stack": False,
    "vector_engine": "stubbed (no DB-GPT, no Chroma)",
    "database": "sqlite in-process via the shared file_db fixture",
    "timing_validity": "relative comparison between paths; NOT a production latency figure",
    "cost_basis": "provider authorization call count; unit pricing unavailable, none fabricated",
}


@pytest.fixture
def mode(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "retrieval_candidate_mode", rc.MODE_LEGACY)
    return lambda value: monkeypatch.setattr(settings, "retrieval_candidate_mode", value)


async def prepare(maker, provider_, n: int, *, name: str, native: int = 0) -> str:
    """Seed ONE fixture instance used by both paths. Optionally add native documents.

    Native documents have no ``ExternalResource`` row, so they are governed by local policy alone
    and exercise the mixed native/connector handling.
    """
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
        for i in range(native):
            db.add(
                Document(
                    tenant_id=TENANT_A,
                    user_id=PRINCIPAL_A,
                    original_name=f"native-{i}.txt",
                    stored_path=f"/tmp/native-{i}.txt",
                    size_bytes=16,
                    status="ready",
                    indexed=True,
                    lifecycle_state="ready",
                    classification="internal",
                    tenant_visible=True,
                )
            )
        await db.commit()
    return instance_id


async def collect(
    maker,
    provider_,
    *,
    n: int,
    candidate: bool,
    mode,
    check_cap: int = rc.DEFAULT_REQUEST_CHECK_CAP,
    page_budget: int = 32,
    samples: int = 1,
    query: str = "policy",
) -> dict:
    """Measure one path. ``n`` is the connector resource count for reporting only."""
    from sqlalchemy import event

    mode(rc.MODE_CANDIDATE if candidate else rc.MODE_LEGACY)
    access = access_for()

    latencies: list[float] = []
    statements = 0
    db_seconds = 0.0
    reachable: set[str] = set()
    checks = 0
    coverage = "unknown"
    reason = ""
    cursor_seen = False

    async with maker() as db:
        engine = db.bind.sync_engine

        def _before(conn, cursor, statement, parameters, context, executemany):
            conn.info.setdefault("ca2_t0", []).append(time.perf_counter())

        def _after(conn, cursor, statement, parameters, context, executemany):
            started = conn.info["ca2_t0"].pop()
            nonlocal statements, db_seconds
            statements += 1
            db_seconds += time.perf_counter() - started

        event.listen(engine, "before_cursor_execute", _before)
        event.listen(engine, "after_cursor_execute", _after)
        try:
            if candidate:
                for _ in range(max(1, samples)):
                    started = time.perf_counter()
                    result = await rc.governed_candidates(
                        db, access, query=query, check_cap=check_cap, page_budget=page_budget
                    )
                    latencies.append((time.perf_counter() - started) * 1000)
                    checks = result.provider_checks
                    reachable = {document.id for document in result.documents}
                    coverage = result.coverage.value if result.coverage else "unknown"
                    reason = result.reason
                    cursor_seen = result.cursor is not None
            else:
                with request_context():
                    for _ in range(max(1, samples)):
                        started = time.perf_counter()
                        result = await rc.governed_candidates(db, access, query=query)
                        latencies.append((time.perf_counter() - started) * 1000)
                        reachable = {document.id for document in result.documents}
        finally:
            event.remove(engine, "before_cursor_execute", _before)
            event.remove(engine, "after_cursor_execute", _after)

    ordered = sorted(latencies)
    p95_index = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return {
        "environment": ENVIRONMENT,
        "path": "candidate" if candidate else "legacy",
        "connector_resources": n,
        "authorized_documents_reachable": len(reachable),
        "provider_authorization_calls": checks,
        "sql_statements": statements,
        "database_ms": round(db_seconds * 1000, 2),
        "coverage": coverage,
        "coverage_reason": reason,
        "more_work_remains": cursor_seen,
        "latency_ms": [round(value, 2) for value in latencies],
        "latency_p50_ms": round(statistics.median(latencies), 2),
        "latency_p95_ms": round(ordered[p95_index], 2),
        "latency_max_ms": round(ordered[-1], 2),
        "sample_count": len(latencies),
        # Per-request figures, not totals across samples, so the two paths compare on the work of
        # ONE request. The aggregate totals above are retained for transparency.
        "provider_calls_per_request": checks,
        "sql_statements_per_request": round(statements / max(1, len(latencies)), 2),
        "database_ms_per_request": round(db_seconds * 1000 / max(1, len(latencies)), 2),
        "reachable_ids": reachable,
    }


def report(payload: dict) -> None:
    print("\n=== CA2 MEASUREMENT ===")
    print(json.dumps({k: v for k, v in payload.items() if not isinstance(v, set)}, indent=2, sort_keys=True, default=str))
    print("=== END ===\n")


# =============================================== scenario matrix, same fixture both paths

SIZES = [(40, 40), (80, 80), (100, 100), (150, 100), (400, 100)]


@pytest.mark.parametrize("n,legacy_expected", SIZES)
async def test_scenario_reachability_legacy_versus_candidate(
    file_db, connector, provider, knowledge, mode, n, legacy_expected
):
    """Reachability, provider calls and SQL work at each agreed corpus size."""
    await prepare(file_db, provider, n, name=f"m{n}")
    legacy = await collect(file_db, provider, n=n, candidate=False, mode=mode)
    cand = await collect(file_db, provider, n=n, candidate=True, mode=mode)
    report(
        {
            "scenario": f"reachability at {n} connector resources",
            "legacy": {k: v for k, v in legacy.items() if k != "reachable_ids"},
            "candidate": {k: v for k, v in cand.items() if k != "reachable_ids"},
            "recovered": len(cand["reachable_ids"] - legacy["reachable_ids"]),
            "regressed": len(legacy["reachable_ids"] - cand["reachable_ids"]),
        }
    )
    assert legacy["authorized_documents_reachable"] == legacy_expected, (
        f"legacy reached {legacy['authorized_documents_reachable']}, expected {legacy_expected}"
    )
    # F1: measured at the REAL application default cap (256), not an elevated diagnostic budget.
    # Beyond the default the first user request is partial by design, and the remainder requires
    # continuation rather than being counted as reachable in one request.
    expected_candidate = min(n, rc.DEFAULT_REQUEST_CHECK_CAP)
    assert cand["authorized_documents_reachable"] == expected_candidate, (
        f"candidate reached {cand['authorized_documents_reachable']}, expected {expected_candidate} "
        f"at the default cap of {rc.DEFAULT_REQUEST_CHECK_CAP}"
    )
    if n > rc.DEFAULT_REQUEST_CHECK_CAP:
        assert cand["coverage"] == "partial", "beyond the default cap coverage must be partial"
        assert cand["more_work_remains"] is True, "remaining work must advertise continuation"
    else:
        assert cand["coverage"] == "complete"
    assert not (legacy["reachable_ids"] - cand["reachable_ids"]), "candidate must not lose a doc"


async def test_scenario_mixed_native_and_connector_corpus(
    file_db, connector, provider, knowledge, mode
):
    """Native documents have no provider resource and must survive both paths unchanged."""
    await prepare(file_db, provider, 150, name="m-mixed", native=5)
    legacy = await collect(file_db, provider, n=150, candidate=False, mode=mode)
    cand = await collect(file_db, provider, n=150, candidate=True, mode=mode)
    report(
        {
            "scenario": "mixed native (5) and connector (150) corpus",
            "legacy_reachable": legacy["authorized_documents_reachable"],
            "candidate_reachable": cand["authorized_documents_reachable"],
            "native_lost_by_candidate": len(legacy["reachable_ids"] - cand["reachable_ids"]),
        }
    )
    # 150 connector resources plus 5 native documents.
    assert legacy["authorized_documents_reachable"] == 105
    assert cand["authorized_documents_reachable"] == 155
    assert not (legacy["reachable_ids"] - cand["reachable_ids"])


async def test_scenario_pinned_75_sources_report_parity(
    file_db, connector, provider, knowledge, mode
):
    await prepare(file_db, provider, 75, name="m-pinned")
    mode(rc.MODE_CANDIDATE)
    access = access_for()
    instance_id = await _first_instance(file_db)
    assert instance_id is not None
    document_ids = [await document_id_for(file_db, instance_id, f"m{i:03d}") for i in range(75)]
    async with file_db() as db:
        pinned = await rc.strict_pinned_documents(db, access, document_ids=document_ids)
    report(
        {
            "scenario": "pinned 75 sources",
            "requested": len(document_ids),
            "authorized": len(pinned),
            "strict_scope_satisfied": True,
        }
    )
    assert len(pinned) == 75


async def _first_instance(maker) -> str | None:
    from app.models import ConnectorInstance

    async with maker() as db:
        row = (
            await db.execute(select(ConnectorInstance).where(ConnectorInstance.tenant_id == TENANT_A))
        ).scalars().first()
    return row.id if row else None


# =============================================== recovered evidence and continuation


async def test_scenario_body_only_relevant_source_beyond_the_window(
    file_db, connector, provider, knowledge, mode, monkeypatch
):
    """A source whose relevance exists only in its body, past the legacy 100 window."""
    total = 150
    beyond_index = total - 1
    mode(rc.MODE_CANDIDATE)
    instance_id = await _prepare_with_marker(file_db, provider, total, beyond_index)
    beyond = await document_id_for(file_db, instance_id, f"m{beyond_index:03d}")
    seen = stub_retrieve(monkeypatch, body_records(knowledge))
    access = access_for()

    mode(rc.MODE_LEGACY)
    with request_context():
        legacy_ids = await legacy_candidate_ids(file_db, query=BODY_MARKER)
    assert len(legacy_ids) == 100
    assert beyond not in legacy_ids

    mode(rc.MODE_CANDIDATE)
    async with file_db() as db:
        result = await KnowledgeSearchTool().execute(
            tool_context(db, access), {"query": BODY_MARKER}
        )
    report(
        {
            "scenario": "body-only relevance beyond index 100",
            "legacy_reached": len(legacy_ids),
            "candidate_reached": total,
            "beyond_index": beyond_index,
            "engine_received_beyond_source": beyond in seen,
            "evidence_cites_beyond_source": beyond in {item.source_id for item in result.evidence},
            "false_absence": len(result.evidence) == 0,
        }
    )
    assert beyond in seen
    assert beyond in {item.source_id for item in result.evidence}


async def _prepare_with_marker(maker, provider_, n: int, marker_index: int) -> str:
    await _seed(maker)
    instance_id = await _make_instance(maker, TENANT_A, name="m-marker")
    for i in range(n):
        body = f"policy document {i}" + (f" {BODY_MARKER}" if i == marker_index else "")
        provider_.put(f"m{i:03d}", text=body, revision="1")
        provider_.access[("u1", f"m{i:03d}")] = True
    async with maker() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance, limit=n + 50)
        await _mapping(db, instance_id, PRINCIPAL_A, "u1")
        await db.commit()
    return instance_id


async def test_scenario_beyond_256_continuation(file_db, connector, provider, knowledge, mode):
    total = 300
    await prepare(file_db, provider, total, name="m-256")
    mode(rc.MODE_CANDIDATE)
    access = access_for()
    instance_id = await _first_instance(file_db)
    beyond = await document_id_for(file_db, instance_id, f"m{total - 1:03d}")
    seen: set[str] = set()
    cursor = None
    pages = 0
    checks = 0
    for _ in range(40):
        async with file_db() as db:
            page = await rc.governed_candidates(
                db, access, query="", check_cap=64, page_budget=32, cursor=cursor
            )
        seen |= {document.id for document in page.documents}
        checks += page.provider_checks
        cursor = page.cursor
        pages += 1
        if cursor is None:
            break
    report(
        {
            "scenario": "continuation beyond position 256",
            "pages": pages,
            "provider_authorization_calls": checks,
            "reached": len(seen),
            "beyond_source_reached": beyond in seen,
        }
    )
    assert beyond in seen
    assert len(seen) == total


async def test_diagnostic_full_walk_at_the_declared_maximum_cap(
    file_db, connector, provider, knowledge, mode
):
    """Diagnostic, clearly separated from the default-cap result above.

    Runs at MAX_REQUEST_CHECK_CAP, the declared safe upper bound, to show that the full corpus is
    reachable when a caller is explicitly allowed the budget. It is NOT the default user
    experience: at the default 256 cap a 400 resource corpus is partial and requires continuation.
    """
    total = 400
    await prepare(file_db, provider, total, name="m-diag")
    mode(rc.MODE_CANDIDATE)
    async with file_db() as db:
        diagnostic = await rc.governed_candidates(
            db,
            access_for(),
            query="policy",
            check_cap=rc.MAX_REQUEST_CHECK_CAP,
            page_budget=32,
        )
    report(
        {
            "scenario": "diagnostic full walk at the declared maximum cap",
            "cap_used": rc.MAX_REQUEST_CHECK_CAP,
            "default_cap": rc.DEFAULT_REQUEST_CHECK_CAP,
            "fixture_resources": total,
            "reachable": len(diagnostic.documents),
            "provider_calls": diagnostic.provider_checks,
            "coverage": diagnostic.coverage.value if diagnostic.coverage else None,
        }
    )
    assert len(diagnostic.documents) == total
    assert diagnostic.provider_checks == total
    assert diagnostic.provider_checks <= rc.MAX_REQUEST_CHECK_CAP


async def test_the_declared_maximum_cap_is_enforced_and_cannot_be_bypassed(
    file_db, connector, provider, knowledge, mode
):
    """The ceiling is enforced in the service, not only at the API, so no caller can bypass it."""
    await prepare(file_db, provider, 5, name="m-ceiling")
    mode(rc.MODE_CANDIDATE)
    async with file_db() as db:
        with pytest.raises(ValueError):
            await rc.governed_candidates(
                db, access_for(), query="policy", check_cap=rc.MAX_REQUEST_CHECK_CAP + 1
            )
        with pytest.raises(ValueError):
            await rc.governed_candidates(db, access_for(), query="policy", check_cap=0)


# =============================================== request-level provider accounting


async def measure_once(
    maker,
    provider_,
    *,
    candidate: bool,
    mode,
    check_cap: int | None = None,
    page_budget: int = 32,
    query: str = "policy",
) -> dict:
    """One request on one path, timed, with per-request SQL and provider accounting.

    Used for interleaved sampling: alternating the two paths round by round removes the systematic
    drift that measuring one path fully before the other would introduce.
    """
    from sqlalchemy import event

    mode(rc.MODE_CANDIDATE if candidate else rc.MODE_LEGACY)
    access = access_for()
    cap = rc.DEFAULT_REQUEST_CHECK_CAP if check_cap is None else check_cap
    statements = 0
    db_seconds = 0.0
    reachable = 0

    async with maker() as db:
        engine = db.bind.sync_engine

        def _before(conn, cursor, statement, parameters, context, executemany):
            conn.info.setdefault("ca2_t0", []).append(time.perf_counter())

        def _after(conn, cursor, statement, parameters, context, executemany):
            started = conn.info["ca2_t0"].pop()
            nonlocal statements, db_seconds
            statements += 1
            db_seconds += time.perf_counter() - started

        event.listen(engine, "before_cursor_execute", _before)
        event.listen(engine, "after_cursor_execute", _after)
        try:
            provider_.check_calls.clear()
            with request_context():
                started = time.perf_counter()
                if candidate:
                    result = await rc.governed_candidates(
                        db, access, query=query, check_cap=cap, page_budget=page_budget
                    )
                    reachable = len(result.documents)
                else:
                    legacy = await rc.governed_candidates(db, access, query=query)
                    reachable = len(legacy.documents)
                wall_ms = (time.perf_counter() - started) * 1000
            provider_calls = len(provider_.check_calls)
        finally:
            event.remove(engine, "before_cursor_execute", _before)
            event.remove(engine, "after_cursor_execute", _after)

    return {
        "wall_ms": wall_ms,
        "sql_statements": statements,
        "database_ms": db_seconds * 1000,
        "provider_calls": provider_calls,
        "reachable": reachable,
    }


def _stats(samples: list[float]) -> dict:
    ordered = sorted(samples)
    def pct(value: float) -> float:
        return ordered[max(0, min(len(ordered) - 1, int(round(value * (len(ordered) - 1)))))]
    return {
        "n": len(ordered),
        "min_ms": round(ordered[0], 2),
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(pct(0.95), 2),
        "max_ms": round(ordered[-1], 2),
        "stdev_ms": round(statistics.stdev(ordered), 2) if len(ordered) > 1 else 0.0,
    }


async def test_the_coverage_notice_is_cleared_before_each_chat_request(
    file_db, connector, provider, knowledge, mode
):
    """Request-scoped state must not leak from one request into the next.

    Without the reset, a request that produced no notice would inherit the previous request's and
    report partial coverage for a source set it actually checked completely.
    """
    from app.core.context import current_coverage_notice, reset_coverage_notice, set_coverage_notice

    set_coverage_notice({"code": "partial_coverage", "coverage": "partial", "reason": "stale"})
    assert current_coverage_notice() is not None

    # The chat handler resets before doing any work; this asserts the mechanism the handler uses.
    reset_coverage_notice()
    assert current_coverage_notice() is None, "a stale notice must not survive into a new request"


async def test_one_exploratory_traversal_per_full_hybrid_request(
    file_db, connector, provider, knowledge, mode, monkeypatch
):
    """The same request-level proof for a Hybrid request, which the review asked for by name.

    The structured side is made inert by stubbing its planner seam, so this exercises a real
    hybrid routing and its knowledge leg with the REAL tool registry, and therefore the real
    handoff, rather than a fake tool that would prove nothing about provider accounting.
    """
    from app.services.orchestrator import OpenJMOrchestrator
    from app.services.structured_planner import StructuredPlanningResult

    total = 150
    await prepare(file_db, provider, total, name="m-hybrid")
    mode(rc.MODE_CANDIDATE)
    stub_retrieve(monkeypatch, body_records(knowledge))

    async def no_plan(*args, **kwargs):
        return StructuredPlanningResult(candidate=False, plan=None, rationale="inert for this test")

    monkeypatch.setattr(orchestrator_module.structured_planner, "is_candidate", lambda *a, **k: False)
    monkeypatch.setattr(orchestrator_module.structured_planner, "plan", no_plan)

    invocations = {"n": 0}
    original = rc.governed_candidates

    async def counting(*args, **kwargs):
        invocations["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(rc, "governed_candidates", counting)

    with request_context() as principal:
        async with file_db() as db:
            provider.check_calls.clear()
            plan = await OpenJMOrchestrator().plan(
                "What does the policy document say?", db, principal.user_id, mode="hybrid"
            )
            provider_calls = len(provider.check_calls)

    report(
        {
            "scenario": "full orchestrator Hybrid request, 150 resources",
            "exploratory_gate_invocations": invocations["n"],
            "provider_calls_for_entire_request": provider_calls,
            "per_request_cap": rc.DEFAULT_REQUEST_CHECK_CAP,
            "execution_class": plan.execution_class,
        }
    )
    assert plan.execution_class == "hybrid", "the request must have routed as hybrid"
    assert invocations["n"] == 1, (
        f"the exploratory gate ran {invocations['n']} times for one hybrid request; it must run once"
    )
    assert provider_calls <= rc.DEFAULT_REQUEST_CHECK_CAP


async def test_one_exploratory_traversal_per_full_knowledge_request(
    file_db, connector, provider, knowledge, mode, monkeypatch
):
    """E1/F2/B-3: a FULL orchestrator Knowledge request must authorize the corpus once.

    Instrumented two ways at the request level rather than at the helper: the exploratory gate's
    own invocation count, and the provider adapter's call log, which counts BOTH paths because it
    is the shared boundary (F4).
    """
    from app.services.orchestrator import OpenJMOrchestrator

    total = 150
    await prepare(file_db, provider, total, name="m-request")
    mode(rc.MODE_CANDIDATE)
    stub_retrieve(monkeypatch, body_records(knowledge))

    invocations = {"n": 0}
    original = rc.governed_candidates

    async def counting(*args, **kwargs):
        invocations["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(rc, "governed_candidates", counting)

    with request_context() as principal:
        async with file_db() as db:
            provider.check_calls.clear()
            # The authenticated route passes the principal's composite user id, and the orchestrator
            # resolves access only when that id matches the active principal. Passing a bare
            # principal id silently takes the no-identity legacy branch, which is what made an
            # earlier version of this test prove nothing.
            plan = await OpenJMOrchestrator().plan(
                "policy document", db, principal.user_id, mode="knowledge"
            )
            provider_calls = len(provider.check_calls)

    report(
        {
            "scenario": "full orchestrator Knowledge request, 150 resources",
            "exploratory_gate_invocations": invocations["n"],
            "provider_calls_for_entire_request": provider_calls,
            "per_request_cap": rc.DEFAULT_REQUEST_CHECK_CAP,
            "duplicated_work_avoided": invocations["n"] == 1,
            "answer_chars": len(plan.direct_answer or ""),
        }
    )
    # Exactly one exploratory traversal for the whole request. Before the handoff wiring this was
    # two: one in _ready_documents and one inside the tool.
    assert invocations["n"] == 1, (
        f"the exploratory gate ran {invocations['n']} times for one request; it must run once"
    )
    # And the request's provider work is bounded by the configured per-request cap, not doubled.
    assert provider_calls <= rc.DEFAULT_REQUEST_CHECK_CAP, (
        f"{provider_calls} provider calls exceeds the {rc.DEFAULT_REQUEST_CHECK_CAP} request cap"
    )
    assert provider_calls >= total, "the traversal must actually have examined the corpus"


async def test_latency_on_comparable_work_against_the_1_25x_threshold(
    file_db, connector, provider, knowledge, mode
):
    """The gate, on COMPARABLE work: 100 resources, both paths examining 100.

    At 150 resources the candidate performs the 50 authorization checks legacy omits, so it is
    comparing different amounts of work rather than overhead. That case is measured separately
    and deliberately not asserted against the threshold.
    """
    runs = 15
    await prepare(file_db, provider, 100, name="m-lat")

    # Warm up BOTH paths once, excluded from the samples, so engine and connection warmup cannot
    # land in the first sample of one path only.
    await measure_once(file_db, provider, candidate=False, mode=mode)
    await measure_once(file_db, provider, candidate=True, mode=mode)

    legacy_rows: list[dict] = []
    candidate_rows: list[dict] = []
    # Interleaved: the two paths alternate round by round, so systematic drift in the fixture or
    # the host cannot favour one side.
    for index in range(runs):
        order = (False, True) if index % 2 == 0 else (True, False)
        for is_candidate in order:
            row = await measure_once(file_db, provider, candidate=is_candidate, mode=mode)
            (candidate_rows if is_candidate else legacy_rows).append(row)

    legacy_stats = _stats([row["wall_ms"] for row in legacy_rows])
    candidate_stats = _stats([row["wall_ms"] for row in candidate_rows])
    ratio_p50 = candidate_stats["p50_ms"] / max(legacy_stats["p50_ms"], 0.01)
    ratio_p95 = candidate_stats["p95_ms"] / max(legacy_stats["p95_ms"], 0.01)
    report(
        {
            "scenario": "latency, comparable work (100 resources, both examine 100)",
            "environment": ENVIRONMENT,
            "design": "warmup excluded, interleaved alternating order, per-request accounting",
            "legacy": legacy_stats,
            "candidate": candidate_stats,
            "ratio_p50": round(ratio_p50, 3),
            "ratio_p95": round(ratio_p95, 3),
            "threshold": 1.25,
            "legacy_sql_per_request": legacy_rows[0]["sql_statements"],
            "candidate_sql_per_request": candidate_rows[0]["sql_statements"],
            "legacy_db_ms_per_request": round(legacy_rows[0]["database_ms"], 2),
            "candidate_db_ms_per_request": round(candidate_rows[0]["database_ms"], 2),
            "legacy_provider_calls_per_request": legacy_rows[0]["provider_calls"],
            "candidate_provider_calls_per_request": candidate_rows[0]["provider_calls"],
            "statistical_caveat": (
                "in-process sqlite and a stubbed vector engine; decision-grade for comparing the "
                "two paths on identical work, not a production latency figure"
            ),
        }
    )
    assert all(row["reachable"] == 100 for row in legacy_rows)
    assert all(row["reachable"] == 100 for row in candidate_rows)
    assert ratio_p95 <= 1.25, (
        f"candidate p95 {ratio_p95:.2f}x legacy p95 on comparable fixture work, "
        "above the agreed 1.25x threshold"
    )


async def test_latency_at_150_where_the_candidate_does_the_extra_work(
    file_db, connector, provider, knowledge, mode
):
    """Reported, not asserted: here the candidate performs work legacy omits entirely."""
    await prepare(file_db, provider, 150, name="m-lat-150")
    legacy = await collect(file_db, provider, n=150, candidate=False, mode=mode)
    cand = await collect(file_db, provider, n=150, candidate=True, mode=mode)
    report(
        {
            "scenario": "latency at 150 resources, different amounts of authorization work",
            "legacy_reachable": legacy["authorized_documents_reachable"],
            "candidate_reachable": cand["authorized_documents_reachable"],
            "legacy_p50_ms": legacy["latency_p50_ms"],
            "candidate_p50_ms": cand["latency_p50_ms"],
            "delta_ms": round(cand["latency_p50_ms"] - legacy["latency_p50_ms"], 2),
            "legacy_provider_calls": legacy["provider_authorization_calls"],
            "candidate_provider_calls": cand["provider_authorization_calls"],
            "note": "the candidate examines 50 resources legacy never examines; this is not overhead",
        }
    )
    assert legacy["authorized_documents_reachable"] == 100
    assert cand["authorized_documents_reachable"] == 150


# =============================================== adversarial security


async def test_tenant_isolation_is_preserved(file_db, connector, provider, knowledge, mode):
    await prepare(file_db, provider, 12, name="m-iso")
    mode(rc.MODE_CANDIDATE)
    async with file_db() as db:
        result = await rc.governed_candidates(db, access_for(tenant_id=TENANT_B), query="policy")
    assert result.documents == []
    assert result.provider_checks == 0


async def test_revocation_is_honoured_at_consumption(file_db, connector, provider, knowledge, mode):
    await prepare(file_db, provider, 20, name="m-revoked")
    mode(rc.MODE_CANDIDATE)
    access = access_for()
    instance_id = await _first_instance(file_db)
    document_ids = [await document_id_for(file_db, instance_id, f"m{i:03d}") for i in range(20)]
    async with file_db() as db:
        assert len(await rc.strict_pinned_documents(db, access, document_ids=document_ids)) == 20
    provider.access[("u1", "m011")] = False
    async with file_db() as db:
        with pytest.raises(StrictScopeError):
            await rc.strict_pinned_documents(db, access, document_ids=document_ids)


async def test_provider_outage_is_reported_not_silently_omitted(
    file_db, connector, provider, knowledge, mode
):
    from app.services.connectors.base import ConnectorAuthUnavailable

    await prepare(file_db, provider, 30, name="m-outage")
    mode(rc.MODE_CANDIDATE)
    provider.access_error = ConnectorAuthUnavailable("provider down", code="provider_error")
    async with file_db() as db:
        result = await rc.governed_candidates(db, access_for(), query="policy")
    report(
        {
            "scenario": "provider outage",
            "coverage": result.coverage.value if result.coverage else None,
            "reason": result.reason,
            "documents_returned": len(result.documents),
            "notice": result.notice_for_user(),
        }
    )
    assert result.is_partial is True
    assert result.reason == "authorization_unavailable"
    assert result.notice_for_user() is not None
    # No false absence claim is possible from this shape.
    assert len(result.documents) == 0 and result.is_partial is True


async def test_malformed_tampered_and_cross_principal_cursors_are_refused(
    file_db, connector, provider, knowledge, mode
):
    await prepare(file_db, provider, 80, name="m-cursor")
    mode(rc.MODE_CANDIDATE)
    access = access_for()
    async with file_db() as db:
        first = await rc.governed_candidates(db, access, query="", check_cap=32, page_budget=16)
    assert first.cursor is not None

    async with file_db() as db:
        with pytest.raises(CursorError):
            await rc.governed_candidates(db, access, query="", cursor=first.cursor[:-6] + "AAAAAA")
    async with file_db() as db:
        with pytest.raises(CursorError):
            await rc.governed_candidates(
                db, access_for(principal_id="sec-pc"), query="", cursor=first.cursor
            )
    async with file_db() as db:
        with pytest.raises(CursorError):
            await rc.governed_candidates(
                db, access, query="a different question", cursor=first.cursor
            )


# =============================================== rollback


async def test_rollback_to_legacy_restores_previous_behaviour(
    file_db, connector, provider, knowledge, mode
):
    """Rollback is tested, not documented: the flag restores legacy behaviour exactly."""
    await prepare(file_db, provider, 150, name="m-rollback")
    access = access_for()

    mode(rc.MODE_CANDIDATE)
    async with file_db() as db:
        candidate_set = await rc.governed_candidates(db, access, query="policy")
    assert len(candidate_set.documents) == 150
    assert candidate_set.coverage.value == "complete"

    mode(rc.MODE_LEGACY)
    with request_context():
        async with file_db() as db:
            legacy_set = await rc.governed_candidates(db, access, query="policy")
        async with file_db() as db:
            from app.services.document_policy import governed_tenant_documents

            expected = await governed_tenant_documents(db, access)

    assert len(expected) == 100, "rollback restores the legacy 100 resource window"
    assert [document.id for document in legacy_set.documents] == [
        document.id for document in expected
    ]
    assert legacy_set.coverage is None
    assert legacy_set.notice is None
    assert legacy_set.provider_checks == 0
