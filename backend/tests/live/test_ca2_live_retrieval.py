"""CA2 live retrieval validation against REAL Chroma and REAL embeddings.

NOT collected in CI. `tests/live/conftest.py` ignores this module unless CA2_LIVE=1, so no CI run
and no ordinary `pytest tests/` picks it up. Run it deliberately:

    cd backend
    CA2_LIVE=1 ./.venv/bin/python -m pytest tests/live/test_ca2_live_retrieval.py -q -s

What is real here
-----------------
REAL: the configured embedding model (`sentence-transformers/all-MiniLM-L6-v2`), DB-GPT's chunking
and `EmbeddingAssembler`, a persistent Chroma collection on a local scratch path, and the
`knowledge_engine.ingest` / `knowledge_engine.retrieve` calls the product actually makes. This
module deliberately does NOT request the `knowledge` fixture, which is what replaces ingest and
retrieve everywhere else in the suite, so nothing in the retrieval path is stubbed.

Locality and safety: disposable scratch paths, a scratch sqlite database, synthetic data only, no
listening port, no credential or customer data, and no change to production configuration or to the
default retrieval mode.
"""

from __future__ import annotations

import contextlib
import importlib.metadata as metadata
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

# tests/live/ is a subdirectory, so the tests package root is added explicitly. This is the same
# approach the existing live harness uses.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Imported so pytest resolves them as fixtures in THIS module. Fixtures defined inside a test module
# are only visible where they are imported, which is why the other CA2 suites do the same.
from test_vs7_connector_security import connector, provider  # noqa: E402,F401

SCRATCH = Path("/home/sjeva/.hermes/cache/scratch/ca2-live")
EVIDENCE_PATH = Path(__file__).with_name("ca2_live_evidence.json")

# The phrase exists ONLY in the body of the document beyond the legacy window. No title and no
# external id contains it, so only real semantic retrieval can find it.
BODY_PHRASE = "the reforestation levy applies to timber shipments"
BEYOND_WINDOW_INDEX = 115
TOTAL_RESOURCES = 120

CHECKS: list[dict] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append({"check": name, "result": "PASS" if passed else "FAIL", "detail": detail})
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def record(key: str, value) -> None:
    CHECKS.append({"environment": key, "value": value})
    print(f"       {key}: {value}")


@contextlib.contextmanager
def principal_context(principal_id: str = "sec-pa", tenant_id: str = "tnt-sec-a"):
    """Publish a request principal.

    The LEGACY path resolves its principal from the request context and fails closed to an empty
    set without one, which is correct behaviour and is exactly why the first version of this
    harness saw zero legacy documents. The authenticated route always has a principal, so a live
    check of the legacy path needs one too.
    """
    from app.core.context import reset_principal, set_principal
    from app.core.identity import Principal
    from app.core.permissions import permissions_for_role

    principal = Principal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        subject="ca2-live",
        role="owner",
        membership_id="ca2-live",
        auth_method="local-dev",
        permissions=permissions_for_role("owner"),
    )
    set_principal(principal)
    try:
        yield principal
    finally:
        reset_principal()


@pytest.fixture(scope="module")
def live_env():
    """Scratch paths and real-engine settings. Skips unless explicitly enabled."""
    if os.environ.get("CA2_LIVE") != "1":
        pytest.skip("CA2 live validation requires CA2_LIVE=1")

    shutil.rmtree(SCRATCH, ignore_errors=True)
    (SCRATCH / "uploads").mkdir(parents=True, exist_ok=True)
    (SCRATCH / "vector").mkdir(parents=True, exist_ok=True)

    os.environ["OPENJM_DATABASE_URL"] = f"sqlite+aiosqlite:///{SCRATCH}/ca2live.db"
    os.environ["OPENJM_UPLOAD_DIR"] = str(SCRATCH / "uploads")

    from app.core.config import get_settings

    settings = get_settings()
    settings.upload_dir = SCRATCH / "uploads"
    settings.vector_path = SCRATCH / "vector"
    settings.vector_collection = "ca2_live"
    settings.retrieval_candidate_mode = "legacy"
    return settings


