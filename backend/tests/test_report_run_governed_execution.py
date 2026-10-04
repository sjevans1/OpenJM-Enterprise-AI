"""VS4-B2C2 Phase 2 governed report execution."""

import json

import pytest
from sqlalchemy import func, select

from app.api import report_runs as report_runs_api
from app.models import Conversation, Document, ExecutionTrace, Message, ReportRun
from app.schemas import Evidence
from app.services.orchestrator import ExecutionPlan
from app.services.report_scope import ReportSourceScope
from app.services.report_runs import reserve_report_run, revoke_report_run

from conftest import seed_definition


class _PlanRecorder:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []

    async def plan(self, **kwargs):
        self.calls.append(kwargs)
        traces = []
        for item in self.evidence:
            tool_name = (
                "knowledge.search"
                if item.source_type == "document"
                else "structured.query"
            )
            sql = item.metadata.get("sql")
            trace = ExecutionTrace(
                request_id=kwargs["request_id"],
                tool_invocation_id=f"inv-{tool_name}",
                user_id=kwargs["user_id"],
                conversation_id=None,
                route="hybrid",
                requested_mode="hybrid",
                tool_name=tool_name,
                operation_class="READ",
                risk_level="LOW",
                requires_approval=False,
                source_id=item.source_id if tool_name == "structured.query" else None,
                input_hash="0" * 64,
                planned_sql=sql,
                executed_sql=sql,
                status="succeeded",
                evidence_ids_json=json.dumps([item.evidence_id]),
            )
            kwargs["db"].add(trace)
            traces.append(trace)
        await kwargs["db"].commit()
        return ExecutionPlan(
            execution_class="hybrid",
            system_prompt="Use only the governed report evidence.",
            evidence=self.evidence,
            requested_mode="hybrid",
            structured_result={
                "source_id": self.evidence[1].source_id,
                "evidence_id": self.evidence[1].evidence_id,
                "sql": self.evidence[1].metadata["sql"],
                "columns": self.evidence[1].metadata["columns"],
                "rows": [[325]],
                "row_count": 1,
                "truncated": False,
            },
            trace_ids=[trace.id for trace in traces],
        )


class _AnswerGateway:
    def __init__(self, answer="Governed report answer."):
        self.answer = answer
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.answer


