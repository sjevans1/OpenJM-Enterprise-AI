"""Owner-scoped report-run execution and read-only history.

Runs are immutable execution-history rows. This router exposes one bounded
reservation POST plus owner-scoped history reads:
  * a bounded, owner-scoped list of a report's runs,
  * the bounded persisted result envelope of a completed run.
Source availability/authorization is re-checked on every read and fails
closed with 409, consistently with saved reports and B2A definitions.
Newly owned reservations execute inline through the existing governed orchestrator.
"""

import json
import math
from datetime import date, datetime, time, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.report_definitions import load_validated_definition_scope
from app.api.reports import (
    _available,
    _owned_report,
    _source_ids,
    _sources_available,
    _structured_tables,
)
from app.core.config import get_settings
from app.db import get_db
from app.models import ExecutionTrace, ReportDefinitionVersion, ReportRun, SavedReport
from app.schemas import (
    CreateReportRunRequest,
    ReportRunDetail,
    ReportRunResult,
    ReportRunSummary,
)
from app.services.report_runs import (
    MAX_RESULT_BYTES,
    BudgetExceeded,
    ReportRunBudget,
    ReportRunConflict,
    _parse_trace_ids,
    _validate_result,
    finalize_failure,
    finalize_success,
    get_report_run,
    list_report_runs,
    reserve_report_run,
)
from app.services.model_gateway import ModelGatewayError, OpenAICompatibleModelGateway
from app.services.orchestrator import orchestrator
from app.services.report_scope import ReportScopeError, ReportSourceScope

router = APIRouter(prefix="/reports", tags=["report-runs"])
settings = get_settings()
model_gateway = OpenAICompatibleModelGateway()

MAX_STRUCTURED_COLUMNS = 64
MAX_STRUCTURED_COLUMN_CHARS = 256
MAX_STRUCTURED_SCALAR_CHARS = 12000


class IncompleteReportResult(ValueError):
    """A report plan is not a complete, provenance-bound result."""


class OversizedReportResult(IncompleteReportResult):
    """A typed structured value exceeds a report-result bound."""


def _json_safe_scalar(value):
    """Preserve scalar meaning without lossy ``default=str`` serialization."""
    if value is None or isinstance(value, (bool, int, str)):
        normalized = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise IncompleteReportResult("non-finite structured scalar")
        normalized = value
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise IncompleteReportResult("non-finite structured scalar")
        normalized = str(value)
    elif isinstance(value, (datetime, date, time)):
        normalized = value.isoformat()
    else:
        raise IncompleteReportResult("unsupported structured scalar")
    if isinstance(normalized, str) and len(normalized) > MAX_STRUCTURED_SCALAR_CHARS:
        raise OversizedReportResult("structured scalar exceeds bound")
    return normalized


