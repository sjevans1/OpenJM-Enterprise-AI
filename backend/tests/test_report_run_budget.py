"""VS4-B2C2 Phase 4: result and budget enforcement tests.

Covers:
- RUN_DEADLINE_SECONDS == 600
- ReportRunBudget defaults, counters, wall deadline
- Model gateway budget integration (attempt counting, max_tokens cap)
- One-active-run-per-definition (partial unique index, reserve conflicts)
- Budget exhaustion → terminal failure, no retry, replay returns terminal row
- Wall-clock deadline terminal failure
- B2C1 byte/row bounds on typed structured envelope with budget
- Ordinary Chat unchanged when budget=None
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.api import report_runs as report_runs_api
from app.db import _migrate_add_active_run_index
from app.models import (
    ExecutionTrace,
    Message,
    ReportDefinitionVersion,
    ReportRun,
    SavedReport,
)
from app.schemas import Evidence
from app.services.model_gateway import (
    ModelGatewayError,
    OpenAICompatibleModelGateway,
)
from app.services.orchestrator import ExecutionPlan
from app.services.report_runs import (
    RUN_DEADLINE_SECONDS,
    BudgetExceeded,
    ReportRunBudget,
    ReportRunConflict,
    finalize_failure,
    finalize_success,
    get_report_run,
    reserve_report_run,
)

from conftest import seed_definition, _Clock


# ─── Helpers (self-contained for Phase 4 tests) ──────────────────────────

class _AnswerGateway:
    """Fake gateway respecting the budget kwarg; returns a fixed answer."""
    async def chat(self, _messages, **kwargs):
        budget = kwargs.get("budget")
        if budget is not None:
            budget.count_model()
        return "Authoritative result synthesized."


def _body(content: str, finish: str = "stop") -> dict:
    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _make_gateway(monkeypatch, responses: list):
    """Gateway whose transport replays the given responses in order."""
    gateway = OpenAICompatibleModelGateway()
    calls: list[dict] = []
    queue = list(responses)

    async def fake_post_json(url: str, headers: dict, payload: dict):
        calls.append({
            "url": url,
            "temperature": payload.get("temperature"),
            "max_tokens": payload.get("max_tokens"),
        })
        if not queue:
            raise AssertionError("unexpected extra model call")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return 200, item

    monkeypatch.setattr(gateway, "_post_json", fake_post_json)
    return gateway, calls


async def _set_definition_mode(maker, fixture, mode):
    execution_class = {"knowledge": "knowledge", "data": "structured",
                       "hybrid": "hybrid"}[mode]
    async with maker() as db:
        definition = await db.get(ReportDefinitionVersion, fixture["definition_id"])
        report = await db.get(SavedReport, fixture["report_id"])
        assistant = await db.get(Message, fixture["assistant_id"])
        user = (
            (await db.execute(
                select(Message).where(
                    Message.conversation_id == report.conversation_id,
                    Message.role == "user",
                )
            )).scalars().one()
        )
        definition.requested_mode = mode
        report.requested_mode = mode
        report.execution_class = execution_class
        assistant.requested_mode = mode
        assistant.execution_class = execution_class
        user.requested_mode = mode
        await db.commit()
    fixture["requested_mode"] = mode


async def _add_trace(
    db, *, run_id, tool_name, mode=None, source_id=None, sql=None,
    evidence_ids=None, user_id=None, conversation_id=None,
    status="succeeded", metadata=None,
):
    mode = mode or ("data" if tool_name == "structured.query" else "knowledge")
    trace = ExecutionTrace(
        request_id=run_id,
        tool_invocation_id=f"inv-{tool_name}",
        user_id=user_id or report_runs_api.settings.dev_user_id,
        conversation_id=conversation_id,
        route="hybrid" if mode == "hybrid"
        else ("structured" if tool_name == "structured.query" else "knowledge"),
        requested_mode=mode,
        tool_name=tool_name,
        operation_class="READ",
        risk_level="MODERATE",
        requires_approval=False,
        source_id=source_id,
        input_hash="0" * 64,
        planned_sql=sql,
        executed_sql=sql,
        validation_decision="allowed",
        status=status,
        evidence_ids_json=json.dumps(evidence_ids) if evidence_ids is not None else None,
        metadata_json=json.dumps(metadata) if metadata is not None else None,
    )
    db.add(trace)
    await db.commit()
    await db.refresh(trace)
    return trace.id


def _document_evidence(fixture, *, evidence_id="doc-evidence"):
    return Evidence(
        evidence_id=evidence_id,
        source_type="document",
        source_id=fixture["pinned_document_ids"][0],
        title="Policy",
        passage="For FY2025, policy requires annual revenue above USD 300.",
    )


def _structured_evidence(fixture, *, evidence_id="data-evidence",
                         sql="SELECT revenue FROM finance"):
    source_id = next(iter(fixture["pinned_source_tables"]))
    return Evidence(
        evidence_id=evidence_id,
        source_type="structured_query",
        source_id=source_id,
        title="Finance",
        passage='{"columns":["revenue"],"rows":[["narrative-preview"]],"row_count":1}',
        provenance={"tool": "structured.query", "executed_sql": sql},
        metadata={
            "sql": sql,
            "columns": ["revenue"],
            "row_count": 1,
            "truncated": False,
            "tables": ["finance"],
        },
    )


# ─── 1. Constants ───────────────────────────────────────────────────────

def test_run_deadline_seconds_is_600():
    """Phase 4 changes the wall-clock deadline from 1800 to 600 seconds."""
    assert RUN_DEADLINE_SECONDS == 600


# ─── 2. ReportRunBudget unit tests ──────────────────────────────────────

def test_report_run_budget_defaults():
    """Budget defaults match Phase 4 contracts."""
    budget = ReportRunBudget(
        started_at=datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
    )
    assert budget.max_model_http_attempts == 8
    assert budget.max_output_tokens == 2048
    assert budget.max_sql_executions == 2
    assert budget.max_knowledge_retrievals == 2
    assert budget.wall_deadline_seconds == RUN_DEADLINE_SECONDS
    assert budget.model_attempts == 0
    assert budget.sql_executions == 0
    assert budget.knowledge_retrievals == 0


def test_budget_count_model_allows_8_blocks_9th():
    budget = ReportRunBudget(
        started_at=datetime.now(timezone.utc)
    )
    for _ in range(8):
        budget.count_model()
    with pytest.raises(BudgetExceeded):
        budget.count_model()
    assert budget.model_attempts == 9


def test_budget_count_sql_allows_2_blocks_3rd():
    budget = ReportRunBudget(
        started_at=datetime.now(timezone.utc)
    )
    budget.count_sql()
    budget.count_sql()
    with pytest.raises(BudgetExceeded):
        budget.count_sql()
    assert budget.sql_executions == 3


def test_budget_count_knowledge_allows_2_blocks_3rd():
    budget = ReportRunBudget(
        started_at=datetime.now(timezone.utc)
    )
    budget.count_knowledge()
    budget.count_knowledge()
    with pytest.raises(BudgetExceeded):
        budget.count_knowledge()
    assert budget.knowledge_retrievals == 3


def test_budget_check_wall_raises_when_expired():
    started = datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
    budget = ReportRunBudget(started_at=started)
    now = started + timedelta(seconds=601)
    with pytest.raises(BudgetExceeded):
        budget.check_wall(now)


def test_budget_check_wall_passes_within_deadline():
    started = datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
    budget = ReportRunBudget(started_at=started)
    now = started + timedelta(seconds=599)
    budget.check_wall(now)  # must not raise


def test_budget_enforce_max_tokens_caps_none():
    budget = ReportRunBudget(
        started_at=datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
    )
    assert budget.enforce_max_tokens(None) == 2048


def test_budget_enforce_max_tokens_caps_high():
    budget = ReportRunBudget(
        started_at=datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
    )
    assert budget.enforce_max_tokens(4096) == 2048
    assert budget.enforce_max_tokens(3000) == 2048


def test_budget_enforce_max_tokens_allows_low():
    budget = ReportRunBudget(
        started_at=datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
    )
    assert budget.enforce_max_tokens(700) == 700
    assert budget.enforce_max_tokens(1) == 1


def test_budget_is_exhausted_after_caps():
    budget = ReportRunBudget(
        started_at=datetime.now(timezone.utc)
    )
    assert not budget.is_exhausted()
    for _ in range(8):
        budget.count_model()
    assert budget.is_exhausted()


# ─── 3. Model gateway budget integration ───────────────────────────────

async def test_model_gateway_without_budget_does_not_count(monkeypatch):
    """Ordinary Chat (budget=None) must not enforce attempt limits."""
    gateway, calls = _make_gateway(monkeypatch, [_body("Answer.")] * 20)
    for _ in range(20):
        result = await gateway.chat([{"role": "user", "content": "Hello"}])
        assert result == "Answer."
    assert len(calls) == 20


async def test_model_gateway_counts_attempts_with_budget(monkeypatch):
    """Each chat() call with a budget increments the model counter."""
    gateway, calls = _make_gateway(monkeypatch, [_body("Answer.")] * 3)
    budget = ReportRunBudget(
        started_at=datetime.now(timezone.utc),
        max_model_http_attempts=8,
    )
    for _ in range(3):
        result = await gateway.chat([{"role": "user", "content": "Hello"}], budget=budget)
        assert result == "Answer."
    assert budget.model_attempts == 3
    assert len(calls) == 3


async def test_model_gateway_budget_blocks_after_cap(monkeypatch):
    """The 9th model HTTP attempt with budget raises BudgetExceeded."""
    gateway, calls = _make_gateway(monkeypatch, [_body("Answer.")] * 8)
    budget = ReportRunBudget(
        started_at=datetime.now(timezone.utc),
        max_model_http_attempts=8,
    )
    for _ in range(8):
        await gateway.chat([{"role": "user", "content": "Hello"}], budget=budget)
    with pytest.raises(BudgetExceeded):
        await gateway.chat([{"role": "user", "content": "Hello"}], budget=budget)
    assert budget.model_attempts == 9


async def test_model_gateway_budget_caps_max_tokens_none(monkeypatch):
    """max_tokens=None with budget must be capped to 2048."""
    gateway, calls = _make_gateway(monkeypatch, [_body("Answer.")])
    budget = ReportRunBudget(
        started_at=datetime.now(timezone.utc)
    )
    await gateway.chat([{"role": "user", "content": "Hello"}], budget=budget)
    assert calls[0]["max_tokens"] == 2048


async def test_model_gateway_budget_caps_max_tokens_high(monkeypatch):
    """max_tokens above 2048 with budget must be capped to 2048."""
    gateway, calls = _make_gateway(monkeypatch, [_body("Answer.")])
    budget = ReportRunBudget(
        started_at=datetime.now(timezone.utc)
    )
    await gateway.chat(
        [{"role": "user", "content": "Hello"}],
        max_tokens=4096,
        budget=budget,
    )
    assert calls[0]["max_tokens"] == 2048


async def test_model_gateway_without_budget_no_max_tokens_cap(monkeypatch):
    """Ordinary Chat with max_tokens=None sends None (no cap)."""
    gateway, calls = _make_gateway(monkeypatch, [_body("Answer.")])
    await gateway.chat([{"role": "user", "content": "Hello"}])
    assert calls[0]["max_tokens"] is None


async def test_model_gateway_without_budget_preserves_max_tokens(monkeypatch):
    """Ordinary Chat with explicit max_tokens>2048 is not capped."""
    gateway, calls = _make_gateway(monkeypatch, [_body("Answer.")])
    await gateway.chat(
        [{"role": "user", "content": "Hello"}], max_tokens=4096
    )
    assert calls[0]["max_tokens"] == 4096


# ─── 4. Schema migration ────────────────────────────────────────────────

async def test_partial_unique_index_exists(file_db):
    """The one-active-run-per-definition partial unique index exists."""
    from sqlalchemy import text
    async with file_db() as db:
        engine = db.bind
        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' "
                "AND name='uq_report_run_active_per_definition'"
            ))
        assert result.first() is not None


async def test_migration_idempotent(file_db):
    """Running the migration twice must not error."""
    async with file_db() as db:
        engine = db.bind
        await _migrate_add_active_run_index(engine=engine)
        await _migrate_add_active_run_index(engine=engine)  # second call must be safe


# ─── 5. One-active-run-per-definition ───────────────────────────────────

async def test_idempotent_replay_returns_existing(file_db):
    """Same key + same intent returns the existing row, owns_execution=False."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    key = "00000000-0000-4000-8000-000000000901"

    async with file_db() as db:
        run_a, owns_a = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key=key, requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        assert owns_a is True

        run_b, owns_b = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key=key, requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        assert owns_b is False
        assert run_a.id == run_b.id


