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
            ),
            Chunk(
                content="  The PRIMARY launch sequence code is 7-3-9-2-5.\r\n",
                score=0.98,
                chunk_id="a1",
                metadata={"document_id": "doc-a", "chunk_id": "a1"},
            ),
            Chunk(
                content="Management attributed the variance to inventory constraints.",
                score=0.96,
                chunk_id="u1",
                metadata={"document_id": "doc-a", "chunk_id": "u1"},
            ),
        ],
    }

    monkeypatch.setattr(knowledge_module, "EmbeddingRetriever", FakeRetriever)
    monkeypatch.setattr(engine, "_store", lambda document_id: document_id)

    result = await engine.retrieve(
        "What is the Phoenix code and management explanation?",
        [
            ("doc-a", "A.md"),
        ],
    )

    assert len(result) == 2
    assert result[0].source_id == "doc-a"
    assert result[0].provenance["duplicate_count"] == 2
    assert result[1].source_id == "doc-a"


@pytest.mark.asyncio
async def test_retrieve_keeps_identical_text_from_distinct_documents(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    monkeypatch.setattr(engine.settings, "rag_top_k", 2)
    duplicate = "Independent sources report the same threshold."
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
    }
    monkeypatch.setattr(knowledge_module, "EmbeddingRetriever", FakeRetriever)
    monkeypatch.setattr(engine, "_store", lambda document_id: document_id)

    result = await engine.retrieve("threshold", [("doc-a", "A.md"), ("doc-b", "B.md")])

    assert [(item.source_id, item.metadata["chunk_id"]) for item in result] == [
        ("doc-a", "a1"),
        ("doc-b", "b1"),
    ]


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


@pytest.mark.asyncio
async def test_retrieve_rejects_primary_with_mismatched_document_identity(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    monkeypatch.setattr(engine.settings, "rag_top_k", 5)
    FakeRetriever.chunks_by_store = {
        "doc-a": [
            Chunk(
                content="Content with mismatched document identity.",
                score=0.99,
                chunk_id="foreign-chunk",
                metadata={
                    "document_id": "doc-b",
                    "chunk_id": "foreign-chunk",
                },
            ),
            Chunk(
                content="Content missing document identity.",
                score=0.98,
                chunk_id="missing-document",
                metadata={"chunk_id": "missing-document"},
            ),
            Chunk(
                content="Content missing chunk identity.",
                score=0.97,
                chunk_id="missing-chunk",
                metadata={"document_id": "doc-a"},
            ),
        ]
    }
    monkeypatch.setattr(knowledge_module, "EmbeddingRetriever", FakeRetriever)
    monkeypatch.setattr(engine, "_store", lambda document_id: document_id)

    result = await engine.retrieve("contaminated", [("doc-a", "A.md")])

    assert result == []
