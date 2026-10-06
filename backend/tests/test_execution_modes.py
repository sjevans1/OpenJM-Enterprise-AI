"""VS3-A3: Explicit execution-mode contract tests.

These tests prove that the user-selected mode is authoritative and that no
automatic source inference occurs. Each mode routes directly into the
corresponding governed execution path with no silent fallbacks.
"""

import pytest

from app.services.orchestrator import MODE_TO_EXECUTION_CLASS, OpenJMOrchestrator
from app.services.structured_planner import StructuredPlan, StructuredPlanningResult
from app.services.tools import ToolResult
from app.services import orchestrator as orchestrator_module
from app.schemas import Evidence


# --------------------------------------------------------------------------- #
# Section 7 — Backward compatibility: omitted mode defaults to Chat          #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_omitted_mode_defaults_to_chat(session, monkeypatch):
    """An API client that does not send a mode must never query enterprise sources."""
    def reject_tools(name, context, payload):
        raise AssertionError(f"Default chat mode must not invoke {name}")

    monkeypatch.setattr(
        orchestrator_module.tool_registry, "execute", reject_tools
    )

    plan = await OpenJMOrchestrator().plan(
        "Hello, how are you?",
        session,
        "local-admin",
    )

    assert plan.requested_mode == "chat"
    assert plan.execution_class == "general"
    assert plan.evidence == []


# --------------------------------------------------------------------------- #
# Section 13 — Chat mode: GENERAL, no enterprise tools                       #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_chat_mode_with_enterprise_sources_available(session, monkeypatch):
    """Even with indexed documents and connected databases, Chat must not query them."""
    tool_call_log = []

    async def fake_execute(name, context, payload):
        tool_call_log.append(name)
        return ToolResult(evidence=[])

    monkeypatch.setattr(
        orchestrator_module.tool_registry, "execute", fake_execute
    )

    plan = await OpenJMOrchestrator().plan(
        "Explain retrieval augmented generation.",
        session,
        "local-admin",
        mode="chat",
    )

    assert plan.execution_class == "general"
    assert plan.requested_mode == "chat"
    assert tool_call_log == [], (
        f"Chat mode invoked tools: {tool_call_log}"
    )


@pytest.mark.asyncio
async def test_chat_mode_revenue_words_do_not_trigger_structured(session, monkeypatch):
    """Revenue/metric words must not cause Structured execution under Chat mode."""
    tool_call_log = []

    async def fake_execute(name, context, payload):
        tool_call_log.append(name)
        return ToolResult(evidence=[])

    monkeypatch.setattr(
        orchestrator_module.tool_registry, "execute", fake_execute
    )

    plan = await OpenJMOrchestrator().plan(
        "What is the total revenue for Blue Mountain Cafe?",
        session,
        "local-admin",
        mode="chat",
    )

    assert plan.execution_class == "general"
    assert tool_call_log == []


# --------------------------------------------------------------------------- #
# Section 11 — Knowledge mode: KNOWLEDGE, no Structured fallback            #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_knowledge_mode_phoenix_question(session, monkeypatch):
    """A Phoenix question routes KNOWLEDGE when mode=knowledge."""
    captured = {}

    async def fake_execute(name, context, payload):
        captured["name"] = name
        captured["requested_mode"] = context.requested_mode
        captured["payload"] = payload
        return ToolResult(evidence=[])

    monkeypatch.setattr(
        orchestrator_module.tool_registry, "execute", fake_execute
    )

    plan = await OpenJMOrchestrator().plan(
        "What is the PRIMARY launch sequence code for Project Phoenix?",
        session,
        "local-admin",
        mode="knowledge",
    )

    assert plan.execution_class == "knowledge"
    assert plan.requested_mode == "knowledge"
    assert captured["name"] == "knowledge.search"
    assert captured["requested_mode"] == "knowledge"


