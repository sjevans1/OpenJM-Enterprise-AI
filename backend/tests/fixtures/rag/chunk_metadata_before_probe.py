"""Phase D pre-implementation probe: actual chunk metadata per fixture type.

Runs the real installed DB-GPT loaders + OpenJM policy splitters (ChunkManager
only, no embedding model, no Chroma) against the RAG fixtures and dumps every
chunk's metadata + content prefix. This is the authoritative 'before' state.
"""

import json
import sys
from pathlib import Path

REPO_BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_BACKEND))

from app.services.ingestion_policy import ingestion_policy  # noqa: E402

FIXTURE_DIR = REPO_BACKEND / "tests" / "fixtures" / "rag"

from dbgpt_ext.rag.knowledge import KnowledgeFactory  # noqa: E402
from dbgpt_ext.rag.chunk_manager import ChunkManager, ChunkParameters  # noqa: E402


def knowledge_for(file_path: Path):
    if file_path.suffix.lower() == ".docx":
        from app.services.docx_extractor import OpenJMDocxKnowledge

        return OpenJMDocxKnowledge(file_path=str(file_path))
    return KnowledgeFactory.from_file_path(str(file_path))


FILES = [
    "long_report.md",
    "heading_context.md",
    "phoenix_launch_protocol.md",
    "boundary_fact.txt",
    "financial_table.docx",
    "financial_table.pdf",
    "multi_page_report.pdf",
    "slide_context.pptx",
]

report = {}
for name in FILES:
    path = FIXTURE_DIR / name
    decision = ingestion_policy().for_document(path)
    entry = {
        "extension": decision.extension,
        "policy": decision.policy_name,
        "strategy": decision.chunk_strategy,
        "knowledge_class": decision.knowledge_class_name,
        "chunks": [],
    }
    try:
        knowledge = knowledge_for(path)
        params = ChunkParameters(**decision.chunk_parameters_kwargs())
        manager = ChunkManager(knowledge=knowledge, chunk_parameter=params)
        chunks = manager.split(knowledge.load())
        entry["chunk_count"] = len(chunks)
        for i, chunk in enumerate(chunks):
            entry["chunks"].append(
                {
                    "index": i,
                    "chunk_id": chunk.chunk_id,
                    "metadata": dict(chunk.metadata or {}),
                    "content_len": len(chunk.content or ""),
                    "content_head": (chunk.content or "")[:160],
                    "content_tail": (chunk.content or "")[-160:],
                }
            )
    except Exception as exc:  # noqa: BLE001
        entry["error"] = f"{type(exc).__name__}: {exc}"
    report[name] = entry
    print(f"== {name} [{entry['policy']}] chunks={entry.get('chunk_count', 'ERR')}")

out = Path(__file__).resolve().parent / "chunk_metadata_before.json"
out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
print(f"\nwritten: {out}")

# Compact console summary of metadata keys per file
for name, entry in report.items():
    if "error" in entry:
        print(f"{name}: ERROR {entry['error']}")
        continue
    keys = sorted({k for c in entry["chunks"] for k in c["metadata"]})
    print(f"{name}: {entry['chunk_count']} chunks, metadata keys = {keys}")
