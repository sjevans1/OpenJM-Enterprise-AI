"""VS6 governed-action endpoints.

Every route is permission-guarded, and every response is derived from persisted
state. Nothing here can be used to execute a capability the registry does not
declare, and no write tool can run without a matching recorded approval.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require
from app.core.identity import Permission, Principal
from app.db import get_db
from app.models import ActionExecution, ActionPlan
from app.services.actions import runtime
from app.services.actions.builtin import registry

router = APIRouter(prefix="/actions", tags=["actions"])


class PlanRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    proposal: str | None = Field(default=None, max_length=20000)
    dry_run: bool = False
    conversation_id: str | None = Field(default=None, max_length=64)


class ApprovalRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=240)


class ExecuteRequest(BaseModel):
    dry_run: bool | None = None


class PlanOut(BaseModel):
    plan_id: str
    tenant_id: str
    role: str
    goal: str
    status: str
    dry_run: bool
    step_count: int
    max_steps: int
    budget_seconds: int
    plan_fingerprint: str
    expires_at: str | None
    steps: list[dict]
    created_at: str | None


def _plan_out(plan: ActionPlan) -> PlanOut:
    return PlanOut(
        plan_id=plan.id,
        tenant_id=plan.tenant_id,
        role=plan.role,
        goal=plan.goal_text,
        status=plan.status,
        dry_run=bool(plan.dry_run),
        step_count=plan.step_count,
        max_steps=plan.max_steps,
        budget_seconds=plan.budget_seconds,
        plan_fingerprint=plan.plan_fingerprint,
        expires_at=plan.expires_at.isoformat() if plan.expires_at else None,
        steps=json.loads(plan.steps_json),
        created_at=plan.created_at.isoformat() if plan.created_at else None,
    )


def _action_http(exc: runtime.ActionError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/tools")
async def list_tools(
    principal: Principal = Depends(require(Permission.ACTIONS_READ)),
):
    """The registry as the server sees it. Read-only, no execution."""
    return {"tools": registry.describe()}


@router.post("/plans", response_model=PlanOut)
async def create_plan(
    payload: PlanRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.ACTIONS_PLAN)),
):
    try:
        plan = await runtime.propose_plan(
            db,
            principal,
            goal=payload.goal,
            proposal=payload.proposal,
            dry_run=payload.dry_run,
            conversation_id=payload.conversation_id,
        )
    except runtime.ActionError as exc:
        raise _action_http(exc) from exc
    return _plan_out(plan)


@router.get("/plans/{plan_id}", response_model=PlanOut)
async def get_plan(
    plan_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.ACTIONS_READ)),
):
    try:
        plan = await runtime.load_plan(db, principal, plan_id)
    except runtime.ActionError as exc:
        raise _action_http(exc) from exc
    return _plan_out(plan)


@router.post("/plans/{plan_id}/steps/{step_index}/approve")
async def approve_step(
    plan_id: str,
    step_index: int,
    payload: ApprovalRequest | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.ACTIONS_APPROVE)),
):
    try:
        approval = await runtime.approve_step(
            db,
            principal,
            plan_id=plan_id,
            step_index=step_index,
            reason=payload.reason if payload else None,
        )
    except runtime.ActionError as exc:
        raise _action_http(exc) from exc
    return {
        "approval_id": approval.id,
        "step_index": approval.step_index,
        "tool": approval.tool_name,
        "status": approval.status,
        "action_fingerprint": approval.action_fingerprint,
    }


@router.post("/plans/{plan_id}/steps/{step_index}/reject")
async def reject_step(
    plan_id: str,
    step_index: int,
    payload: ApprovalRequest | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.ACTIONS_APPROVE)),
):
    try:
        approval = await runtime.reject_step(
            db,
            principal,
            plan_id=plan_id,
            step_index=step_index,
            reason=payload.reason if payload else None,
        )
    except runtime.ActionError as exc:
        raise _action_http(exc) from exc
    return {"approval_id": approval.id, "status": approval.status}


@router.post("/plans/{plan_id}/execute")
async def execute_plan(
    plan_id: str,
    payload: ExecuteRequest | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.ACTIONS_EXECUTE)),
):
    try:
        return await runtime.execute_plan(
            db,
            principal,
            plan_id=plan_id,
            dry_run=payload.dry_run if payload else None,
        )
    except runtime.ActionError as exc:
        raise _action_http(exc) from exc


@router.get("/executions")
async def list_executions(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.ACTIONS_READ)),
):
    """The append-only execution trail for this tenant."""
    rows = (
        (
            await db.execute(
                select(ActionExecution)
                .where(ActionExecution.tenant_id == principal.tenant_id)
                .order_by(ActionExecution.started_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "count": len(rows),
        "executions": [
            {
                "execution_id": r.id,
                "plan_id": r.plan_id,
                "step_index": r.step_index,
                "tool": r.tool_name,
                "operation_class": r.operation_class,
                "risk_level": r.risk_level,
                "status": r.status,
                "failure_category": r.failure_category,
                "dry_run": bool(r.dry_run),
                "principal_id": r.principal_id,
                "role": r.role,
                "approval_id": r.approval_id,
                "arguments": json.loads(r.arguments_json),
                "result": json.loads(r.result_json) if r.result_json else None,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ],
    }
