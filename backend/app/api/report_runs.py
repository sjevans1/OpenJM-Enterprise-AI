"""VS4-B2C1: owner-scoped, read-only report-run history + revocation API.

Runs are immutable execution-history rows. This router only exposes:
  * a bounded, owner-scoped list of a report's runs (read-only),
  * the bounded persisted result envelope of a completed run (read-only),
  * owner-initiated cancellation of a *running* run (running -> interrupted).
No route here ever executes a report or writes a terminal result.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db import get_db
from app.models import ReportRun, SavedReport
from app.schemas import ReportRunDetail, ReportRunResult, ReportRunSummary
from app.services.report_runs import (
    _parse_trace_ids,
    get_report_run,
    list_report_runs,
    revoke_report_run,
)

router = APIRouter(prefix="/reports", tags=["report-runs"])
settings = get_settings()

MAX_RESULT_BYTES = 65536


async def _owned_report_or_404(db: AsyncSession, report_id: str) -> SavedReport:
    report = (
        await db.execute(
            select(SavedReport).where(
                SavedReport.id == report_id,
                SavedReport.user_id == settings.dev_user_id,
            )
        )
    ).scalars().first()
    if report is None:
        raise HTTPException(status_code=404, detail="Saved report not found")
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


async def _detail(db: AsyncSession, run: ReportRun) -> ReportRunDetail:
    summary = _summary(run)
    result = None
    if run.status == "succeeded" and run.result_json is not None:
        if len(run.result_json.encode("utf-8")) > MAX_RESULT_BYTES:
            raise HTTPException(status_code=422, detail="Persisted result envelope is oversized")
        try:
            result = ReportRunResult.model_validate_json(run.result_json)
        except ValueError:
            raise HTTPException(status_code=422, detail="Persisted result envelope is malformed") from None
    return ReportRunDetail(**summary.model_dump(), result=result)


@router.get("/{report_id}/runs", response_model=list[ReportRunSummary])
async def list_runs(
    report_id: str,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100000),
    db: AsyncSession = Depends(get_db),
):
    """Read-only, owner-scoped history of a saved report's execution runs."""
    await _owned_report_or_404(db, report_id)
    runs = await list_report_runs(
        db, settings.dev_user_id, report_id, limit=limit, offset=offset
    )
    return [_summary(run) for run in runs]


@router.get("/runs/{run_id}", response_model=ReportRunDetail)
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """Read-only detail for one owned run, including its bounded result envelope."""
    run = await get_report_run(db, settings.dev_user_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Report run not found")
    return await _detail(db, run)


@router.post("/runs/{run_id}/revoke", response_model=ReportRunSummary)
async def revoke_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """Owner-initiated cancellation of a running run; terminal runs are immutable."""
    revoked = await revoke_report_run(db, run_id, settings.dev_user_id)
    if revoked == 0:
        run = await get_report_run(db, settings.dev_user_id, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Report run not found")
        raise HTTPException(
            status_code=409,
            detail=f"Report run is {run.status} and cannot be revoked",
        )
    refreshed = await get_report_run(db, settings.dev_user_id, run_id)
    return _summary(refreshed)
