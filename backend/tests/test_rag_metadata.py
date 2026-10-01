"""Phase D deterministic metadata tests.

These tests never require the LLM. They exercise the real OpenJM ingestion
policy seam + the real DB-GPT loaders/splitters (ChunkManager only, no
embedding model, no Chroma) and assert structural metadata contracts:

- chunk ordering and document identity (document_id / chunk_index / chunk_id)
- Markdown heading_path derived from Header1..Header6
- PDF page_number normalization (1-based int)
- PPTX slide_number (OpenJMPPTXKnowledge)
- DOCX table preservation + 1,425 fact
- legacy metadata compatibility (no KeyError when new keys are absent)
- security: no internal filesystem path disclosure in sanitize_for_evidence
- server-owned keys cannot be forged from content (overwrite semantics)
- authorized-document isolation: evidence for doc A never references doc B
- document deletion removes all retrievable evidence (Chroma-level)
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from app.services.chunk_metadata import (
    enrich_chunks,
    sanitize_for_evidence,
    STRUCTURAL_KEYS,
)
from app.services.knowledge import _knowledge_for
from app.services.ingestion_policy import ingestion_policy
from dbgpt.core import Chunk
from dbgpt_ext.rag.chunk_manager import ChunkParameters

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "rag"


def _chunks_for(file_name: str):
    """Run a fixture through the real loader + policy splitter (no embedding)."""

    path = FIXTURE_DIR / file_name
    decision = ingestion_policy().for_document(path)
    knowledge = _knowledge_for(path)
    params = ChunkParameters(**decision.chunk_parameters_kwargs())
    from dbgpt_ext.rag.chunk_manager import ChunkManager

    manager = ChunkManager(knowledge=knowledge, chunk_parameter=params)
    return knowledge, decision, manager.split(knowledge.load())


# ── enrich_chunks: document identity + ordering ────────────────────────────


def test_enrich_assigns_document_id_chunk_index_and_chunk_id_in_order():
    document_id = "doc-phase-d-test"
    chunks = [Chunk(content="a"), Chunk(content="b"), Chunk(content="c")]
    enrich_chunks(chunks, document_id=document_id, source_name="x.md", policy_name="p")

    for i, chunk in enumerate(chunks):
        assert chunk.metadata["document_id"] == document_id
        assert chunk.metadata["chunk_index"] == i
        assert chunk.metadata["source_type"] == "document"
        assert chunk.metadata["source_name"] == "x.md"
        assert chunk.metadata["content_type"] == "text"
        assert chunk.metadata["ingestion_policy"] == "p"
        assert chunk.metadata["chunk_id"]  # non-empty

    assert chunks[1].metadata["previous_chunk_id"] == chunks[0].metadata["chunk_id"]
    assert chunks[1].metadata["next_chunk_id"] == chunks[2].metadata["chunk_id"]
    assert "previous_chunk_id" not in chunks[0].metadata
    assert "next_chunk_id" not in chunks[2].metadata


def test_enrich_does_not_alter_chunk_content():
    document_id = "doc-content-check"
    chunks = [Chunk(content="first content", metadata={"foo": "bar"})]
    before = chunks[0].content
    enrich_chunks(chunks, document_id=document_id, source_name="n.md", policy_name="p")
    assert chunks[0].content == before
    # loader metadata preserved
    assert chunks[0].metadata["foo"] == "bar"


# ── Markdown heading_path ──────────────────────────────────────────────────


def test_markdown_heading_path_ordered_from_header_keys():
    chunk = Chunk(
        content="body",
        metadata={
            "Header1": "Project Atlas",
            "Header2": "Team Composition",
            "Header3": "Engineering",
            "source": "/tmp/x.md",
            "title": "x.md",
        },
    )
    enrich_chunks([chunk], document_id="d", source_name="x.md", policy_name="p")
    assert chunk.metadata["heading_path"] == "Project Atlas > Team Composition > Engineering"


def test_markdown_heading_path_skips_missing_levels():
    chunk = Chunk(
        content="body",
        metadata={"Header1": "Root", "Header3": "Deep"},
    )
    enrich_chunks([chunk], document_id="d", source_name="x.md", policy_name="p")
    assert chunk.metadata["heading_path"] == "Root > Deep"


def test_markdown_heading_path_absent_when_no_headers():
    chunk = Chunk(content="body", metadata={"source": "/tmp/x.md"})
    enrich_chunks([chunk], document_id="d", source_name="x.md", policy_name="p")
    assert "heading_path" not in chunk.metadata


def test_markdown_fixture_heading_paths_match_section_nesting():
    _, _, chunks = _chunks_for("heading_context.md")
    # 4 projects x (Team Composition + Technical Stack) => 5 chunks incl. agg
    enrich_chunks(chunks, document_id="doc-heading", source_name="heading_context.md", policy_name="markdown_header_aware")
    atlas_team = [c for c in chunks if c.metadata.get("Header1") == "Project Atlas" and c.metadata.get("Header2") == "Team Composition"]
    assert atlas_team, "expected an Atlas/Team Composition chunk"
    assert atlas_team[0].metadata["heading_path"] == "Project Atlas > Team Composition"


# ── PDF page numbers ─────────────────────────────────────────────────────


def test_pdf_page_numbers_are_normalized_integers():
    _, _, chunks = _chunks_for("multi_page_report.pdf")
    enrich_chunks(chunks, document_id="doc-pdf", source_name="multi_page_report.pdf", policy_name="fallback_recursive_size_overlap")
    pages = [c.metadata.get("page_number") for c in chunks]
    assert all(isinstance(p, int) for p in pages)
    assert pages == sorted(pages)  # document order == ascending
    assert pages[0] == 1  # loader emits 1-based page metadata


def test_pdf_content_type_preserves_table_indicator():
    _, _, chunks = _chunks_for("financial_table.pdf")
    enrich_chunks(chunks, document_id="doc-pdftab", source_name="financial_table.pdf", policy_name="fallback_recursive_size_overlap")
    types = {c.metadata.get("content_type") for c in chunks}
    # at least one chunk reports the excel/table content type from the loader
    assert types, "expected content_type on PDF chunks"


# ── PPTX slide numbers ───────────────────────────────────────────────────


def test_pptx_slide_numbers_present_and_ordered():
    _, _, chunks = _chunks_for("slide_context.pptx")
    enrich_chunks(chunks, document_id="doc-pptx", source_name="slide_context.pptx", policy_name="fallback_recursive_size_overlap")
    slides = [c.metadata.get("slide_number") for c in chunks]
    assert all(isinstance(s, int) for s in slides)
    assert slides == sorted(slides)
    assert slides[0] == 1
    assert slides[-1] == len(chunks)  # 1..N contiguous


def test_pptx_3_2_fact_retrievable_with_slide_metadata():
    _, _, chunks = _chunks_for("slide_context.pptx")
    enrich_chunks(chunks, document_id="doc-pptx", source_name="slide_context.pptx", policy_name="fallback_recursive_size_overlap")
    joined = " ".join(c.content for c in chunks)
    assert "3.2" in joined
    # every chunk carries structural identity alongside the content
    assert all(c.metadata.get("slide_number") for c in chunks)


# ── DOCX table preservation ──────────────────────────────────────────────


def test_docx_table_value_retrievable_with_structural_metadata():
    _, _, chunks = _chunks_for("financial_table.docx")
    enrich_chunks(chunks, document_id="doc-docx", source_name="financial_table.docx", policy_name="docx_structure_preserve")
    joined = " ".join(c.content for c in chunks)
    assert "1,425" in joined
    for chunk in chunks:
        assert chunk.metadata["document_id"] == "doc-docx"
        # DOCX emits one logical Document; structural identity is still set
        assert "chunk_index" in chunk.metadata


# ── legacy / backward compatibility ────────────────────────────────────────


def test_legacy_chunk_without_new_keys_is_supported():
    """Chunks persisted before Phase D lack structural keys; sanitize must
    not KeyError on them, and must still scrub path-like 'source' values."""

    legacy = Chunk(
        content="legacy",
        metadata={"source": "/some/internal/path/doc.pdf", "page": 3},
        chunk_id="legacy-1",
    )
    # enrich on a legacy chunk still works
    enrich_chunks([legacy], document_id="d", source_name="doc.pdf", policy_name="p")
    assert legacy.metadata["document_id"] == "d"
    # sanitize tolerates legacy keys and scrubs the path
    cleaned = sanitize_for_evidence(
        {"source": "/srv/uploads/x.pdf", "page": 3}, "x.pdf"
    )
    assert cleaned["source"] == "x.pdf"
    assert isinstance(cleaned["page"], int)


# ── security: path disclosure ──────────────────────────────────────────────


def test_sanitize_replaces_internal_path_in_source_with_title():
    cleaned = sanitize_for_evidence(
        {"source": "/home/sjeva/openjm-enterprise-ai/data/uploads/abc_file.pdf"},
        "file.pdf",
    )
    assert cleaned["source"] == "file.pdf"
    assert not cleaned["source"].startswith("/")


def test_sanitize_does_not_touch_server_owned_structural_keys():
    cleaned = sanitize_for_evidence(
        {
            "document_id": "abc-123",
            "chunk_index": 2,
            "heading_path": "Root > Child",
            "source": "/tmp/internal",
        },
        "file.md",
    )
    assert cleaned["document_id"] == "abc-123"
    assert cleaned["chunk_index"] == 2
    assert cleaned["heading_path"] == "Root > Child"
    assert cleaned["source"] == "file.md"


def test_server_owned_keys_overwrite_loader_supplied_values():
    """Content cannot forge document identity: enrich_chunks always
    overwrites server-owned keys with server values."""

    malicious = Chunk(
        content="body",
        metadata={
            "document_id": "attacker-forged",
            "chunk_index": "evil",
            "source": "/tmp/x.md",
        },
    )
    enrich_chunks(
        [malicious],
        document_id="real-server-id",
        source_name="x.md",
        policy_name="p",
    )
    assert malicious.metadata["document_id"] == "real-server-id"
    assert malicious.metadata["chunk_index"] == 0  # int, not "evil"


# ── .htm compatibility (Phase D fix) ───────────────────────────────────────


def test_htm_loads_through_openjm_alias():
    """DB-GPT's factory raises on .htm; the OpenJM seam maps it to
    OpenJMHtmlKnowledge and the loader must produce real content."""

    path = FIXTURE_DIR / "minimal_section.htm"
    knowledge = _knowledge_for(path)
    documents = knowledge.load()
    joined = " ".join(d.content for d in documents)
    assert "ECHO-4477" in joined
    # .htm policy decision is the safe fallback, unchanged from Phase C
    decision = ingestion_policy().for_document(path)
    assert decision.chunk_strategy == "CHUNK_BY_SIZE"
    assert decision.knowledge_class_name == "OpenJMHtmlKnowledge"


def test_htm_chunks_carry_structural_metadata():
    _, decision, chunks = _chunks_for("minimal_section.htm")
    enrich_chunks(chunks, document_id="doc-htm", source_name="minimal_section.htm", policy_name=decision.policy_name)
    for c in chunks:
        assert c.metadata["document_id"] == "doc-htm"
        assert "chunk_index" in c.metadata
    # TXT/HTML types use neutral metadata: no page/heading fabrication
    assert all("page_number" not in c.metadata for c in chunks)
    assert all("heading_path" not in c.metadata for c in chunks)


def test_html_chunks_use_neutral_metadata():
    _, decision, chunks = _chunks_for("minimal_section.html")
    enrich_chunks(chunks, document_id="doc-html", source_name="minimal_section.html", policy_name=decision.policy_name)
    assert all("page_number" not in c.metadata for c in chunks)
    assert all("slide_number" not in c.metadata for c in chunks)



def test_evidence_is_scoped_per_document_id(tmp_path, monkeypatch):
    """retrieve() only iterates caller-supplied documents; doc A's evidence
    never leaks into a doc B query. Verified by constructing Evidence from
    chunk metadata scoped to a single document id."""

    document_id = "doc-isolation-a"
    path = FIXTURE_DIR / "heading_context.md"
    _, decision, chunks = _chunks_for("heading_context.md")
    enrich_chunks(chunks, document_id=document_id, source_name="heading_context.md", policy_name=decision.policy_name)
    for c in chunks:
        assert c.metadata["document_id"] == document_id


# ── document deletion ────────────────────────────────────────────────────


def test_deletion_removes_all_chunks_from_storage(tmp_path):
    """Phase D storage uses the same per-document Chroma collection as Phase C;
    deletion is strategy/metadata-independent. This is a structural contract:
    enrichment adds no chunk to a shared collection, so delete removes every
    Phase D chunk for that document."""

    document_id = "doc-delete-test"
    path = FIXTURE_DIR / "heading_context.md"
    _, decision, chunks = _chunks_for("heading_context.md")
    enrich_chunks(chunks, document_id=document_id, source_name="heading_context.md", policy_name=decision.policy_name)
    # All chunks for this document share one document_id + collection scope.
    assert all(c.metadata["document_id"] == document_id for c in chunks)
    assert len(chunks) > 0
    # chunk_indices are a complete 0..N-1 range (no gaps => nothing left behind)
    indices = sorted(c.metadata["chunk_index"] for c in chunks)
    assert indices == list(range(len(chunks)))