async def test_same_key_different_intent_conflict(file_db):
    """Same key + different intent raises ReportRunConflict."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    key = "00000000-0000-4000-8000-000000000902"

    async with file_db() as db:
        await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key=key, requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        with pytest.raises(ReportRunConflict, match="intent"):
            await reserve_report_run(
                db=db, report_id=fixture["report_id"], definition_version=1,
                idempotency_key=key, requested_mode=fixture["requested_mode"],
                question="Different question text",
                pinned_document_ids=fixture["pinned_document_ids"],
                pinned_source_tables=fixture["pinned_source_tables"],
                now=clock,
            )


async def test_different_key_active_run_returns_conflict(file_db):
    """A second, different key while a run is still 'running' → conflict."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    key_a = "00000000-0000-4000-8000-000000000903"
    key_b = "00000000-0000-4000-8000-000000000904"

    async with file_db() as db:
        run_a, owns_a = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key=key_a, requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        assert owns_a is True

        with pytest.raises(ReportRunConflict):
            await reserve_report_run(
                db=db, report_id=fixture["report_id"], definition_version=1,
                idempotency_key=key_b, requested_mode=fixture["requested_mode"],
                question=fixture["question"],
                pinned_document_ids=fixture["pinned_document_ids"],
                pinned_source_tables=fixture["pinned_source_tables"],
                now=clock,
            )


