"""One-to-one usage attribution beside an M1 ledger event.

The sidecar never modifies the M1 row and never invents an attribution for a
historical event: a legacy row simply has no sidecar, and M2 reports those as
explicitly unattributed. Recording is deliberately strict:

* the sidecar tenant must equal the M1 event tenant and the routing decision
  tenant, otherwise the write fails closed;
* the relation is one to one, so a replay returns the existing row only when it
  is the same attempt, and any other conflict is refused;
* a failure here is a recording failure and must surface to the caller rather
  than being swallowed into an unattributed success.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.inference import (
    AttributionError,
    ExecutionCertainty,
    NormalizedTiming,
    NormalizedUsage,
    ResolvedDeployment,
    RoutingDecision,
    UsageCompleteness,
)
from app.models import InferenceUsageAttribution, ModelUsageEvent


@dataclass(frozen=True)
class ApprovalContext:
    """The caller-side identity of one logical call.

    These identifiers are server generated. ``business_request_id`` covers one
    turn or workflow operation, ``logical_call_id`` one purpose inside it and
    ``attempt_id`` one actual dispatch.
    """

    business_request_id: str
    logical_call_id: str
    attempt_id: str
    parent_attempt_id: str | None = None


async def attribute_usage_event(
    db: AsyncSession,
    *,
    usage_event: ModelUsageEvent,
    context: ApprovalContext,
    decision: RoutingDecision,
    usage: NormalizedUsage,
    timing: NormalizedTiming | None = None,
    target: ResolvedDeployment | None = None,
    certainty: str = ExecutionCertainty.UNKNOWN.value,
    completeness: str | None = None,
) -> InferenceUsageAttribution:
    """Write the sidecar for one finalized M1 event, or return the existing one.

    Must be called inside the same transaction that finalized the M1 event, so an
    attribution failure rolls back with the usage record instead of leaving a
    silently unattributed success behind.
    """
    if usage_event.tenant_id != decision.tenant_id:
        raise AttributionError("usage event tenant does not match the routing decision tenant")

    if target is not None:
        if decision.deployment_id != target.deployment_id:
            raise AttributionError("routing decision and resolved deployment disagree")
        if target.owner_tenant_id is not None and target.owner_tenant_id != usage_event.tenant_id:
            raise AttributionError("deployment is not owned by the usage event tenant")

    existing = (
        await db.execute(
            select(InferenceUsageAttribution).where(
                InferenceUsageAttribution.usage_event_id == usage_event.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # A replay of the same attempt is idempotent; anything else is a conflict
        # and must fail closed rather than overwrite history.
        if existing.attempt_id == context.attempt_id:
            return existing
        raise AttributionError("usage event already has a different attribution")

    row = InferenceUsageAttribution(
        usage_event_id=usage_event.id,
        tenant_id=usage_event.tenant_id,
        business_request_id=context.business_request_id,
        logical_call_id=context.logical_call_id,
        attempt_id=context.attempt_id,
        parent_attempt_id=context.parent_attempt_id,
        routing_decision_id=decision.decision_id or None,
        deployment_id=decision.deployment_id,
        deployment_revision=decision.deployment_revision,
        model_release_id=decision.model_release_id,
        runtime_profile_id=decision.runtime_profile_id,
        installation_id=target.installation_id if target is not None else None,
        site_id=target.site_id if target is not None else None,
        inference_mode=target.inference_mode if target is not None else None,
        execution_certainty=certainty,
        completeness=completeness or usage.completeness or UsageCompleteness.UNKNOWN.value,
        count_method=usage.count_method,
        tokenizer_revision=usage.tokenizer_revision,
        gateway_queue_ms=timing.gateway_queue_ms if timing else None,
        runtime_queue_ms=timing.runtime_queue_ms if timing else None,
        service_latency_ms=timing.service_latency_ms if timing else None,
        ttft_ms=timing.ttft_ms if timing else None,
        generation_ms=timing.generation_ms if timing else None,
        output_tokens_per_second=timing.output_tokens_per_second if timing else None,
        gpu_seconds=timing.gpu_seconds if timing else None,
        gpu_method=timing.gpu_method if timing else None,
    )
    db.add(row)
    await db.flush()
    return row


async def attribution_for_event(
    db: AsyncSession, *, usage_event_id: str, tenant_id: str
) -> InferenceUsageAttribution | None:
    """Read the sidecar for one event, tenant scoped.

    A cross tenant read returns nothing rather than another tenant's attribution.
    """
    return (
        await db.execute(
            select(InferenceUsageAttribution).where(
                InferenceUsageAttribution.usage_event_id == usage_event_id,
                InferenceUsageAttribution.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()


__all__ = ["ApprovalContext", "attribute_usage_event", "attribution_for_event"]
