"""Owner-scoped report-run execution and read-only history.

Runs are immutable execution-history rows. This router exposes one bounded
reservation POST plus owner-scoped history reads:
  * a bounded, owner-scoped list of a report's runs,
  * the bounded persisted result envelope of a completed run.
Source availability/authorization is re-checked on every read and fails
closed with 409, consistently with saved reports and B2A definitions.
Newly owned reservations execute inline through the existing governed orchestrator.
"""
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
from app.models import ReportDefinitionVersion, ReportRun, SavedReport
from app.schemas import (
    CreateReportRunRequest,
    ReportRunDetail,
    ReportRunResult,
    ReportRunSummary,
)
from app.services.report_runs import (
    MAX_RESULT_BYTES,
    ReportRunConflict,
    _parse_trace_ids,
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
) -> ReportRunDetail:
    """Execute one newly owned reservation across two current-authority gates."""
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
        )
    except ReportScopeError:
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="authorization"
        )
    except Exception:
        return await _fail_owned_run(
            db, run_id=run_id, fingerprint=fingerprint, category="planning"
        )

    if plan.direct_answer is not None:
        answer = plan.direct_answer
    else:
        try:
            answer = await model_gateway.chat(
                [
                    {"role": "system", "content": plan.system_prompt},
                    {"role": "user", "content": question},
                ],
                max_tokens=2048,
            )
        except ModelGatewayError:
            return await _fail_owned_run(
                db, run_id=run_id, fingerprint=fingerprint, category="model"
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

    persisted = await finalize_success(
        db=db,
        run_id=run_id,
        user_id=settings.dev_user_id,
        fingerprint=fingerprint,
        answer=answer,
        evidence=[item.model_dump(mode="json") for item in plan.evidence],
        structured_result=None,
        trace_ids=None,
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
    return ReportRunSummary(
        id=run.id,
        report_id=run.report_id,
        definition_version=run.definition_version,
        requested_mode=run.requested_mode,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
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
            raise HTTPException(status_code=422, detail="Persisted result envelope is oversized")
        try:
            result = ReportRunResult.model_validate_json(run.result_json)
        except ValueError:
            raise HTTPException(status_code=422, detail="Persisted result envelope is malformed") from None
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
        raise HTTPException(status_code=409, detail="Report run definition no longer matches")

    result = _persisted_result(run)
    if result is None:
        return None
    if not (1 <= len(result.evidence) <= 24):
        raise HTTPException(status_code=422, detail="Persisted result envelope has invalid evidence")
    for item in result.evidence:
        if item.source_type not in {"document", "structured_query"} or not item.source_id:
            raise HTTPException(status_code=422, detail="Persisted result envelope has invalid evidence")
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
        raise HTTPException(status_code=409, detail="Report run evidence is outside its definition")
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
        raise HTTPException(status_code=409, detail="Report run reservation conflict") from exc
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
