"""QA2 Finding 1 regression: the structured EXECUTION path must resolve
candidates through the accepted governed policy, not source ownership.

`orchestrator._structured_sources` already implements the accepted rule (a native
`public`/`internal` source with `tenant_visible=true` is consumable by any active
tenant member; `confidential`/`highly_restricted` need an explicit grant). The
Data and Hybrid execution paths bypassed it: they called
`structured_planner.plan(...)`, which fell back to
`DataSource.user_id == user_id` (ownership only). An ordinary member therefore
could not use an internal tenant-visible source, while an owner could reach a
restricted one they were not otherwise authorized for.

These tests pin the seam the orchestrator now supplies: the governed candidate set
resolved by `_structured_sources`.
"""

from __future__ import annotations

import json

import pytest

from app.core.context import reset_principal, set_principal
from app.core.identity import Principal
from app.core.permissions import permissions_for_role
from app.models import DataSource, Tenant
from app.services import identity as identity_service

TENANT_A = "tnt-qa2-sv-a"
TENANT_B = "tnt-qa2-sv-b"
OWNER = "sv-owner"
MEMBER = "sv-member"
GROUP = "grp-hr-leadership"


def _key(tenant: str, principal: str) -> str:
    return f"{tenant}:{principal}"


def _principal_of(principal_id: str, tenant_id: str = TENANT_A, **kwargs) -> Principal:
    return Principal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        subject=f"sub:{principal_id}",
        role="viewer",
        membership_id=f"m-{principal_id}",
        auth_method="local-dev",
        permissions=permissions_for_role("viewer"),
        **kwargs,
    )


def _source(**kwargs) -> DataSource:
    base = {
        "id": "src",
        "tenant_id": TENANT_A,
        "user_id": _key(TENANT_A, OWNER),
        "name": "Warehouse",
        "engine": "sqlite",
        "connection_secret": "x",
        "status": "connected",
        "enabled": True,
        "schema_json": json.dumps([{"name": "orders", "columns": [{"name": "id"}]}]),
        "authorized_objects_json": json.dumps(["orders"]),
        "classification": "internal",
        "tenant_visible": True,
    }
    base.update(kwargs)
    return DataSource(**base)


@pytest.fixture
async def world(file_db):
    async with file_db() as db:
        db.add(Tenant(id=TENANT_A, slug="qa2-sv-a", name="A", status="active"))
        db.add(Tenant(id=TENANT_B, slug="qa2-sv-b", name="B", status="active"))
        await db.flush()
        for subject, pid in (("sub:sv-owner", OWNER), ("sub:sv-member", MEMBER)):
            await identity_service.get_or_create_principal(
                db, subject=subject, principal_id=pid
            )
        await identity_service.add_membership(
            db, tenant_id=TENANT_A, principal_id=OWNER, role="editor"
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_A, principal_id=MEMBER, role="viewer"
        )
        await db.commit()
    set_principal(_principal_of(MEMBER))
    try:
        yield {}
    finally:
        reset_principal()


class _RecordingPlanner:
    """Captures the governed candidate set the execution path supplies."""

    def __init__(self) -> None:
        self.sources: object = "NOT_CALLED"


def _install_recorder(monkeypatch, recorder: _RecordingPlanner):
    """Patch only `plan` on the real planner so `_sources` stays intact."""
    from app.services import orchestrator as orch
    from app.services.structured_planner import StructuredPlanningResult

    async def fake_plan(message, db, user_id, **kwargs):
        recorder.sources = kwargs.get("sources")
        # Stop before any model or SQL work: this test pins source resolution.
        return StructuredPlanningResult(candidate=True, plan=None)

    monkeypatch.setattr(orch.structured_planner, "plan", fake_plan)


async def _candidates_seen_by_execution(monkeypatch, file_db, message: str = "how many orders"):
    from app.services import orchestrator as orch

    recorder = _RecordingPlanner()
    _install_recorder(monkeypatch, recorder)
    async with file_db() as db:
        await orch.orchestrator._execute_structured_plan(
            message, db, _key(TENANT_A, MEMBER), None, "data",
        )
    return recorder.sources


async def test_data_execution_uses_internal_tenant_visible_source_for_non_owner(
    world, file_db, monkeypatch
):
    """The defect: a non-owning member must be offered the internal source."""
    async with file_db() as db:
        db.add(_source(id="src-internal", classification="internal", tenant_visible=True))
        await db.commit()

    sources = await _candidates_seen_by_execution(monkeypatch, file_db)
    assert sources is not None, "execution path did not supply a governed candidate set"
    assert [s.id for s in sources] == ["src-internal"]


async def test_hybrid_structured_side_uses_the_same_source(world, file_db, monkeypatch):
    """The Hybrid structured side runs through the same execution path."""
    async with file_db() as db:
        db.add(_source(id="src-internal", classification="internal", tenant_visible=True))
        await db.commit()

    sources = await _candidates_seen_by_execution(
        monkeypatch, file_db, message="what is the policy and how many orders"
    )
    assert [s.id for s in sources] == ["src-internal"]