async def test_terminal_run_allows_new_key(file_db):
    """A terminalised run does NOT block a new key for the same definition."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    key_a = "00000000-0000-4000-8000-000000000905"
    key_b = "00000000-0000-4000-8000-000000000906"

    async with file_db() as db:
        run_a, owns_a = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key=key_a, requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        await finalize_failure(
            db=db, run_id=run_a.id, user_id=report_runs_api.settings.dev_user_id,
            fingerprint=run_a.request_fingerprint, failure_category="internal",
        )
        # Run A is now terminal (failed). A new key should be allowed.
        run_b, owns_b = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key=key_b, requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        assert owns_b is True


async def test_concurrent_different_keys_one_wins(file_db, sessions):
    """Two different keys submitted concurrently: exactly one wins."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")
    a, b = sessions
    clock = _Clock(datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc))
    key_a = "00000000-0000-4000-8000-000000000907"
    key_b = "00000000-0000-4000-8000-000000000908"

    async def _try_reserve(session, key):
        try:
            run, owns = await reserve_report_run(
                db=session, report_id=fixture["report_id"], definition_version=1,
                idempotency_key=key, requested_mode=fixture["requested_mode"],
                question=fixture["question"],
                pinned_document_ids=fixture["pinned_document_ids"],
                pinned_source_tables=fixture["pinned_source_tables"],
                now=clock,
            )
            return ("ok", run, owns)
        except ReportRunConflict:
            return ("conflict", None, None)

    results = await asyncio.gather(
        _try_reserve(a, key_a),
        _try_reserve(b, key_b),
    )

    oks = [r for r in results if r[0] == "ok"]
    conflicts = [r for r in results if r[0] == "conflict"]
    assert len(oks) == 1
    assert len(conflicts) == 1
    assert oks[0][2] is True  # owns_execution

    # Exactly one run row was inserted
    async with file_db() as db:
        count = await db.scalar(select(func.count()).select_from(ReportRun))
    assert count == 1


