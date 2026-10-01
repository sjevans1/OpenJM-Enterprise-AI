"""Phase E acceptance: exact dedup + bounded immediate neighbours.

Run from repository root:

    backend/.venv/bin/python backend/tests/fixtures/rag/phase_e_benchmark.py

This harness uses the real OpenJM ingestion policy, embedding model, semantic
retrieval and Chroma stack in an isolated temporary vector directory. It exits
nonzero when Phase E behavior is not proven.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from app.services.knowledge import DBGPTKnowledgeEngine


FIXTURE_DIR = Path(__file__).resolve().parent


def summarize(evidence):
    return [
        {
            "source_id": item.source_id,
            "title": item.title,
            "passage": item.passage,
            "score": item.score,
            "retrieval_role": item.provenance.get("retrieval_role"),
            "duplicate_count": item.provenance.get("duplicate_count"),
            "chunk_id": item.metadata.get("chunk_id"),
            "chunk_index": item.metadata.get("chunk_index"),
            "heading_path": item.metadata.get("heading_path"),
            "direction": item.provenance.get("direction"),
            "lookup_mode": item.provenance.get("lookup_mode"),
            "expanded_from_chunk_id": item.provenance.get(
                "expanded_from_chunk_id"
            ),
        }
        for item in evidence
    ]


async def ingest(engine, document_id: str, fixture: str, source_name: str | None = None):
    path = FIXTURE_DIR / fixture
    await engine.ingest(
        document_id,
        path,
        source_name=source_name or path.name,
    )


async def evaluate_adjacency(engine, fixture: str, document_id: str, query: str, fact: str):
    await ingest(engine, document_id, fixture)
    fixture_chunk_count = engine._store(document_id)._collection.count()

    baseline = await engine.retrieve(query, [(document_id, fixture)])
    expanded = await engine.retrieve(
        query,
        [(document_id, fixture)],
        neighbor_primary_limit=2,
        neighbor_max_chunks=2,
    )

    def has_fact(items):
        return any(fact.lower() in item.passage.lower() for item in items)

    primary_items = [
        item for item in expanded if item.provenance.get("retrieval_role") == "primary"
    ]
    primary_ids = {
        (item.source_id, str(item.metadata.get("chunk_id") or ""))
        for item in primary_items
    }
    requested_neighbor_ids = []
    seen_neighbor_ids = set()
    for item in primary_items[: engine.settings.rag_neighbor_primary_limit]:
        for key in ("previous_chunk_id", "next_chunk_id"):
            neighbor_id = str(item.metadata.get(key) or "")
            identity = (item.source_id, neighbor_id)
            if not neighbor_id or identity in primary_ids or identity in seen_neighbor_ids:
                continue
            seen_neighbor_ids.add(identity)
            requested_neighbor_ids.append(identity)
            if len(requested_neighbor_ids) >= engine.settings.rag_neighbor_max_chunks:
                break
        if len(requested_neighbor_ids) >= engine.settings.rag_neighbor_max_chunks:
            break

    evidence_limit = (
        engine.settings.rag_top_k + engine.settings.rag_neighbor_max_chunks
    )
    diagnostics = {
        "primary_semantic_hits": sum(
            item.provenance.get("retrieval_role") == "primary" for item in expanded
        ),
        "candidate_neighbors": len(requested_neighbor_ids),
        "accepted_neighbors": sum(
            item.provenance.get("retrieval_role") == "neighbor" for item in expanded
        ),
        "removed_by_deduplication": sum(
            max(int(item.provenance.get("duplicate_count", 1)) - 1, 0)
            for item in expanded
            if item.provenance.get("retrieval_role") == "primary"
        ),
        "final_evidence_count": len(expanded),
        "neighbor_primary_limit": engine.settings.rag_neighbor_primary_limit,
        "neighbor_max_chunks": engine.settings.rag_neighbor_max_chunks,
        "evidence_limit": evidence_limit,
        "within_evidence_limit": len(expanded) <= evidence_limit,
        "final_character_count": sum(len(item.passage) for item in expanded),
        "character_limit": evidence_limit * 2000,
    }

    return {
        "fixture": fixture,
        "fixture_chunk_count": fixture_chunk_count,
        "query": query,
        "fact": fact,
        "baseline_semantic_top_5": {
            "fact_present": has_fact(baseline),
            "count": len(baseline),
            "evidence": summarize(baseline),
        },
        "expanded_evidence": {
            "fact_present": has_fact(expanded),
            "count": len(expanded),
            "evidence": summarize(expanded),
        },
        "diagnostics": diagnostics,
    }


async def evaluate_distinct_sources(engine):
    phoenix = "phoenix_launch_protocol.md"
    refs = []
    for index in range(1, 4):
        document_id = f"phase-e-phoenix-source-{index}"
        await ingest(
            engine,
            document_id,
            phoenix,
            source_name=f"phoenix-source-{index}.md",
        )
        refs.append((document_id, f"phoenix-source-{index}.md"))

    evidence = await engine.retrieve(
        "What is the PRIMARY launch sequence code for Project Phoenix?",
        refs,
    )
    source_ids = sorted({item.source_id for item in evidence})
    return {
        "document_count": len(refs),
        "final_evidence_count": len(evidence),
        "source_ids_preserved": source_ids,
        "evidence": summarize(evidence),
    }


def validate_adjacency(result):
    baseline = result["baseline_semantic_top_5"]
    expanded = result["expanded_evidence"]
    assert result["fixture_chunk_count"] > 5
    assert baseline["count"] == 5
    assert baseline["fact_present"] is False
    assert expanded["fact_present"] is True

    primary_indices = {
        item["chunk_id"]: item["chunk_index"]
        for item in expanded["evidence"]
        if item["retrieval_role"] == "primary"
    }
    neighbors = [
        item
        for item in expanded["evidence"]
        if item["retrieval_role"] == "neighbor"
    ]
    fact_neighbors = [
        item for item in neighbors if result["fact"].lower() in item["passage"].lower()
    ]
    assert len(fact_neighbors) == 1
    assert fact_neighbors[0]["lookup_mode"] == "exact_chunk_id"
    assert fact_neighbors[0]["direction"] in {"previous", "next"}
    assert fact_neighbors[0]["expanded_from_chunk_id"] in primary_indices

    evidence_positions = {
        item["chunk_id"]: index
        for index, item in enumerate(expanded["evidence"])
    }
    all_neighbors_immediate = True
    for neighbor in neighbors:
        origin_index = primary_indices[neighbor["expanded_from_chunk_id"]]
        is_immediate = abs(neighbor["chunk_index"] - origin_index) == 1
        all_neighbors_immediate = all_neighbors_immediate and is_immediate
        assert is_immediate
        if neighbor["direction"] == "previous":
            assert evidence_positions[neighbor["chunk_id"]] < evidence_positions[
                neighbor["expanded_from_chunk_id"]
            ]
        else:
            assert evidence_positions[neighbor["chunk_id"]] > evidence_positions[
                neighbor["expanded_from_chunk_id"]
            ]
    assert len(neighbors) <= 2
    assert result["diagnostics"]["within_evidence_limit"] is True
    assert result["diagnostics"]["final_character_count"] <= result["diagnostics"][
        "character_limit"
    ]

    result["proof"] = {
        "semantic_top_5_qualifier_absent": True,
        "qualifier_supplied_by_exact_neighbor": fact_neighbors[0]["chunk_id"],
        "neighbor_direction": fact_neighbors[0]["direction"],
        "grounded_result": result["fact"],
        "unrelated_non_adjacent_context_expanded": not all_neighbors_immediate,
    }


async def main():
    with tempfile.TemporaryDirectory(prefix="openjm-phase-e-") as temp:
        engine = DBGPTKnowledgeEngine()
        engine.settings.vector_path = Path(temp)

        results = {
            "distinct_sources": await evaluate_distinct_sources(engine),
            "next_neighbor": await evaluate_adjacency(
                engine,
                "adjacent_context_hard.md",
                "phase-e-cedar",
                "What is the controlling value for the Project CEDAR escalation threshold?",
                "47 minutes",
            ),
            "previous_neighbor": await evaluate_adjacency(
                engine,
                "adjacent_context_previous.md",
                "phase-e-maple",
                "What is the controlling value for the Project MAPLE escalation threshold?",
                "31 minutes",
            ),
        }

        assert len(results["distinct_sources"]["source_ids_preserved"]) == 3
        validate_adjacency(results["next_neighbor"])
        validate_adjacency(results["previous_neighbor"])
        print(json.dumps({**results, "acceptance": "PASS"}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
