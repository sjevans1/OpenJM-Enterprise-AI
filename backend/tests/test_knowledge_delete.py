import pytest

from app.services.knowledge import DBGPTKnowledgeEngine


class FakeStore:
    def __init__(self):
        self.deleted_names = []

    def delete_vector_name(self, name: str):
        self.deleted_names.append(name)
        return True


@pytest.mark.asyncio
async def test_delete_uses_chroma_fallback_when_dbgpt_leaves_collection(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    fake_store = FakeStore()
    document_id = "12345678-1234-1234-1234-123456789abc"
    collection_name = engine._collection_name(document_id)

    monkeypatch.setattr(engine, "_store", lambda _document_id: fake_store)

    checks = iter([True, False])
    monkeypatch.setattr(
        engine,
        "_chroma_collection_exists",
        lambda _collection_name: next(checks),
    )

    forced = []
    monkeypatch.setattr(
        engine,
        "_force_delete_chroma_collection",
        lambda name: forced.append(name),
    )

    await engine.delete(document_id)

    assert fake_store.deleted_names == [collection_name]
    assert forced == [collection_name]


@pytest.mark.asyncio
async def test_delete_skips_fallback_when_dbgpt_removed_collection(monkeypatch):
    engine = DBGPTKnowledgeEngine()
    fake_store = FakeStore()
    document_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    collection_name = engine._collection_name(document_id)

    monkeypatch.setattr(engine, "_store", lambda _document_id: fake_store)
    monkeypatch.setattr(
        engine,
        "_chroma_collection_exists",
        lambda _collection_name: False,
    )

    forced = []
    monkeypatch.setattr(
        engine,
        "_force_delete_chroma_collection",
        lambda name: forced.append(name),
    )

    await engine.delete(document_id)

    assert fake_store.deleted_names == [collection_name]
    assert forced == []
