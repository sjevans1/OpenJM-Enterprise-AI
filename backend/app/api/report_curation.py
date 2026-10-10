"""BV6-A governed report curation HTTP surface.

Thin route layer over :mod:`app.services.report_curation`. Routes authenticate
and translate errors; the service owns the state machine, the source
re-authorization and the audit trail. No route exposes retrieval behaviour, a
ranking change or a stored SQL plan.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require
from app.core.identity import AuthorizationError, Permission, Principal
from app.db import get_db
from app.models import SavedReport
from app.schemas import (
    CurationFeatureRequest,
    CurationTransitionRequest,
    ReportCurationView,
)
from app.services import report_curation as curation

router = APIRouter(prefix="/reports", tags=["report-curation"])


def _view(report: SavedReport) -> ReportCurationView:
    return ReportCurationView(
        report_id=report.id,
        curation_state=report.curation_state or curation.CURATION_NONE,
        featured=bool(report.featured),
        department_id=report.curation_department_id,
        reason=report.curation_reason,
        updated_at=report.curation_updated_at,
    )


async def _load(db: AsyncSession, principal: Principal, report_id: str) -> SavedReport:
    report = await curation.load_report(db, principal=principal, report_id=report_id)
    if report is None:
        # A report outside the actor's tenant is reported as absent, never
        # revealed, so no foreign title, evidence or state leaks.
        raise HTTPException(status_code=404, detail="Saved report not found")
    return report


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, curation.CurationStateError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, curation.CurationError):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


@router.get("/{report_id}/curation", response_model=ReportCurationView)
async def get_curation(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.REPORTS_READ)),
):
    return _view(await _load(db, principal, report_id))


@router.post("/{report_id}/curation/request-review", response_model=ReportCurationView)
async def request_curation_review(
    report_id: str,
    payload: CurationTransitionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.REPORTS_READ)),
):
    report = await _load(db, principal, report_id)
    try:
        await curation.request_review(
            db, principal=principal, report=report, reason=payload.reason if payload else None
        )
    except Exception as exc:  # noqa: BLE001 - translated below
        raise _translate(exc) from exc
    await db.commit()
    return _view(report)


@router.post("/{report_id}/curation/approve", response_model=ReportCurationView)
async def approve_curation(
    report_id: str,
    payload: CurationTransitionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.REPORTS_READ)),
):
    report = await _load(db, principal, report_id)
    try:
        await curation.approve(
            db, principal=principal, report=report, reason=payload.reason if payload else None
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    await db.commit()
    return _view(report)


@router.post("/{report_id}/curation/authoritative", response_model=ReportCurationView)
async def mark_curation_authoritative(
    report_id: str,
    payload: CurationTransitionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.REPORTS_READ)),
):
    report = await _load(db, principal, report_id)
    try:
        await curation.mark_authoritative(
            db, principal=principal, report=report, reason=payload.reason if payload else None
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    await db.commit()
    return _view(report)


@router.post("/{report_id}/curation/feature", response_model=ReportCurationView)
async def feature_curation(
    report_id: str,
    payload: CurationFeatureRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.REPORTS_READ)),
):
    report = await _load(db, principal, report_id)
    try:
        await curation.set_featured(
            db,
            principal=principal,
            report=report,
            featured=payload.featured,
            reason=payload.reason,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    await db.commit()
    return _view(report)


@router.post("/{report_id}/curation/withdraw", response_model=ReportCurationView)
async def withdraw_curation(
    report_id: str,
    payload: CurationTransitionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.REPORTS_READ)),
):
    report = await _load(db, principal, report_id)
    try:
        await curation.withdraw(
            db, principal=principal, report=report, reason=payload.reason if payload else None
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    await db.commit()
    return _view(report)
