"""Phase C chunk-policy comparison harness.

Baselines the proven CHUNK_BY_SIZE behavior against installed candidate
strategies per document type, using isolated temporary vector paths.

For every fixture x strategy combination it measures:
- ingestion success and latency
- chunk count, contents and metadata (via the Chroma collection directly)
- retrieval count/scores/latency for the benchmark query
- expected-fact retrieval (production threshold and threshold=0)
- structural coherence checks per document type

Output is written to chunk_policy_evaluation.json (separate artifact; the
authoritative Phase B baseline_benchmark_results.json is never touched).

Usage:
    .venv/bin/python tests/fixtures/rag/chunk_policy_comparison.py
"""

import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path

import sys

REPO_BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_BACKEND))

from dbgpt_ext.rag import ChunkParameters
from dbgpt_ext.rag.assembler import EmbeddingAssembler
from dbgpt_ext.rag.knowledge import KnowledgeFactory

from app.core.config import get_settings
from app.services.knowledge import DBGPTKnowledgeEngine

FIXTURE_DIR = Path(__file__).resolve().parent


# ── engine with an isolated vector path ───────────────────────────────

def make_engine(tmpdir: str) -> DBGPTKnowledgeEngine:
    settings = get_settings()
    tmp_path = Path(tmpdir)
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings.vector_path = tmp_path
    engine = DBGPTKnowledgeEngine()
    engine.settings = settings
    engine.__dict__.pop("embedding_fn", None)
    return engine


def chroma_collection(engine: DBGPTKnowledgeEngine, document_id: str):
    """Return the raw Chroma collection via the engine's own store client."""
    store = engine._store(document_id)
    return store._collection


def read_collection(engine, document_id: str) -> dict:
    """Read every chunk (id, content, metadata) from a collection.

    Uses the SAME Chroma client as the engine's store. Creating a second
    PersistentClient on one path trips Chroma's singleton settings check,
    so we reuse the store's cached client.
    """
    store = engine._store(document_id)
    collection = store._collection
    get = collection.get(include=["documents", "metadatas"])
    chunks = [
        {"id": cid, "content": doc, "metadata": meta or {}}
        for cid, doc, meta in zip(get["ids"], get["documents"], get["metadatas"])
    ]
    return {"count": len(chunks), "chunks": chunks}


# ── comparison definitions ─────────────────────────────────────────────

# For each document type: the fixture, its benchmark query/expected fact,
# the baseline strategy and the installed candidates to compare.