def _validated_structured_result(
    plan, structured_evidence: list, scope: ReportSourceScope
):
    raw = plan.structured_result
    required = {
        "source_id",
        "evidence_id",
        "sql",
        "columns",
        "rows",
        "row_count",
        "truncated",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise IncompleteReportResult("structured output envelope is malformed")
    source_id = raw["source_id"]
    evidence_id = raw["evidence_id"]
    sql = raw["sql"]
    columns = raw["columns"]
    rows = raw["rows"]
    if (
        not isinstance(source_id, str)
        or source_id not in scope.source_ids
        or not isinstance(evidence_id, str)
        or not evidence_id
        or not isinstance(sql, str)
        or not sql.strip()
        or not isinstance(columns, list)
        or not (1 <= len(columns) <= MAX_STRUCTURED_COLUMNS)
        or any(
            not isinstance(column, str)
            or not column
            or len(column) > MAX_STRUCTURED_COLUMN_CHARS
            for column in columns
        )
        or len(set(columns)) != len(columns)
        or not isinstance(rows, list)
        or not (1 <= len(rows) <= settings.structured_max_rows)
        or isinstance(raw["row_count"], bool)
        or not isinstance(raw["row_count"], int)
        or raw["row_count"] != len(rows)
        or not isinstance(raw["truncated"], bool)
    ):
        raise IncompleteReportResult("structured output envelope is inconsistent")
    matches = [
        item
        for item in structured_evidence
        if item.evidence_id == evidence_id and item.source_id == source_id
    ]
    if len(matches) != 1:
        raise IncompleteReportResult("structured output does not match evidence")
    evidence = matches[0]
    evidence_sql = evidence.metadata.get("sql") or evidence.provenance.get(
        "executed_sql"
    )
    if (
        evidence_sql != sql
        or evidence.metadata.get("columns") != columns
        or evidence.metadata.get("row_count") != raw["row_count"]
        or evidence.metadata.get("truncated", False) != raw["truncated"]
    ):
        raise IncompleteReportResult("structured output provenance is inconsistent")
    normalized_rows = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != len(columns):
            raise IncompleteReportResult("structured row width is inconsistent")
        normalized_rows.append([_json_safe_scalar(value) for value in row])
    return {**raw, "rows": normalized_rows}


def _validate_dependent_provenance(
    document_evidence: list, structured_evidence: list
) -> None:
    """Check only cross-result provenance; AST/tool validation remains authoritative."""
    for item in structured_evidence:
        grounded = item.provenance.get("grounded_parameter")
        if grounded is None:
            continue
        if not isinstance(grounded, dict):
            raise IncompleteReportResult("grounded provenance is malformed")
        required = ("evidence_id", "source_id", "operator", "fiscal_year", "currency")
        if any(key not in grounded for key in required):
            raise IncompleteReportResult("grounded provenance is incomplete")
        matched = [
            evidence
            for evidence in document_evidence
            if evidence.evidence_id == grounded["evidence_id"]
            and evidence.source_id == grounded["source_id"]
        ]
        if (
            len(matched) != 1
            or grounded["operator"] not in {">", ">="}
            or isinstance(grounded["fiscal_year"], bool)
            or not isinstance(grounded["fiscal_year"], int)
            or not 2000 <= grounded["fiscal_year"] < 2100
            or grounded["currency"] not in {"USD", "JMD"}
        ):
            raise IncompleteReportResult(
                "grounded provenance does not match document evidence"
            )


async def _validated_trace_ids(db, *, plan, run_id: str, mode: str, structured_result):
    trace_ids = plan.trace_ids
    expected_names = {
        "knowledge": ["knowledge.search"],
        "data": ["structured.query"],
        "hybrid": ["knowledge.search", "structured.query"],
    }[mode]
    if (
        not isinstance(trace_ids, list)
        or len(trace_ids) != len(expected_names)
        or len(set(trace_ids)) != len(trace_ids)
        or any(not isinstance(item, str) or not item for item in trace_ids)
    ):
        raise IncompleteReportResult("report traces are incomplete")
    traces = list(
        (
            await db.execute(
                select(ExecutionTrace).where(ExecutionTrace.id.in_(trace_ids))
            )
        )
        .scalars()
        .all()
    )
    if len(traces) != len(trace_ids):
        raise IncompleteReportResult("report trace is missing")
    allowed_route = (
        "hybrid"
        if mode == "hybrid"
        else ("structured" if mode == "data" else "knowledge")
    )
    if sorted(trace.tool_name for trace in traces) != sorted(expected_names):
        raise IncompleteReportResult("report trace tools are invalid")
    document_evidence_ids = sorted(
        item.evidence_id for item in plan.evidence if item.source_type == "document"
    )
    structured_evidence = next(
        (
            item
            for item in plan.evidence
            if item.source_type == "structured_query"
            and structured_result is not None
            and item.evidence_id == structured_result["evidence_id"]
        ),
        None,
    )
    structured_evidence_id = (
        structured_result["evidence_id"] if structured_result is not None else None
    )
    structured_source_id = (
        structured_result["source_id"] if structured_result is not None else None
    )
    structured_sql = structured_result["sql"] if structured_result is not None else None
    for trace in traces:
        if (
            trace.id not in trace_ids
            or trace.request_id != run_id
            or trace.user_id != settings.dev_user_id
            or trace.conversation_id is not None
            or trace.status != "succeeded"
            or trace.route != allowed_route
            or trace.requested_mode != mode
        ):
            raise IncompleteReportResult("report trace is outside this run")
        try:
            trace_evidence_ids = json.loads(trace.evidence_ids_json or "null")
        except (TypeError, ValueError):
            raise IncompleteReportResult("report trace evidence is malformed") from None
        expected_evidence_ids = (
            document_evidence_ids
            if trace.tool_name == "knowledge.search"
            else [structured_evidence_id]
        )
        if (
            not isinstance(trace_evidence_ids, list)
            or sorted(trace_evidence_ids) != expected_evidence_ids
        ):
            raise IncompleteReportResult("report trace evidence does not match output")
        if trace.tool_name == "structured.query" and (
            structured_evidence_id is None
            or trace.source_id != structured_source_id
            or trace.executed_sql != structured_sql
        ):
            raise IncompleteReportResult("structured trace does not match output")
        if trace.tool_name == "structured.query":
            try:
                trace_metadata = (
                    json.loads(trace.metadata_json) if trace.metadata_json else {}
                )
            except (TypeError, ValueError):
                raise IncompleteReportResult(
                    "structured trace metadata is malformed"
                ) from None
            evidence_grounded = (
                structured_evidence.provenance.get("grounded_parameter")
                if structured_evidence is not None
                else None
            )
            if trace_metadata.get("grounded_parameter") != evidence_grounded:
                raise IncompleteReportResult(
                    "grounded trace provenance does not match evidence"
                )
    return trace_ids


async def _complete_report_result(
    db, *, plan, run_id: str, mode: str, scope: ReportSourceScope
):
    expected_class = {
        "knowledge": "knowledge",
        "data": "structured",
        "hybrid": "hybrid",
    }[mode]
    if (
        plan.execution_class != expected_class
        or plan.requested_mode != mode
        or plan.direct_answer is not None
    ):
        raise IncompleteReportResult(
            "report plan is a refusal or wrong execution class"
        )
    documents = [item for item in plan.evidence if item.source_type == "document"]
    structured = [
        item for item in plan.evidence if item.source_type == "structured_query"
    ]
    if any(
        item.source_type not in {"document", "structured_query"}
        for item in plan.evidence
    ):
        raise IncompleteReportResult("report evidence type is invalid")
    if any(not item.evidence_id or not item.source_id for item in plan.evidence):
        raise IncompleteReportResult("report evidence identity is missing")
    if mode in {"knowledge", "hybrid"} and (
        not documents
        or any(item.source_id not in scope.document_ids for item in documents)
    ):
        raise IncompleteReportResult("authorized document evidence is incomplete")
    if mode in {"data", "hybrid"} and not structured:
        raise IncompleteReportResult("structured evidence is incomplete")
    structured_result = None
    if mode in {"data", "hybrid"}:
        structured_result = _validated_structured_result(plan, structured, scope)
    elif plan.structured_result is not None:
        raise IncompleteReportResult("knowledge report contains structured output")
    if mode == "hybrid":
        _validate_dependent_provenance(documents, structured)
    trace_ids = await _validated_trace_ids(
        db, plan=plan, run_id=run_id, mode=mode, structured_result=structured_result
    )
    return structured_result, trace_ids


def _same_execution_intent(
    question: str,
    mode: str,
    scope: ReportSourceScope,
    refreshed: tuple[str, str, ReportSourceScope],
) -> bool:
    """Compare the complete trusted immutable intent at an execution boundary."""
    return refreshed == (question, mode, scope)


async def _reload_execution_intent(
    db: AsyncSession,
    *,
    report_id: str,
    definition_version: int,
    definition_id: str,
) -> tuple[str, str, ReportSourceScope]:
    """Force a current authority read rather than reusing identity-map state."""
    db.expire_all()
    return await load_validated_definition_scope(
        db,
        report_id,
        definition_version,
        settings.dev_user_id,
        definition_id=definition_id,
    )


async def _failed_execution_detail(
    db: AsyncSession,
    run_id: str,
) -> ReportRunDetail:
    """Return bounded terminal metadata only; never expose generated payloads."""
    run = await get_report_run(db, settings.dev_user_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Report run not found")
    return ReportRunDetail(**_summary(run).model_dump(), result=None)


async def _fail_owned_run(
    db: AsyncSession,
    *,
    run_id: str,
    fingerprint: str,
    category: str,
) -> ReportRunDetail:
    await finalize_failure(
        db=db,
        run_id=run_id,
        user_id=settings.dev_user_id,
        fingerprint=fingerprint,
        failure_category=category,
    )
    return await _failed_execution_detail(db, run_id)


async def _execute_owned_run(
    db: AsyncSession,
    *,
    run_id: str,
    report_id: str,
    definition_id: str,
    definition_version: int,
    fingerprint: str,
    question: str,
    mode: str,
    scope: ReportSourceScope,
    started_at: datetime,
) -> ReportRunDetail:
    """Execute one newly owned reservation across two current-authority gates."""
    budget = ReportRunBudget(started_at=started_at)
    budget.check_wall(datetime.now(timezone.utc))
    try:
        refreshed = await _reload_execution_intent(
            db,
            report_id=report_id,
            definition_version=definition_version,
            definition_id=definition_id,
        )
    except HTTPException as exc:
        category = "invalid_definition" if exc.status_code == 404 else "authorization"
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category=category
        )
    if not _same_execution_intent(question, mode, scope, refreshed):
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="invalid_definition"
        )

    try:
        plan = await orchestrator.plan(
            message=question,
            db=db,
            user_id=settings.dev_user_id,
            conversation_id=None,
            mode=mode,
            scope=scope,
            request_id=run_id,
            budget=budget,
        )
    except ReportScopeError:
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="authorization"
        )
    except BudgetExceeded:
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="budget_exceeded"
        )
    except Exception:
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="planning"
        )

    try:
        structured_result, trace_ids = await _complete_report_result(
            db, plan=plan, run_id=run_id, mode=mode, scope=scope
        )
    except OversizedReportResult:
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="result_too_large"
        )
    except (IncompleteReportResult, KeyError, TypeError, ValueError):
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="incomplete_result"
        )

    try:
        answer = await model_gateway.chat(
            [
                {"role": "system", "content": plan.system_prompt},
                {"role": "user", "content": question},
            ],
            max_tokens=2048,
            budget=budget,
        )
    except ModelGatewayError:
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="model"
        )
    except BudgetExceeded:
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="budget_exceeded"
        )
    except Exception:
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="model"
        )

    try:
        refreshed = await _reload_execution_intent(
            db,
            report_id=report_id,
            definition_version=definition_version,
            definition_id=definition_id,
        )
    except HTTPException as exc:
        category = "invalid_definition" if exc.status_code == 404 else "authorization"
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category=category
        )
    if not _same_execution_intent(question, mode, scope, refreshed):
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="invalid_definition"
        )

    evidence_payload = [item.model_dump(mode="json") for item in plan.evidence]
    try:
        _validate_result(answer, evidence_payload, structured_result, trace_ids)
    except (TypeError, ValueError):
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="result_too_large"
        )

    persisted = await finalize_success(
        db=db,
        run_id=run_id,
        user_id=settings.dev_user_id,
        fingerprint=fingerprint,
        answer=answer,
        evidence=evidence_payload,
        structured_result=structured_result,
        trace_ids=trace_ids,
    )
    if persisted != 1:
        # A concurrent revocation wins the lifecycle compare-and-set. Never
        # deliver the generated answer when this reservation no longer runs.
        current = await get_report_run(db, settings.dev_user_id, run_id)
        if current is not None and current.status == "running":
            return await _fail_owned_run(
                db, run_id=run_id, fingerprint=fingerprint, category="internal"
            )
        return await _failed_execution_detail(db, run_id)

    completed = await get_report_run(db, settings.dev_user_id, run_id)
    if completed is None:
        raise HTTPException(status_code=404, detail="Report run not found")
    return await _detail(db, completed)


