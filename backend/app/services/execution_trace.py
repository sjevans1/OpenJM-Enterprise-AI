import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExecutionTrace


def _json_default(value: Any) -> str:
    return f"<{value.__class__.__name__}>"


def hash_tool_input(payload: dict[str, Any]) -> str:
    """Hash tool input for audit without persisting raw request payloads."""
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def start_execution_trace(
    db: AsyncSession,
    *,
    request_id: str,
    tool_invocation_id: str,
    user_id: str,
    conversation_id: str | None,
    route: str | None,
    tool_name: str,
    operation_class: str,
    risk_level: str,
    requires_approval: bool,
    model_name: str | None,
    payload: dict[str, Any],
    planned_sql: str | None = None,
) -> ExecutionTrace:
    trace = ExecutionTrace(
        request_id=request_id,
        tool_invocation_id=tool_invocation_id,
        user_id=user_id,
        conversation_id=conversation_id,
        route=route,
        tool_name=tool_name,
        operation_class=operation_class,
        risk_level=risk_level,
        requires_approval=requires_approval,
        model_name=model_name,
        input_hash=hash_tool_input(payload),
        planned_sql=planned_sql,
        status="started",
    )
    db.add(trace)
    await db.commit()
    await db.refresh(trace)
    return trace


async def complete_execution_trace(
    db: AsyncSession,
    trace: ExecutionTrace,
    *,
    status: str,
    source_id: str | None = None,
    executed_sql: str | None = None,
    validation_decision: str | None = None,
    policy_decision: str | None = None,
    row_limit: int | None = None,
    duration_ms: int | None = None,
    error_class: str | None = None,
    evidence_ids: list[str] | None = None,
    processing_location: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    trace.status = status
    trace.source_id = source_id
    trace.executed_sql = executed_sql
    trace.validation_decision = validation_decision
    trace.policy_decision = policy_decision
    trace.row_limit = row_limit
    trace.duration_ms = duration_ms
    trace.error_class = error_class
    trace.evidence_ids_json = (
        json.dumps(evidence_ids) if evidence_ids is not None else None
    )
    trace.processing_location = processing_location
    trace.metadata_json = (
        json.dumps(metadata, sort_keys=True, default=_json_default)
        if metadata
        else None
    )
    trace.completed_at = datetime.now(timezone.utc)
    await db.commit()