def _authorized_evidence(fixture):
    document_id = fixture["pinned_document_ids"][0]
    source_id = next(iter(fixture["pinned_source_tables"]))
    return [
        Evidence(
            evidence_id="document-evidence",
            source_type="document",
            source_id=document_id,
            title="Policy",
            passage="Authorized policy evidence.",
        ),
        Evidence(
            evidence_id="structured-evidence",
            source_type="structured_query",
            source_id=source_id,
            title="Finance",
            passage="Authorized structured evidence.",
            metadata={
                "tables": ["finance"],
                "sql": "SELECT revenue FROM finance",
                "columns": ["revenue"],
                "row_count": 1,
                "truncated": False,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_new_reservation_executes_exact_validated_intent_without_chat_rows(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    source_id = next(iter(fixture["pinned_source_tables"]))
    evidence = _authorized_evidence(fixture)
    planner = _PlanRecorder(evidence)
    gateway = _AnswerGateway()
    loads = []
    original_load = report_runs_api.load_validated_definition_scope

    async def tracked_load(*args, **kwargs):
        value = await original_load(*args, **kwargs)
        loads.append((kwargs["definition_id"], value))
        return value

    monkeypatch.setattr(
        report_runs_api, "load_validated_definition_scope", tracked_load
    )
    monkeypatch.setattr(report_runs_api, "orchestrator", planner, raising=False)
    monkeypatch.setattr(report_runs_api, "model_gateway", gateway, raising=False)

    async with file_db() as db:
        conversations_before = await db.scalar(
            select(func.count()).select_from(Conversation)
        )
        messages_before = await db.scalar(select(func.count()).select_from(Message))

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000220"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["answer"] == "Governed report answer."
    assert (
        len(loads) >= 3
    )  # reservation, immediately pre-work, immediately pre-persist/read
    assert all(item[0] == fixture["definition_id"] for item in loads)
    assert len(planner.calls) == 1
    call = planner.calls[0]
    assert call["message"] == fixture["question"]
    assert call["mode"] == fixture["requested_mode"]
    assert call["conversation_id"] is None
    assert call["request_id"] == body["id"]
    assert isinstance(call["scope"], ReportSourceScope)
    assert call["scope"].document_ids == frozenset(fixture["pinned_document_ids"])
    assert dict(call["scope"].source_tables) == {source_id: frozenset({"finance"})}
    assert gateway.calls == [
        (
            [
                {"role": "system", "content": "Use only the governed report evidence."},
                {"role": "user", "content": fixture["question"]},
            ],
            {"max_tokens": 2048},
        )
    ]
    async with file_db() as db:
        assert (
            await db.scalar(select(func.count()).select_from(Conversation))
            == conversations_before
        )
        assert (
            await db.scalar(select(func.count()).select_from(Message))
            == messages_before
        )
        run = await db.get(ReportRun, body["id"])
        assert run.status == "succeeded"


@pytest.mark.asyncio
async def test_running_replay_reauthorizes_and_performs_no_execution_work(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    key = "00000000-0000-4000-8000-000000000221"
    async with file_db() as db:
        run, owns = await reserve_report_run(
            db=db,
            report_id=fixture["report_id"],
            definition_version=1,
            idempotency_key=key,
            requested_mode=fixture["requested_mode"],
            question=fixture["question"],
            pinned_document_ids=fixture["pinned_document_ids"],
            pinned_source_tables=fixture["pinned_source_tables"],
        )
        assert owns is True
        run_id = run.id

    class Forbidden:
        async def plan(self, **_kwargs):
            raise AssertionError("running replay must not orchestrate")

        async def chat(self, *_args, **_kwargs):
            raise AssertionError("running replay must not synthesize")

    forbidden = Forbidden()
    monkeypatch.setattr(report_runs_api, "orchestrator", forbidden)
    monkeypatch.setattr(report_runs_api, "model_gateway", forbidden)

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": key},
    )

    assert response.status_code == 202
    assert response.json()["id"] == run_id
    assert response.json()["status"] == "running"
    assert response.json()["result"] is None


@pytest.mark.asyncio
async def test_terminal_replay_reauthorizes_and_performs_no_execution_work(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    key = "00000000-0000-4000-8000-000000000222"
    planner = _PlanRecorder(_authorized_evidence(fixture))
    gateway = _AnswerGateway()
    monkeypatch.setattr(report_runs_api, "orchestrator", planner)
    monkeypatch.setattr(report_runs_api, "model_gateway", gateway)
    path = f"/api/reports/{fixture['report_id']}/definitions/1/runs"

    first = await client.post(path, json={"idempotency_key": key})
    assert first.status_code == 202
    assert first.json()["status"] == "succeeded"

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("terminal replay must perform zero external work")

    monkeypatch.setattr(planner, "plan", forbidden)
    monkeypatch.setattr(gateway, "chat", forbidden)
    replay = await client.post(path, json={"idempotency_key": key})

    assert replay.status_code == 202
    assert replay.json() == first.json()


@pytest.mark.asyncio
async def test_pre_execution_revocation_finalizes_authorization_failure_without_work(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    original_reserve = report_runs_api.reserve_report_run
    calls = []

    async def reserve_then_revoke(**kwargs):
        result = await original_reserve(**kwargs)
        document = await kwargs["db"].get(Document, fixture["pinned_document_ids"][0])
        document.indexed = False
        await kwargs["db"].commit()
        return result

    class Forbidden:
        async def plan(self, **_kwargs):
            calls.append("plan")
            raise AssertionError("revoked scope must fail before orchestration")

        async def chat(self, *_args, **_kwargs):
            calls.append("model")
            raise AssertionError("revoked scope must fail before synthesis")

    monkeypatch.setattr(report_runs_api, "reserve_report_run", reserve_then_revoke)
    monkeypatch.setattr(report_runs_api, "orchestrator", Forbidden())
    monkeypatch.setattr(report_runs_api, "model_gateway", Forbidden())

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000223"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert response.json()["failure_category"] == "authorization"
    assert response.json()["result"] is None
    assert calls == []


@pytest.mark.asyncio
async def test_in_flight_revocation_discards_generated_answer_before_delivery(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    planner = _PlanRecorder(_authorized_evidence(fixture))

    class RevokingGateway:
        async def chat(self, *_args, **_kwargs):
            async with file_db() as other:
                document = await other.get(Document, fixture["pinned_document_ids"][0])
                document.indexed = False
                await other.commit()
            return "SECRET GENERATED ANSWER"

    monkeypatch.setattr(report_runs_api, "orchestrator", planner)
    monkeypatch.setattr(report_runs_api, "model_gateway", RevokingGateway())

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000224"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_category"] == "authorization"
    assert body["result"] is None
    assert "SECRET GENERATED ANSWER" not in response.text
    async with file_db() as db:
        run = await db.get(ReportRun, body["id"])
        assert run.result_json is None


@pytest.mark.asyncio
async def test_in_flight_run_revocation_wins_persistence_and_delivers_no_answer(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    planner = _PlanRecorder(_authorized_evidence(fixture))

    class RevokingGateway:
        async def chat(self, *_args, **_kwargs):
            async with file_db() as other:
                run_id = await other.scalar(
                    select(ReportRun.id).where(ReportRun.status == "running")
                )
                assert run_id is not None
                assert (
                    await revoke_report_run(
                        other, run_id, report_runs_api.settings.dev_user_id
                    )
                    == 1
                )
            return "ANSWER GENERATED AFTER RUN REVOCATION"

    monkeypatch.setattr(report_runs_api, "orchestrator", planner)
    monkeypatch.setattr(report_runs_api, "model_gateway", RevokingGateway())

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000228"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "interrupted"
    assert response.json()["failure_category"] == "revoked"
    assert response.json()["result"] is None
    assert "ANSWER GENERATED AFTER RUN REVOCATION" not in response.text


@pytest.mark.asyncio
async def test_changed_intent_at_pre_execution_boundary_fails_invalid_definition(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    original_load = report_runs_api.load_validated_definition_scope
    calls = 0

    async def changed_second_load(*args, **kwargs):
        nonlocal calls
        calls += 1
        question, mode, scope = await original_load(*args, **kwargs)
        if calls == 2:
            question += " altered"
        return question, mode, scope

    class Forbidden:
        async def plan(self, **_kwargs):
            raise AssertionError("changed immutable intent must not orchestrate")

    monkeypatch.setattr(
        report_runs_api, "load_validated_definition_scope", changed_second_load
    )
    monkeypatch.setattr(report_runs_api, "orchestrator", Forbidden())

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000225"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert response.json()["failure_category"] == "invalid_definition"
    assert response.json()["result"] is None


@pytest.mark.asyncio
async def test_changed_intent_at_pre_persistence_boundary_discards_answer(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    original_load = report_runs_api.load_validated_definition_scope
    calls = 0
    planner = _PlanRecorder(_authorized_evidence(fixture))
    gateway = _AnswerGateway("ANSWER FOR STALE INTENT")

    async def changed_third_load(*args, **kwargs):
        nonlocal calls
        calls += 1
        question, mode, scope = await original_load(*args, **kwargs)
        if calls == 3:
            question += " altered in flight"
        return question, mode, scope

    monkeypatch.setattr(
        report_runs_api, "load_validated_definition_scope", changed_third_load
    )
    monkeypatch.setattr(report_runs_api, "orchestrator", planner)
    monkeypatch.setattr(report_runs_api, "model_gateway", gateway)

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000229"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert response.json()["failure_category"] == "invalid_definition"
    assert response.json()["result"] is None
    assert "ANSWER FOR STALE INTENT" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_site,expected_category",
    [("planning", "planning"), ("model", "model")],
)
async def test_execution_failures_are_bounded_without_raw_exception_leakage(
    file_db, client, monkeypatch, failure_site, expected_category
):
    fixture = await seed_definition(file_db)
    raw = "secret-uri://credential@internal/raw-stack-marker"

    class Planner:
        async def plan(self, **kwargs):
            if failure_site == "planning":
                raise RuntimeError(raw)
            return await _PlanRecorder(_authorized_evidence(fixture)).plan(**kwargs)

    class Gateway:
        async def chat(self, *_args, **_kwargs):
            raise RuntimeError(raw)

    monkeypatch.setattr(report_runs_api, "orchestrator", Planner())
    monkeypatch.setattr(report_runs_api, "model_gateway", Gateway())

    suffix = "226" if failure_site == "planning" else "227"
    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": f"00000000-0000-4000-8000-000000000{suffix}"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert response.json()["failure_category"] == expected_category
    assert response.json()["result"] is None
    assert raw not in response.text
