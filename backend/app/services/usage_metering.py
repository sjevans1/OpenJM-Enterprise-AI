"""Immutable LLM usage metering (#46 M1).

Records one append-only row per model invocation. This layer captures what was
consumed; it contains no pricing, entitlement or billing logic.

Exactly-once finalization: the row key is
``<request_id>:<call_role>:<attempt>``. A retried or replayed request resolves to
the existing row instead of inserting a second one, so a retry cannot double
count. A retry/fallback of the same request is a *separate* row linked by
``parent_call_id``, so a multi-call request stays attributable without being
collapsed.

Provider-reported usage is captured when the provider returns it. A local or
private-hosted model that returns none is metered from a deterministic estimate
and marked ``estimated``: it is never presented as provider-grade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage import (
    USAGE_STATUS_FAILED,
    USAGE_STATUS_SUCCEEDED,
    UsageCallRole,
    UsageSource,
    estimate_tokens,
)
from app.models import ModelUsageEvent


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ModelCallUsage:
    """Token usage for one invocation, provider-reported or estimated."""

    input_tokens: int
    output_tokens: int
    provider_reported: bool
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def usage_source(self) -> str:
        return (
            UsageSource.PROVIDER_REPORTED.value
            if self.provider_reported
            else UsageSource.ESTIMATED.value
        )

    @classmethod
    def estimate(cls, prompt_text: str, completion_text: str) -> "ModelCallUsage":
        return cls(
            input_tokens=estimate_tokens(prompt_text),
            output_tokens=estimate_tokens(completion_text),
            provider_reported=False,
        )

    @classmethod
    def from_provider_usage(cls, usage: object) -> "ModelCallUsage | None":
        """Parse an OpenAI-compatible ``usage`` object, or None when absent.

        Malformed or partial usage returns None so the caller falls back to an
        explicit estimate rather than trusting an unclear provider payload.
        """
        if not isinstance(usage, dict):
            return None
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if (
            not isinstance(prompt, int)
            or not isinstance(completion, int)
            or prompt < 0
            or completion < 0
        ):
            return None
        cached = _nested_int(usage, "prompt_tokens_details", "cached_tokens")
        reasoning = _nested_int(usage, "completion_tokens_details", "reasoning_tokens")
        return cls(
            input_tokens=prompt,
            output_tokens=completion,
            provider_reported=True,
            cached_input_tokens=cached,
            reasoning_tokens=reasoning,
        )


def _nested_int(payload: dict, parent: str, key: str) -> int | None:
    child = payload.get(parent)
    if not isinstance(child, dict):
        return None
    value = child.get(key)
    return value if isinstance(value, int) and value >= 0 else None


@dataclass(frozen=True)
class UsageContext:
    """Attribution for one model invocation."""

    tenant_id: str
    request_id: str
    provider_route: str
    model_name: str
    principal_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    execution_class: str | None = None


def _idempotency_key(context: UsageContext, call_role: str, attempt: int) -> str:
    return f"{context.request_id}:{call_role}:{attempt}"


async def record_model_usage(
    db: AsyncSession,
    *,
    context: UsageContext,
    usage: ModelCallUsage | None = None,
    call_role: str = UsageCallRole.PRIMARY.value,
    attempt: int = 0,
    status: str = USAGE_STATUS_SUCCEEDED,
    failure_category: str | None = None,
    parent_call_id: str | None = None,
    prompt_text: str = "",
    completion_text: str = "",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> ModelUsageEvent | None:
    """Finalize one model invocation, exactly once.

    Returns the existing row when the same (request, role, attempt) was already
    finalized, so a retry or a replayed request cannot create a second row. When
    no usage is supplied and no provider usage was available, an estimate is
    derived from the prompt/completion text and marked ``estimated``.
    """
    if not context.tenant_id or not context.request_id:
        return None

    key = _idempotency_key(context, call_role, attempt)
    existing = (
        await db.execute(
            select(ModelUsageEvent).where(ModelUsageEvent.idempotency_key == key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    resolved = usage
    if resolved is None:
        resolved = ModelCallUsage.estimate(prompt_text, completion_text)

    now = utcnow()
    event = ModelUsageEvent(
        tenant_id=context.tenant_id,
        principal_id=context.principal_id,
        request_id=context.request_id,
        conversation_id=context.conversation_id,
        message_id=context.message_id,
        execution_class=context.execution_class,
        provider_route=context.provider_route,
        model_name=context.model_name,
        usage_source=resolved.usage_source,
        input_tokens=resolved.input_tokens,
        output_tokens=resolved.output_tokens,
        total_tokens=resolved.total_tokens,
        cached_input_tokens=resolved.cached_input_tokens,
        reasoning_tokens=resolved.reasoning_tokens,
        call_role=call_role,
        parent_call_id=parent_call_id,
        idempotency_key=key,
        status=status if status in (USAGE_STATUS_SUCCEEDED, USAGE_STATUS_FAILED) else USAGE_STATUS_SUCCEEDED,
        failure_category=failure_category,
        started_at=started_at or now,
        finished_at=finished_at or now,
    )
    db.add(event)
    try:
        await db.flush()
    except IntegrityError:
        # Concurrent finalization of the same key: keep the winner.
        await db.rollback()
        return (
            await db.execute(
                select(ModelUsageEvent).where(ModelUsageEvent.idempotency_key == key)
            )
        ).scalar_one_or_none()
    return event


async def summarize_usage(
    db: AsyncSession, *, tenant_id: str, request_id: str | None = None
) -> dict:
    """Totals for one tenant, optionally narrowed to one parent request.

    Tenant-scoped by construction: the caller can never total another tenant.
    """
    query = select(
        func.count(ModelUsageEvent.id),
        func.coalesce(func.sum(ModelUsageEvent.input_tokens), 0),
        func.coalesce(func.sum(ModelUsageEvent.output_tokens), 0),
        func.coalesce(func.sum(ModelUsageEvent.total_tokens), 0),
    ).where(ModelUsageEvent.tenant_id == tenant_id)
    if request_id is not None:
        query = query.where(ModelUsageEvent.request_id == request_id)
    events, input_tokens, output_tokens, total_tokens = (
        await db.execute(query)
    ).one()
    return {
        "events": int(events or 0),
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "total_tokens": int(total_tokens or 0),
    }