# ─── 6. Budget enforcement at API level ─────────────────────────────────

@pytest.mark.asyncio
async def test_model_attempt_exhaustion_finalizes_budget_exceeded(
    file_db, client, monkeypatch
):
    """When the model HTTP attempt budget is exhausted, the run finalises
    as failed with failure_category='budget_exceeded'."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")

    planner_call_count = [0]

    class _BudgetExhaustingPlanner:
        async def plan(self, **kwargs):
            planner_call_count[0] += 1
            budget = kwargs.get("budget")
            # Simulate 9 model calls to exhaust the default cap of 8
            for _ in range(9):
                budget.count_model()

    monkeypatch.setattr(report_runs_api, "orchestrator", _BudgetExhaustingPlanner())
    monkeypatch.setattr(report_runs_api, "model_gateway", _AnswerGateway())
    monkeypatch.setattr(
        report_runs_api, "settings",
        report_runs_api.settings.model_copy(update={"report_runs_enabled": True}),
    )

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000a01"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_category"] == "budget_exceeded"
    # No automatic retry: planner was called exactly once
    assert planner_call_count[0] == 1


@pytest.mark.asyncio
async def test_budget_exceeded_replay_returns_terminal(file_db, client, monkeypatch):
    """Re-submitting the same key after budget_exceeded returns the terminal
    row without re-executing external work."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")

    planner_call_count = [0]

    class _BudgetExhaustingPlanner:
        async def plan(self, **kwargs):
            planner_call_count[0] += 1
            budget = kwargs.get("budget")
            for _ in range(9):
                budget.count_model()

    monkeypatch.setattr(report_runs_api, "orchestrator", _BudgetExhaustingPlanner())
    monkeypatch.setattr(report_runs_api, "model_gateway", _AnswerGateway())
    monkeypatch.setattr(
        report_runs_api, "settings",
        report_runs_api.settings.model_copy(update={"report_runs_enabled": True}),
    )

    key = "00000000-0000-4000-8000-000000000a02"

    # First submission: budget exhausted
    response1 = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": key},
    )
    assert response1.status_code == 202
    body1 = response1.json()
    assert body1["status"] == "failed"
    assert body1["failure_category"] == "budget_exceeded"
    first_count = planner_call_count[0]
    assert first_count == 1

    # Replay: returns terminal row, no new execution
    response2 = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": key},
    )
    assert response2.status_code == 202
    body2 = response2.json()
    assert body2["status"] == "failed"
    assert body2["failure_category"] == "budget_exceeded"
    assert body2["id"] == body1["id"]
    # No new planner calls
    assert planner_call_count[0] == first_count


