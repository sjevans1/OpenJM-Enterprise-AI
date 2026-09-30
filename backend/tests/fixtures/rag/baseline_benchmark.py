"""Phase A baseline benchmark: deterministic fixtures + current-state measurement.

Runs the full OpenJM ingestion + retrieval pipeline (actual production code,
no stubs) against deterministic fixtures and records the complete baseline
for Gate J (document intelligence / RAG fidelity).

Usage:
    .venv/bin/python tests/fixtures/rag/baseline_benchmark.py
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

from app.core.config import get_settings
from app.services.knowledge import DBGPTKnowledgeEngine

FIXTURE_DIR = Path(__file__).resolve().parent


def make_engine(tmpdir: str):
    """Create a DBGPTKnowledgeEngine with a temp vector store path."""
    settings = get_settings()
    # Override vector_path to an isolated temp directory
    tmp_path = Path(tmpdir)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "chromadb").mkdir(parents=True, exist_ok=True)
    settings.vector_path = tmp_path
    engine = DBGPTKnowledgeEngine()
    engine.settings = settings
    # Clear cached_property so it re-reads overridden settings
    engine.__dict__.pop("embedding_fn", None)
    return engine


def inspect_chroma(engine, document_id: str):
    """Inspect the Chroma collection directly for chunk/metadata detail."""
    collection_name = engine._collection_name(document_id)
    persist_dir = engine.settings.vector_path / "chromadb"
    try:
        from chromadb import PersistentClient, Settings as ChromaSettings

        client = PersistentClient(
            path=str(persist_dir),
            settings=ChromaSettings(anonymous_telemetry=False),
        )
        info = {"collection_name": collection_name, "found": False}
        try:
            col = client.get_collection(collection_name)
            info["found"] = True
            info["count"] = col.count()
            peek = col.peek(limit=100)
            ids = peek.get("ids", [])
            docs = peek.get("documents", [])
            metas = peek.get("metadatas", [])
            info["chunk_ids"] = ids
            info["chunk_count"] = len(ids)
            info["stored_docs"] = docs
            info["stored_metadatas"] = metas
            # Check which metadata keys are present
            if metas:
                all_keys = set()
                for m in metas:
                    all_keys.update(m.keys())
                info["metadata_keys"] = sorted(all_keys)
        except Exception as e:
            info["error"] = str(e)
        return info
    except Exception as e:
        return {"collection_name": collection_name, "found": False, "error": f"chroma_client: {e}"}


# ── benchmark definitions ─────────────────────────────────────────────
# Each entry: (label, file, document_id, title, query, expected_fact)

BENCHMARKS = [
    {
        "label": "A. long_report.md - ordinary narrative retrieval",
        "file": "long_report.md",
        "doc_id": "doc-long-report",
        "title": "QALO Strategic Insights Report",
        "query": "What was the total Q3 2024 revenue reached?",
        "expected": "QALO-7031",
        "category": "A",
    },
    {
        "label": "B. heading_context.md - heading-dependent context",
        "file": "heading_context.md",
        "doc_id": "doc-heading-ctx",
        "title": "Project Atlas Team Composition",
        "query": "How many engineers and data scientists are on the Project Atlas team?",
        "expected": "12 engineers and 4 data scientists",
        "category": "B",
    },
    {
        "label": "C. boundary_fact.txt - fact near chunk boundary",
        "file": "boundary_fact.txt",
        "doc_id": "doc-boundary-fact",
        "title": "Quarterly Review Metadata",
        "query": "What is the identifier for the Q3 2024 quarterly review?",
        "expected": "BR-7749-Q3",
        "category": "C",
    },
    {
        "label": "D. financial_table.docx - value ONLY in DOCX table",
        "file": "financial_table.docx",
        "doc_id": "doc-docx-table",
        "title": "Q3 2024 Financial Summary (Word)",
        "query": "What is the Analytics Services revenue value?",
        "expected": "1,425",
        "category": "D",
    },
    {
        "label": "E. financial_table.pdf - value in PDF table cell",
        "file": "financial_table.pdf",
        "doc_id": "doc-pdf-table",
        "title": "Q3 2024 Projected ROI by Product Line",
        "query": "What was the projected ROI for Kefir Gold?",
        "expected": "18.5%",
        "category": "E",
    },
    {
        "label": "F. multi_page_report.pdf - page-specific context",
        "file": "multi_page_report.pdf",
        "doc_id": "doc-multi-page",
        "title": "Multi-Quarter Project Report",
        "query": "What project codename is mentioned on page 2?",
        "expected": "PHOENIX-8822",
        "category": "F",
    },
    {
        "label": "G. adjacent_context.md - statement requiring adjacent context",
        "file": "adjacent_context.md",
        "doc_id": "doc-adjacent-ctx",
        "title": "Forecast Document",
        "query": "What assumption does the Q4 forecast rely on regarding new regulations?",
        "expected": "no new regulatory constraints",
        "category": "G",
    },
    {
        "label": "H. slide_context.pptx - slide-specific context",
        "file": "slide_context.pptx",
        "doc_id": "doc-slide-ctx",
        "title": "Q4 2024 Forecast Review Slides",
        "query": "What is the CAC ratio mentioned in the presentation?",
        "expected": "3.2",
        "category": "H",
    },
]


async def run_benchmark():
    tmpdir = tempfile.mkdtemp(prefix="rag_baseline_")
    engine = make_engine(tmpdir)

    results = {"tmpdir": tmpdir, "fixtures": {}, "phoenix": {}, "errors": []}

    for bm in BENCHMARKS:
        label = bm["label"]
        file_path = FIXTURE_DIR / bm["file"]
        doc_id = bm["doc_id"]

        print(f"\n{'='*60}")
        print(f"BENCHMARK: {label}")
        print(f"{'='*60}")

        fixture_result = {
            "label": label,
            "file": bm["file"],
            "document_id": doc_id,
            "query": bm["query"],
            "expected": bm["expected"],
            "category": bm["category"],
        }

        # --- Ingestion ---
        try:
            t0 = time.perf_counter()
            await engine.ingest(doc_id, file_path)
            t1 = time.perf_counter()
            fixture_result["ingestion_duration_s"] = round(t1 - t0, 4)
            fixture_result["ingestion_status"] = "success"
            print(f"  Ingestion: {fixture_result['ingestion_duration_s']}s")
        except Exception as exc:
            fixture_result["ingestion_status"] = "failed"
            fixture_result["ingestion_error"] = f"{type(exc).__name__}: {exc}"
            results["errors"].append(f"{label}: ingestion failed: {exc}")
            print(f"  Ingestion FAILED: {exc}")
            results["fixtures"][label] = fixture_result
            continue

        # --- Chroma inspection ---
        chroma_info = inspect_chroma(engine, doc_id)
        fixture_result["chroma"] = chroma_info
        print(f"  Chroma: found={chroma_info['found']}, count={chroma_info.get('count', 'N/A')}")
        print(f"  Metadata keys: {chroma_info.get('metadata_keys', [])}")

        # --- Retrieval (production threshold) ---
        try:
            t0 = time.perf_counter()
            evidence = await engine.retrieve(bm["query"], [(doc_id, bm["title"])])
            t1 = time.perf_counter()
            fixture_result["retrieval_duration_s"] = round(t1 - t0, 4)
            fixture_result["retrieval_count"] = len(evidence)
            print(f"  Retrieval (threshold={engine.settings.rag_score_threshold}): {len(evidence)} results in {fixture_result['retrieval_duration_s']}s")

            fixture_result["retrieval_results"] = []
            all_passage_text = ""
            for ev in evidence:
                all_passage_text += ev.passage + " "
                fixture_result["retrieval_results"].append({
                    "score": ev.score,
                    "passage_preview": ev.passage[:300],
                    "metadata": ev.metadata,
                })
                print(f"    score={ev.score:.4f}, passage: {ev.passage[:100]}...")

            # --- Retrieval (threshold=0.0, to see all top-K) ---
            # Directly use EmbeddingRetriever for all results
            from dbgpt.rag.retriever import EmbeddingRetriever
            store = engine._store(doc_id)
            retriever = EmbeddingRetriever(
                top_k=engine.settings.rag_top_k,
                index_store=store,
            )
            all_chunks = await retriever.aretrieve_with_scores(bm["query"], score_threshold=0.0)
            fixture_result["retrieval_all_k"] = len(all_chunks)
            fixture_result["retrieval_all_results"] = [
                {
                    "score": round(c.score, 6) if c.score else None,
                    "chunk_id": getattr(c, "chunk_id", None),
                    "content_preview": c.content[:200] if c.content else "(empty)",
                    "metadata": dict(getattr(c, "metadata", {}) or {}),
                }
                for c in all_chunks
            ]
            print(f"  Retrieval (threshold=0.0): {len(all_chunks)} results")

            # --- Answer correctness ---
            found = bm["expected"] in all_passage_text
            if not found:
                # Also check in all retrieved chunks
                for c in all_chunks:
                    if bm["expected"] in c.content:
                        found = True
                        break

            if found and len(evidence) > 0:
                status = "PASS"
            elif found and len(evidence) == 0:
                status = "PARTIAL"
            elif found:
                status = "PASS"
            else:
                status = "FAIL"

            fixture_result["answer_correctness"] = status
            fixture_result["expected_in_passage"] = found
            print(f"  CORRECTNESS: {status} (expected '{bm['expected']}' found={found})")

        except Exception as exc:
            fixture_result["retrieval_status"] = "failed"
            fixture_result["retrieval_error"] = f"{type(exc).__name__}: {exc}"
            results["errors"].append(f"{label}: retrieval failed: {exc}")
            print(f"  Retrieval FAILED: {exc}")

        results["fixtures"][label] = fixture_result

    # --- Phoenix acceptance (Gate C) ---
    print(f"\n{'='*60}")
    print("PHOENIX ACCEPTANCE (Gate C)")
    print(f"{'='*60}")

    phoenix_path = FIXTURE_DIR / "phoenix_launch_protocol.md"
    phoenix_doc_id = "PHOENIX-2024"
    phoenix_title = "PHOENIX Launch Protocol"
    phoenix_query = "What is the PRIMARY launch sequence code for Project Phoenix?"
    phoenix_expected = "7-3-9-2-5"

    phoenix_result = {
        "query": phoenix_query,
        "expected": phoenix_expected,
        "document_id": phoenix_doc_id,
    }

    try:
        t0 = time.perf_counter()
        await engine.ingest(phoenix_doc_id, phoenix_path)
        t1 = time.perf_counter()
        phoenix_result["ingestion_duration_s"] = round(t1 - t0, 4)
        phoenix_result["ingestion_status"] = "success"
        print(f"  Ingestion: {phoenix_result['ingestion_duration_s']}s")
    except Exception as exc:
        phoenix_result["ingestion_status"] = "failed"
        phoenix_result["ingestion_error"] = str(exc)
        results["errors"].append(f"Phoenix: ingestion failed: {exc}")
        print(f"  Ingestion FAILED: {exc}")

    try:
        t0 = time.perf_counter()
        evidence = await engine.retrieve(
            phoenix_query, [(phoenix_doc_id, phoenix_title)]
        )
        t1 = time.perf_counter()
        phoenix_result["retrieval_duration_s"] = round(t1 - t0, 4)
        phoenix_result["retrieval_count"] = len(evidence)
        phoenix_result["retrieval_results"] = [
            {
                "score": ev.score,
                "passage_preview": ev.passage[:300],
                "source_id": ev.source_id,
                "title": ev.title,
                "metadata": ev.metadata,
            }
            for ev in evidence
        ]
        all_passage_text = " ".join(ev.passage for ev in evidence)
        found = phoenix_expected in all_passage_text
        phoenix_result["expected_in_passage"] = found
        phoenix_result["status"] = "PASS" if found and len(evidence) > 0 else (
            "PARTIAL" if found else "FAIL"
        )
        print(f"  Retrieval: {len(evidence)} results in {phoenix_result['retrieval_duration_s']}s")
        print(f"  CORRECTNESS: {phoenix_result['status']}")
    except Exception as exc:
        phoenix_result["retrieval_status"] = "failed"
        phoenix_result["retrieval_error"] = str(exc)
        phoenix_result["status"] = "FAIL"
        results["errors"].append(f"Phoenix: retrieval failed: {exc}")
        print(f"  Retrieval FAILED: {exc}")

    results["phoenix"] = phoenix_result

    # Cleanup
    shutil.rmtree(tmpdir, ignore_errors=True)

    return results


if __name__ == "__main__":
    results = asyncio.run(run_benchmark())

    output_path = FIXTURE_DIR / "baseline_benchmark_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n{'='*60}")
    print("BASELINE SUMMARY")
    print(f"{'='*60}")
    for label, fr in results["fixtures"].items():
        status = fr.get("answer_correctness", "N/A")
        ing = fr.get("ingestion_duration_s", "N/A")
        ret_count = fr.get("retrieval_count", 0)
        print(f"  {status:8s} | ing={ing}s | ret={ret_count} | {label}")

    print(f"\nPhoenix: {results['phoenix'].get('status', 'N/A')}")
    if results["errors"]:
        print(f"\nErrors ({len(results['errors'])}):")
        for e in results["errors"]:
            print(f"  - {e}")

    print(f"\nResults saved to {output_path}")