async def _owned_available_report(db: AsyncSession, report_id: str) -> SavedReport:
    """Owner check plus current source authorization; 404 then 409 fail closed."""
    report = await _owned_report(db, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Saved report not found")
    if not await _available(db, report):
        raise HTTPException(
            status_code=409,
            detail="Report source is no longer available or authorized",
        )
    return report


def _summary(run: ReportRun) -> ReportRunSummary:
    # SQLite DateTime(timezone=True) may round-trip as naive; normalize
    # both timestamps so API output is always tz-aware and consistent.
    started_at = run.started_at
    if started_at is not None and started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    finished_at = run.finished_at
    if finished_at is not None and finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    return ReportRunSummary(
        id=run.id,
        report_id=run.report_id,
        definition_version=run.definition_version,
        requested_mode=run.requested_mode,
        status=run.status,
        started_at=started_at,
        finished_at=finished_at,
        failure_category=run.failure_category,
        result_size_bytes=run.result_size_bytes,
        trace_count=len(_parse_trace_ids(run.trace_ids_json)),
    )


def _persisted_result(run: ReportRun) -> ReportRunResult | None:
    result = None
    if run.status == "succeeded" and run.result_json is not None:
        # The read bound is the SAME bound the writer enforced at persistence
        # time; anything larger in storage is corrupt/injected and refused.
        if len(run.result_json.encode("utf-8")) > MAX_RESULT_BYTES:
            raise HTTPException(
                status_code=422, detail="Persisted result envelope is oversized"
            )
        try:
            result = ReportRunResult.model_validate_json(run.result_json)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="Persisted result envelope is malformed"
            ) from None
    return result