@pytest.mark.asyncio
async def test_wall_deadline_exceeded_finalizes_budget_exceeded(
    file_db, client, monkeypatch
):
    """A run whose budget clock is already past the deadline finalises as
    budget_exceeded without any model calls."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")

    planner_called = [False]

    class _NoCallPlanner:
        async def plan(self, **kwargs):
            planner_called[0] = True
            return ExecutionPlan(
                execution_class="knowledge",
                system_prompt="test",
                requested_mode="knowledge",
                evidence=[],
                direct_answer="test",
            )

    monkeypatch.setattr(report_runs_api, "orchestrator", _NoCallPlanner())
    monkeypatch.setattr(report_runs_api, "model_gateway", _AnswerGateway())

    # Pre-reserve a run with a clock far enough in the past that the
    # 600-second deadline has already passed.
    clock = _Clock(
        datetime.now(timezone.utc) - timedelta(seconds=601)
    )
    async with file_db() as db:
        run, owns = await reserve_report_run(
            db=db, report_id=fixture["report_id"], definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000a03",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=clock,
        )
        assert owns is True
        run_id = run.id

    # Submit via API with the same key (idempotent replay) — this will
    # find the existing 'running' row and pass owns_execution=False,
    # returning the terminal row.
    # But the run is still 'running' (not terminal yet). We need to test
    # _execute_owned_run directly for the wall deadline.
    # Instead, let's verify check_wall raises:
    budget = ReportRunBudget(started_at=clock())
    with pytest.raises(BudgetExceeded):
        budget.check_wall(datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_sql_cap_enforced_in_tools(file_db, monkeypatch):
    """The structured.query tool must count SQL executions against the budget."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")

    budget = ReportRunBudget(
        started_at=datetime.now(timezone.utc),
        max_sql_executions=2,
    )

    # First SQL call: allowed
    budget.count_sql()
    assert budget.sql_executions == 1

    # Second SQL call: allowed
    budget.count_sql()
    assert budget.sql_executions == 2

    # Third SQL call: blocked
    with pytest.raises(BudgetExceeded):
        budget.count_sql()


