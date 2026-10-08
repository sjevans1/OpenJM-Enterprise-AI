"""Operator-only registry service for the INF1 inference plane.

Every mutation requires the explicit ``platform:inference:admin`` capability and
is audited. Tenant owners and ordinary platform metadata readers cannot mutate
the registry, and a client role never implies binding authority.

Cross entity rules are validated transactionally here, next to the database
constraints that cover the single row cases:

* a local or hosted-dedicated deployment has an owner tenant and may only be
  bound to that tenant;
* a hosted-shared deployment has no sole owner, every tenant needs its own
  binding, and shared mode stays unqualified in this package;
* no wildcard tenant or deployment entry is accepted, and there is no bootstrap
  binding that quietly covers all tenants.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import Principal
from app.core.inference import (
    INFERENCE_MODES,
    UNQUALIFIED_MODES,
    DeploymentLifecycle,
    ensure_aware,
    utc_now,
    validate_opaque_id,
    validate_rahkia_alias,
)
from app.core.platform import PlatformCapability
from app.models import (
    InferenceDeployment,
    InferenceHealthObservation,
    InferenceModelRelease,
    InferenceRuntimeProfile,
    InferenceTenantBinding,
    Tenant,
)
from app.services import identity as identity_service

WILDCARD_VALUES = frozenset({"*", "all", "any", "true"})

DEFAULT_HEALTH_TTL_SECONDS = 120


def _require_inference_admin(actor: Principal) -> None:
    actor.require_platform(PlatformCapability.INFERENCE_ADMIN)


def _reject_wildcard(values: Iterable[str], field_name: str) -> list[str]:
    cleaned: list[str] = []
    for value in values or ():
        text = str(value).strip()
        if text.lower() in WILDCARD_VALUES:
            raise ValueError(f"{field_name} may not contain a wildcard entry")
        if text:
            cleaned.append(text)
    return cleaned


async def _audit(
    db: AsyncSession,
    actor: Principal,
    action: str,
    resource_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await identity_service.record_audit(
        db,
        principal=actor,
        action=f"inference.registry.{action}",
        decision="allow",
        resource_type="inference_registry",
        resource_id=resource_id,
        metadata=metadata,
    )


def _capabilities_json(values: Sequence[str] | None) -> str:
    return json.dumps(sorted({str(item) for item in (values or ())}))


async def create_model_release(
    db: AsyncSession,
    *,
    actor: Principal,
    rahkia_alias: str,
    artifact_ref: str | None,
    tokenizer_ref: str | None,
    tokenizer_revision: str | None,
    chat_template_revision: str | None,
    capabilities: Sequence[str] | None,
    max_context_tokens: int,
    max_output_tokens: int,
    lineage_ref: str | None = None,
    commercial_use_ref: str | None = None,
    evaluation_ref: str | None = None,
    release_version: int = 1,
) -> InferenceModelRelease:
    _require_inference_admin(actor)
    alias = validate_rahkia_alias(rahkia_alias)
    if max_context_tokens <= 0 or max_output_tokens <= 0:
        raise ValueError("context and output ceilings must be positive")

    existing = (
        await db.execute(
            select(InferenceModelRelease).where(
                InferenceModelRelease.rahkia_alias == alias,
                InferenceModelRelease.release_version == release_version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError("that alias and release version already exist")

    release = InferenceModelRelease(
        rahkia_alias=alias,
        release_version=release_version,
        artifact_ref=artifact_ref,
        lineage_ref=lineage_ref,
        tokenizer_ref=tokenizer_ref,
        tokenizer_revision=tokenizer_revision,
        chat_template_revision=chat_template_revision,
        capabilities_json=_capabilities_json(capabilities),
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
        commercial_use_ref=commercial_use_ref,
        evaluation_ref=evaluation_ref,
        status="active",
        created_by=actor.principal_id,
    )
    db.add(release)
    await db.flush()
    await _audit(
        db,
        actor,
        "model_release.create",
        release.id,
        {"alias": alias, "release_version": release_version},
    )
    return release


async def create_runtime_profile(
    db: AsyncSession,
    *,
    actor: Principal,
    adapter_kind: str,
    adapter_version: str,
    engine_kind: str,
    engine_version: str,
    capabilities: Sequence[str] | None,
    usage_method: str,
    cache_policy: str = "none",
    qualification_ref: str | None = None,
    image_ref: str | None = None,
    cancellation_supported: bool = False,
    profile_version: int = 1,
) -> InferenceRuntimeProfile:
    _require_inference_admin(actor)
    if not all([adapter_kind, adapter_version, engine_kind, engine_version]):
        raise ValueError("adapter and engine kind/version are required")
    # An unpinned moving reference is not a qualification.
    for value in (adapter_version, engine_version, image_ref or ""):
        if str(value).strip().lower() in {"latest", "main", "head"}:
            raise ValueError("an unpinned moving reference cannot be registered")

    profile = InferenceRuntimeProfile(
        adapter_kind=adapter_kind,
        adapter_version=adapter_version,
        engine_kind=engine_kind,
        engine_version=engine_version,
        profile_version=profile_version,
        image_ref=image_ref,
        capabilities_json=_capabilities_json(capabilities),
        usage_method=usage_method,
        cancellation_supported=cancellation_supported,
        cache_policy=cache_policy,
        qualification_ref=qualification_ref,
        status="active",
        created_by=actor.principal_id,
    )
    db.add(profile)
    await db.flush()
    await _audit(
        db,
        actor,
        "runtime_profile.create",
        profile.id,
        {"adapter_kind": adapter_kind, "engine_kind": engine_kind},
    )
    return profile


async def create_deployment(
    db: AsyncSession,
    *,
    actor: Principal,
    deployment_id: str,
    model_release_id: str,
    runtime_profile_id: str,
    inference_mode: str,
    owner_tenant_id: str | None,
    site_id: str | None = None,
    installation_id: str | None = None,
    residency_domain: str | None = None,
    endpoint_ref: str | None = None,
    credential_ref: str | None = None,
    lifecycle: str = DeploymentLifecycle.DISABLED.value,
    connectivity_mode: str = "connected",
    max_context_tokens: int | None = None,
    max_output_tokens: int | None = None,
    max_request_bytes: int | None = None,
    security_qualification_ref: str | None = None,
    revision: int = 1,
) -> InferenceDeployment:
    _require_inference_admin(actor)
    validate_opaque_id(deployment_id, "deployment_id")
    if inference_mode not in INFERENCE_MODES:
        raise ValueError(f"unsupported inference mode {inference_mode!r}")
    if inference_mode in UNQUALIFIED_MODES:
        # Shared mode may be registered, but never as ready in this package.
        if lifecycle == DeploymentLifecycle.READY.value:
            raise ValueError("shared serving cannot be registered as ready before its isolation gate")
        if owner_tenant_id is not None:
            raise ValueError("a shared deployment has no sole owner tenant")
    else:
        if not owner_tenant_id:
            raise ValueError("a local or dedicated deployment requires an owner tenant")
        tenant = await db.get(Tenant, owner_tenant_id)
        if tenant is None:
            raise ValueError("unknown owner tenant")

    release = await db.get(InferenceModelRelease, model_release_id)
    if release is None:
        raise ValueError("unknown model release")
    profile = await db.get(InferenceRuntimeProfile, runtime_profile_id)
    if profile is None:
        raise ValueError("unknown runtime profile")

    deployment = InferenceDeployment(
        deployment_id=deployment_id,
        revision=revision,
        model_release_id=model_release_id,
        runtime_profile_id=runtime_profile_id,
        installation_id=installation_id,
        site_id=site_id,
        residency_domain=residency_domain,
        inference_mode=inference_mode,
        owner_tenant_id=owner_tenant_id,
        endpoint_ref=endpoint_ref,
        credential_ref=credential_ref,
        lifecycle=lifecycle,
        connectivity_mode=connectivity_mode,
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
        max_request_bytes=max_request_bytes,
        security_qualification_ref=security_qualification_ref,
        created_by=actor.principal_id,
    )
    db.add(deployment)
    await db.flush()
    await _audit(
        db,
        actor,
        "deployment.create",
        deployment.id,
        {"deployment_id": deployment_id, "mode": inference_mode, "lifecycle": lifecycle},
    )
    return deployment


async def set_deployment_lifecycle(
    db: AsyncSession,
    *,
    actor: Principal,
    deployment_id: str,
    lifecycle: str,
    expected_revision: int,
) -> InferenceDeployment:
    """Change lifecycle with optimistic concurrency.

    A stale ``expected_revision`` is refused rather than applied, so two
    operators cannot quietly overwrite each other's decision.
    """
    _require_inference_admin(actor)
    deployment = (
        await db.execute(
            select(InferenceDeployment)
            .where(InferenceDeployment.deployment_id == deployment_id)
            .order_by(InferenceDeployment.revision.desc())
        )
    ).scalars().first()
    if deployment is None:
        raise ValueError("unknown deployment")
    if deployment.revision != expected_revision:
        raise ValueError("deployment revision changed; re-read before updating")
    if lifecycle == DeploymentLifecycle.READY.value and deployment.inference_mode in UNQUALIFIED_MODES:
        raise ValueError("shared serving cannot be made ready before its isolation gate")
    previous = deployment.lifecycle
    deployment.lifecycle = lifecycle
    await db.flush()
    await _audit(
        db,
        actor,
        "deployment.lifecycle",
        deployment.id,
        {"deployment_id": deployment_id, "from": previous, "to": lifecycle},
    )
    return deployment


async def record_health_observation(
    db: AsyncSession,
    *,
    deployment_id: str,
    deployment_revision: int,
    status: str,
    model_present: bool | None = None,
    failure_code: str | None = None,
    ttl_seconds: int = DEFAULT_HEALTH_TTL_SECONDS,
    actor: Principal | None = None,
) -> InferenceHealthObservation:
    """Store a transient observation with an expiry.

    Health is never authority on its own: routing only accepts a fresh, healthy
    observation, so an expired row denies new dispatch instead of lingering.
    """
    if status not in {"healthy", "degraded", "unknown"}:
        raise ValueError(f"unsupported health status {status!r}")
    now = utc_now()
    observation = InferenceHealthObservation(
        deployment_id=deployment_id,
        deployment_revision=deployment_revision,
        status=status,
        model_present=model_present,
        failure_code=failure_code,
        observed_at=now,
        expires_at=now + timedelta(seconds=max(1, int(ttl_seconds))),
    )
    db.add(observation)
    await db.flush()
    if actor is not None:
        await _audit(
            db,
            actor,
            "health.observe",
            observation.id,
            {"deployment_id": deployment_id, "status": status},
        )
    return observation


async def upsert_tenant_binding(
    db: AsyncSession,
    *,
    actor: Principal,
    tenant_id: str,
    rahkia_alias: str,
    allowed_model_release_ids: Sequence[str] | None = None,
    allowed_deployment_ids: Sequence[str] | None = None,
    allowed_sites: Sequence[str] | None = None,
    allowed_modes: Sequence[str] | None = None,
    allowed_data_classes: Sequence[str] | None = None,
    required_capabilities: Sequence[str] | None = None,
    priority: Sequence[str] | None = None,
    allow_hosted: bool = False,
    fallback_policy: str = "none",
    expected_revision: int | None = None,
) -> InferenceTenantBinding:
    """Create or update one tenant binding. There is no wildcard binding."""
    _require_inference_admin(actor)
    alias = validate_rahkia_alias(rahkia_alias)
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError("unknown tenant")

    deployments = _reject_wildcard(allowed_deployment_ids or (), "allowed_deployment_ids")
    modes = _reject_wildcard(allowed_modes or (), "allowed_modes")
    for mode in modes:
        if mode not in INFERENCE_MODES:
            raise ValueError(f"unsupported inference mode {mode!r}")
    if modes and not allow_hosted and any(mode not in {"customer_local", "openjm_local"} for mode in modes):
        raise ValueError("a binding that forbids hosted may not allow a hosted mode")

    # A deployment owned by another tenant can never be named in this binding.
    for deployment_id in deployments:
        row = (
            await db.execute(
                select(InferenceDeployment)
                .where(InferenceDeployment.deployment_id == deployment_id)
                .order_by(InferenceDeployment.revision.desc())
            )
        ).scalars().first()
        if row is None:
            raise ValueError(f"unknown deployment {deployment_id!r}")
        if row.owner_tenant_id is not None and row.owner_tenant_id != tenant_id:
            raise ValueError("a deployment owned by another tenant cannot be bound")

    binding = (
        await db.execute(
            select(InferenceTenantBinding).where(
                InferenceTenantBinding.tenant_id == tenant_id,
                InferenceTenantBinding.rahkia_alias == alias,
            )
        )
    ).scalar_one_or_none()

    if binding is None:
        binding = InferenceTenantBinding(
            tenant_id=tenant_id,
            rahkia_alias=alias,
            revision=1,
            created_by=actor.principal_id,
        )
        db.add(binding)
    else:
        if expected_revision is None or binding.revision != expected_revision:
            raise ValueError("binding revision changed; re-read before updating")
        binding.revision += 1

    binding.allowed_model_release_ids_json = json.dumps(
        _reject_wildcard(allowed_model_release_ids or (), "allowed_model_release_ids")
    )
    binding.allowed_deployment_ids_json = json.dumps(deployments)
    binding.allowed_sites_json = json.dumps(_reject_wildcard(allowed_sites or (), "allowed_sites"))
    binding.allowed_modes_json = json.dumps(modes)
    binding.allowed_data_classes_json = json.dumps(
        _reject_wildcard(allowed_data_classes or (), "allowed_data_classes")
    )
    binding.required_capabilities_json = json.dumps(
        _reject_wildcard(required_capabilities or (), "required_capabilities")
    )
    binding.priority_json = json.dumps(_reject_wildcard(priority or (), "priority"))
    binding.allow_hosted = bool(allow_hosted)
    binding.fallback_policy = fallback_policy
    binding.status = "active"
    binding.updated_at = utc_now()
    await db.flush()
    await _audit(
        db,
        actor,
        "tenant_binding.upsert",
        binding.id,
        {"tenant_id": tenant_id, "alias": alias, "revision": binding.revision},
    )
    return binding


async def revoke_tenant_binding(
    db: AsyncSession, *, actor: Principal, tenant_id: str, rahkia_alias: str, expected_revision: int
) -> InferenceTenantBinding:
    """Revoke a binding. The next dispatch is refused, history stays readable."""
    _require_inference_admin(actor)
    binding = (
        await db.execute(
            select(InferenceTenantBinding).where(
                InferenceTenantBinding.tenant_id == tenant_id,
                InferenceTenantBinding.rahkia_alias == rahkia_alias,
            )
        )
    ).scalar_one_or_none()
    if binding is None:
        raise ValueError("unknown binding")
    if binding.revision != expected_revision:
        raise ValueError("binding revision changed; re-read before revoking")
    binding.status = "revoked"
    binding.revision += 1
    binding.updated_at = utc_now()
    await db.flush()
    await _audit(
        db,
        actor,
        "tenant_binding.revoke",
        binding.id,
        {"tenant_id": tenant_id, "alias": rahkia_alias},
    )
    return binding


async def list_bindings(db: AsyncSession, *, tenant_id: str) -> list[InferenceTenantBinding]:
    return list(
        (
            await db.execute(
                select(InferenceTenantBinding)
                .where(InferenceTenantBinding.tenant_id == tenant_id)
                .order_by(InferenceTenantBinding.rahkia_alias)
            )
        ).scalars().all()
    )


async def list_deployments(
    db: AsyncSession, *, site_id: str | None = None, limit: int = 100
) -> list[InferenceDeployment]:
    query = select(InferenceDeployment).order_by(InferenceDeployment.deployment_id)
    if site_id is not None:
        query = query.where(InferenceDeployment.site_id == site_id)
    return list((await db.execute(query.limit(max(1, min(limit, 500))))).scalars().all())


async def latest_health(
    db: AsyncSession, *, deployment_id: str, now: datetime | None = None
) -> InferenceHealthObservation | None:
    row = (
        await db.execute(
            select(InferenceHealthObservation)
            .where(InferenceHealthObservation.deployment_id == deployment_id)
            .order_by(InferenceHealthObservation.observed_at.desc())
        )
    ).scalars().first()
    if row is None:
        return None
    if ensure_aware(row.expires_at) <= ensure_aware(now or utc_now()):
        return None
    return row


__all__ = [
    "DEFAULT_HEALTH_TTL_SECONDS",
    "WILDCARD_VALUES",
    "create_deployment",
    "create_model_release",
    "create_runtime_profile",
    "latest_health",
    "list_bindings",
    "list_deployments",
    "record_health_observation",
    "revoke_tenant_binding",
    "set_deployment_lifecycle",
    "upsert_tenant_binding",
]