async def _authorize_run_read(
    db: AsyncSession,
    run: ReportRun,
) -> ReportRunResult | None:
    """Revalidate the run's exact definition and its own persisted evidence."""
    try:
        _question, mode, scope = await load_validated_definition_scope(
            db,
            run.report_id,
            run.definition_version,
            run.user_id,
            definition_id=run.definition_id,
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=409,
                detail="Report run definition is no longer valid",
            ) from None
        raise
    if mode != run.requested_mode:
        raise HTTPException(
            status_code=409, detail="Report run definition no longer matches"
        )

    result = _persisted_result(run)
    if result is None:
        return None
    if not (1 <= len(result.evidence) <= 24):
        raise HTTPException(
            status_code=422, detail="Persisted result envelope has invalid evidence"
        )
    for item in result.evidence:
        if (
            item.source_type not in {"document", "structured_query"}
            or not item.source_id
        ):
            raise HTTPException(
                status_code=422, detail="Persisted result envelope has invalid evidence"
            )
    document_ids, source_ids = _source_ids(result.evidence)
    structured_tables = _structured_tables(result.evidence)
    if (
        not document_ids.issubset(scope.document_ids)
        or not source_ids.issubset(scope.source_ids)
        or any(
            not tables.issubset(scope.tables_for(source_id))
            for source_id, tables in structured_tables.items()
        )
    ):
        raise HTTPException(
            status_code=409, detail="Report run evidence is outside its definition"
        )
    if not await _sources_available(db, result.evidence):
        raise HTTPException(
            status_code=409,
            detail="Report run evidence is no longer available or authorized",
        )
    return result