async def test_restricted_source_is_not_a_candidate_without_a_grant(
    world, file_db, monkeypatch
):
    async with file_db() as db:
        db.add(_source(id="src-secret", classification="highly_restricted", tenant_visible=False))
        await db.commit()

    sources = await _candidates_seen_by_execution(monkeypatch, file_db)
    assert sources == []


async def test_group_grant_makes_a_restricted_source_a_candidate(world, file_db, monkeypatch):
    set_principal(_principal_of(MEMBER, group_ids=frozenset({GROUP})))
    async with file_db() as db:
        db.add(
            _source(
                id="src-secret",
                classification="highly_restricted",
                tenant_visible=False,
                allowed_group_ids_json=json.dumps([GROUP]),
            )
        )
        await db.commit()

    sources = await _candidates_seen_by_execution(monkeypatch, file_db)
    assert [s.id for s in sources] == ["src-secret"]


async def test_cross_tenant_source_is_never_a_candidate(world, file_db, monkeypatch):
    async with file_db() as db:
        db.add(
            _source(
                id="src-b",
                tenant_id=TENANT_B,
                user_id=_key(TENANT_B, OWNER),
                classification="internal",
                tenant_visible=True,
            )
        )
        db.add(_source(id="src-a", classification="internal", tenant_visible=True))
        await db.commit()

    sources = await _candidates_seen_by_execution(monkeypatch, file_db)
    assert [s.id for s in sources] == ["src-a"]


async def test_ownership_alone_does_not_bypass_policy(world, file_db, monkeypatch):
    """A principal who OWNS a restricted source must not reach it without a grant."""
    set_principal(_principal_of(OWNER))
    async with file_db() as db:
        db.add(
            _source(
                id="src-owned-secret",
                user_id=_key(TENANT_A, OWNER),
                classification="highly_restricted",
                tenant_visible=False,
            )
        )
        await db.commit()

    from app.services import orchestrator as orch

    recorder = _RecordingPlanner()
    _install_recorder(monkeypatch, recorder)
    async with file_db() as db:
        await orch.orchestrator._execute_structured_plan(
            "how many orders", db, _key(TENANT_A, OWNER), None, "data",
        )
    assert recorder.sources == [], "ownership must not widen governed read authorization"


# ---------------------------------------------------------------------------
# Second gate: the structured.query tool's own source lookup
# ---------------------------------------------------------------------------


async def _run_structured_tool(monkeypatch, file_db, *, user_key: str, source_id: str):
    import types

    from app.services import tools as tools_mod
    from app.services.tools import StructuredQueryTool, ToolContext

    async def fake_execute(source, sql, **kwargs):
        return types.SimpleNamespace(
            source_id=source.id, source_name=source.name, sql=sql,
            columns=("n",), rows=((1,),), row_count=1, truncated=False,
            policy=types.SimpleNamespace(row_limit=200, tables=("orders",)),
        )

    monkeypatch.setattr(tools_mod, "execute_structured_query", fake_execute)
    async with file_db() as db:
        context = ToolContext(
            user_id=user_key, permissions=frozenset({"structured.read"}), db=db
        )
        return await StructuredQueryTool().execute(
            context, {"source_id": source_id, "sql": "select count(*) as n from orders"}
        )


async def test_tool_executes_visible_internal_source_owned_by_someone_else(
    world, file_db, monkeypatch
):
    async with file_db() as db:
        db.add(_source(id="src-internal", classification="internal", tenant_visible=True))
        await db.commit()

    result = await _run_structured_tool(
        monkeypatch, file_db, user_key=_key(TENANT_A, MEMBER), source_id="src-internal"
    )
    assert [e.source_id for e in result.evidence] == ["src-internal"]


async def test_tool_refuses_restricted_source_without_grant(world, file_db, monkeypatch):
    from app.services.tools import ToolPermissionError

    async with file_db() as db:
        db.add(_source(id="src-secret", classification="highly_restricted", tenant_visible=False))
        await db.commit()

    with pytest.raises(ToolPermissionError):
        await _run_structured_tool(
            monkeypatch, file_db, user_key=_key(TENANT_A, MEMBER), source_id="src-secret"
        )


async def test_tool_refuses_cross_tenant_source(world, file_db, monkeypatch):
    from app.services.tools import ToolPermissionError

    async with file_db() as db:
        db.add(
            _source(
                id="src-b", tenant_id=TENANT_B, user_id=_key(TENANT_B, OWNER),
                classification="internal", tenant_visible=True,
            )
        )
        await db.commit()

    with pytest.raises(ToolPermissionError):
        await _run_structured_tool(
            monkeypatch, file_db, user_key=_key(TENANT_A, MEMBER), source_id="src-b"
        )


async def test_tool_ownership_does_not_admit_a_restricted_source(
    world, file_db, monkeypatch
):
    from app.services.tools import ToolPermissionError

    set_principal(_principal_of(OWNER))
    async with file_db() as db:
        db.add(
            _source(
                id="src-owned-secret", user_id=_key(TENANT_A, OWNER),
                classification="highly_restricted", tenant_visible=False,
            )
        )
        await db.commit()

    with pytest.raises(ToolPermissionError):
        await _run_structured_tool(
            monkeypatch, file_db, user_key=_key(TENANT_A, OWNER), source_id="src-owned-secret"
        )
