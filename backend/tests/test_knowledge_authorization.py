import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Document
from app.schemas import Evidence
from app.services import tools as tools_module
from app.services.tools import KnowledgeSearchTool, ToolContext, ToolRegistry
from app.services.tools import ToolPermissionError


@pytest.mark.asyncio
async def test_knowledge_search_resolves_only_documents_owned_by_context_user(
    monkeypatch,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all(
            [
                Document(
                    id="doc-a",
                    user_id="user-a",
                    original_name="a.md",
                    stored_path="/tmp/a.md",
                    mime_type="text/markdown",
                    size_bytes=10,
                    status="ready",
                    indexed=True,
                ),
                Document(
                    id="doc-b",
                    user_id="user-b",
                    original_name="b.md",
                    stored_path="/tmp/b.md",
                    mime_type="text/markdown",
                    size_bytes=10,
                    status="ready",
                    indexed=True,
                ),
            ]
        )
        await db.commit()

        seen_refs = []
        seen_kwargs = {}

        async def fake_retrieve(query, refs, **kwargs):
            del query
            seen_refs.extend(refs)
            seen_kwargs.update(kwargs)
            return [
                Evidence(
                    source_type="document",
                    source_id=document_id,
                    title=title,
                    passage="authorized primary evidence",
                    provenance={"retrieval_role": "primary", "duplicate_count": 2},
                )
                for document_id, title in refs
            ] + [
                Evidence(
                    source_type="document",
                    source_id=document_id,
                    title=title,
                    passage="authorized neighbor evidence",
                    provenance={
                        "retrieval_role": "neighbor",
                        "lookup_mode": "exact_chunk_id",
                    },
                )
                for document_id, title in refs
            ]

        monkeypatch.setattr(
            tools_module.knowledge_engine,
            "retrieve",
            fake_retrieve,
        )

        registry = ToolRegistry()
        registry.register(KnowledgeSearchTool())
        result = await registry.execute(
            "knowledge.search",
            ToolContext(
                user_id="user-a",
                permissions=frozenset({"knowledge.read"}),
                db=db,
            ),
            {"query": "show my authorized evidence"},
        )

        assert seen_refs == [("doc-a", "a.md")]
        assert seen_kwargs == {
            "neighbor_primary_limit": tools_module.knowledge_engine.settings.rag_neighbor_primary_limit,
            "neighbor_max_chunks": tools_module.knowledge_engine.settings.rag_neighbor_max_chunks,
        }
        assert [item.source_id for item in result.evidence] == ["doc-a", "doc-a"]
        assert all(item.source_id != "doc-b" for item in result.evidence)
        assert [item.provenance["retrieval_mode"] for item in result.evidence] == [
            "semantic",
            "exact_chunk_id",
        ]
        assert result.output == {
            "evidence_count": 2,
            "primary_evidence_count": 1,
            "neighbor_evidence_count": 1,
            "deduplicated_count": 1,
            "evidence_limit": (
                tools_module.knowledge_engine.settings.rag_top_k
                + tools_module.knowledge_engine.settings.rag_neighbor_max_chunks
            ),
        }

    await engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_search_rejects_evidence_outside_authorized_set(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        db.add(
            Document(
                id="doc-a",
                user_id="user-a",
                original_name="a.md",
                stored_path="/tmp/a.md",
                mime_type="text/markdown",
                size_bytes=10,
                status="ready",
                indexed=True,
            )
        )
        await db.commit()

        async def contaminated_retrieve(_query, _refs, **_kwargs):
            return [
                Evidence(
                    source_type="document",
                    source_id="doc-b",
                    title="b.md",
                    passage="unauthorized evidence",
                    provenance={"retrieval_role": "primary"},
                )
            ]

        monkeypatch.setattr(
            tools_module.knowledge_engine,
            "retrieve",
            contaminated_retrieve,
        )
        registry = ToolRegistry()
        registry.register(KnowledgeSearchTool())

        with pytest.raises(ToolPermissionError):
            await registry.execute(
                "knowledge.search",
                ToolContext(
                    user_id="user-a",
                    permissions=frozenset({"knowledge.read"}),
                    db=db,
                ),
                {"query": "show authorized evidence"},
            )

    await engine.dispose()
