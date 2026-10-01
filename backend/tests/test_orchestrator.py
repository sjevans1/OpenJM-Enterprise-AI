import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Document
from app.schemas import Evidence
from app.services.structured_planner import StructuredPlan, StructuredPlanningResult
from app.services.tools import ToolResult
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
    async def no_results(name, context, payload):
        return ToolResult(evidence=[])

    monkeypatch.setattr(orchestrator_module.tool_registry, "execute", no_results)

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

    captured = {}

    async def evidence_results(name, context, payload):
        captured["name"] = name
        captured["permissions"] = context.permissions
        captured["route"] = context.route
        captured["conversation_id"] = context.conversation_id
        captured["payload"] = payload
        return ToolResult(
            evidence=[
                Evidence(
                    source_id="doc-2",
                    title="operations.md",
                    passage="The loading bay opens at 7:30 AM.",
                    score=0.91,
                )
            ]
        )

    monkeypatch.setattr(
        orchestrator_module.tool_registry,
        "execute",
        evidence_results,
    )

    plan = await OpenJMOrchestrator().plan(
        "When does the loading bay open?",
        session,
        "local-admin",
        conversation_id="conversation-knowledge",
    )

    assert plan.execution_class == "knowledge"
    assert plan.direct_answer is None
    assert plan.evidence[0].source_id == "doc-2"
    assert captured == {
        "name": "knowledge.search",
        "permissions": frozenset({"knowledge.read"}),
        "route": "knowledge",
        "conversation_id": "conversation-knowledge",
        "payload": {"query": "When does the loading bay open?"},
    }



@pytest.mark.asyncio
async def test_structured_plan_routes_through_governed_tool(session, monkeypatch):
    async def fake_plan(message, db, user_id):
        return StructuredPlanningResult(
            candidate=True,
            plan=StructuredPlan(
                source_id="source-1",
                sql="SELECT 325.0 AS revenue",
                rationale="Use the authorized business source.",
            ),
        )

    captured = {}

    async def fake_execute(name, context, payload):
        captured["name"] = name
        captured["permissions"] = context.permissions
        captured["route"] = context.route
        captured["payload"] = payload
        return ToolResult(
            evidence=[
                Evidence(
                    evidence_id="evidence-1",
                    source_type="structured_query",
                    source_id="source-1",
                    title="Demo Source",
                    passage='{"columns":["revenue"],"rows":[[325.0]],"row_count":1}',
                    metadata={"sql": "SELECT 325.0 AS revenue LIMIT 200"},
                )
            ]
        )

    monkeypatch.setattr(
        orchestrator_module.structured_planner,
        "plan",
        fake_plan,
    )
    monkeypatch.setattr(
        orchestrator_module.tool_registry,
        "execute",
        fake_execute,
    )

    plan = await OpenJMOrchestrator().plan(
        "What is the total revenue for Blue Mountain Cafe?",
        session,
        "local-admin",
        conversation_id="conversation-1",
    )

    assert plan.execution_class == "structured"
    assert plan.direct_answer is None
    assert plan.evidence[0].source_type == "structured_query"
    assert captured["name"] == "structured.query"
    assert "structured.read" in captured["permissions"]
    assert captured["route"] == "structured"
    assert captured["payload"]["source_id"] == "source-1"


@pytest.mark.asyncio
async def test_structured_planner_failure_returns_safe_no_execution_answer(
    session,
    monkeypatch,
):
    async def fail_plan(message, db, user_id):
        raise orchestrator_module.StructuredPlannerError("bad model output")

    monkeypatch.setattr(
        orchestrator_module.structured_planner,
        "plan",
        fail_plan,
    )

    plan = await OpenJMOrchestrator().plan(
        "Show total customer revenue",
        session,
        "local-admin",
    )

    assert plan.execution_class == "structured"
    assert plan.evidence == []
    assert plan.direct_answer is not None
    assert "No database query was executed" in plan.direct_answer



@pytest.mark.asyncio
async def test_structured_schema_decline_fails_closed(session, monkeypatch):
    async def decline_plan(message, db, user_id):
        return StructuredPlanningResult(
            candidate=True,
            plan=None,
            rationale="The authorized schema has no payroll table.",
        )

    monkeypatch.setattr(
        orchestrator_module.structured_planner,
        "plan",
        decline_plan,
    )

    plan = await OpenJMOrchestrator().plan(
        "What is the total payroll bonus this month?",
        session,
        "local-admin",
    )

    assert plan.execution_class == "structured"
    assert plan.evidence == []
    assert plan.direct_answer is not None
    assert "No database query was executed" in plan.direct_answer
    assert "no database value was fabricated" in plan.direct_answer
