import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Document
from app.schemas import Evidence
from app.services import tools as tools_module
from app.services.tools import KnowledgeSearchTool, ToolContext


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

        async def fake_retrieve(query, refs, **kwargs):
            del query, kwargs
            seen_refs.extend(refs)
            return [
                Evidence(
                    source_type="document",
                    source_id=document_id,
                    title=title,
                    passage="authorized evidence",
                )
                for document_id, title in refs
            ]

        monkeypatch.setattr(
            tools_module.knowledge_engine,
            "retrieve",
            fake_retrieve,
        )

        result = await KnowledgeSearchTool().execute(
            ToolContext(
                user_id="user-a",
                permissions=frozenset({"knowledge.read"}),
                db=db,
            ),
            {"query": "show my authorized evidence"},
        )

        assert seen_refs == [("doc-a", "a.md")]
        assert [item.source_id for item in result.evidence] == ["doc-a"]
        assert all(item.source_id != "doc-b" for item in result.evidence)

    await engine.dispose()