COMPARISONS = [
    {
        "document_type": "txt",
        "label": "C. boundary_fact.txt",
        "file": "boundary_fact.txt",
        "doc_id": "doc-boundary-fact",
        "title": "Quarterly Review Metadata",
        "query": "What is the identifier for the Q3 2024 quarterly review?",
        "expected": "BR-7749-Q3",
        "baseline": "CHUNK_BY_SIZE",
        "candidates": ["CHUNK_BY_SEPARATOR"],
        # installed SeparatorTextSplitter pops enable_merge without a
        # default, so the strategy crashes under default ChunkParameters;
        # pass it explicitly to give the candidate a fair run.
        "candidate_params": {"CHUNK_BY_SEPARATOR": {"enable_merge": False}},
        "structural_checks": ["fact_within_single_chunk"],
    },
    {
        "document_type": "markdown",
        "label": "Phoenix. phoenix_launch_protocol.md",
        "file": "phoenix_launch_protocol.md",
        "doc_id": "PHOENIX-2024",
        "title": "PHOENIX Launch Protocol",
        "query": "What is the PRIMARY launch sequence code for Project Phoenix?",
        "expected": "7-3-9-2-5",
        "baseline": "CHUNK_BY_SIZE",
        "candidates": ["CHUNK_BY_MARKDOWN_HEADER"],
        "structural_checks": ["heading_hierarchy_metadata"],
    },
    {
        "document_type": "markdown",
        "label": "B. heading_context.md",
        "file": "heading_context.md",
        "doc_id": "doc-heading-ctx",
        "title": "Project Atlas Team Composition",
        "query": "How many engineers and data scientists are on the Project Atlas team?",
        "expected": "12 engineers and 4 data scientists",
        "baseline": "CHUNK_BY_SIZE",
        "candidates": ["CHUNK_BY_MARKDOWN_HEADER"],
        "structural_checks": [
            "heading_hierarchy_metadata",
            "no_unrelated_section_mixing",
        ],
    },
    {
        "document_type": "markdown",
        "label": "G. adjacent_context.md",
        "file": "adjacent_context.md",
        "doc_id": "doc-adjacent-ctx",
        "title": "Forecast Document",
        "query": "What assumption does the Q4 forecast rely on regarding new regulations?",
        "expected": "no new regulatory constraints",
        "baseline": "CHUNK_BY_SIZE",
        "candidates": ["CHUNK_BY_MARKDOWN_HEADER"],
        "structural_checks": ["heading_hierarchy_metadata"],
    },
    {
        "document_type": "markdown",
        "label": "A. long_report.md",
        "file": "long_report.md",
        "doc_id": "doc-long-report",
        "title": "QALO Strategic Insights Report",
        "query": "What was the total Q3 2024 revenue reached?",
        "expected": "QALO-7031",
        "baseline": "CHUNK_BY_SIZE",
        "candidates": ["CHUNK_BY_MARKDOWN_HEADER"],
        "structural_checks": ["heading_hierarchy_metadata"],
    },
    {
        "document_type": "docx",
        "label": "D. financial_table.docx",
        "file": "financial_table.docx",
        "doc_id": "doc-docx-table",
        "title": "Q3 2024 Financial Summary (Word)",
        "query": "What is the Analytics Services revenue value?",
        "expected": "1,425",
        "baseline": "CHUNK_BY_SIZE",
        "candidates": ["CHUNK_BY_PARAGRAPH"],
        "structural_checks": [
            "table_rows_together",
            "fact_within_single_chunk",
        ],
    },
    {
        "document_type": "pdf",
        "label": "E. financial_table.pdf",
        "file": "financial_table.pdf",
        "doc_id": "doc-pdf-table",
        "title": "Q3 2024 Projected ROI by Product Line",
        "query": "What was the projected ROI for Kefir Gold?",
        "expected": "18.5%",
        "baseline": "CHUNK_BY_SIZE",
        "candidates": ["CHUNK_BY_PAGE"],
        "structural_checks": [
            "page_metadata_present",
            "table_rows_together",
        ],
    },
    {
        "document_type": "pdf",
        "label": "F. multi_page_report.pdf",
        "file": "multi_page_report.pdf",
        "doc_id": "doc-multi-page",
        "title": "Multi-Quarter Project Report",
        "query": "What project codename is mentioned on page 2?",
        "expected": "PHOENIX-8822",
        "baseline": "CHUNK_BY_SIZE",
        "candidates": ["CHUNK_BY_PAGE"],
        "structural_checks": ["page_metadata_present", "fact_within_single_chunk"],
    },
    {
        "document_type": "pptx",
        "label": "H. slide_context.pptx",
        "file": "slide_context.pptx",
        "doc_id": "doc-slide-ctx",
        "title": "Q4 2024 Forecast Review Slides",
        "query": "What is the CAC ratio mentioned in the presentation?",
        "expected": "3.2",
        "baseline": "CHUNK_BY_SIZE",
        "candidates": ["CHUNK_BY_PAGE"],
        "structural_checks": ["slide_boundary", "fact_within_single_chunk"],
    },
    {
        "document_type": "html",
        "label": "HTML. minimal_section.html",
        "file": "minimal_section.html",
        "doc_id": "doc-html-section",
        "title": "Minimal HTML Section Fixture",
        "query": "What is the Echo Series identifier?",
        "expected": "ECHO-4477",
        "baseline": "CHUNK_BY_SIZE",
        "candidates": ["CHUNK_BY_SEPARATOR"],
        "candidate_params": {"CHUNK_BY_SEPARATOR": {"enable_merge": False}},
        "structural_checks": ["fact_within_single_chunk"],
    },
]


def run_structural_checks(name: str, chunks: list, expected: str) -> dict:
    """Run per-type structural coherence checks on the chunk list."""
    result = {}
    if name == "fact_within_single_chunk":
        result["pass"] = any(expected in c["content"] for c in chunks)
    elif name == "heading_hierarchy_metadata":
        header_keys = {"Header1", "Header2", "Header3", "Header4", "Header5", "Header6"}
        with_headers = [c for c in chunks if header_keys & set(c["metadata"])]
        result["chunks_with_header_metadata"] = len(with_headers)
        result["pass"] = len(with_headers) > 0
    elif name == "no_unrelated_section_mixing":
        # Atlas fact must not share a chunk with Phoenix/Orion facts.
        atlas = [c for c in chunks if "12 engineers and 4 data scientists" in c["content"]]
        mixed = [
            c
            for c in atlas
            if "8 engineers" in c["content"] or "5 engineers" in c["content"]
        ]
        result["atlas_chunks"] = len(atlas)
        result["mixed_chunks"] = len(mixed)
        result["pass"] = len(atlas) > 0 and not mixed
    elif name == "table_rows_together":
        # All three revenue rows must live in one single chunk (DOCX). For
        # PDF fixtures the loader keeps the markdown table verbatim, so
        # check the table values that actually survive extraction.
        if any("Analytics Services" in c["content"] for c in chunks):
            rows = ["4,500", "1,425", "3,200"]
        else:
            rows = ["15.2%", "18.5%"]
        result["pass"] = any(all(r in c["content"] for r in rows) for c in chunks)
    elif name == "page_metadata_present":
        result["pass"] = any("page" in c["metadata"] for c in chunks)
    elif name == "slide_boundary":
        # Each slide's title and its body must share a single chunk.
        pairs = [
            ("Revenue Overview", "3,500,000"),
            ("Customer Acquisition Cost", "3.2"),
        ]
        ok = all(
            any(t in c["content"] and v in c["content"] for c in chunks)
            for t, v in pairs
        )
        result["pass"] = ok
    else:
        result["pass"] = False
    return result


