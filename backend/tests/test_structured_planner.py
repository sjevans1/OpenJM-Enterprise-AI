import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import DataSource
from app.schemas import DataColumnSchema, DataTableSchema
from app.services.data_sources import encode_schema
from app.services.structured_planner import (
    StructuredPlan,
    StructuredPlanner,
    StructuredPlannerError,
)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT name FROM customers WHERE fy2025_annual_revenue > 300",
            True,
        ),
        (
            "SELECT name FROM customers WHERE fy2025_annual_revenue > 500",
            False,
        ),
        ("SELECT name FROM customers", False),
        ("SELECT name FROM customers WHERE score > 300", False),
        (
            "SELECT name, fy2025_annual_revenue > 300 AS policy_match "
            "FROM customers",
            False,
        ),
        (
            "SELECT name FROM customers "
            "ORDER BY fy2025_annual_revenue > 300",
            False,
        ),
        (
            "SELECT name FROM customers "
            "WHERE fy2025_annual_revenue > 300 OR 1 = 1",
            False,
        ),
        (
            "SELECT name FROM customers WHERE fy2025_annual_revenue > 300 "
            "AND fy2025_annual_revenue > 500",
            False,
        ),
        (
            "SELECT name FROM customers WHERE NOT (fy2025_annual_revenue <= 300)",
            False,
        ),
        (
            "SELECT name FROM customers WHERE fy2025_annual_revenue BETWEEN 301 AND 500",
            False,
        ),
        (
            "SELECT name FROM customers WHERE CAST(fy2025_annual_revenue AS INT) > 300",
            False,
        ),
    ],
)
def test_grounded_parameter_sql_validation(sql, expected):
    plan = StructuredPlan(source_id="source-1", sql=sql, rationale="test")
    parameter = {
        "name": "fy2025_annual_revenue_threshold",
        "value": "300",
        "type": "threshold",
        "operator": ">",
        "unit": None,
        "field": "revenue",
        "period": "annual",
        "fiscal_year": 2025,
        "currency": "USD",
        "citation": "[DOC 1]",
        "evidence_id": "evidence-1",
        "source_id": "document-1",
    }

    assert StructuredPlanner.validates_grounded_parameter(plan, parameter) is expected


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        yield db

    await engine.dispose()


def demo_schema():
    return [
        DataTableSchema(
            schema_name="main",
            name="customers",
            qualified_name="customers",
            columns=[
                DataColumnSchema(
                    name="id",
                    type="INTEGER",
                    nullable=False,
                    primary_key=True,
                ),
                DataColumnSchema(
                    name="name",
                    type="TEXT",
                    nullable=False,
                ),
            ],
            primary_key=["id"],
        ),
        DataTableSchema(
            schema_name="main",
            name="order_items",
            qualified_name="order_items",
            columns=[
                DataColumnSchema(
                    name="order_id",
                    type="INTEGER",
                    nullable=False,
                ),
                DataColumnSchema(
                    name="quantity",
                    type="INTEGER",
                    nullable=False,
                ),
                DataColumnSchema(
                    name="unit_price",
                    type="REAL",
                    nullable=False,
                ),
            ],
        ),
    ]


async def add_source(session, source_id="source-1"):
    source = DataSource(
        id=source_id,
        user_id="local-admin",
        name="Demo Business",
        engine="sqlite",
        connection_secret="encrypted",
        status="connected",
        enabled=True,
        schema_json=encode_schema(demo_schema()),
        authorized_objects_json=json.dumps(["customers", "order_items"]),
    )
    session.add(source)
    await session.commit()
    return source


def test_structured_candidate_uses_business_cues():
    planner = StructuredPlanner()
    source = DataSource(
        id="source-1",
        user_id="local-admin",
        name="Demo",
        engine="sqlite",
        connection_secret="encrypted",
        status="connected",
        enabled=True,
        schema_json=encode_schema(demo_schema()),
    )

    assert planner.is_candidate(
        "What is the total revenue for Blue Mountain Cafe?",
        [source],
    )
    assert not planner.is_candidate("What is revenue?", [source])
    assert planner.looks_structured("What is the total payroll bonus this month?")


@pytest.mark.asyncio
async def test_planner_returns_authorized_structured_plan(session, monkeypatch):
    await add_source(session)
    planner = StructuredPlanner()

    async def fake_chat(messages, **kwargs):
        assert "AUTHORIZED SCHEMAS" in messages[0]["content"]
        assert kwargs["temperature"] == 0.0
        return json.dumps(
            {
                "use_structured": True,
                "source_id": "source-1",
                "sql": (
                    "SELECT c.name, SUM(oi.quantity * oi.unit_price) AS revenue "
                    "FROM customers c JOIN order_items oi ON 1 = 1 "
                    "WHERE c.name = 'Blue Mountain Cafe' GROUP BY c.name"
                ),
                "rationale": "Aggregate revenue for the named customer.",
            }
        )

    monkeypatch.setattr(planner.model_gateway, "chat", fake_chat)

    decision = await planner.plan(
        "What is the total revenue for Blue Mountain Cafe?",
        session,
        "local-admin",
    )

    assert decision.candidate is True
    assert decision.plan is not None
    assert decision.plan.source_id == "source-1"
    assert "SELECT" in decision.plan.sql


