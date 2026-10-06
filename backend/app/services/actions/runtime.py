"""Bounded, server-controlled action runtime (VS6).

The model may *propose*; the server decides. Nothing in this module executes a
tool just because a plan named it.

Controls enforced here
----------------------
* **Registry bound** — a step naming an unregistered tool is refused.
* **Step bound** — a plan longer than ``max_steps`` is refused at creation.
* **Time bound** — each step gets its own timeout, and the plan has an overall
  budget checked before every step.
* **Permission bound** — permissions are validated at planning time *and again
  immediately before each step executes*, so a revocation in between fails
  closed. The plan also records a permissions fingerprint; if the principal's
  effective permissions changed after planning, execution refuses.
* **Approval bound** — a write tool needs an unconsumed approval bound to the
  exact action fingerprint. Changing any argument changes the fingerprint, so a
  stale approval cannot authorize a materially different action.
* **Replay bound** — each step carries an idempotency key with a uniqueness
  constraint, so a retry reuses the prior outcome instead of duplicating the
  external effect.
* **Evidence or abstain** — a step that cannot safely run is recorded as
  ``refused`` with a reason. The runtime never reports a success it did not
  observe.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.context import (
    current_principal_or_none,
    reset_principal,
    set_principal,
)
from app.core.identity import Permission, Principal
from app.models import ActionApproval, ActionExecution, ActionPlan
from app.services.actions.builtin import registry, serialize_result
from app.services.actions.registry import RegistryError
from app.services.identity import record_audit

settings = get_settings()


class ActionError(Exception):
    status_code = 400

    def __init__(self, message: str, *, code: str = "action_error", status: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        if status:
            self.status_code = status


def _require(principal: Principal, permission: Permission) -> None:
    """Permission check that reports through the action error surface."""
    if not principal.has(permission):
        raise ActionError(
            f"Principal {principal.principal_id} lacks required permission "
            f"'{permission.value}'",
            code="missing_permission",
            status=403,
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a stored datetime (SQLite returns naive UTC values)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_steps(steps: list[dict]) -> str:
    return json.dumps(
        [{"tool": s["tool"], "arguments": s.get("arguments", {})} for s in steps],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def plan_fingerprint(steps: list[dict]) -> str:
    return _hash(canonical_steps(steps))


def permissions_fingerprint(principal: Principal) -> str:
    return _hash(f"{principal.role}|{','.join(sorted(principal.permission_strings()))}")


def arguments_hash(arguments: dict) -> str:
    return _hash(json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str))


def action_fingerprint(tenant_id: str, tool: str, arguments: dict) -> str:
    """Bind an approval to one exact action, parameters included."""
    return _hash(f"{tenant_id}|{tool}|{arguments_hash(arguments)}")


def idempotency_key(
    tenant_id: str, plan_id: str, step_index: int, tool: str, arguments: dict
) -> str:
    return _hash(f"{tenant_id}|{plan_id}|{step_index}|{tool}|{arguments_hash(arguments)}")


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------


@dataclass
class ProposalStep:
    tool: str
    arguments: dict


def parse_proposal(text: str) -> list[ProposalStep]:
    """Extract a strict plan proposal from model output.

    Anything that is not a well-formed ``{"steps": [...]}`` object is refused.
    Free text can describe a plan, but it cannot become one.
    """
    if not text or not text.strip():
        raise ActionError("Empty plan proposal", code="empty_proposal")
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ActionError("Plan proposal is not a JSON object", code="malformed_proposal")
    try:
        payload = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ActionError("Plan proposal is not valid JSON", code="malformed_proposal") from exc
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        raise ActionError("Plan proposal has no steps list", code="malformed_proposal")
    steps: list[ProposalStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict) or "tool" not in raw:
            raise ActionError("Plan step is malformed", code="malformed_proposal")
        arguments = raw.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ActionError("Plan step arguments must be an object", code="malformed_proposal")
        steps.append(ProposalStep(tool=str(raw["tool"]), arguments=arguments))
    return steps


def deterministic_proposal(goal: str) -> list[ProposalStep]:
    """A rule-based planner for the built-in read tools.

    Bounded and predictable: it maps intent keywords to at most two read steps.
    It never proposes a write tool, because a write needs an explicit human
    decision rather than an inferred one.
    """
    text = (goal or "").lower()
    steps: list[ProposalStep] = []
    if any(w in text for w in ("report", "snapshot", "saved")):
        steps.append(ProposalStep("report.list", {}))
    if any(w in text for w in ("source", "data source", "table", "database", "finance")):
        steps.append(ProposalStep("data_source.list", {}))
    if any(w in text for w in ("audit", "who accessed", "trail")):
        steps.append(ProposalStep("audit.list", {"limit": 20}))
    if any(w in text for w in ("document", "knowledge", "policy", "search", "find")):
        words = [w for w in text.replace("?", " ").split() if len(w) > 3]
        query = words[-1] if words else "policy"
        steps.append(ProposalStep("knowledge.search", {"query": query}))
    if not steps:
        steps.append(ProposalStep("report.list", {}))
    return steps[:2]


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


async def propose_plan(
    db: AsyncSession,
    principal: Principal,
    *,
    goal: str,
    proposal: str | list[ProposalStep] | None = None,
    max_steps: int | None = None,
    dry_run: bool = False,
    conversation_id: str | None = None,
) -> ActionPlan:
    """Validate a proposal into a persisted, bounded plan."""
    if not settings.actions_enabled:
        raise ActionError(
            "The action runtime is disabled on this deployment",
            code="actions_disabled",
            status=503,
        )
    _require(principal, Permission.ACTIONS_PLAN)
    limit = max_steps or settings.action_max_steps

    if proposal is None:
        steps = deterministic_proposal(goal)
    elif isinstance(proposal, str):
        steps = parse_proposal(proposal)
    else:
        steps = list(proposal)

    if not steps:
        raise ActionError("A plan must contain at least one step", code="empty_proposal")
    if len(steps) > limit:
        raise ActionError(
            f"Plan has {len(steps)} steps, exceeding the limit of {limit}",
            code="too_many_steps",
        )

    validated: list[dict] = []
    for index, step in enumerate(steps):
        try:
            tool = registry.get(step.tool)
        except RegistryError as exc:
            raise ActionError(exc.message, code=exc.code) from exc
        try:
            arguments = tool.spec.validate_arguments(step.arguments)
        except RegistryError as exc:
            raise ActionError(
                f"Step {index} ({step.tool}): {exc.message}", code=exc.code
            ) from exc
        # Planning-time permission check. This is repeated immediately before
        # execution, so a revocation in between still fails closed.
        missing = [
            p for p in tool.spec.required_permissions if not principal.has(p)
        ]
        if missing:
            raise ActionError(
                f"Step {index} ({step.tool}) requires permission(s) {missing}",
                code="missing_permission",
                status=403,
            )
        validated.append({"tool": step.tool, "arguments": arguments})

    now = utcnow()
    plan = ActionPlan(
        tenant_id=principal.tenant_id,
        principal_id=principal.user_id,
        role=principal.role,
        conversation_id=conversation_id,
        goal_text=goal or "",
        steps_json=canonical_steps(validated),
        max_steps=limit,
        step_count=len(validated),
        budget_seconds=settings.action_budget_seconds,
        dry_run=dry_run,
        status="proposed",
        plan_fingerprint=plan_fingerprint(validated),
        permissions_fingerprint=permissions_fingerprint(principal),
        expires_at=now + timedelta(seconds=settings.action_plan_ttl_seconds),
    )
    db.add(plan)
    await db.flush()
    await record_audit(
        db,
        principal=principal,
        action="action.plan.proposed",
        decision="allow",
        resource_type="action_plan",
        resource_id=plan.id,
        metadata={"steps": [s["tool"] for s in validated]},
    )
    await db.commit()
    return plan


def plan_steps(plan: ActionPlan) -> list[dict]:
    return json.loads(plan.steps_json)


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


async def approve_step(
    db: AsyncSession,
    principal: Principal,
    *,
    plan_id: str,
    step_index: int,
    reason: str | None = None,
) -> ActionApproval:
    """Approve exactly one step, bound to its current parameters."""
    _require(principal, Permission.ACTIONS_APPROVE)
    plan = await load_plan(db, principal, plan_id)
    steps = plan_steps(plan)
    if step_index < 0 or step_index >= len(steps):
        raise ActionError("Unknown plan step", code="unknown_step", status=404)
    step = steps[step_index]
    tool = registry.get(step["tool"])

    if not tool.spec.is_write:
        raise ActionError(
            "Read steps do not require approval", code="approval_not_required"
        )
    # An approver must themselves hold what the action needs: approval cannot
    # be used to launder a capability the approver does not have.
    missing = [p for p in tool.spec.required_permissions if not principal.has(p)]
    if missing:
        raise ActionError(
            f"Approver lacks required permission(s) {missing}",
            code="missing_permission",
            status=403,
        )

    fingerprint = action_fingerprint(principal.tenant_id, step["tool"], step["arguments"])
    approval = ActionApproval(
        tenant_id=principal.tenant_id,
        plan_id=plan.id,
        step_index=step_index,
        tool_name=step["tool"],
        action_fingerprint=fingerprint,
        requested_by=plan.principal_id,
        decided_by=principal.principal_id,
        status="approved",
        reason=reason,
        expires_at=utcnow() + timedelta(seconds=settings.action_approval_ttl_seconds),
    )
    db.add(approval)
    await db.flush()
    await record_audit(
        db,
        principal=principal,
        action="action.approval.granted",
        decision="allow",
        resource_type="action_approval",
        resource_id=approval.id,
        metadata={"plan_id": plan.id, "step": step_index, "tool": step["tool"]},
    )
    await db.commit()
    return approval


async def reject_step(
    db: AsyncSession,
    principal: Principal,
    *,
    plan_id: str,
    step_index: int,
    reason: str | None = None,
) -> ActionApproval:
    _require(principal, Permission.ACTIONS_APPROVE)
    plan = await load_plan(db, principal, plan_id)
    steps = plan_steps(plan)
    if step_index < 0 or step_index >= len(steps):
        raise ActionError("Unknown plan step", code="unknown_step", status=404)
    step = steps[step_index]
    approval = ActionApproval(
        tenant_id=principal.tenant_id,
        plan_id=plan.id,
        step_index=step_index,
        tool_name=step["tool"],
        action_fingerprint=action_fingerprint(
            principal.tenant_id, step["tool"], step["arguments"]
        ),
        requested_by=plan.principal_id,
        decided_by=principal.principal_id,
        status="rejected",
        reason=reason,
    )
    db.add(approval)
    await db.flush()
    await record_audit(
        db,
        principal=principal,
        action="action.approval.rejected",
        decision="deny",
        resource_type="action_approval",
        resource_id=approval.id,
        reason=reason,
    )
    await db.commit()
    return approval


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


async def load_plan(db: AsyncSession, principal: Principal, plan_id: str) -> ActionPlan:
    plan = (
        await db.execute(
            select(ActionPlan).where(
                ActionPlan.id == plan_id,
                ActionPlan.tenant_id == principal.tenant_id,
                ActionPlan.principal_id == principal.user_id,
            )
        )
    ).scalar_one_or_none()
    if plan is None:
        raise ActionError("Action plan not found", code="plan_not_found", status=404)
    return plan


async def _consume_approval(
    db: AsyncSession, principal: Principal, plan: ActionPlan, step_index: int, step: dict
) -> tuple[ActionApproval | None, str | None]:
    """Atomically claim a matching approval, or explain why we cannot."""
    expected = action_fingerprint(principal.tenant_id, step["tool"], step["arguments"])
    candidate = (
        await db.execute(
            select(ActionApproval)
            .where(
                ActionApproval.tenant_id == principal.tenant_id,
                ActionApproval.plan_id == plan.id,
                ActionApproval.step_index == step_index,
                ActionApproval.status == "approved",
            )
            .order_by(ActionApproval.created_at.desc())
        )
    ).scalars().first()

    if candidate is None:
        return None, "no_approval"
    if candidate.action_fingerprint != expected:
        # The parameters changed after approval: the approval no longer applies.
        return None, "approval_does_not_match_action"
    expires_at = _as_utc(candidate.expires_at)
    if expires_at is not None and expires_at <= utcnow():
        return None, "approval_expired"

    result = await db.execute(
        update(ActionApproval)
        .where(ActionApproval.id == candidate.id, ActionApproval.status == "approved")
        .values(status="consumed", consumed_at=utcnow())
    )
    if result.rowcount != 1:
        return None, "approval_already_consumed"
    return candidate, None


async def _existing_execution(
    db: AsyncSession, tenant_id: str, key: str
) -> ActionExecution | None:
    return (
        await db.execute(
            select(ActionExecution).where(
                ActionExecution.tenant_id == tenant_id,
                ActionExecution.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()


async def _run_step(db: AsyncSession, principal: Principal, plan: ActionPlan, index: int, step: dict):
    tool = registry.get(step["tool"])
    spec = tool.spec
    arguments = step["arguments"]
    dry = bool(plan.dry_run)
    key = idempotency_key(principal.tenant_id, plan.id, index, spec.name, arguments)

    # Replay protection: a retry of an identical step reuses the prior outcome.
    existing = await _existing_execution(db, principal.tenant_id, key)
    if existing is not None:
        return {
            "step": index,
            "tool": spec.name,
            "status": existing.status,
            "reused": True,
            "result": json.loads(existing.result_json) if existing.result_json else None,
            "failure_category": existing.failure_category,
        }

    execution = ActionExecution(
        tenant_id=principal.tenant_id,
        plan_id=plan.id,
        step_index=index,
        principal_id=principal.user_id,
        role=principal.role,
        tool_name=spec.name,
        operation_class=spec.operation_class.value,
        risk_level=spec.risk_level.value,
        arguments_json=json.dumps(arguments, sort_keys=True, default=str),
        arguments_hash=arguments_hash(arguments),
        idempotency_key=key,
        dry_run=dry,
        status="started",
    )
    db.add(execution)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await _existing_execution(db, principal.tenant_id, key)
        return {
            "step": index,
            "tool": spec.name,
            "status": existing.status if existing else "failed",
            "reused": True,
            "result": json.loads(existing.result_json)
            if existing and existing.result_json
            else None,
            "failure_category": existing.failure_category if existing else "duplicate",
        }

    def _finish(status: str, *, result=None, failure=None, detail=None, approval_id=None):
        execution.status = status
        execution.failure_category = failure
        execution.detail = detail
        execution.approval_id = approval_id
        execution.result_json = serialize_result(result) if result is not None else None
        execution.finished_at = utcnow()

    # --- dry run: validate everything, change nothing ---
    if dry:
        _finish("dry_run", detail="Dry run: validated, not executed")
        await db.commit()
        return {
            "step": index,
            "tool": spec.name,
            "status": "dry_run",
            "reused": False,
            "would_execute": {"tool": spec.name, "arguments": arguments},
        }

    # --- approval gate for write tools ---
    approval_id = None
    if spec.approval_required:
        approval, refusal = await _consume_approval(db, principal, plan, index, step)
        if approval is None:
            _finish(
                "refused",
                failure=refusal,
                detail="Write action requires a matching, unconsumed human approval",
            )
            await db.commit()
            return {
                "step": index,
                "tool": spec.name,
                "status": "refused",
                "reused": False,
                "failure_category": refusal,
            }
        approval_id = approval.id

    # --- execute, bounded by the per-tool timeout ---
    try:
        result = await asyncio.wait_for(
            tool.handler(db, arguments), timeout=spec.timeout_seconds
        )
    except asyncio.TimeoutError:
        _finish("failed", failure="timeout", approval_id=approval_id)
        await db.commit()
        return {
            "step": index,
            "tool": spec.name,
            "status": "failed",
            "reused": False,
            "failure_category": "timeout",
        }
    except Exception as exc:  # noqa: BLE001
        _finish(
            "failed",
            failure="tool_error",
            detail=str(exc)[:500],
            approval_id=approval_id,
        )
        await db.commit()
        return {
            "step": index,
            "tool": spec.name,
            "status": "failed",
            "reused": False,
            "failure_category": "tool_error",
        }

    if isinstance(result, dict) and result.get("ok") is False:
        _finish(
            "failed",
            failure=result.get("failure_category") or "tool_refused",
            detail=result.get("detail"),
            result=result,
            approval_id=approval_id,
        )
        await db.commit()
        return {
            "step": index,
            "tool": spec.name,
            "status": "failed",
            "reused": False,
            "result": result,
            "failure_category": execution.failure_category,
        }

    _finish("succeeded", result=result, approval_id=approval_id)
    await db.commit()
    return {
        "step": index,
        "tool": spec.name,
        "status": "succeeded",
        "reused": False,
        "result": result,
    }


async def execute_plan(
    db: AsyncSession, principal: Principal, *, plan_id: str, dry_run: bool | None = None
) -> dict:
    """Execute a persisted plan under every bound described in the module docstring."""
    if not settings.actions_enabled:
        raise ActionError(
            "The action runtime is disabled on this deployment",
            code="actions_disabled",
            status=503,
        )
    _require(principal, Permission.ACTIONS_EXECUTE)

    # Tenant context cannot be switched mid-execution.
    ambient = current_principal_or_none()
    if ambient is not None and ambient.tenant_id != principal.tenant_id:
        raise ActionError(
            "Tenant context cannot change during execution", code="tenant_switch_denied"
        )
    set_principal(principal)

    try:
        plan = await load_plan(db, principal, plan_id)

        if plan.status in ("succeeded", "failed", "rejected"):
            return {
                "plan_id": plan.id,
                "status": plan.status,
                "already_final": True,
                "steps": [],
            }

        plan_expires = _as_utc(plan.expires_at)
        if plan_expires is not None and plan_expires <= utcnow():
            plan.status = "expired"
            await db.commit()
            raise ActionError("Action plan has expired", code="plan_expired", status=410)

        # A plan carries the permission context it was created under. If that
        # no longer matches, the plan is refused rather than partially applied.
        current_fingerprint = permissions_fingerprint(principal)
        if current_fingerprint != plan.permissions_fingerprint:
            plan.status = "rejected"
            await record_audit(
                db,
                principal=principal,
                action="action.plan.rejected",
                decision="deny",
                resource_type="action_plan",
                resource_id=plan.id,
                reason="permissions changed after planning",
            )
            await db.commit()
            raise ActionError(
                "Permissions changed after this plan was created; it must be re-planned",
                code="permissions_changed",
                status=403,
            )

        steps = plan_steps(plan)
        deadline = utcnow() + timedelta(seconds=plan.budget_seconds)
        if dry_run is not None:
            plan.dry_run = dry_run
        plan.status = "executing"
        await db.commit()

        outcomes: list[dict] = []
        for index, step in enumerate(steps):
            if utcnow() >= deadline:
                outcomes.append(
                    {
                        "step": index,
                        "tool": step["tool"],
                        "status": "refused",
                        "failure_category": "budget_exhausted",
                    }
                )
                continue

            try:
                tool = registry.get(step["tool"])
            except RegistryError as exc:
                outcomes.append(
                    {
                        "step": index,
                        "tool": step["tool"],
                        "status": "refused",
                        "failure_category": exc.code,
                    }
                )
                continue

            # Revalidate immediately before execution. This is the check that
            # makes a revocation between planning and acting fail closed.
            missing = [p for p in tool.spec.required_permissions if not principal.has(p)]
            if missing:
                outcomes.append(
                    {
                        "step": index,
                        "tool": step["tool"],
                        "status": "refused",
                        "failure_category": "missing_permission",
                    }
                )
                await record_audit(
                    db,
                    principal=principal,
                    action="action.step.refused",
                    decision="deny",
                    resource_type="action_plan",
                    resource_id=plan.id,
                    reason=f"missing permissions {missing}",
                )
                await db.commit()
                continue

            # A step may also re-check tenant ownership of any resource it names.
            if tool.spec.tenant_scoped and "arguments" in step:
                pass  # handlers apply owned_by(...) themselves

            outcome = await _run_step(db, principal, plan, index, step)
            outcomes.append(outcome)
            await record_audit(
                db,
                principal=principal,
                action=f"action.step.{outcome['status']}",
                decision="allow" if outcome["status"] in ("succeeded", "dry_run") else "deny",
                resource_type="action_plan",
                resource_id=plan.id,
                metadata={"tool": outcome["tool"], "step": index},
            )
            await db.commit()

        statuses = {o["status"] for o in outcomes}
        if "refused" in statuses:
            plan.status = "rejected"
        elif "failed" in statuses:
            plan.status = "failed"
        elif statuses <= {"dry_run"}:
            plan.status = "proposed"
        else:
            plan.status = "succeeded"
        await db.commit()

        return {
            "plan_id": plan.id,
            "tenant_id": plan.tenant_id,
            "status": plan.status,
            "dry_run": bool(plan.dry_run),
            "steps": outcomes,
        }
    finally:
        reset_principal()
