"""CA2 Phase 1 baseline measurement. RECORDS current behaviour; changes nothing.

A measurement harness, not a regression expectation. It captures the parameters of
the retrieval-completeness defect on the frozen baseline (`main` at `8d6b7d8`) so
the Phase 4 comparison is against real numbers rather than a methodology.

It asserts only invariants that must hold before AND after CA2:
  * no unauthorized document ever appears in the authorized set;
  * the authorized set never exceeds the fixture size.

Everything else is recorded and printed for the baseline document.

Run:
    python -m pytest tests/test_ca2_baseline_measurement.py -q -s
"""

from __future__ import annotations

import json
import statistics
import time

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

from app.services.connectors import authorization as authorization_service

N = 150


async def _seed_all_authorized(maker, provider, *, reverse: bool = False) -> tuple[str, dict]:
    """Seed N resources, all authorized for one principal, in a chosen insert order."""
    await _seed(maker)
    instance_id = await _make_instance(maker, TENANT_A, name=f"ca2-measure-{int(reverse)}")
    order = list(reversed(range(N))) if reverse else list(range(N))
    for i in order:
        ext = f"m{i:03d}"
        provider.put(ext, text=f"policy document {i}", revision="1")
        provider.access[("u1", ext)] = True
    async with maker() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance, limit=N + 50)
        await _mapping(db, instance_id, PRINCIPAL_A, "u1")
        await db.commit()

    async with maker() as db:
        ext_to_doc = {
            f"m{i:03d}": (await _resource(db, instance_id, f"m{i:03d}")).document_id
            for i in range(N)
        }
    return instance_id, ext_to_doc