@pytest.mark.asyncio
async def test_planner_rejects_model_selected_unknown_source(session, monkeypatch):
    await add_source(session)
    planner = StructuredPlanner()

    async def fake_chat(messages, **kwargs):
        return json.dumps(
            {
                "use_structured": True,
                "source_id": "attacker-source",
                "sql": "SELECT * FROM customers",
                "rationale": "",
            }
        )

    monkeypatch.setattr(planner.model_gateway, "chat", fake_chat)

    with pytest.raises(StructuredPlannerError, match="unauthorized|unknown"):
        await planner.plan(
            "List customers",
            session,
            "local-admin",
        )


@pytest.mark.asyncio
async def test_planner_can_decline_structured_route(session, monkeypatch):
    await add_source(session)
    planner = StructuredPlanner()

    async def fake_chat(messages, **kwargs):
        return json.dumps(
            {
                "use_structured": False,
                "source_id": "",
                "sql": "",
                "rationale": "Not answerable from schema.",
            }
        )

    monkeypatch.setattr(planner.model_gateway, "chat", fake_chat)

    decision = await planner.plan(
        "What is the total payroll bonus this month?",
        session,
        "local-admin",
    )

    assert decision.candidate is True
    assert decision.plan is None
    assert "schema" in decision.rationale.lower()



@pytest.mark.asyncio
async def test_planner_fails_closed_without_enabled_source(session, monkeypatch):
    source = await add_source(session, source_id="disabled-source")
    source.enabled = False
    await session.commit()

    planner = StructuredPlanner()

    async def should_not_call_model(*args, **kwargs):
        raise AssertionError("model planner should not run without an eligible source")

    monkeypatch.setattr(planner.model_gateway, "chat", should_not_call_model)

    decision = await planner.plan(
        "What is the total revenue for Blue Mountain Cafe?",
        session,
        "local-admin",
    )

    assert decision.candidate is True
    assert decision.plan is None
    assert "no authorized enabled" in decision.rationale.lower()



def test_general_conversation_is_not_structured_from_single_schema_term():
    planner = StructuredPlanner()
    source = DataSource(
        id="source-1",
        user_id="local-admin",
        name="Demo",
        engine="sqlite",
        connection_secret="encrypted",
        status="connected",
        enabled=True,
        schema_json=encode_schema(demo_schema()),
    )

    assert not planner.is_candidate("Hello, my name is Sam.", [source])
    assert not planner.is_candidate("Please remember my name.", [source])


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT s.ssn FROM customers c CROSS JOIN secrets s WHERE c.fy2025_annual_revenue > 300",
        "SELECT s.ssn FROM customers c JOIN secrets s ON 1=1 WHERE c.fy2025_annual_revenue > 300",
        "SELECT name FROM customers WHERE c.fy2025_annual_revenue > 300",
    ],
)
def test_sql_with_unauthorized_or_unqualified_relation_is_rejected(sql):
    plan = StructuredPlan(source_id="source-1", sql=sql, rationale="test")
    parameter = {
        "name": "fy2025_annual_revenue_threshold",
        "value": "300",
        "type": "threshold",
        "operator": ">",
        "unit": None,
        "field": "revenue",
        "period": "annual",
        "fiscal_year": 2025,
        "currency": "USD",
        "citation": "[DOC 1]",
        "evidence_id": "evidence-1",
        "source_id": "document-1",
    }
    assert StructuredPlanner.validates_grounded_parameter(plan, parameter) is False


@pytest.mark.parametrize("value_text,expected", [
    ("1e309", False),
    ("inf", False),
    ("NaN", False),
])
def test_oversized_or_non_finite_policy_value_is_rejected(value_text, expected):
    plan = StructuredPlan(
        source_id="source-1",
        sql=f"SELECT name FROM customers WHERE fy2025_annual_revenue > {value_text}",
        rationale="test",
    )
    parameter = {
        "name": "fy2025_annual_revenue_threshold",
        "value": 1e309,
        "type": "threshold",
        "operator": ">",
        "unit": None,
        "field": "revenue",
        "period": "annual",
        "fiscal_year": 2025,
        "currency": "USD",
        "citation": "[DOC 1]",
        "evidence_id": "evidence-1",
        "source_id": "document-1",
    }
    assert StructuredPlanner.validates_grounded_parameter(plan, parameter) is expected


def test_canonical_decimal_above_business_maximum_is_rejected():
    value = "1000000000000001"
    plan = StructuredPlan(
        source_id="source-1",
        sql=f"SELECT name FROM customers WHERE fy2025_annual_revenue > {value}",
        rationale="test",
    )
    parameter = {
        "name": "fy2025_annual_revenue_threshold",
        "value": value,
        "type": "threshold",
        "operator": ">",
        "unit": None,
        "field": "revenue",
        "period": "annual",
        "fiscal_year": 2025,
        "currency": "USD",
        "citation": "[DOC 1]",
        "evidence_id": "evidence-1",
        "source_id": "document-1",
    }
    assert StructuredPlanner.validates_grounded_parameter(plan, parameter) is False


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("SELECT name FROM sales WHERE fy2025_monthly_revenue > 300", True),
        ("SELECT name FROM sales WHERE fy2025_monthly_revenue > 500", False),
    ],
)
def test_monthly_period_predicate_is_supported(sql, expected):
    plan = StructuredPlan(source_id="source-1", sql=sql, rationale="test")
    parameter = {
        "name": "fy2025_monthly_revenue_threshold",
        "value": "300",
        "type": "threshold",
        "operator": ">",
        "unit": None,
        "field": "revenue",
        "period": "monthly",
        "fiscal_year": 2025,
        "currency": "USD",
        "citation": "[DOC 1]",
        "evidence_id": "evidence-1",
        "source_id": "document-1",
    }
    assert StructuredPlanner.validates_grounded_parameter(plan, parameter) is expected
