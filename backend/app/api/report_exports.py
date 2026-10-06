"""VS4-C2: governed bounded exports of persisted report results.

Explicit CSV and self-contained HTML downloads for an owned saved snapshot or a
terminal successful ReportRun. Every request reauthorizes current documents,
sources and table grants before reading the persisted payload. No generation,
retrieval, SQL planning/execution, model call, external fetch or refresh occurs
on export, and failures never echo evidence content.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.report_runs import _authorize_run_read, _owned_available_report
from app.api.reports import _available, _owned_report, _parse_evidence
from app.api.deps import require
from app.core.config import get_settings
from app.core.context import current_principal
from app.core.identity import Permission, Principal
from app.db import get_db
from app.services.export_render import (
    ExportError,
    render_csv,
    render_html,
    safe_filename,
)
from app.services.report_runs import get_report_run

router = APIRouter(prefix="/reports", tags=["report-exports"])
settings = get_settings()

_CSV_MEDIA = "text/csv; charset=utf-8"
_HTML_MEDIA = "text/html; charset=utf-8"
_HTML_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
_BASE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}

_PROVENANCE = (
    "Bounded export of an already-persisted result. A downloaded file cannot be "
    "recalled after later source revocation."
)


def _iso(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _attachment(filename: str, media_type: str, body: str, extra: dict | None = None) -> Response:
    headers = dict(_BASE_HEADERS)
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    if extra:
        headers.update(extra)
    return Response(content=body.encode("utf-8"), media_type=media_type, headers=headers)


def _respond(
    export_format: str,
    *,
    title: str,
    identity: list[tuple[str, str]],
    as_of: datetime | None,
    answer: str,
    evidence,
    index: int | None,
) -> Response:
    try:
        if export_format == "csv":
            body = render_csv(evidence, index=index)
            return _attachment(safe_filename(title, "export.csv"), _CSV_MEDIA, body)
        if export_format == "html":
            body = render_html(
                title=title,
                identity=identity,
                as_of=as_of,
                answer=answer,
                evidence=evidence,
                provenance_note=_PROVENANCE,
            )
            return _attachment(
                safe_filename(title, "export.html"),
                _HTML_MEDIA,
                body,
                {"Content-Security-Policy": _HTML_POLICY},
            )
    except ExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    raise HTTPException(status_code=404, detail="Unsupported export format")


@router.get("/{report_id}/exports/{export_format}")
async def export_saved_report(
    report_id: str,
    export_format: str,
    index: int | None = Query(default=None, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.REPORTS_EXPORT)),
) -> Response:
    """Export an owned saved snapshot after reauthorizing its current sources."""
    report = await _owned_report(db, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Saved report not found")
    if not await _available(db, report):
        raise HTTPException(
            status_code=409,
            detail="Saved report source is no longer available or authorized",
        )
    evidence = _parse_evidence(report.evidence_json)
    identity = [
        ("Report", report.id),
        ("Execution class", report.execution_class),
        ("Mode", report.requested_mode or "unavailable"),
        ("Snapshot as-of", _iso(report.snapshot_as_of)),
    ]
    return _respond(
        export_format,
        title=report.title,
        identity=identity,
        as_of=report.snapshot_as_of,
        answer=report.answer_text,
        evidence=evidence,
        index=index,
    )


@router.get("/{report_id}/runs/{run_id}/exports/{export_format}")
async def export_report_run(
    report_id: str,
    run_id: str,
    export_format: str,
    index: int | None = Query(default=None, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.REPORTS_EXPORT)),
) -> Response:
    """Export a terminal successful run's persisted result, never a fresh run."""
    run = await get_report_run(db, current_principal().user_id, run_id)
    if run is None or run.report_id != report_id:
        raise HTTPException(status_code=404, detail="Report run not found")
    report = await _owned_available_report(db, run.report_id)
    if run.status != "succeeded":
        raise HTTPException(
            status_code=409,
            detail="Report run has no exportable result",
        )
    result = await _authorize_run_read(db, run)
    if result is None or result.answer is None or not result.answer.strip():
        raise HTTPException(
            status_code=409,
            detail="Report run has no exportable result",
        )
    identity = [
        ("Report", report.id),
        ("Run", run.id),
        ("Definition version", str(run.definition_version)),
        ("Mode", run.requested_mode),
        ("Started", _iso(run.started_at)),
        ("Completed", _iso(run.finished_at)),
    ]
    return _respond(
        export_format,
        title=report.title,
        identity=identity,
        as_of=run.finished_at or run.started_at,
        answer=result.answer,
        evidence=result.evidence,
        index=index,
    )