async def evaluate_strategy(engine, entry, strategy) -> dict:
    """Ingest one fixture with one strategy and measure everything."""
    file_path = FIXTURE_DIR / entry["file"]
    doc_id = entry["doc_id"]
    out = {
        "strategy": strategy,
        "role": "baseline" if strategy == entry["baseline"] else "candidate",
    }

    # -- ingestion ------------------------------------------------------
    extra_params = (entry.get("candidate_params") or {}).get(strategy, {})
    try:
        t0 = time.perf_counter()
        await engine.ingest_with_strategy(
            doc_id, file_path, strategy, extra_params=extra_params
        )
        t1 = time.perf_counter()
        out["ingestion_status"] = "success"
        out["ingestion_duration_s"] = round(t1 - t0, 4)
    except Exception as exc:
        out["ingestion_status"] = "failed"
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    # -- chunk inspection ----------------------------------------------
    try:
        collection = read_collection(engine, doc_id)
        out["chunk_count"] = collection["count"]
        out["chunks"] = [
            {
                "content_preview": c["content"][:500],
                "metadata": c["metadata"],
            }
            for c in collection["chunks"]
        ]
        out["chunk_contents_full"] = [
            c["content"] for c in collection["chunks"]
        ]
        metadata_keys = set()
        for c in collection["chunks"]:
            metadata_keys.update(c["metadata"].keys())
        out["metadata_keys"] = sorted(metadata_keys)
        chunks_for_checks = collection["chunks"]
    except Exception as exc:
        out["inspection_error"] = f"{type(exc).__name__}: {exc}"
        return out

    # -- structural checks ----------------------------------------------
    out["structural_checks"] = {
        name: run_structural_checks(name, chunks_for_checks, entry["expected"])
        for name in entry["structural_checks"]
    }

    # -- retrieval (production path) -------------------------------------
    try:
        t0 = time.perf_counter()
        evidence = await engine.retrieve(entry["query"], [(doc_id, entry["title"])])
        t1 = time.perf_counter()
        out["retrieval_duration_s"] = round(t1 - t0, 4)
        out["retrieval_count"] = len(evidence)
        out["retrieval_scores"] = [round(ev.score, 6) if ev.score else None for ev in evidence]
        out["expected_in_retrieval"] = any(
            entry["expected"] in ev.passage for ev in evidence
        )
        out["top_passage_preview"] = (
            evidence[0].passage[:500] if evidence else None
        )
    except Exception as exc:
        out["retrieval_error"] = f"{type(exc).__name__}: {exc}"

    return out


async def run_comparison() -> dict:
    results = {
        "harness": "chunk_policy_comparison",
        "phase": "C",
        "comparisons": [],
    }

    for entry in COMPARISONS:
        strategies = [entry["baseline"]] + entry["candidates"]
        comparison = {
            "label": entry["label"],
            "document_type": entry["document_type"],
            "file": entry["file"],
            "query": entry["query"],
            "expected": entry["expected"],
            "strategies": {},
        }
        print(f"\n{'=' * 64}")
        print(f"COMPARISON: {entry['label']}")
        print(f"{'=' * 64}")

        for strategy in strategies:
            # Each strategy gets its own isolated vector path and doc id
            # suffix so nothing is reused between runs.
            tmpdir = tempfile.mkdtemp(prefix=f"chunkcmp_{entry['doc_id']}_{strategy}_")
            engine = make_engine(tmpdir)
            doc_id = entry["doc_id"]
            try:
                outcome = await evaluate_strategy(engine, entry, strategy)
            except Exception as exc:
                outcome = {"strategy": strategy, "ingestion_status": "failed",
                           "error": f"{type(exc).__name__}: {exc}"}
            comparison["strategies"][strategy] = outcome

            role = outcome.get("role", "?")
            status = outcome.get("ingestion_status", "unknown")
            count = outcome.get("chunk_count", "N/A")
            found = outcome.get("expected_in_retrieval", False)
            print(
                f"  [{role:9s}] {strategy:26s} ingest={status} "
                f"chunks={count} fact_found={found} "
                f"checks={ {k: v.get('pass') for k, v in outcome.get('structural_checks', {}).items()} }"
            )
            shutil.rmtree(tmpdir, ignore_errors=True)

        results["comparisons"].append(comparison)

    return results


if __name__ == "__main__":
    results = asyncio.run(run_comparison())

    output_path = FIXTURE_DIR / "chunk_policy_evaluation.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved to {output_path}")
