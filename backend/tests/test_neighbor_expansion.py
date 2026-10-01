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
async def test_opt_in_neighbor_expansion_adds_adjacent_context(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    monkeypatch.setattr(engine.settings, "rag_top_k", 1)

    FakeRetriever.chunks_by_store = {
        "doc-a": [
            Chunk(
                content="Project CEDAR escalation threshold.",
                score=0.95,
                chunk_id="c2",
                metadata={
                    "document_id": "doc-a",
                    "chunk_id": "c2",
                    "chunk_index": 1,
                    "next_chunk_id": "c3",
                },
            )
        ]
    }

    async def fake_get_chunks_by_ids(document_id, chunk_ids):
        assert document_id == "doc-a"
        assert chunk_ids == ["c3"]
        return [
            Chunk(
                content="The controlling value is 47 minutes.",
                chunk_id="c3",
                metadata={
                    "document_id": "doc-a",
                    "chunk_id": "c3",
                    "chunk_index": 2,
                    "previous_chunk_id": "c2",
                },
            )
        ]

    monkeypatch.setattr(knowledge_module, "EmbeddingRetriever", FakeRetriever)
    monkeypatch.setattr(engine, "_store", lambda document_id: document_id)
    monkeypatch.setattr(engine, "get_chunks_by_ids", fake_get_chunks_by_ids)

    result = await engine.retrieve(
        "What is the Project CEDAR escalation threshold?",
        [("doc-a", "cedar.md")],
        neighbor_primary_limit=1,
        neighbor_max_chunks=1,
    )

    assert [item.passage for item in result] == [
        "Project CEDAR escalation threshold.",
        "The controlling value is 47 minutes.",
    ]
    assert result[0].provenance["retrieval_role"] == "primary"
    assert result[1].provenance["retrieval_role"] == "neighbor"
    assert result[1].provenance["expanded_from_chunk_id"] == "c2"
    assert result[1].provenance["direction"] == "next"
    assert result[1].score is None


@pytest.mark.asyncio
async def test_neighbor_expansion_does_not_repeat_primary_content(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    monkeypatch.setattr(engine.settings, "rag_top_k", 1)
    content_value = "Project CEDAR escalation threshold."

    FakeRetriever.chunks_by_store = {
        "doc-a": [
            Chunk(
                content=content_value,
                score=0.95,
                chunk_id="c2",
                metadata={
                    "document_id": "doc-a",
                    "chunk_id": "c2",
                    "next_chunk_id": "c3",
                },
            )
        ]
    }

    async def fake_get_chunks_by_ids(_document_id, _chunk_ids):
        return [
            Chunk(
                content=content_value,
                chunk_id="c3",
                metadata={"document_id": "doc-a", "chunk_id": "c3"},
            )
        ]

    monkeypatch.setattr(knowledge_module, "EmbeddingRetriever", FakeRetriever)
    monkeypatch.setattr(engine, "_store", lambda document_id: document_id)
    monkeypatch.setattr(engine, "get_chunks_by_ids", fake_get_chunks_by_ids)

    result = await engine.retrieve(
        "CEDAR",
        [("doc-a", "cedar.md")],
        neighbor_primary_limit=1,
        neighbor_max_chunks=1,
    )

    assert len(result) == 1
    assert result[0].provenance["retrieval_role"] == "primary"
