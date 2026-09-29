import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Document
from app.schemas import Evidence
from app.services.orchestrator import OpenJMOrchestrator
from app.services import orchestrator as orchestrator_module


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        yield db

    await engine.dispose()


@pytest.mark.asyncio
async def test_catalog_question_is_deterministic_knowledge(session):
    session.add(
        Document(
            id="doc-1",
            user_id="local-admin",
            original_name="leave-policy.pdf",
            stored_path="/tmp/leave-policy.pdf",
            mime_type="application/pdf",
            size_bytes=100,
            status="ready",
            indexed=True,
        )
    )
    await session.commit()

    plan = await OpenJMOrchestrator().plan(
        "What documents do you have loaded?",
        session,
        "local-admin",
    )

    assert plan.execution_class == "knowledge"
    assert plan.direct_answer is not None
    assert "leave-policy.pdf" in plan.direct_answer
    assert len(plan.evidence) == 1


@pytest.mark.asyncio
async def test_no_evidence_routes_to_general(session, monkeypatch):
    async def no_results(query, documents):
        return []

    monkeypatch.setattr(orchestrator_module.knowledge_engine, "retrieve", no_results)

    plan = await OpenJMOrchestrator().plan(
        "What is my name?",
        session,
        "local-admin",
    )

    assert plan.execution_class == "general"
    assert plan.evidence == []


@pytest.mark.asyncio
async def test_retrieved_evidence_routes_to_knowledge(session, monkeypatch):
    session.add(
        Document(
            id="doc-2",
            user_id="local-admin",
            original_name="operations.md",
            stored_path="/tmp/operations.md",
            mime_type="text/markdown",
            size_bytes=100,
            status="ready",
            indexed=True,
        )
    )
    await session.commit()

    async def evidence_results(query, documents):
        return [
            Evidence(
                source_id="doc-2",
                title="operations.md",
                passage="The loading bay opens at 7:30 AM.",
                score=0.91,
            )
        ]

    monkeypatch.setattr(
        orchestrator_module.knowledge_engine,
        "retrieve",
        evidence_results,
    )

    plan = await OpenJMOrchestrator().plan(
        "When does the loading bay open?",
        session,
        "local-admin",
    )

    assert plan.execution_class == "knowledge"
    assert plan.direct_answer is None
    assert plan.evidence[0].source_id == "doc-2"
