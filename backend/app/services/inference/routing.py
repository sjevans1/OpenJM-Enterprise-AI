"""Deterministic, fail-closed routing to an INF1 deployment.

The resolver performs the accepted priority order and returns either a resolved
deployment plus an immutable decision record, or a denial. Every check denies by
default: an unknown, missing, revoked or stale input never becomes an allowance.

Two properties matter more than the feature set:

* Tenant identity is always the server resolved value on the request. No field a
  caller could set selects another tenant's deployment, and a caller can never
  enumerate or infer one.
* A decision is not an authorization token. It is re-evaluated immediately
  before every dispatch, so a revocation or a policy change blocks the next
  call rather than a cached decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.inference import (
    INFERENCE_MODES,
    LOCAL_ONLY_MODES,
    UNQUALIFIED_MODES,
    DeploymentLifecycle,
    InferenceDenied,
    InferenceRequest,
    ResolvedDeployment,
    RouteReason,
    RoutingDecision,
    ensure_aware,
    utc_now,
)
from app.models import (
    InferenceDeployment,
    InferenceHealthObservation,
    InferenceModelRelease,
    InferenceRoutingDecision,
    InferenceRuntimeProfile,
    InferenceTenantBinding,
    Tenant,
)

# A conservative characters-per-token ratio used only to prove that a request
# cannot fit. It may refuse a request that would in fact fit; it must never admit
# one that cannot, so it is deliberately pessimistic rather than clever.
CONSERVATIVE_CHARS_PER_TOKEN = 2


@dataclass(frozen=True)
class RoutingOutcome:
    """Either an authorized target or a denial. Never both, never neither."""

    decision: RoutingDecision
    target: ResolvedDeployment | None

    @property
    def allowed(self) -> bool:
        return self.target is not None

    def require(self) -> ResolvedDeployment:
        if self.target is None:
            raise InferenceDenied(self.decision.reason)
        return self.target


def _json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _estimate_request_tokens(request: InferenceRequest) -> int:
    """A deliberately pessimistic upper bound on the prompt size.

    Character count divided by a small ratio over-counts tokens, so an accepted
    request is comfortably inside the window. An uncertain estimate is never
    allowed to *prove* a hard context bound fits in the optimistic direction.
    """
    characters = 0
    for message in request.messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            characters += len(content)
        role = message.get("role") if isinstance(message, dict) else None
        if isinstance(role, str):
            characters += len(role)
    return characters // CONSERVATIVE_CHARS_PER_TOKEN


async def _active_tenant(db: AsyncSession, tenant_id: str) -> bool:
    tenant = await db.get(Tenant, tenant_id)
    return tenant is not None and tenant.status == "active"


async def _binding_for(
    db: AsyncSession, *, tenant_id: str, alias: str
) -> InferenceTenantBinding | None:
    return (
        await db.execute(
            select(InferenceTenantBinding).where(
                InferenceTenantBinding.tenant_id == tenant_id,
                InferenceTenantBinding.rahkia_alias == alias,
            )
        )
    ).scalar_one_or_none()


async def _release_for_alias(
    db: AsyncSession, *, alias: str
) -> InferenceModelRelease | None:
    """The newest active release for an alias. A retired release is not offered."""
    return (
        await db.execute(
            select(InferenceModelRelease)
            .where(
                InferenceModelRelease.rahkia_alias == alias,
                InferenceModelRelease.status == "active",
            )
            .order_by(InferenceModelRelease.release_version.desc())
        )
    ).scalars().first()


async def _fresh_health(
    db: AsyncSession, *, deployment_id: str, revision: int, now: datetime
) -> InferenceHealthObservation | None:
    row = (
        await db.execute(
            select(InferenceHealthObservation)
            .where(
                InferenceHealthObservation.deployment_id == deployment_id,
                InferenceHealthObservation.deployment_revision == revision,
            )
            .order_by(InferenceHealthObservation.observed_at.desc())
        )
    ).scalars().first()
    if row is None:
        return None
    if ensure_aware(row.expires_at) <= now:
        return None
    return row


def _denial(
    request: InferenceRequest, reason: str, *, binding: InferenceTenantBinding | None = None
) -> RoutingDecision:
    return RoutingDecision(
        decision_id="",
        attempt_id=request.attempt_id,
        tenant_id=request.tenant_id,
        reason=reason,
        decided_at=utc_now(),
        binding_id=binding.binding_id if binding is not None else None,
        binding_revision=binding.revision if binding is not None else None,
    )


async def _record_decision(db: AsyncSession, decision: RoutingDecision) -> RoutingDecision:
    row = InferenceRoutingDecision(
        attempt_id=decision.attempt_id,
        tenant_id=decision.tenant_id,
        binding_id=decision.binding_id,
        binding_revision=decision.binding_revision,
        deployment_id=decision.deployment_id,
        deployment_revision=decision.deployment_revision,
        model_release_id=decision.model_release_id,
        model_release_revision=decision.model_release_revision,
        runtime_profile_id=decision.runtime_profile_id,
        runtime_profile_revision=decision.runtime_profile_revision,
        reason=decision.reason,
        decided_at=decision.decided_at,
    )
    db.add(row)
    await db.flush()
    return RoutingDecision(
        decision_id=row.id,
        attempt_id=decision.attempt_id,
        tenant_id=decision.tenant_id,
        reason=decision.reason,
        decided_at=decision.decided_at,
        binding_id=decision.binding_id,
        binding_revision=decision.binding_revision,
        deployment_id=decision.deployment_id,
        deployment_revision=decision.deployment_revision,
        model_release_id=decision.model_release_id,
        model_release_revision=decision.model_release_revision,
        runtime_profile_id=decision.runtime_profile_id,
        runtime_profile_revision=decision.runtime_profile_revision,
    )


async def _deny(
    db: AsyncSession,
    request: InferenceRequest,
    reason: str,
    *,
    binding: InferenceTenantBinding | None = None,
) -> RoutingOutcome:
    decision = await _record_decision(db, _denial(request, reason, binding=binding))
    return RoutingOutcome(decision=decision, target=None)


async def resolve_route(
    db: AsyncSession,
    *,
    request: InferenceRequest,
    now: datetime | None = None,
) -> RoutingOutcome:
    """Resolve the alias to one authorized deployment, or deny.

    The decision is always recorded, including denials, because the record is the
    audit trail for a refusal as much as for a dispatch.
    """
    moment = ensure_aware(now or utc_now())

    if not await _active_tenant(db, request.tenant_id):
        return await _deny(db, request, RouteReason.TENANT_INACTIVE.value)

    binding = await _binding_for(db, tenant_id=request.tenant_id, alias=request.rahkia_alias)
    if binding is None:
        return await _deny(db, request, RouteReason.BINDING_MISSING.value)
    if binding.status != "active":
        return await _deny(db, request, RouteReason.BINDING_REVOKED.value, binding=binding)

    release = await _release_for_alias(db, alias=request.rahkia_alias)
    if release is None:
        return await _deny(db, request, RouteReason.MODEL_NOT_ALLOWED.value, binding=binding)

    allowed_releases = _json_list(binding.allowed_model_release_ids_json)
    if allowed_releases and release.id not in allowed_releases:
        return await _deny(db, request, RouteReason.MODEL_NOT_ALLOWED.value, binding=binding)

    allowed_deployments = set(_json_list(binding.allowed_deployment_ids_json))
    allowed_sites = set(_json_list(binding.allowed_sites_json))
    allowed_modes = set(_json_list(binding.allowed_modes_json))
    required_capabilities = set(_json_list(binding.required_capabilities_json))
    priority = _json_list(binding.priority_json)

    if request.required_capability:
        required_capabilities.add(request.required_capability)

    candidates = (
        await db.execute(
            select(InferenceDeployment).where(
                InferenceDeployment.model_release_id == release.id,
                InferenceDeployment.lifecycle == DeploymentLifecycle.READY.value,
            )
        )
    ).scalars().all()

    # Track why candidates dropped out so a denial names the most specific cause.
    denied_by_placement = False
    denied_by_capability = False
    denied_by_health = False
    eligible: list[
        tuple[InferenceDeployment, InferenceRuntimeProfile, InferenceHealthObservation]
    ] = []

    for deployment in candidates:
        if allowed_deployments and deployment.deployment_id not in allowed_deployments:
            continue

        mode = deployment.inference_mode
        if mode not in INFERENCE_MODES:
            continue

        # Placement: a shared deployment is never activated in INF1-A, a binding
        # that forbids hosted can never select one, and site/residency/mode
        # restrictions are all placement constraints.
        if mode in UNQUALIFIED_MODES:
            denied_by_placement = True
            continue
        if deployment.owner_tenant_id != request.tenant_id:
            # A local or dedicated deployment belongs to exactly one tenant.
            denied_by_placement = True
            continue
        if not binding.allow_hosted and mode not in LOCAL_ONLY_MODES:
            denied_by_placement = True
            continue
        if allowed_modes and mode not in allowed_modes:
            denied_by_placement = True
            continue
        if allowed_sites and (deployment.site_id or "") not in allowed_sites:
            denied_by_placement = True
            continue

        profile = await db.get(InferenceRuntimeProfile, deployment.runtime_profile_id)
        if profile is None or profile.status != "active":
            continue

        profile_capabilities = set(_json_list(profile.capabilities_json))
        release_capabilities = set(_json_list(release.capabilities_json))
        if required_capabilities and not required_capabilities.issubset(
            profile_capabilities & release_capabilities
        ):
            denied_by_capability = True
            continue

        health = await _fresh_health(
            db, deployment_id=deployment.deployment_id, revision=deployment.revision, now=moment
        )
        if health is None or health.status != "healthy" or health.model_present is False:
            denied_by_health = True
            continue

        eligible.append((deployment, profile, health))

    if not eligible:
        if denied_by_capability:
            return await _deny(
                db, request, RouteReason.CAPABILITY_UNSUPPORTED.value, binding=binding
            )
        if denied_by_placement:
            return await _deny(db, request, RouteReason.SITE_NOT_ALLOWED.value, binding=binding)
        if denied_by_health:
            return await _deny(db, request, RouteReason.HEALTH_STALE.value, binding=binding)
        return await _deny(
            db, request, RouteReason.DEPLOYMENT_UNAVAILABLE.value, binding=binding
        )

    # Deterministic order: configured priority first, then the deployment id.
    def sort_key(
        item: tuple[InferenceDeployment, InferenceRuntimeProfile, InferenceHealthObservation]
    ) -> tuple[int, str]:
        deployment = item[0]
        rank = priority.index(deployment.deployment_id) if deployment.deployment_id in priority else len(priority)
        return (rank, deployment.deployment_id)

    deployment, profile, health = sorted(eligible, key=sort_key)[0]

    # Bounds. The strictest of request, binding, deployment and model wins, and
    # an unprovable context bound denies rather than being assumed to fit.
    max_output = min(
        value
        for value in (
            request.max_output_tokens,
            release.max_output_tokens,
            deployment.max_output_tokens if deployment.max_output_tokens is not None else release.max_output_tokens,
        )
        if value is not None
    )
    if request.max_output_tokens > max_output:
        return await _deny(db, request, RouteReason.CONTEXT_LIMIT.value, binding=binding)

    context_limit = min(
        value
        for value in (
            release.max_context_tokens,
            deployment.max_context_tokens if deployment.max_context_tokens is not None else release.max_context_tokens,
        )
        if value is not None
    )
    if _estimate_request_tokens(request) + request.max_output_tokens > context_limit:
        return await _deny(db, request, RouteReason.CONTEXT_LIMIT.value, binding=binding)

    if deployment.max_request_bytes is not None:
        payload_bytes = sum(
            len(str(message.get("content", ""))) for message in request.messages if isinstance(message, dict)
        )
        if payload_bytes > deployment.max_request_bytes:
            return await _deny(db, request, RouteReason.REQUEST_TOO_LARGE.value, binding=binding)

    target = ResolvedDeployment(
        deployment_id=deployment.deployment_id,
        deployment_revision=deployment.revision,
        model_release_id=release.id,
        model_release_revision=release.release_version,
        runtime_profile_id=profile.id,
        runtime_profile_revision=profile.profile_version,
        inference_mode=deployment.inference_mode,
        owner_tenant_id=deployment.owner_tenant_id,
        installation_id=deployment.installation_id,
        site_id=deployment.site_id,
        endpoint_ref=deployment.endpoint_ref,
        credential_ref=deployment.credential_ref,
        runtime_model_ref=release.artifact_ref,
        max_context_tokens=context_limit,
        max_output_tokens=max_output,
        max_request_bytes=deployment.max_request_bytes,
        health_observed_at=ensure_aware(health.observed_at),
    )

    allowed = RoutingDecision(
        decision_id="",
        attempt_id=request.attempt_id,
        tenant_id=request.tenant_id,
        reason=RouteReason.ALLOWED.value,
        decided_at=utc_now(),
        binding_id=binding.binding_id,
        binding_revision=binding.revision,
        deployment_id=deployment.deployment_id,
        deployment_revision=deployment.revision,
        model_release_id=release.id,
        model_release_revision=release.release_version,
        runtime_profile_id=profile.id,
        runtime_profile_revision=profile.profile_version,
    )
    decision = await _record_decision(db, allowed)
    return RoutingOutcome(decision=decision, target=target)


__all__ = ["CONSERVATIVE_CHARS_PER_TOKEN", "RoutingOutcome", "resolve_route"]