@pytest.mark.asyncio
async def test_knowledge_mode_historical_revenue_stays_knowledge(session, monkeypatch):
    """A historical revenue question against 'Quarterly Revenue Report' must stay KNOWLEDGE."""
    tool_call_log = []

    async def fake_execute(name, context, payload):
        tool_call_log.append(name)
        return ToolResult(evidence=[])

    monkeypatch.setattr(
        orchestrator_module.tool_registry, "execute", fake_execute
    )

    plan = await OpenJMOrchestrator().plan(
        "What was the total revenue in the Quarterly Revenue Report?",
        session,
        "local-admin",
        mode="knowledge",
    )

    assert plan.execution_class == "knowledge"
    assert tool_call_log == ["knowledge.search"]
    assert "structured.query" not in tool_call_log


@pytest.mark.asyncio
async def test_knowledge_mode_revenue_words_cannot_cause_structured(session, monkeypatch):
    """Words like revenue, actual, current, orders must not cause Structured execution."""
    tool_call_log = []

    async def fake_execute(name, context, payload):
        tool_call_log.append(name)
        return ToolResult(evidence=[])

    monkeypatch.setattr(
        orchestrator_module.tool_registry, "execute", fake_execute
    )

    for question in [
        "What is the actual revenue for Q3?",
        "What are the current orders for Blue Mountain Cafe?",
        "How many customers bought the product?",
        "What is the projected revenue for next quarter?",
    ]:
        tool_call_log.clear()
        plan = await OpenJMOrchestrator().plan(
            question,
            session,
            "local-admin",
            mode="knowledge",
        )
        assert plan.execution_class == "knowledge", (
            f"Knowledge mode for '{question}' unexpectedly routed to {plan.execution_class}"
        )
        assert tool_call_log == ["knowledge.search"], (
            f"Knowledge mode for '{question}' invoked: {tool_call_log}"
        )


@pytest.mark.asyncio
async def test_knowledge_mode_no_documents_still_knowledge(session, monkeypatch):
    """If documents cannot support the answer, Knowledge says so—no Structured fallback."""
    captured = {}

    async def fake_execute(name, context, payload):
        captured["name"] = name
        return ToolResult(evidence=[])

    monkeypatch.setattr(
        orchestrator_module.tool_registry, "execute", fake_execute
    )

    plan = await OpenJMOrchestrator().plan(
        "What does the employee handbook say about overtime?",
        session,
        "local-admin",
        mode="knowledge",
    )

    assert plan.execution_class == "knowledge"
    assert captured["name"] == "knowledge.search"
    assert plan.execution_class != "structured"


# --------------------------------------------------------------------------- #
# Section 12 — Data mode: STRUCTURED, no Knowledge fallback                    #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_data_mode_routes_structured(session, monkeypatch):
    """mode=data routes STRUCTURED."""
    captured = {}

    async def fake_plan(message, db, user_id, **kwargs):
        return StructuredPlanningResult(
            candidate=True,
            plan=StructuredPlan(
                source_id="source-1",
                sql="SELECT SUM(revenue) FROM sales WHERE cafe = 'Blue Mountain Cafe'",
                rationale="Use the authorized business source.",
            ),
        )

    async def fake_execute(name, context, payload):
        captured["name"] = name
        captured["requested_mode"] = context.requested_mode
        captured["payload"] = payload
        return ToolResult(
            evidence=[
                Evidence(
                    evidence_id="evidence-1",
                    source_type="structured_query",
                    source_id="source-1",
                    title="Demo Source",
                    passage='{"columns":["total_revenue"],"rows":[[325.0]],"row_count":1}',
                    metadata={"sql": "SELECT SUM(revenue) FROM sales WHERE cafe = 'Blue Mountain Cafe'"},
                )
            ]
        )

    monkeypatch.setattr(orchestrator_module.structured_planner, "plan", fake_plan)
    monkeypatch.setattr(
        orchestrator_module.tool_registry, "execute", fake_execute
    )

    plan = await OpenJMOrchestrator().plan(
        "What is Blue Mountain Cafe's total revenue?",
        session,
        "local-admin",
        mode="data",
    )

    assert plan.execution_class == "structured"
    assert plan.requested_mode == "data"
    assert captured["name"] == "structured.query"
    assert captured["requested_mode"] == "data"
    assert captured["payload"]["source_id"] == "source-1"


