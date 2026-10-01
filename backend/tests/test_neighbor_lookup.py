import pytest

from app.services.knowledge import DBGPTKnowledgeEngine


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows
        self.requests = []

    def get(self, ids, include):
        self.requests.append((list(ids), list(include)))
        # Deliberately return reverse order to prove OpenJM restores request order.
        selected = [row for row in self.rows if row["id"] in ids]
        selected.reverse()
        return {
            "ids": [row["id"] for row in selected],
            "documents": [row["content"] for row in selected],
            "metadatas": [row.get("metadata", {}) for row in selected],
        }


class FakeStore:
    def __init__(self, collection):
        self._collection = collection


@pytest.mark.asyncio
async def test_exact_chunk_lookup_restores_requested_order(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    collection = FakeCollection(
        [
            {
                "id": "c1",
                "content": "first",
                "metadata": {"document_id": "doc-a", "chunk_id": "c1", "chunk_index": 0},
            },
            {
                "id": "c2",
                "content": "second",
                "metadata": {"document_id": "doc-a", "chunk_id": "c2", "chunk_index": 1},
            },
        ]
    )

    monkeypatch.setattr(engine, "_chroma_collection_exists", lambda _name: True)
    monkeypatch.setattr(engine, "_store", lambda _document_id: FakeStore(collection))

    chunks = await engine.get_chunks_by_ids("doc-a", ["c1", "c2"])

    assert [chunk.chunk_id for chunk in chunks] == ["c1", "c2"]
    assert [chunk.content for chunk in chunks] == ["first", "second"]
    assert collection.requests == [(["c1", "c2"], ["documents", "metadatas"])]


@pytest.mark.asyncio
async def test_exact_chunk_lookup_deduplicates_requested_ids(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    collection = FakeCollection(
        [
            {
                "id": "c1",
                "content": "first",
                "metadata": {"document_id": "doc-a", "chunk_id": "c1"},
            }
        ]
    )
    monkeypatch.setattr(engine, "_chroma_collection_exists", lambda _name: True)
    monkeypatch.setattr(engine, "_store", lambda _document_id: FakeStore(collection))

    chunks = await engine.get_chunks_by_ids("doc-a", ["c1", "c1", "", "c1"])

    assert [chunk.chunk_id for chunk in chunks] == ["c1"]
    assert collection.requests[0][0] == ["c1"]


@pytest.mark.asyncio
async def test_exact_chunk_lookup_rejects_document_metadata_mismatch(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    collection = FakeCollection(
        [
            {
                "id": "good",
                "content": "allowed",
                "metadata": {"document_id": "doc-a", "chunk_id": "good"},
            },
            {
                "id": "bad",
                "content": "wrong document",
                "metadata": {"document_id": "doc-b", "chunk_id": "bad"},
            },
        ]
    )
    monkeypatch.setattr(engine, "_chroma_collection_exists", lambda _name: True)
    monkeypatch.setattr(engine, "_store", lambda _document_id: FakeStore(collection))

    chunks = await engine.get_chunks_by_ids("doc-a", ["good", "bad"])

    assert [chunk.chunk_id for chunk in chunks] == ["good"]


@pytest.mark.asyncio
async def test_exact_chunk_lookup_rejects_chunk_without_document_identity(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    collection = FakeCollection(
        [{"id": "legacy", "content": "legacy", "metadata": {"source": "legacy.md"}}]
    )
    monkeypatch.setattr(engine, "_chroma_collection_exists", lambda _name: True)
    monkeypatch.setattr(engine, "_store", lambda _document_id: FakeStore(collection))

    chunks = await engine.get_chunks_by_ids("doc-a", ["legacy"])

    assert chunks == []


@pytest.mark.asyncio
async def test_exact_chunk_lookup_rejects_chunk_without_chunk_identity(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    collection = FakeCollection(
        [
            {
                "id": "legacy",
                "content": "legacy",
                "metadata": {"document_id": "doc-a"},
            }
        ]
    )
    monkeypatch.setattr(engine, "_chroma_collection_exists", lambda _name: True)
    monkeypatch.setattr(engine, "_store", lambda _document_id: FakeStore(collection))

    chunks = await engine.get_chunks_by_ids("doc-a", ["legacy"])

    assert chunks == []


@pytest.mark.asyncio
async def test_exact_chunk_lookup_missing_collection_returns_empty(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    monkeypatch.setattr(engine, "_chroma_collection_exists", lambda _name: False)

    called = []
    monkeypatch.setattr(engine, "_store", lambda _document_id: called.append(True))

    assert await engine.get_chunks_by_ids("doc-a", ["c1"]) == []
    assert called == []