async def _detail(db: AsyncSession, run: ReportRun) -> ReportRunDetail:
    summary = _summary(run)
    result = await _authorize_run_read(db, run)
    return ReportRunDetail(**summary.model_dump(), result=result)


@router.post(
    "/{report_id}/definitions/{version}/runs",
    response_model=ReportRunDetail,
    status_code=202,
)
async def submit_run(
    report_id: str,
    version: int,
    request: CreateReportRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """Reserve an explicitly versioned run and execute only a newly owned row."""
    definition_id = (
        await db.execute(
            select(ReportDefinitionVersion.id)
            .join(SavedReport, SavedReport.id == ReportDefinitionVersion.report_id)
            .where(
                ReportDefinitionVersion.report_id == report_id,
                ReportDefinitionVersion.version == version,
                ReportDefinitionVersion.user_id == settings.dev_user_id,
                SavedReport.user_id == settings.dev_user_id,
            )
        )
    ).scalar_one_or_none()
    if definition_id is None:
        raise HTTPException(status_code=404, detail="Report definition not found")

    if not settings.report_runs_enabled:
        raise HTTPException(
            status_code=424,
            detail="Report-run execution is not enabled",
        )

    question, mode, scope = await load_validated_definition_scope(
        db,
        report_id,
        version,
        settings.dev_user_id,
        definition_id=definition_id,
    )
    pinned_documents = sorted(scope.document_ids)
    pinned_tables = {
        source_id: sorted(tables) for source_id, tables in scope.source_tables
    }
    try:
        run, owns_execution = await reserve_report_run(
            db=db,
            report_id=report_id,
            definition_version=version,
            idempotency_key=request.idempotency_key,
            requested_mode=mode,
            question=question,
            pinned_document_ids=pinned_documents,
            pinned_source_tables=pinned_tables,
        )
    except ReportRunConflict as exc:
        raise HTTPException(
            status_code=409, detail="Report run reservation conflict"
        ) from exc
    if owns_execution:
        return await _execute_owned_run(
            db,
            run_id=run.id,
            report_id=run.report_id,
            definition_id=run.definition_id,
            definition_version=run.definition_version,
            fingerprint=run.request_fingerprint,
            question=question,
            mode=mode,
            scope=scope,
            started_at=run.started_at,
        )
    return await _detail(db, run)


@router.get("/{report_id}/runs", response_model=list[ReportRunSummary])
async def list_runs(
    report_id: str,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100000),
    db: AsyncSession = Depends(get_db),
):
    """Read-only, owner-scoped history of a saved report's execution runs."""
    await _owned_available_report(db, report_id)
    runs = await list_report_runs(
        db, settings.dev_user_id, report_id, limit=limit, offset=offset
    )
    for run in runs:
        await _authorize_run_read(db, run)
    return [_summary(run) for run in runs]


@router.get("/runs/{run_id}", response_model=ReportRunDetail)
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """Read-only detail for one owned run, including its bounded result envelope."""
    run = await get_report_run(db, settings.dev_user_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Report run not found")
    # Authorization is enforced for the run's own parent report, not the
    # caller's guess; revoked sources mask the result exactly like saved reports.
    await _owned_available_report(db, run.report_id)
    return await _detail(db, run)