async def test_ca2_live_retrieval_beyond_the_legacy_window(
    file_db, connector, provider, live_env, monkeypatch
):
    """One ordered live run, so the ordering is itself part of the evidence."""
    from app.core.config import get_settings
    from app.services import retrieval_candidates as rc
    from app.services.connectors.candidate_authorization import revalidate_document_ids
    from app.services.document_policy import DocumentAccess
    from app.services.knowledge import knowledge_engine
    from test_vs7_connector_security import (
        PRINCIPAL_A,
        TENANT_A,
        _instance,
        _make_instance,
        _mapping,
        _resource,
        _run_initial,
        _seed,
    )
    settings = get_settings()

    # ---- environment, recorded so the numbers carry their provenance
    for package in ("dbgpt", "dbgpt-ext", "chromadb", "sentence-transformers", "torch"):
        try:
            record(f"version.{package}", metadata.version(package))
        except Exception:  # noqa: BLE001
            record(f"version.{package}", "not installed")
    record("embedding_model", settings.embedding_model)
    record("vector_backend", "chromadb persistent, local scratch path")
    record("llm_gateway", "not required by the retrieval path")
    record("scratch_path", str(SCRATCH))

    model_cache = (
        Path.home()
        / ".cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2"
    )
    if model_cache.exists():
        size = sum(f.stat().st_size for f in model_cache.rglob("*") if f.is_file())
        record("embedding_model_cache_mb", round(size / 1024 / 1024, 1))

    # ---- real ingestion of the whole corpus through the real engine
    await _seed(file_db)
    instance_id = await _make_instance(file_db, TENANT_A, name="ca2-live")
    for i in range(TOTAL_RESOURCES):
        external_id = f"m{i:03d}"
        body = f"Quarterly operations note number {i}. Routine administrative content."
        if i == BEYOND_WINDOW_INDEX:
            body = (
                "Quarterly operations note. Routine administrative content. "
                f"Policy annex: {BODY_PHRASE}."
            )
        provider.put(external_id, text=body, revision="1")
        provider.access[("u1", external_id)] = True

    started = time.perf_counter()
    async with file_db() as db:
        instance = await _instance(db, instance_id)
        await _run_initial(db, instance, limit=TOTAL_RESOURCES + 50)
        await _mapping(db, instance_id, PRINCIPAL_A, "u1")
        await db.commit()
    ingest_seconds = time.perf_counter() - started

    record("resources_ingested", TOTAL_RESOURCES)
    record("real_ingest_seconds_total", round(ingest_seconds, 1))
    record("real_ingest_ms_per_document", round(ingest_seconds * 1000 / TOTAL_RESOURCES, 1))
    vector_bytes = sum(f.stat().st_size for f in (SCRATCH / "vector").rglob("*") if f.is_file())
    record("chroma_vector_dir_mb", round(vector_bytes / 1024 / 1024, 2))

    async with file_db() as db:
        row = await _resource(db, instance_id, f"m{BEYOND_WINDOW_INDEX:03d}")
        beyond_document = row.document_id
    access = DocumentAccess(tenant_id=TENANT_A, principal_id=PRINCIPAL_A, owner_key="u1")

    def refs_for(documents):
        return [(document.id, document.original_name) for document in documents]

    async def retrieve(query: str, documents):
        return await knowledge_engine.retrieve(
            query,
            refs_for(documents),
            neighbor_primary_limit=settings.rag_neighbor_primary_limit,
            neighbor_max_chunks=settings.rag_neighbor_max_chunks,
        )

    # ---- 1. the legacy window
    settings.retrieval_candidate_mode = rc.MODE_LEGACY
    with principal_context():
        async with file_db() as db:
            legacy_set = await rc.governed_candidates(db, access, query="policy")
    legacy_ids = {document.id for document in legacy_set.documents}
    check(
        "legacy_window_truncates_at_100",
        len(legacy_ids) == 100 and beyond_document not in legacy_ids,
        f"legacy reached {len(legacy_ids)}; beyond-window present={beyond_document in legacy_ids}",
    )

    legacy_started = time.perf_counter()
    legacy_evidence = await retrieve(BODY_PHRASE, legacy_set.documents)
    legacy_query_ms = (time.perf_counter() - legacy_started) * 1000
    record("live_legacy_query_ms", round(legacy_query_ms, 1))
    record("live_legacy_refs", len(legacy_set.documents))
    check(
        "legacy_live_retrieval_misses_the_beyond_window_document",
        beyond_document not in {item.source_id for item in legacy_evidence},
        f"legacy evidence items={len(legacy_evidence)}",
    )

    # ---- 2. candidate mode
    settings.retrieval_candidate_mode = rc.MODE_CANDIDATE
    async with file_db() as db:
        candidate_set = await rc.governed_candidates(db, access, query="policy")
    candidate_ids = {document.id for document in candidate_set.documents}
    check(
        "candidate_mode_reaches_every_authorized_document",
        len(candidate_ids) == TOTAL_RESOURCES and beyond_document in candidate_ids,
        f"candidate reached {len(candidate_ids)}/{TOTAL_RESOURCES}; coverage={candidate_set.coverage}",
    )

    started = time.perf_counter()
    live_evidence = await retrieve(BODY_PHRASE, candidate_set.documents)
    query_ms = (time.perf_counter() - started) * 1000
    cited = {item.source_id for item in live_evidence}
    record("live_query_ms", round(query_ms, 1))
    record("live_evidence_items", len(live_evidence))
    record("live_candidate_refs", len(candidate_set.documents))
    record(
        "live_retrieval_latency_ratio_candidate_over_legacy",
        round(query_ms / max(legacy_query_ms, 1), 3),
    )
    check(
        "live_retrieval_cites_the_beyond_window_document",
        beyond_document in cited,
        f"target cited={beyond_document in cited}; distinct cited={len(cited)}",
    )
    check(
        "live_evidence_passage_is_real_indexed_content",
        any("reforestation" in (item.passage or "").lower() for item in live_evidence),
        "the retrieved passage contains the indexed body text",
    )

    # ---- 3. evidence cannot come from outside the authorized refs
    withheld = [d for d in candidate_set.documents if d.id != beyond_document]
    outside_evidence = await retrieve(BODY_PHRASE, withheld)
    check(
        "an_indexed_but_unauthorized_document_is_never_returned",
        beyond_document not in {item.source_id for item in outside_evidence},
        "withholding one document from the refs withheld its content from live retrieval",
    )

    # ---- 4. revocation at consumption
    provider.access[("u1", f"m{BEYOND_WINDOW_INDEX:03d}")] = False
    async with file_db() as db:
        revoked_set = await rc.governed_candidates(db, access, query="policy")
    check(
        "a_revoked_resource_leaves_the_candidate_set",
        beyond_document not in {document.id for document in revoked_set.documents},
        f"coverage after revocation={revoked_set.coverage}",
    )
    async with file_db() as db:
        strict = await revalidate_document_ids(db, access=access, document_ids=[beyond_document])
    check(
        "an_authoritative_scope_refuses_a_revoked_source",
        strict.strict_scope_satisfied is False,
        f"strict_satisfied={strict.strict_scope_satisfied}",
    )
    revoked_evidence = await retrieve(BODY_PHRASE, revoked_set.documents)
    check(
        "revoked_content_is_not_retrieved_live",
        beyond_document not in {item.source_id for item in revoked_evidence},
        "the revoked document was indexed but is no longer cited",
    )
    provider.access[("u1", f"m{BEYOND_WINDOW_INDEX:03d}")] = True

    # ---- 5. partial coverage is reported, not hidden
    async with file_db() as db:
        partial = await rc.governed_candidates(db, access, query="policy", check_cap=32)
    notice = partial.notice_for_user()
    check(
        "partial_coverage_is_reported_with_a_notice",
        partial.is_partial and notice is not None and "authorized to read" in notice["message"],
        f"reason={partial.reason}",
    )
    check(
        "partial_notice_leaks_no_identifiers",
        not any(token in json.dumps(notice) for token in ("m1", "tnt-", "tenant_")),
        json.dumps(notice),
    )

    # ---- 6. tenant isolation with a real index
    foreign = DocumentAccess(tenant_id="tnt-other", principal_id=PRINCIPAL_A, owner_key="u1")
    async with file_db() as db:
        other = await rc.governed_candidates(db, foreign, query="policy")
    check(
        "tenant_isolation_holds_with_a_real_index",
        other.documents == [] and other.provider_checks == 0,
        "a foreign tenant sees no candidates and spends no provider budget",
    )

    # ---- 7. rollback restores legacy behaviour with the real index in place
    settings.retrieval_candidate_mode = rc.MODE_LEGACY
    with principal_context():
        async with file_db() as db:
            rolled_back = await rc.governed_candidates(db, access, query="policy")
    check(
        "rollback_restores_the_legacy_window",
        len(rolled_back.documents) == 100
        and rolled_back.coverage is None
        and rolled_back.notice is None,
        f"rolled back to {len(rolled_back.documents)} documents, coverage={rolled_back.coverage}",
    )

    EVIDENCE_PATH.write_text(
        json.dumps({"checks": CHECKS}, indent=2, sort_keys=True), encoding="utf-8"
    )
    failures = [entry for entry in CHECKS if entry.get("result") == "FAIL"]
    print(f"\nlive evidence written to {EVIDENCE_PATH}")
    assert not failures, f"live checks failed: {[f['check'] for f in failures]}"
