import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import DataSource, ExecutionTrace
from app.schemas import Evidence
from app.services.sql_policy import SQLPolicyDecision
from app.services.structured_executor import StructuredQueryResult
from app.services.tools import (
    StructuredQueryTool,
    ToolApprovalRequiredError,
    ToolContext,
    ToolNotFoundError,
    ToolPermissionError,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from app.services import tools as tools_module


class ReadTool:
    spec = ToolSpec(
        name="test.read",
        description="test",
        operation_class="READ",
        risk_level="LOW",
        requires_approval=False,
        required_permissions=frozenset({"test.read"}),
    )

    async def execute(self, context, payload):
        return ToolResult(
            evidence=[
                Evidence(
                    source_type="test",
                    source_id="source-1",
                    title="Test",
                    passage="grounded",
                )
            ]
        )


class ApprovalTool:
    spec = ToolSpec(
        name="test.action",
        description="test",
        operation_class="EXTERNAL_ACTION",
        risk_level="HIGH",
        requires_approval=True,
        required_permissions=frozenset({"test.action"}),
    )

    async def execute(self, context, payload):
        return ToolResult(output={"ok": True})


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        yield db

    await engine.dispose()


def test_tool_registry_rejects_unknown_tools():
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        registry.get("not.registered")


@pytest.mark.asyncio
async def test_tool_permissions_are_enforced():
    registry = ToolRegistry()
    registry.register(ReadTool())

    with pytest.raises(ToolPermissionError):
        await registry.execute(
            "test.read",
            ToolContext(user_id="local-admin"),
            {},
        )


@pytest.mark.asyncio
async def test_tool_approval_is_enforced():
    registry = ToolRegistry()
    registry.register(ApprovalTool())
    context = ToolContext(
        user_id="local-admin",
        permissions=frozenset({"test.action"}),
        approval_granted=False,
    )

    with pytest.raises(ToolApprovalRequiredError):
        await registry.execute("test.action", context, {})


@pytest.mark.asyncio
async def test_registered_tool_normalizes_evidence_id_and_trace(session):
    registry = ToolRegistry()
    registry.register(ReadTool())
    context = ToolContext(
        user_id="local-admin",
        permissions=frozenset({"test.read"}),
        route="knowledge",
        db=session,
    )

    result = await registry.execute(
        "test.read",
        context,
        {"query": "safe input", "password": "must-not-be-persisted"},
    )

    assert result.evidence[0].evidence_id is not None

    traces = (await session.execute(select(ExecutionTrace))).scalars().all()
    assert len(traces) == 1
    trace = traces[0]
    assert trace.tool_name == "test.read"
    assert trace.operation_class == "READ"
    assert trace.risk_level == "LOW"
    assert trace.status == "succeeded"
    assert len(trace.input_hash) == 64
    assert "must-not-be-persisted" not in (trace.metadata_json or "")
    assert json.loads(trace.evidence_ids_json or "[]") == [
        result.evidence[0].evidence_id
    ]


@pytest.mark.asyncio
async def test_structured_tool_returns_normalized_evidence_and_trace(
    session,
    monkeypatch,
):
    source = DataSource(
        id="structured-source",
        user_id="local-admin",
        name="Demo Source",
        engine="sqlite",
        connection_secret="encrypted-secret",
        status="connected",
        enabled=True,
        schema_json="[]",
        authorized_objects_json="[]",
    )
    session.add(source)
    await session.commit()

    async def fake_execute(_source, proposed_sql):
        assert _source.id == "structured-source"
        return StructuredQueryResult(
            source_id=_source.id,
            source_name=_source.name,
            sql="SELECT customer, revenue FROM sales LIMIT 200",
            columns=("customer", "revenue"),
            rows=(("Blue Mountain Cafe", 325.0),),
            row_count=1,
            truncated=False,
            policy=SQLPolicyDecision(
                sql="SELECT customer, revenue FROM sales LIMIT 200",
                tables=("sales",),
                row_limit=200,
                limit_added_or_capped=True,
            ),
        )

    monkeypatch.setattr(
        tools_module,
        "execute_structured_query",
        fake_execute,
    )

    registry = ToolRegistry()
    registry.register(StructuredQueryTool())
    context = ToolContext(
        user_id="local-admin",
        permissions=frozenset({"structured.read"}),
        route="structured",
        model_name="gemma-4-12b-local",
        db=session,
    )

    result = await registry.execute(
        "structured.query",
        context,
        {
            "source_id": "structured-source",
            "sql": "SELECT customer, revenue FROM sales",
            "grounded_parameter": {
                "name": "fy2025_annual_revenue_threshold",
                "value": 300.0,
                "type": "threshold",
                "operator": ">",
                "unit": None,
                "evidence_id": "policy-evidence-1",
                "source_id": "policy-document-1",
            },
        },
    )

    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.source_type == "structured_query"
    assert evidence.source_id == "structured-source"
    assert evidence.provenance["tool"] == "structured.query"
    assert evidence.provenance["grounded_parameter"]["evidence_id"] == (
        "policy-evidence-1"
    )
    assert evidence.metadata["sql"].endswith("LIMIT 200")
    assert evidence.processing_location == "local"

    trace = (
        await session.execute(
            select(ExecutionTrace).where(
                ExecutionTrace.tool_name == "structured.query"
            )
        )
    ).scalars().one()
    assert trace.status == "succeeded"
    assert trace.source_id == "structured-source"
    assert trace.route == "structured"
    assert trace.operation_class == "READ"
    assert trace.risk_level == "MODERATE"
    assert trace.executed_sql.endswith("LIMIT 200")
    assert trace.policy_decision == "read_only_allowed"
    assert trace.row_limit == 200
    assert trace.evidence_ids_json is not None
    assert json.loads(trace.metadata_json)["grounded_parameter"]["source_id"] == (
        "policy-document-1"
    )
    assert "encrypted-secret" not in (trace.metadata_json or "")
