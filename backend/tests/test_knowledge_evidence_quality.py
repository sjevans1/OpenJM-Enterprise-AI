import pytest

from dbgpt.core import Chunk

from app.services.knowledge import DBGPTKnowledgeEngine
from app.services import knowledge as knowledge_module


class FakeRetriever:
    chunks_by_store = {}

    def __init__(self, top_k, index_store):
        self.top_k = top_k
        self.index_store = index_store

    async def aretrieve_with_scores(self, query, score_threshold):
        del query, score_threshold
        return list(self.chunks_by_store.get(self.index_store, []))


@pytest.mark.asyncio
async def test_retrieve_deduplicates_before_global_top_k_and_preserves_sources(
    monkeypatch,
):
    engine = DBGPTKnowledgeEngine()
    monkeypatch.setattr(engine.settings, "rag_top_k", 2)

    duplicate = "The PRIMARY launch sequence code is 7-3-9-2-5."
    FakeRetriever.chunks_by_store = {
        "doc-a": [
            Chunk(
                content=duplicate,
                score=0.99,
                chunk_id="a1",
                metadata={"document_id": "doc-a", "chunk_id": "a1"},
            )
        ],
        "doc-b": [
            Chunk(
                content=duplicate,
                score=0.98,
                chunk_id="b1",
                metadata={"document_id": "doc-b", "chunk_id": "b1"},
            )
        ],
        "doc-c": [
            Chunk(
                content=duplicate,
                score=0.97,
                chunk_id="c1",
                metadata={"document_id": "doc-c", "chunk_id": "c1"},
            )
        ],
        "doc-unique": [
            Chunk(
                content="Management attributed the variance to inventory constraints.",
                score=0.96,
                chunk_id="u1",
                metadata={"document_id": "doc-unique", "chunk_id": "u1"},
            )
        ],
    }

    monkeypatch.setattr(knowledge_module, "EmbeddingRetriever", FakeRetriever)
    monkeypatch.setattr(engine, "_store", lambda document_id: document_id)

    result = await engine.retrieve(
        "What is the Phoenix code and management explanation?",
        [
            ("doc-a", "A.md"),
            ("doc-b", "B.md"),
            ("doc-c", "C.md"),
            ("doc-unique", "Management.md"),
        ],
    )

    assert len(result) == 2
    assert result[0].source_id == "doc-a"
    assert result[0].provenance["duplicate_count"] == 3
    assert {
        item["source_id"]
        for item in result[0].provenance["equivalent_sources"]
    } == {"doc-b", "doc-c"}
    assert result[1].source_id == "doc-unique"


@pytest.mark.asyncio
async def test_retrieve_does_not_collapse_materially_different_passages(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    monkeypatch.setattr(engine.settings, "rag_top_k", 5)

    FakeRetriever.chunks_by_store = {
        "doc-a": [
            Chunk(
                content="Projected ROI is 18.5%.",
                score=0.91,
                chunk_id="a1",
                metadata={"document_id": "doc-a", "chunk_id": "a1"},
            )
        ],
        "doc-b": [
            Chunk(
                content="Projected ROI is 19.5%.",
                score=0.90,
                chunk_id="b1",
                metadata={"document_id": "doc-b", "chunk_id": "b1"},
            )
        ],
    }

    monkeypatch.setattr(knowledge_module, "EmbeddingRetriever", FakeRetriever)
    monkeypatch.setattr(engine, "_store", lambda document_id: document_id)

    result = await engine.retrieve(
        "What is projected ROI?",
        [("doc-a", "A.md"), ("doc-b", "B.md")],
    )

    assert len(result) == 2
    assert {item.passage for item in result} == {
        "Projected ROI is 18.5%.",
        "Projected ROI is 19.5%.",
    }
    assert all(item.provenance["duplicate_count"] == 1 for item in result)
