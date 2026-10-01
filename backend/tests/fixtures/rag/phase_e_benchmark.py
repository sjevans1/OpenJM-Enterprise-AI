"""Phase E deterministic benchmark: exact dedup + bounded neighbours.

Run from repository root:

    backend/.venv/bin/python backend/tests/fixtures/rag/phase_e_benchmark.py

This benchmark is model-independent. It uses the real OpenJM ingestion policy,
embedding model and Chroma stack in an isolated temporary vector directory.
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
            "equivalent_sources": item.provenance.get("equivalent_sources", []),
            "chunk_id": item.metadata.get("chunk_id"),
            "chunk_index": item.metadata.get("chunk_index"),
            "heading_path": item.metadata.get("heading_path"),
            "direction": item.provenance.get("direction"),
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

    baseline = await engine.retrieve(
        query,
        [(document_id, fixture)],
    )
    candidate_a = await engine.retrieve(
        query,
        [(document_id, fixture)],
        neighbor_primary_limit=1,
        neighbor_max_chunks=2,
    )
    candidate_b = await engine.retrieve(
        query,
        [(document_id, fixture)],
        neighbor_primary_limit=2,
        neighbor_max_chunks=4,
    )
    candidate_c = await engine.retrieve(
        query,
        [(document_id, fixture)],
        neighbor_primary_limit=2,
        neighbor_max_chunks=2,
    )

    def has_fact(items):
        return any(fact.lower() in item.passage.lower() for item in items)

    return {
        "fixture": fixture,
        "query": query,
        "fact": fact,
        "baseline": {
            "fact_present": has_fact(baseline),
            "count": len(baseline),
            "evidence": summarize(baseline),
        },
        "candidate_a_top1_pm1": {
            "fact_present": has_fact(candidate_a),
            "count": len(candidate_a),
            "evidence": summarize(candidate_a),
        },
        "candidate_b_top2_pm1": {
            "fact_present": has_fact(candidate_b),
            "count": len(candidate_b),
            "evidence": summarize(candidate_b),
        },
        "candidate_c_top2_budget2": {
            "fact_present": has_fact(candidate_c),
            "count": len(candidate_c),
            "evidence": summarize(candidate_c),
        },
    }


async def evaluate_duplicates(engine):
    phoenix = "phoenix_launch_protocol.md"
    refs = []
    for index in range(1, 4):
        document_id = f"phase-e-phoenix-dup-{index}"
        await ingest(
            engine,
            document_id,
            phoenix,
            source_name=f"phoenix-copy-{index}.md",
        )
        refs.append((document_id, f"phoenix-copy-{index}.md"))

    evidence = await engine.retrieve(
        "What is the PRIMARY launch sequence code for Project Phoenix?",
        refs,
    )

    return {
        "document_count": len(refs),
        "final_evidence_count": len(evidence),
        "evidence": summarize(evidence),
        "duplicate_group_observed": any(
            (item.provenance.get("duplicate_count") or 1) > 1
            for item in evidence
        ),
        "source_ids_preserved": sorted(
            {
                item.source_id
                for item in evidence
            }
            | {
                source["source_id"]
                for item in evidence
                for source in item.provenance.get("equivalent_sources", [])
            }
        ),
    }


async def main():
    with tempfile.TemporaryDirectory(prefix="openjm-phase-e-") as temp:
        engine = DBGPTKnowledgeEngine()
        engine.settings.vector_path = Path(temp)

        results = {
            "duplicates": await evaluate_duplicates(engine),
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

        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
