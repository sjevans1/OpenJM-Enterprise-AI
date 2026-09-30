"""Phase D AFTER probe: full ingest->Chroma->retrieve->Evidence with enrichment.

Runs the real pipeline (embedding model + Chroma) against the fixtures in an
isolated temp vector store and verifies:
  1. Chroma-stored metadata actually contains the Phase D structural keys
     (proves _transform_chroma_metadata compatibility).
  2. customer-visible Evidence carries the structural keys.
  3. no internal filesystem path appears in Evidence metadata (security).
  4. PPTX slide numbers verified through the full Evidence response.
  5. long_section.md: does the BOUNDARY-FACT-PHASE-D-9931 near the end of a
     >2,000-char section survive into the Evidence passage? (passage cap test)
"""

import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_BACKEND))

from app.core.config import get_settings
from app.services.knowledge import DBGPTKnowledgeEngine

FIXTURE_DIR = REPO_BACKEND / "tests" / "fixtures" / "rag"


def make_engine(tmpdir: str):
    settings = get_settings()
    tmp_path = Path(tmpdir)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "chromadb").mkdir(parents=True, exist_ok=True)
    settings.vector_path = tmp_path
    engine = DBGPTKnowledgeEngine()
    engine.settings = settings
    engine.__dict__.pop("embedding_fn", None)
    return engine


def chroma_meta(engine, document_id):
    """Read stored metadata via the engine's own ChromaStore client (the
    ChromaSharedSystem cache rejects a second client with different
    settings for the same path)."""

    store = engine._store(document_id)
    col = store._chroma_client.get_collection(engine._collection_name(document_id))
    got = col.get(include=["metadatas", "documents"])
    return got


CASES = [
    # (file, doc_id, title, query, expected_fact)
    ("heading_context.md", "doc-heading-ctx", "heading_context.md",
     "How many engineers and data scientists are on the Project Atlas team?",
     "12 engineers and 4 data scientists"),
    ("multi_page_report.pdf", "doc-multi-page", "multi_page_report.pdf",
     "What project codename is mentioned on page 2?",
     "PHOENIX-8822"),
    ("financial_table.docx", "doc-docx-table", "financial_table.docx",
     "What is the Analytics Services revenue value?", "1,425"),
    ("slide_context.pptx", "doc-slide-ctx", "slide_context.pptx",
     "What is the CAC ratio mentioned in the presentation?", "3.2"),
    ("financial_table.pdf", "doc-pdf-table", "financial_table.pdf",
     "What was the projected ROI for Kefir Gold?", "18.5%"),
    ("boundary_fact.txt", "doc-boundary-fact", "boundary_fact.txt",
     "What is the identifier for the Q3 2024 quarterly review?", "BR-7749-Q3"),
    ("minimal_section.htm", "doc-htm-echo", "minimal_section.htm",
     "What is the Echo Series identifier?", "ECHO-4477"),
    ("long_section.md", "doc-long-section", "long_section.md",
     "What is the unique probe identifier near the end of the long section?",
     "BOUNDARY-FACT-PHASE-D-9931"),
]


async def main():
    tmpdir = tempfile.mkdtemp(prefix="phase_d_after_")
    engine = make_engine(tmpdir)
    report = {}

    try:
        for file_name, doc_id, title, query, expected in CASES:
            print(f"\n=== {file_name}")
            entry = {"file": file_name}
            path = FIXTURE_DIR / file_name

            t0 = time.perf_counter()
            await engine.ingest(doc_id, path)
            entry["ingest_s"] = round(time.perf_counter() - t0, 3)

            stored = chroma_meta(engine, doc_id)
            metas = stored.get("metadatas", []) or []
            entry["chunk_count"] = len(metas)
            if metas:
                all_keys = sorted({k for m in metas for k in (m or {})})
                entry["stored_metadata_keys"] = all_keys
                entry["stored_metadata_sample"] = metas[0]
                # Chroma-level structural key presence
                entry["has_document_id"] = all("document_id" in (m or {}) for m in metas)
                entry["has_chunk_index"] = all("chunk_index" in (m or {}) for m in metas)
                entry["path_leak_in_chroma"] = any(
                    isinstance(m.get("source"), str) and m["source"].startswith("/")
                    for m in metas
                )

            t0 = time.perf_counter()
            evidence = await engine.retrieve(query, [(doc_id, title)])
            entry["retrieve_s"] = round(time.perf_counter() - t0, 3)
            entry["evidence_count"] = len(evidence)

            found = False
            ev_meta_keys = set()
            path_leak = False
            for ev in evidence:
                ev_meta_keys.update(ev.metadata.keys())
                if expected in ev.passage:
                    found = True
                for v in ev.metadata.values():
                    if isinstance(v, str) and v.startswith("/"):
                        path_leak = True
                # also check title
            entry["expected_fact_in_passage"] = found
            entry["evidence_metadata_keys"] = sorted(ev_meta_keys)
            entry["path_leak_in_evidence"] = path_leak
            entry["status"] = "PASS" if found else "FAIL"

            if evidence:
                entry["top_evidence_metadata"] = evidence[0].metadata
                entry["top_passage_tail"] = evidence[0].passage[-120:]

            # long-section passage-cap detail
            if file_name == "long_section.md":
                docs = stored.get("documents", []) or []
                entry["chunk_lengths"] = [len(d) for d in docs]
                for d in docs:
                    if "BOUNDARY-FACT-PHASE-D-9931" in d:
                        entry["fact_in_stored_chunk"] = True
                        entry["chunk_with_fact_len"] = len(d)
                        break
                else:
                    entry["fact_in_stored_chunk"] = False
                entry["passage_lengths"] = [len(ev.passage) for ev in evidence]
                entry["fact_in_evidence_passage"] = found

            report[file_name] = entry
            print(f"  chunks={entry['chunk_count']} evidence={entry['evidence_count']} "
                  f"fact={entry['expected_fact_in_passage']} path_leak={entry['path_leak_in_evidence']}")
            if "chunk_lengths" in entry:
                print(f"  chunk_lengths={entry['chunk_lengths']}")
                print(f"  fact_in_stored_chunk={entry['fact_in_stored_chunk']}")

        # deletion check on one enriched doc
        await engine.delete("doc-heading-ctx")
        report["deletion"] = {
            "collection_removed": not await asyncio.to_thread(
                engine._chroma_collection_exists, engine._collection_name("doc-heading-ctx")
            ),
        }
        print(f"\ndeletion: collection_removed={report['deletion']['collection_removed']}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    out = Path(__file__).resolve().parent / "chunk_metadata_after.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwritten: {out}")

    for name, e in report.items():
        if name == "deletion":
            continue
        print(f"{e['status']:4s} {name}: keys={e.get('stored_metadata_keys', [])}")


if __name__ == "__main__":
    asyncio.run(main())