@pytest.mark.asyncio
async def test_data_mode_with_sales_data_still_structured(session, monkeypatch):
    """'According to the sales data, what is Blue Mountain Cafe's total revenue?' remains STRUCTURED."""
    captured = {}

    async def fake_plan(message, db, user_id, **kwargs):
        return StructuredPlanningResult(
            candidate=True,
            plan=StructuredPlan(
                source_id="source-1",
                sql="SELECT SUM(revenue) FROM sales WHERE cafe = 'Blue Mountain Cafe'",
                rationale="Use the authorized business source.",
            ),
        )

    async def fake_execute(name, context, payload):
        captured["name"] = name
        captured["requested_mode"] = context.requested_mode
        return ToolResult(
            evidence=[
                Evidence(
                    evidence_id="evidence-1",
                    source_type="structured_query",
                    source_id="source-1",
                    title="Demo Source",
                    passage='{"columns":["total_revenue"],"rows":[[325.0]],"row_count":1}',
                    metadata={"sql": "SELECT SUM(revenue) FROM sales WHERE cafe = 'Blue Mountain Cafe'"},
                )
            ]
        )

    monkeypatch.setattr(orchestrator_module.structured_planner, "plan", fake_plan)
    monkeypatch.setattr(
        orchestrator_module.tool_registry, "execute", fake_execute
    )

    plan = await OpenJMOrchestrator().plan(
        "According to the sales data, what is Blue Mountain Cafe's total revenue?",
        session,
        "local-admin",
        mode="data",
    )

    assert plan.execution_class == "structured"
    assert captured["name"] == "structured.query"
    assert plan.evidence and plan.evidence[0].source_type != "document"


@pytest.mark.asyncio
async def test_data_mode_fail_closed_when_no_schema_support(session, monkeypatch):
    """Data mode fails closed when the authorized schema cannot support the answer."""
    async def decline_plan(message, db, user_id, **kwargs):
        return StructuredPlanningResult(
            candidate=True,
            plan=None,
            rationale="The authorized schema has no payroll table.",
        )

    monkeypatch.setattr(orchestrator_module.structured_planner, "plan", decline_plan)

    plan = await OpenJMOrchestrator().plan(
        "What is the average happiness index of employees?",
        session,
        "local-admin",
        mode="data",
    )

    assert plan.execution_class == "structured"
    assert plan.evidence == []
    assert plan.direct_answer is not None
    assert "No database query was executed" in plan.direct_answer


# --------------------------------------------------------------------------- #
# Section 9 — Execution trace records both requested_mode and execution_class #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_chat_mode_records_requested_mode_in_plan(session, monkeypatch):
    """The ExecutionPlan preserves requested_mode alongside execution_class."""
    def reject_tools(name, context, payload):
        raise AssertionError(f"Chat mode must not invoke {name}")

    monkeypatch.setattr(
        orchestrator_module.tool_registry, "execute", reject_tools
    )

    plan = await OpenJMOrchestrator().plan(
        "Hello.",
        session,
        "local-admin",
        mode="chat",
    )

    assert plan.requested_mode == "chat"
    assert plan.execution_class == "general"


# --------------------------------------------------------------------------- #
# Mode mapping table                                                          #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "mode,expected_class",
    [
        ("chat", "general"),
        ("knowledge", "knowledge"),
        ("data", "structured"),
        ("hybrid", "hybrid"),
    ],
)
def test_mode_to_execution_class_mapping(mode, expected_class):
    """The internal mode-to-execution-class mapping must be stable."""
    assert MODE_TO_EXECUTION_CLASS[mode] == expected_class
