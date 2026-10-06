"""VS4-B2C2 Phase 3 complete immutable report results."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.api import report_runs as report_runs_api
from app.models import (
    Conversation,
    ExecutionTrace,
    Message,
    ReportDefinitionVersion,
    ReportRun,
    SavedReport,
)
from app.schemas import Evidence
from app.services.orchestrator import ExecutionPlan, OpenJMOrchestrator
from app.services.structured_planner import StructuredPlan, StructuredPlanningResult
from app.services.tools import ToolResult

from conftest import seed_definition


class _AnswerGateway:
    async def chat(self, _messages, **_kwargs):
        return "Authoritative result synthesized."


def _structured_evidence(
    fixture, *, evidence_id="data-evidence", sql="SELECT revenue FROM finance"
):
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


async def _set_definition_mode(file_db, fixture, mode):
    execution_class = {
        "knowledge": "knowledge",
        "data": "structured",
        "hybrid": "hybrid",
    }[mode]
    async with file_db() as db:
        definition = await db.get(ReportDefinitionVersion, fixture["definition_id"])
        report = await db.get(SavedReport, fixture["report_id"])
        assistant = await db.get(Message, fixture["assistant_id"])
        user = (
            (
                await db.execute(
                    select(Message).where(
                        Message.conversation_id == report.conversation_id,
                        Message.role == "user",
                    )
                )
            )
            .scalars()
            .one()
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
    db,
    *,
    run_id,
    tool_name,
    mode=None,
    source_id=None,
    sql=None,
    evidence_ids=None,
    user_id=None,
    conversation_id=None,
    status="succeeded",
    metadata=None,
):
    mode = mode or ("data" if tool_name == "structured.query" else "knowledge")
    trace = ExecutionTrace(
        request_id=run_id,
        tool_invocation_id=f"inv-{tool_name}",
        user_id=user_id or report_runs_api.settings.dev_user_id,
        conversation_id=conversation_id,
        route="hybrid"
        if mode == "hybrid"
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
        evidence_ids_json=(
            __import__("json").dumps(evidence_ids) if evidence_ids is not None else None
        ),
        metadata_json=(
            __import__("json").dumps(metadata) if metadata is not None else None
        ),
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


@pytest.mark.asyncio
async def test_data_report_persists_authoritative_typed_output_and_bound_trace_without_chat_rows(
    file_db, client, monkeypatch
):
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
    async with file_db() as db:
        conversations_before = await db.scalar(
            select(func.count()).select_from(Conversation)
        )
        messages_before = await db.scalar(select(func.count()).select_from(Message))

    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000301"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["structured_result"]["rows"] == [["325.10"]]
    assert body["result"]["structured_result"]["rows"] != [["narrative-preview"]]
    assert body["result"]["trace_ids"]
    async with file_db() as db:
        assert (
            await db.scalar(select(func.count()).select_from(Conversation))
            == conversations_before
        )
        assert (
            await db.scalar(select(func.count()).select_from(Message))
            == messages_before
        )
        trace = await db.get(ExecutionTrace, body["result"]["trace_ids"][0])
        assert trace.request_id == body["id"]
        assert trace.conversation_id is None
        assert trace.status == "succeeded"


@pytest.mark.asyncio
async def test_structured_orchestrator_carries_tool_output_and_trace_ids(
    monkeypatch, session
):
    evidence = Evidence(
        evidence_id="e-1",
        source_type="structured_query",
        source_id="source-1",
        title="Finance",
        passage="preview differs",
        provenance={"executed_sql": "SELECT amount FROM finance"},
        metadata={
            "sql": "SELECT amount FROM finance",
            "columns": ["amount"],
            "row_count": 1,
        },
    )

    async def fake_plan(*_args, **_kwargs):
        return StructuredPlanningResult(
            candidate=True,
            plan=StructuredPlan(
                source_id="source-1",
                sql="SELECT amount FROM finance",
                rationale="test",
            ),
        )

    async def fake_execute(*_args, **_kwargs):
        return ToolResult(
            evidence=[evidence],
            output={
                "columns": ["amount"],
                "rows": [[Decimal("1.20")]],
                "row_count": 1,
                "truncated": False,
            },
            trace_ids=["trace-1"],
        )

    from app.services import orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module.structured_planner, "plan", fake_plan)
    monkeypatch.setattr(orchestrator_module.tool_registry, "execute", fake_execute)
    plan = await OpenJMOrchestrator()._execute_structured_plan(
        "amount", session, "owner", None, "data", request_id="run-1"
    )

    assert plan.structured_result == {
        "source_id": "source-1",
        "evidence_id": "e-1",
        "sql": "SELECT amount FROM finance",
        "columns": ["amount"],
        "rows": [[Decimal("1.20")]],
        "row_count": 1,
        "truncated": False,
    }
    assert plan.trace_ids == ["trace-1"]


@pytest.mark.asyncio
async def test_knowledge_report_requires_and_persists_authorized_document_evidence(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")
    evidence = _document_evidence(fixture)

    class Planner:
        async def plan(self, **kwargs):
            trace_id = await _add_trace(
                kwargs["db"],
                run_id=kwargs["request_id"],
                tool_name="knowledge.search",
                evidence_ids=[evidence.evidence_id],
            )
            return ExecutionPlan(
                execution_class="knowledge",
                system_prompt="Use document evidence.",
                evidence=[evidence],
                requested_mode="knowledge",
                trace_ids=[trace_id],
            )

    monkeypatch.setattr(report_runs_api, "orchestrator", Planner())
    monkeypatch.setattr(report_runs_api, "model_gateway", _AnswerGateway())
    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": "00000000-0000-4000-8000-000000000302"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "succeeded"
    assert response.json()["result"]["structured_result"] == {}
    assert response.json()["result"]["evidence"][0]["source_id"] == evidence.source_id


@pytest.mark.asyncio
@pytest.mark.parametrize("dependent", [False, True], ids=["independent", "dependent"])
async def test_complete_hybrid_report_persists_both_evidence_classes_and_structured_output(
    file_db, client, monkeypatch, dependent
):
    fixture = await seed_definition(file_db)
    document = _document_evidence(fixture)
    structured = _structured_evidence(fixture)
    if dependent:
        structured = structured.model_copy(
            update={
                "provenance": {
                    **structured.provenance,
                    "grounded_parameter": {
                        "evidence_id": document.evidence_id,
                        "source_id": document.source_id,
                        "operator": ">",
                        "fiscal_year": 2025,
                        "currency": "USD",
                    },
                }
            }
        )

    class Planner:
        async def plan(self, **kwargs):
            knowledge_trace = await _add_trace(
                kwargs["db"],
                run_id=kwargs["request_id"],
                tool_name="knowledge.search",
                mode="hybrid",
                evidence_ids=[document.evidence_id],
            )
            structured_trace = await _add_trace(
                kwargs["db"],
                run_id=kwargs["request_id"],
                tool_name="structured.query",
                mode="hybrid",
                source_id=structured.source_id,
                sql=structured.metadata["sql"],
                evidence_ids=[structured.evidence_id],
                metadata=(
                    {"grounded_parameter": structured.provenance["grounded_parameter"]}
                    if dependent
                    else None
                ),
            )
            return ExecutionPlan(
                execution_class="hybrid",
                system_prompt="Use both evidence classes.",
                evidence=[document, structured],
                requested_mode="hybrid",
                structured_result={
                    "source_id": structured.source_id,
                    "evidence_id": structured.evidence_id,
                    "sql": structured.metadata["sql"],
                    "columns": ["revenue"],
                    "rows": [[325]],
                    "row_count": 1,
                    "truncated": False,
                },
                trace_ids=[knowledge_trace, structured_trace],
            )

    monkeypatch.setattr(report_runs_api, "orchestrator", Planner())
    monkeypatch.setattr(report_runs_api, "model_gateway", _AnswerGateway())
    suffix = "303" if dependent else "304"
    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": f"00000000-0000-4000-8000-000000000{suffix}"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "succeeded"
    assert {item["source_type"] for item in body["result"]["evidence"]} == {
        "document",
        "structured_query",
    }
    assert body["result"]["structured_result"]["rows"] == [[325]]
    assert len(body["result"]["trace_ids"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "direct_refusal",
        "empty_evidence",
        "partial_hybrid",
        "missing_output",
        "mismatched_output",
        "wrong_width",
        "unsupported_scalar",
        "oversized_scalar",
        "tampered_dependent",
        "missing_trace",
        "failed_trace",
        "foreign_trace",
        "trace_evidence_mismatch",
        "trace_grounding_mismatch",
    ],
)
async def test_incomplete_or_tampered_hybrid_is_terminal_failure_without_answer_or_data(
    file_db, client, monkeypatch, case
):
    fixture = await seed_definition(file_db)
    document = _document_evidence(fixture)
    structured = _structured_evidence(fixture)
    if case == "tampered_dependent":
        structured = structured.model_copy(
            update={
                "provenance": {
                    **structured.provenance,
                    "grounded_parameter": {
                        "evidence_id": "not-the-retrieved-evidence",
                        "source_id": document.source_id,
                        "operator": ">",
                        "fiscal_year": 2025,
                        "currency": "USD",
                    },
                }
            }
        )
    gateway_calls = []

    class Gateway:
        async def chat(self, *_args, **_kwargs):
            gateway_calls.append(True)
            return "MUST NOT PERSIST"

    class Planner:
        async def plan(self, **kwargs):
            knowledge_trace = await _add_trace(
                kwargs["db"],
                run_id=kwargs["request_id"],
                tool_name="knowledge.search",
                mode="hybrid",
                evidence_ids=[document.evidence_id],
            )
            structured_trace = await _add_trace(
                kwargs["db"],
                run_id=kwargs["request_id"],
                tool_name="structured.query",
                mode="hybrid",
                source_id=structured.source_id,
                sql=structured.metadata["sql"],
                evidence_ids=(
                    ["other-evidence"]
                    if case == "trace_evidence_mismatch"
                    else [structured.evidence_id]
                ),
                status="failed" if case == "failed_trace" else "succeeded",
                user_id="other-owner" if case == "foreign_trace" else None,
                metadata=(
                    {
                        "grounded_parameter": {
                            **structured.provenance.get("grounded_parameter", {}),
                            "currency": "JMD",
                        }
                    }
                    if case == "trace_grounding_mismatch"
                    else None
                ),
            )
            evidence = [document, structured]
            output = {
                "source_id": structured.source_id,
                "evidence_id": structured.evidence_id,
                "sql": structured.metadata["sql"],
                "columns": ["revenue"],
                "rows": [[325]],
                "row_count": 1,
                "truncated": False,
            }
            direct_answer = None
            trace_ids = [knowledge_trace, structured_trace]
            if case == "direct_refusal":
                direct_answer = "Could not safely answer."
            elif case == "empty_evidence":
                evidence = []
            elif case == "partial_hybrid":
                evidence = [document]
            elif case == "missing_output":
                output = None
            elif case == "mismatched_output":
                output["evidence_id"] = "different-evidence"
            elif case == "wrong_width":
                output["rows"] = [[325, 400]]
            elif case == "unsupported_scalar":
                output["rows"] = [[{"nested": "object"}]]
            elif case == "oversized_scalar":
                output["rows"] = [
                    ["x" * (report_runs_api.MAX_STRUCTURED_SCALAR_CHARS + 1)]
                ]
            elif case == "missing_trace":
                trace_ids = [knowledge_trace]
            return ExecutionPlan(
                execution_class="hybrid",
                system_prompt="must not synthesize",
                evidence=evidence,
                direct_answer=direct_answer,
                requested_mode="hybrid",
                structured_result=output,
                trace_ids=trace_ids,
            )

    monkeypatch.setattr(report_runs_api, "orchestrator", Planner())
    monkeypatch.setattr(report_runs_api, "model_gateway", Gateway())
    number = 320 + [
        "direct_refusal",
        "empty_evidence",
        "partial_hybrid",
        "missing_output",
        "mismatched_output",
        "wrong_width",
        "unsupported_scalar",
        "oversized_scalar",
        "tampered_dependent",
        "missing_trace",
        "failed_trace",
        "foreign_trace",
        "trace_evidence_mismatch",
        "trace_grounding_mismatch",
    ].index(case)
    response = await client.post(
        f"/api/reports/{fixture['report_id']}/definitions/1/runs",
        json={"idempotency_key": f"00000000-0000-4000-8000-{number:012d}"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_category"] in {"incomplete_result", "result_too_large"}
    assert body["result"] is None
    assert gateway_calls == []
    async with file_db() as db:
        run = await db.get(ReportRun, body["id"])
        assert run.result_json is None


@pytest.mark.asyncio
async def test_terminal_complete_result_replay_is_byte_immutable_and_does_no_work(
    file_db, client, monkeypatch
):
    fixture = await seed_definition(file_db)
    await _set_definition_mode(file_db, fixture, "knowledge")
    evidence = _document_evidence(fixture)
    calls = []

    class Planner:
        async def plan(self, **kwargs):
            calls.append("plan")
            trace_id = await _add_trace(
                kwargs["db"],
                run_id=kwargs["request_id"],
                tool_name="knowledge.search",
                evidence_ids=[evidence.evidence_id],
            )
            return ExecutionPlan(
                execution_class="knowledge",
                system_prompt="bounded",
                evidence=[evidence],
                requested_mode="knowledge",
                trace_ids=[trace_id],
            )

    class Gateway:
        async def chat(self, *_args, **_kwargs):
            calls.append("model")
            return "Immutable answer"

    monkeypatch.setattr(report_runs_api, "orchestrator", Planner())
    monkeypatch.setattr(report_runs_api, "model_gateway", Gateway())
    path = f"/api/reports/{fixture['report_id']}/definitions/1/runs"
    payload = {"idempotency_key": "00000000-0000-4000-8000-000000000305"}
    first = await client.post(path, json=payload)
    async with file_db() as db:
        persisted_before = (await db.get(ReportRun, first.json()["id"])).result_json

    replay = await client.post(path, json=payload)
    async with file_db() as db:
        persisted_after = (await db.get(ReportRun, first.json()["id"])).result_json

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    assert persisted_after == persisted_before
    assert calls == ["plan", "model"]