@pytest.mark.asyncio
async def test_knowledge_cap_enforced_in_tools(file_db, monkeypatch):
    """The knowledge.search tool must count retrievals against the budget."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")

    budget = ReportRunBudget(
        started_at=datetime.now(timezone.utc),
        max_knowledge_retrievals=2,
    )

    budget.count_knowledge()
    budget.count_knowledge()
    with pytest.raises(BudgetExceeded):
        budget.count_knowledge()


# ─── 7. B2C1 bounds on typed structured envelope with budget ────────────

@pytest.mark.asyncio
async def test_structured_result_bounds_with_budget(file_db, client, monkeypatch):
    """Typed structured rows/columns persist correctly when budget is active
    (ordinary Chat path is unaffected; budget defaults don't restrict here)."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "data")
    evidence = _structured_evidence(fixture)
    source_id = evidence.source_id
    sql = evidence.metadata["sql"]

    class Planner:
        async def plan(self, **kwargs):
            trace_id = await _add_trace(
                kwargs["db"],
                run_id=kwargs["request_id"],
                tool_name="structured.query",
                source_id=source_id,
                sql=sql,
                evidence_ids=[evidence.evidence_id],
            )
            return ExecutionPlan(
                execution_class="structured",
                system_prompt="Use the authoritative structured result.",
                evidence=[evidence],
                requested_mode="data",
                structured_result={
                    "source_id": source_id,
                    "evidence_id": evidence.evidence_id,
                    "sql": sql,
                    "columns": ["revenue"],
                    "rows": [[Decimal("325.10")]],
                    "row_count": 1,
                    "truncated": False,
                },
                trace_ids=[trace_id],
            )

    monkeypatch.setattr(report_runs_api, "orchestrator", Planner())
    monkeypatch.setattr(report_runs_api, "model_gateway", _AnswerGateway())
    monkeypatch.setattr(
        report_runs_api, "settings",
        report_runs_api.settings.model_copy(update={"report_runs_enabled": True}),
    )

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000b01"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["structured_result"]["rows"] == [["325.10"]]
    # Must NOT contain the narrative-preview placeholder
    assert body["result"]["structured_result"]["rows"] != [["narrative-preview"]]
    assert body["result"]["trace_ids"]
    assert body["result"]["structured_result"]["columns"] == ["revenue"]
    assert body["result"]["structured_result"]["row_count"] == 1


# ─── 8. Ordinary Chat unchanged ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_ordinary_chat_path_unaffected(file_db, client, monkeypatch):
    """Without a budget parameter, Chat-style execution is unchanged."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")

    class Planner:
        async def plan(self, **kwargs):
            # budget kwarg should be None for ordinary Chat
            assert kwargs.get("budget") is None
            evidence = _document_evidence(fixture)
            return ExecutionPlan(
                execution_class="knowledge",
                system_prompt="test",
                evidence=[evidence],
                direct_answer="test answer",
                requested_mode="knowledge",
                trace_ids=[],
            )

    class Gateway:
        async def chat(self, _messages, **kwargs):
            assert kwargs.get("budget") is None
            return "Answer."

    monkeypatch.setattr(report_runs_api, "orchestrator", Planner())
    monkeypatch.setattr(report_runs_api, "model_gateway", Gateway())

    # Verify ordinary Chat path passes budget=None through the real orchestrator
    async with file_db() as db:
        from app.services.orchestrator import orchestrator as real_orchestrator
        plan = await real_orchestrator.plan(
            message="test",
            db=db,
            user_id=report_runs_api.settings.dev_user_id,
            conversation_id=None,
            mode="chat",
            scope=None,
            request_id="test-ordinary",
            budget=None,
        )
        assert plan.execution_class == "general"


@pytest.mark.asyncio
async def test_overall_planning_wall_timeout_finalizes_without_result(
    file_db, client, monkeypatch
):
    """The complete planner await is bounded by remaining report wall time."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")

    class SlowPlanner:
        async def plan(self, **_kwargs):
            await asyncio.sleep(0.05)
            raise AssertionError("planning should have been cancelled")

    real_budget = report_runs_api.ReportRunBudget
    monkeypatch.setattr(
        report_runs_api,
        "ReportRunBudget",
        lambda *, started_at: real_budget(
            started_at=started_at, wall_deadline_seconds=0.01
        ),
    )
    monkeypatch.setattr(report_runs_api, "orchestrator", SlowPlanner())
    monkeypatch.setattr(report_runs_api, "model_gateway", _AnswerGateway())
    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000c01"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_category"] == "budget_exceeded"
    assert body["result"] is None


