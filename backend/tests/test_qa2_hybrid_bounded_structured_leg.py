"""QA2 Finding 5: Hybrid must plan the bounded structured sub-question.

`_plan_hybrid` already decomposes the request and computes
`structured_query = " and ".join(decomposition.structured_queries)`, then passed
the ORIGINAL compound message to the structured planner. For a compound request
the planner therefore receives unrelated Knowledge wording, produces no plan, and
the turn degrades to Knowledge-only evidence. The bounded sub-question was
computed and then ignored.

The original request still reaches synthesis and the trace; it must not be what
the structured planner is asked to interpret.
"""

from __future__ import annotations

import json

import pytest

from app.core.context import reset_principal, set_principal
from app.core.identity import Principal
from app.core.permissions import permissions_for_role
from app.models import DataSource, Tenant
from app.schemas import Evidence
from app.services import identity as identity_service

TENANT = "tnt-qa2-hy"
MEMBER = "hy-mem"

COMPOUND = "Summarize the employee handbook and how many orders are there?"
BOUNDED = "how many orders are there?"
SINGLE = "How many orders are there in total?"
KNOWLEDGE_ONLY = "Summarize the employee handbook."


def _key(tenant: str, principal: str) -> str:
    return f"{tenant}:{principal}"


@pytest.fixture
async def world(file_db):
    async with file_db() as db:
        db.add(Tenant(id=TENANT, slug="qa2-hy", name="HY", status="active"))
        await db.flush()
        await identity_service.get_or_create_principal(db, subject=f"sub:{MEMBER}", principal_id=MEMBER)
        await identity_service.add_membership(db, tenant_id=TENANT, principal_id=MEMBER, role="viewer")
        db.add(
            DataSource(
                id="src-ops", tenant_id=TENANT, user_id=_key(TENANT, "someone-else"),
                name="Ops", engine="sqlite", connection_secret="x", status="connected",
                enabled=True,
                schema_json=json.dumps([{"name": "orders", "columns": [{"name": "id"}]}]),
                authorized_objects_json=json.dumps(["orders"]),
                classification="internal", tenant_visible=True,
            )
        )
        await db.commit()
    set_principal(
        Principal(
            principal_id=MEMBER, tenant_id=TENANT, subject=f"sub:{MEMBER}", role="viewer",
            membership_id="m", auth_method="oidc", permissions=permissions_for_role("viewer"),
        )
    )
    try:
        yield {}
    finally:
        reset_principal()


class _Recorder:
    def __init__(self) -> None:
        self.messages: list[str] = []


def _install(monkeypatch) -> _Recorder:
    from app.services import orchestrator as orch
    from app.services.structured_planner import StructuredPlanningResult

    rec = _Recorder()

    async def fake_plan(message, db, user_id, **kwargs):
        rec.messages.append(message)
        # Stop before any model call: this pins what the planner is asked.
        return StructuredPlanningResult(candidate=False)

    monkeypatch.setattr(orch.structured_planner, "plan", fake_plan)
    return rec


async def _run_hybrid(file_db, message: str):
    from app.services.orchestrator import orchestrator

    prefetched = [
        Evidence(source_type="document", source_id="hb", title="Employee handbook", passage="leave")
    ]
    async with file_db() as db:
        return await orchestrator._plan_hybrid(
            message, db, _key(TENANT, MEMBER), None, "hybrid",
            prefetched_knowledge_evidence=prefetched,
        )


async def test_compound_hybrid_plans_the_bounded_structured_subquestion(world, file_db, monkeypatch):
    rec = _install(monkeypatch)
    plan = await _run_hybrid(file_db, COMPOUND)

    assert rec.messages == [BOUNDED], f"planner saw {rec.messages!r}"
    assert plan.execution_class == "hybrid"


async def test_single_question_hybrid_still_plans_the_full_wording(world, file_db, monkeypatch):
    """A single question has no conjunction, so the full wording is the bounded one."""
    rec = _install(monkeypatch)
    await _run_hybrid(file_db, SINGLE)

    assert rec.messages == [SINGLE], f"planner saw {rec.messages!r}"


async def test_knowledge_only_hybrid_produces_no_structured_evidence(world, file_db, monkeypatch):
    rec = _install(monkeypatch)
    plan = await _run_hybrid(file_db, KNOWLEDGE_ONLY)

    assert plan.execution_class == "hybrid"
    assert all(e.source_type != "structured_query" for e in plan.evidence)
    assert plan.evidence, "the knowledge side must still produce evidence"