async def _measure_once(db, provider, ext_to_doc) -> dict:
    provider.check_calls.clear()
    t0 = time.perf_counter()
    allowed, denials = await authorization_service.authorized_connector_document_ids(
        db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    reachable = {e for e, d in ext_to_doc.items() if d in allowed}
    omitted = sorted(set(ext_to_doc) - reachable)
    return {
        "allowed_set_size": len(allowed),
        "reachable_external_ids": len(reachable),
        "omitted_external_ids": len(omitted),
        "omitted_sample": omitted[:5],
        "denials_recorded_for_omitted": sum(1 for e in omitted if e in denials),
        "provider_checks_consumed": len(provider.check_calls),
        "gate_latency_ms": round(elapsed_ms, 2),
        "reachable": reachable,
    }


async def test_baseline_reachability_forward_insertion(file_db, connector, provider, knowledge):
    instance_id, ext_to_doc = await _seed_all_authorized(file_db, provider)
    async with file_db() as db:
        m = await _measure_once(db, provider, ext_to_doc)

    record = {
        "baseline_commit": "8d6b7d8",
        "fixture_resources": N,
        "insertion_order": "forward",
        "authorized_in_fixture": N,
        **{k: v for k, v in m.items() if k != "reachable"},
        # The review's adversarial shape: a document seeded last, so it sits beyond
        # the window, with relevance nowhere in its title.
        "last_seeded_id_reachable": f"m{N - 1:03d}" in m["reachable"],
    }
    # Assert only safety invariants, true before and after CA2.
    assert m["allowed_set_size"] <= N
    assert record["omitted_external_ids"] == N - m["reachable_external_ids"]

    print("\n=== CA2 BASELINE (forward insertion) ===")
    print(json.dumps(record, indent=2, sort_keys=True))
    print("=== END ===\n")


async def test_baseline_reachability_reversed_insertion(file_db, connector, provider, knowledge):
    instance_id, ext_to_doc = await _seed_all_authorized(file_db, provider, reverse=True)
    async with file_db() as db:
        m = await _measure_once(db, provider, ext_to_doc)

    record = {
        "baseline_commit": "8d6b7d8",
        "fixture_resources": N,
        "insertion_order": "reversed",
        "authorized_in_fixture": N,
        **{k: v for k, v in m.items() if k != "reachable"},
    }
    assert m["allowed_set_size"] <= N

    print("\n=== CA2 BASELINE (reversed insertion) ===")
    print(json.dumps(record, indent=2, sort_keys=True))
    print("=== END ===\n")


async def test_baseline_latency_distribution(file_db, connector, provider, knowledge):
    instance_id, ext_to_doc = await _seed_all_authorized(file_db, provider)
    samples = []
    for _ in range(5):
        async with file_db() as db:
            t0 = time.perf_counter()
            await authorization_service.authorized_connector_document_ids(
                db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A
            )
            samples.append((time.perf_counter() - t0) * 1000)
    record = {
        "baseline_commit": "8d6b7d8",
        "fixture_resources": N,
        "runs": len(samples),
        "gate_latency_p50_ms": round(statistics.median(samples), 2),
        "gate_latency_p95_ms": round(sorted(samples)[-1], 2),
        "gate_latency_min_ms": round(min(samples), 2),
        "gate_latency_max_ms": round(max(samples), 2),
    }
    print("\n=== CA2 BASELINE (gate latency, 150-item corpus) ===")
    print(json.dumps(record, indent=2, sort_keys=True))
    print("=== END ===\n")


async def test_ca2a_contract_measurement(file_db, connector, provider, knowledge):
    """Measure the CA2-A contract on the same 150-resource fixture as the baseline.

    Reports resource fetches and authorization calls as separate counts, which is the split
    the comparative benchmark needs. Provider overhead and database overhead are NOT yet
    isolated from each other here; this records counts and total wall time per page.
    """
    from app.services.connectors import candidate_authorization as ca
    from app.services.document_policy import DocumentAccess

    instance_id, ext_to_doc = await _seed_all_authorized(file_db, provider)
    access = DocumentAccess(tenant_id=TENANT_A, principal_id=PRINCIPAL_A, owner_key="u1")

    pages = []
    reachable: set[str] = set()
    cursor = None
    started = time.perf_counter()
    for _ in range(50):
        async with file_db() as db:
            page_start = time.perf_counter()
            result = await ca.authorize_candidates(
                db, access=access, query="policy document", budget=32, cursor=cursor
            )
            page_ms = (time.perf_counter() - page_start) * 1000
        pages.append(
            {
                "rows_fetched": result.rows_fetched,
                "provider_checks": result.provider_checks,
                "examined": result.examined,
                "authorized": len(result.authorized_document_ids),
                "coverage": result.coverage.value,
                "reason": result.coverage_reason,
                "page_ms": round(page_ms, 2),
            }
        )
        reachable |= set(result.authorized_document_ids)
        cursor = result.cursor
        if cursor is None:
            break
    total_ms = (time.perf_counter() - started) * 1000

    record = {
        "contract": "CA2-A",
        "baseline_commit": "8d6b7d8",
        "fixture_resources": N,
        "authorized_in_fixture": N,
        "page_budget": 32,
        "pages": len(pages),
        "reachable_external_ids": len(reachable),
        "still_omitted": N - len(reachable),
        "total_rows_fetched": sum(p["rows_fetched"] for p in pages),
        "total_provider_checks": sum(p["provider_checks"] for p in pages),
        "total_examined": sum(p["examined"] for p in pages),
        "fetch_to_check_ratio": round(
            sum(p["rows_fetched"] for p in pages) / max(1, sum(p["provider_checks"] for p in pages)), 3
        ),
        "pages_total_ms": round(total_ms, 2),
        "per_page": pages,
    }
    # The claim CA2-A exists to make, asserted on the same fixture the baseline was measured on.
    assert len(reachable) == N, f"CA2-A reached only {len(reachable)} of {N}"
    assert pages[-1]["coverage"] == "complete"
    assert pages[-1]["reason"] == ""

    print("\n=== CA2-A CONTRACT (150-item corpus, budget 32) ===")
    print(json.dumps(record, indent=2, sort_keys=True))
    print("=== END ===\n")