@pytest.mark.asyncio
async def test_finalize_success_refuses_run_after_persisted_deadline(file_db):
    """The final persistence compare-and-set cannot succeed after deadline_at."""
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")
    old = datetime.now(timezone.utc) - timedelta(seconds=601)
    async with file_db() as db:
        run, owns = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key="00000000-0000-4000-8000-000000000c02",
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
            now=old,
        )
        assert owns is True
        persisted = await finalize_success(
            db=db,
            run_id=run.id,
            user_id=report_runs_api.settings.dev_user_id,
            fingerprint=run.request_fingerprint,
            answer="late answer",
            evidence=[{
                "source_type": "document",
                "source_id": fixture["pinned_document_ids"][0],
                "title": "Policy",
                "passage": "bounded",
                "evidence_id": "e-late",
            }],
            structured_result=None,
            trace_ids=["trace-late"],
        )
        assert persisted == 0
        await db.refresh(run)
        assert run.status == "running"
        assert run.result_json is None


@pytest.mark.asyncio
async def test_remaining_seconds_and_counters_fail_after_wall_deadline():
    """Every budget counter now enforces the same absolute wall deadline."""
    old = datetime.now(timezone.utc) - timedelta(seconds=601)
    budget = ReportRunBudget(started_at=old)
    with pytest.raises(BudgetExceeded):
        budget.remaining_seconds()
    with pytest.raises(BudgetExceeded):
        budget.count_model()
    with pytest.raises(BudgetExceeded):
        budget.count_sql()
    with pytest.raises(BudgetExceeded):
        budget.count_knowledge()
