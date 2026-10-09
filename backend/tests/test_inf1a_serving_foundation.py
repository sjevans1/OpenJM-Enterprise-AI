"""INF1-A serving foundation acceptance.

Covers the automatable acceptance areas: deterministic fail-closed routing and
tenant isolation (A02, A03, A04, A05), one-to-one attribution (A06, A07), token
normalization (A09), the aggregate-only serializer (A10), registry authorization
and audit (A11), the additive migration (A12) and an isolated local and
private-hosted fake transport for the adapter contract (A01).

A14 (real engine and hardware qualification) is a separate controlled runtime
gate and is NOT RUN here.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.core.identity import AuthorizationError, Principal
from app.core.inference import (
    CountMethod,
    ExportPolicyDisabled,
    InferenceError,
    InferenceRequest,
    RouteReason,
    TelemetryError,
    normalize_usage,
    strict_token_count,
)
from app.core.platform import PlatformCapability
from app.core.tenancy import LEGACY_PRINCIPAL_ID
from app.models import (
    AuditRecord,
    InferenceDeployment,
    InferenceModelRelease,
    InferenceRoutingDecision,
    InferenceRuntimeProfile,
    InferenceTenantBinding,
    ModelUsageEvent,
    Tenant,
)
from app.services import access_governance as governance
from app.services.inference import registry as registry_service
from app.services.inference import routing as routing_service
from app.services.inference.adapter import (
    LOCAL_ENDPOINT_PROFILE,
    ModelMismatchError,
    OpenAICompatibleAdapter,
    PRIVATE_HOSTED_PROFILE,
    build_chat_payload,
)
from app.services.inference.attribution import ApprovalContext, attribute_usage_event
from app.services.inference.serializer import (
    ExportPolicy,
    ExportRow,
    ExportSchemaError,
    build_aggregate_export,
)


def _admin() -> Principal:
    return Principal(
        principal_id="op-inf",
        tenant_id="tnt-op",
        subject="sub:op-inf",
        role="",
        membership_id="m",
        auth_method="oidc",
        permissions=frozenset(),
        platform_capabilities=frozenset({PlatformCapability.INFERENCE_ADMIN.value}),
    )


def _request(tenant_id: str, *, alias: str = "rahkia-balanced", **overrides) -> InferenceRequest:
    payload = {
        "tenant_id": tenant_id,
        "principal_id": "p-1",
        "business_request_id": "biz-1",
        "logical_call_id": "call-1",
        "attempt_id": "att-1",
        "rahkia_alias": alias,
        "max_output_tokens": 64,
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=30),
        "messages": ({"role": "user", "content": "hello"},),
    }
    payload.update(overrides)
    return InferenceRequest(**payload)


async def _tenant(db, tenant_id: str) -> Tenant:
    row = await db.get(Tenant, tenant_id)
    if row is None:
        row = Tenant(id=tenant_id, slug=tenant_id, name=tenant_id, status="active")
        db.add(row)
        await db.flush()
    return row


async def _seed(
    db,
    *,
    tenant_id: str,
    alias: str = "rahkia-balanced",
    mode: str = "customer_local",
    lifecycle: str = "ready",
    health: str | None = "healthy",
    health_ttl_seconds: int = 120,
    capabilities: tuple[str, ...] = ("text_completion",),
    runtime_capabilities: tuple[str, ...] = ("text_completion",),
    allow_hosted: bool = False,
    binding_status: str = "active",
    allowed_deployments: tuple[str, ...] = (),
    site_id: str = "site-a",
):
    """Seed a complete, eligible local deployment for one tenant."""
    await _tenant(db, tenant_id)
    # A model release and runtime profile are shared registry facts, so seeding a
    # second tenant reuses them rather than colliding on the unique keys.
    release = (
        await db.execute(
            select(InferenceModelRelease).where(
                InferenceModelRelease.rahkia_alias == alias,
                InferenceModelRelease.release_version == 1,
            )
        )
    ).scalars().first()
    if release is None:
        release = await registry_service.create_model_release(
            db,
            actor=_admin(),
            rahkia_alias=alias,
            artifact_ref="model://balanced/v1",
            tokenizer_ref="tokenizer://balanced",
            tokenizer_revision="tok-1",
            chat_template_revision="tpl-1",
            capabilities=list(capabilities),
            max_context_tokens=4096,
            max_output_tokens=512,
        )
    profile = (await db.execute(select(InferenceRuntimeProfile))).scalars().first()
    if profile is None:
        profile = await registry_service.create_runtime_profile(
            db,
            actor=_admin(),
            adapter_kind="openai_compatible",
            adapter_version="1.0.0",
            engine_kind="local-engine",
            engine_version="0.9.1",
            capabilities=list(runtime_capabilities),
            usage_method="character_estimate",
            qualification_ref="qual-1",
        )
    deployment = await registry_service.create_deployment(
        db,
        actor=_admin(),
        deployment_id=f"dep-{tenant_id}",
        model_release_id=release.id,
        runtime_profile_id=profile.id,
        inference_mode=mode,
        owner_tenant_id=tenant_id,
        site_id=site_id,
        endpoint_ref="endpoint://local",
        credential_ref="cred://local",
        lifecycle=lifecycle,
        max_request_bytes=100_000,
    )
    binding = await registry_service.upsert_tenant_binding(
        db,
        actor=_admin(),
        tenant_id=tenant_id,
        rahkia_alias=alias,
        allowed_deployment_ids=list(allowed_deployments),
        allow_hosted=allow_hosted,
        fallback_policy="none",
    )
    if binding_status != "active":
        binding.status = binding_status
        await db.flush()
    if health is not None:
        await registry_service.record_health_observation(
            db,
            deployment_id=deployment.deployment_id,
            deployment_revision=deployment.revision,
            status=health,
            model_present=health == "healthy",
            ttl_seconds=health_ttl_seconds,
        )
    await db.commit()
    return release, profile, deployment, binding


# ---------------------------------------------------------------------------
# A01 / adapter contract with isolated fake transports
# ---------------------------------------------------------------------------


class FakeTransport:
    """A deterministic in-process transport. No network, no engine."""

    def __init__(self, *, model="model://balanced/v1", usage=None, health_models=("model://balanced/v1",)):
        self.calls: list[dict] = []
        self._model = model
        self._usage = usage
        self._health_models = health_models

    async def chat_completion(self, *, endpoint_ref, credential_ref, payload, timeout_s):
        self.calls.append({"endpoint_ref": endpoint_ref, "payload": payload, "timeout_s": timeout_s})
        body = {
            "model": self._model,
            "choices": [{"message": {"content": "hi there"}, "finish_reason": "stop"}],
        }
        if self._usage is not None:
            body["usage"] = self._usage
        return body

    async def health(self, *, endpoint_ref, credential_ref):
        return {"models": list(self._health_models)}


async def test_adapter_completes_with_local_and_hosted_profiles(file_db):
    async with file_db() as db:
        _, _, deployment, _ = await _seed(db, tenant_id="tnt-local")
        request = _request("tnt-local")

    # Build the resolved target through the real router so the test exercises the
    # same object the gateway would receive.
    async with file_db() as db:
        outcome = await routing_service.resolve_route(db, request=request)
        assert outcome.allowed is True
        target = outcome.require()
        await db.commit()

    for profile_ref in (LOCAL_ENDPOINT_PROFILE, PRIVATE_HOSTED_PROFILE):
        transport = FakeTransport(usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        adapter = OpenAICompatibleAdapter(transport, profile_ref=profile_ref)
        result = await adapter.complete(request, target)
        assert result.text == "hi there"
        assert result.usage.total_tokens == 15
        assert result.usage.usage_source == "provider_reported"
        assert transport.calls[0]["payload"]["model"] == "model://balanced/v1"

    # The payload is built from the authorized target, never from the request.
    payload = build_chat_payload(request, target)
    assert set(payload) == {"model", "messages", "temperature", "max_tokens", "stream"}


async def test_adapter_rejects_a_model_mismatch_but_keeps_observed_usage(file_db):
    async with file_db() as db:
        await _seed(db, tenant_id="tnt-local")
        outcome = await routing_service.resolve_route(db, request=_request("tnt-local"))
        target = outcome.require()
        await db.commit()

    transport = FakeTransport(
        model="model://someone-elses",
        usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    )
    adapter = OpenAICompatibleAdapter(transport, profile_ref=PRIVATE_HOSTED_PROFILE)
    with pytest.raises(ModelMismatchError) as excinfo:
        await adapter.complete(_request("tnt-local"), target)
    # Observed usage is retained so the ledger still records real consumption.
    assert excinfo.value.usage.total_tokens == 10


async def test_adapter_probe_reports_unknown_without_leaking_detail(file_db):
    async with file_db() as db:
        await _seed(db, tenant_id="tnt-local")
        outcome = await routing_service.resolve_route(db, request=_request("tnt-local"))
        target = outcome.require()
        await db.commit()

    transport = FakeTransport(health_models=())
    adapter = OpenAICompatibleAdapter(transport, profile_ref=LOCAL_ENDPOINT_PROFILE)
    health = await adapter.probe(target)
    assert health.model_present is False
    assert health.status == "degraded"


# ---------------------------------------------------------------------------
# A02 / A03 / A04: routing denies, and never crosses tenants
# ---------------------------------------------------------------------------


async def test_routing_allows_the_authorized_local_deployment(file_db):
    async with file_db() as db:
        await _seed(db, tenant_id="tnt-local")
        outcome = await routing_service.resolve_route(db, request=_request("tnt-local"))
        await db.commit()
    assert outcome.allowed is True
    assert outcome.decision.reason == RouteReason.ALLOWED.value
    assert outcome.decision.deployment_id == "dep-tnt-local"
    assert outcome.decision.deployment_revision == 1


@pytest.mark.parametrize(
    "hook,expected",
    [
        ("suspended", RouteReason.TENANT_INACTIVE.value),
        ("no_binding", RouteReason.BINDING_MISSING.value),
        ("revoked_binding", RouteReason.BINDING_REVOKED.value),
        ("disabled", RouteReason.DEPLOYMENT_UNAVAILABLE.value),
        ("stale_health", RouteReason.HEALTH_STALE.value),
        ("capability", RouteReason.CAPABILITY_UNSUPPORTED.value),
    ],
)
async def test_routing_denies_fail_closed(file_db, hook, expected):
    async with file_db() as db:
        if hook == "suspended":
            await _seed(db, tenant_id="tnt-local")
            tenant = await db.get(Tenant, "tnt-local")
            tenant.status = "suspended"
            await db.commit()
        elif hook == "no_binding":
            await _seed(db, tenant_id="tnt-local")
            binding = (
                await db.execute(select(InferenceTenantBinding))
            ).scalars().first()
            await db.delete(binding)
            await db.commit()
        elif hook == "revoked_binding":
            await _seed(db, tenant_id="tnt-local", binding_status="revoked")
        elif hook == "disabled":
            await _seed(db, tenant_id="tnt-local", lifecycle="disabled")
        elif hook == "stale_health":
            await _seed(db, tenant_id="tnt-local", health_ttl_seconds=1)
        elif hook == "capability":
            await _seed(db, tenant_id="tnt-local", capabilities=("text_completion",))

        request = _request("tnt-local")
        if hook == "stale_health":
            # Let the observation expire rather than sleeping in the test.
            row = (
                await db.execute(select(InferenceDeployment))
            ).scalars().first()
            await registry_service.record_health_observation(
                db,
                deployment_id=row.deployment_id,
                deployment_revision=row.revision,
                status="healthy",
                ttl_seconds=1,
            )
            from app.models import InferenceHealthObservation

            obs = (
                await db.execute(select(InferenceHealthObservation))
            ).scalars().all()
            for item in obs:
                item.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            await db.commit()
        if hook == "capability":
            request = _request("tnt-local", required_capability="vision")

        outcome = await routing_service.resolve_route(db, request=request)
        await db.commit()

    assert outcome.allowed is False
    assert outcome.decision.reason == expected


async def test_routing_never_selects_another_tenants_deployment(file_db):
    """A02: tenant B's dedicated deployment is invisible to tenant A."""
    async with file_db() as db:
        await _seed(db, tenant_id="tnt-local")
        await _seed(db, tenant_id="tnt-other")
        # Tenant A gets a binding naming tenant B's deployment.
        binding = (
            await db.execute(
                select(InferenceTenantBinding).where(
                    InferenceTenantBinding.tenant_id == "tnt-local"
                )
            )
        ).scalars().first()
        binding.allowed_deployment_ids_json = json.dumps(["dep-tnt-other"])
        await db.commit()

        outcome = await routing_service.resolve_route(db, request=_request("tnt-local"))
        await db.commit()

    assert outcome.allowed is False
    assert outcome.decision.reason == RouteReason.SITE_NOT_ALLOWED.value
    assert outcome.decision.deployment_id is None


async def test_local_only_binding_cannot_fall_back_to_hosted(file_db):
    """A04: a sovereign binding never widens to a hosted deployment."""
    async with file_db() as db:
        await _seed(db, tenant_id="tnt-local-inner")
        await _seed(
            db,
            tenant_id="tnt-local",
            mode="hosted_dedicated",
            allow_hosted=False,
            alias="rahkia-balanced",
        )
        outcome = await routing_service.resolve_route(db, request=_request("tnt-local"))
        await db.commit()

    assert outcome.allowed is False
    assert outcome.decision.reason == RouteReason.SITE_NOT_ALLOWED.value


async def test_shared_mode_is_never_activated(file_db):
    """A04: shared serving is registered but never dispatchable in INF1-A."""
    async with file_db() as db:
        await _seed(db, tenant_id="tnt-local")
        release = (
            await db.execute(select(InferenceModelRelease))
        ).scalars().first()
        profile = (
            await db.execute(select(InferenceRuntimeProfile))
        ).scalars().first()
        with pytest.raises(ValueError):
            await registry_service.create_deployment(
                db,
                actor=_admin(),
                deployment_id="dep-shared",
                model_release_id=release.id,
                runtime_profile_id=profile.id,
                inference_mode="hosted_shared",
                owner_tenant_id=None,
                lifecycle="ready",
            )


# ---------------------------------------------------------------------------
# A05: pinning, revocation and policy change
# ---------------------------------------------------------------------------


async def test_decision_pins_revisions_and_revocation_blocks_next_dispatch(file_db):
    async with file_db() as db:
        release, profile, deployment, binding = await _seed(db, tenant_id="tnt-local")
        first = await routing_service.resolve_route(db, request=_request("tnt-local"))
        await db.commit()

    assert first.decision.model_release_revision == release.release_version
    assert first.decision.runtime_profile_revision == profile.profile_version
    assert first.decision.binding_revision == binding.revision

    async with file_db() as db:
        await registry_service.revoke_tenant_binding(
            db,
            actor=_admin(),
            tenant_id="tnt-local",
            rahkia_alias="rahkia-balanced",
            expected_revision=binding.revision,
        )
        await db.commit()

    async with file_db() as db:
        second = await routing_service.resolve_route(
            db, request=_request("tnt-local", attempt_id="att-2")
        )
        await db.commit()
    assert second.allowed is False
    assert second.decision.reason == RouteReason.BINDING_REVOKED.value


async def test_routing_decision_is_content_free(file_db):
    async with file_db() as db:
        await _seed(db, tenant_id="tnt-local")
        await routing_service.resolve_route(
            db, request=_request("tnt-local", messages=({"role": "user", "content": "secret"},))
        )
        await db.commit()
        row = (await db.execute(select(InferenceRoutingDecision))).scalars().first()

    rendered = json.dumps(
        {
            "attempt_id": row.attempt_id,
            "reason": row.reason,
            "deployment_id": row.deployment_id,
            "binding_id": row.binding_id,
        }
    )
    assert "secret" not in rendered
    assert "endpoint://local" not in rendered
    assert "cred://local" not in rendered


# ---------------------------------------------------------------------------
# A06 / A07: one-to-one attribution
# ---------------------------------------------------------------------------


async def _usage_event(db, *, tenant_id: str, event_id: str = "evt-1") -> ModelUsageEvent:
    row = ModelUsageEvent(
        id=event_id,
        tenant_id=tenant_id,
        principal_id="p-1",
        request_id="req-1",
        provider_route="local",
        model_name="balanced",
        usage_source="estimated",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        call_role="primary",
        idempotency_key=f"{event_id}-key",
        status="succeeded",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    return row


async def test_attribution_is_one_to_one_and_idempotent(file_db):
    async with file_db() as db:
        await _seed(db, tenant_id="tnt-local")
        outcome = await routing_service.resolve_route(db, request=_request("tnt-local"))
        event = await _usage_event(db, tenant_id="tnt-local")
        usage = normalize_usage(
            input_tokens=10,
            output_tokens=5,
            usage_source="estimated",
            count_method=CountMethod.CHARACTER_ESTIMATE.value,
        )
        context = ApprovalContext("biz-1", "call-1", "att-1")

        first = await attribute_usage_event(
            db,
            usage_event=event,
            context=context,
            decision=outcome.decision,
            usage=usage,
            target=outcome.target,
        )
        # Replaying the same attempt is idempotent.
        again = await attribute_usage_event(
            db,
            usage_event=event,
            context=context,
            decision=outcome.decision,
            usage=usage,
            target=outcome.target,
        )
        assert first.id == again.id

        # A different attempt against the same event is a conflict.
        with pytest.raises(InferenceError):
            await attribute_usage_event(
                db,
                usage_event=event,
                context=ApprovalContext("biz-1", "call-1", "att-9"),
                decision=outcome.decision,
                usage=usage,
                target=outcome.target,
            )
        await db.commit()

    async with file_db() as db:
        from sqlalchemy import func

        from app.models import InferenceUsageAttribution

        rows = await db.scalar(
            select(func.count()).select_from(InferenceUsageAttribution)
        )
    assert int(rows) == 1


async def test_attribution_refuses_a_tenant_mismatch(file_db):
    async with file_db() as db:
        await _seed(db, tenant_id="tnt-local")
        outcome = await routing_service.resolve_route(db, request=_request("tnt-local"))
        event = await _usage_event(db, tenant_id="tnt-other", event_id="evt-other")
        usage = normalize_usage(
            input_tokens=1,
            output_tokens=1,
            usage_source="estimated",
            count_method=CountMethod.CHARACTER_ESTIMATE.value,
        )
        with pytest.raises(InferenceError):
            await attribute_usage_event(
                db,
                usage_event=event,
                context=ApprovalContext("biz-1", "call-1", "att-1"),
                decision=outcome.decision,
                usage=usage,
                target=outcome.target,
            )
        # With no resolved target the deployment ownership check cannot fire, so
        # this case isolates the tenant equality rule itself.
        with pytest.raises(InferenceError):
            await attribute_usage_event(
                db,
                usage_event=event,
                context=ApprovalContext("biz-1", "call-1", "att-1"),
                decision=outcome.decision,
                usage=usage,
                target=None,
            )


# ---------------------------------------------------------------------------
# A09: strict token normalization
# ---------------------------------------------------------------------------


def test_token_normalization_is_strict():
    with pytest.raises(TelemetryError):
        strict_token_count(True, "input_tokens")
    with pytest.raises(TelemetryError):
        strict_token_count(-1, "input_tokens")
    with pytest.raises(TelemetryError):
        strict_token_count("many", "input_tokens")
    with pytest.raises(TelemetryError):
        normalize_usage(
            input_tokens=1,
            output_tokens=2,
            total_tokens=99,
            usage_source="provider_reported",
            count_method=CountMethod.RUNTIME_REPORTED.value,
        )
    with pytest.raises(TelemetryError):
        normalize_usage(
            input_tokens=1,
            output_tokens=2,
            cached_input_tokens=5,
            usage_source="provider_reported",
            count_method=CountMethod.RUNTIME_REPORTED.value,
        )
    with pytest.raises(TelemetryError):
        normalize_usage(
            input_tokens=1,
            output_tokens=2,
            usage_source="estimated",
            count_method=CountMethod.TOKENIZER_ESTIMATE.value,
        )

    usage = normalize_usage(
        input_tokens=10,
        output_tokens=5,
        cached_input_tokens=4,
        reasoning_tokens=2,
        usage_source="provider_reported",
        count_method=CountMethod.RUNTIME_REPORTED.value,
    )
    # Cached and reasoning are subsets and are never added a second time.
    assert usage.total_tokens == 15
    assert usage.cached_input_tokens == 4
    assert usage.reasoning_tokens == 2


# ---------------------------------------------------------------------------
# A10: the aggregate-only serializer
# ---------------------------------------------------------------------------


def _window():
    end = datetime.now(timezone.utc)
    return end - timedelta(days=1), end


def test_export_is_disabled_by_default():
    start, end = _window()
    with pytest.raises(ExportPolicyDisabled):
        build_aggregate_export(
            policy=ExportPolicy(),
            export_id="exp-1",
            installation_id="inst-1",
            sequence=1,
            commercial_tenant_ref="acct-1",
            window_start=start,
            window_end=end,
            rows=[ExportRow(rahkia_alias="rahkia-balanced", attempts=1)],
        )


def test_export_rejects_unknown_and_forbidden_fields():
    start, end = _window()
    policy = ExportPolicy(enabled=True, telemetry_policy_revision="tp-1", signing_key_id="k-1")
    payload = build_aggregate_export(
        policy=policy,
        export_id="exp-1",
        installation_id="inst-1",
        sequence=1,
        commercial_tenant_ref="acct-1",
        window_start=start,
        window_end=end,
        rows=[
            ExportRow(
                rahkia_alias="rahkia-balanced",
                inference_mode="customer_local",
                usage_quality="estimated",
                attempts=3,
                succeeded=2,
                failed=1,
                retries=1,
                fallbacks=0,
                input_tokens=30,
                output_tokens=15,
                total_tokens=45,
                unknown_usage_count=0,
            )
        ],
    )
    assert payload["schema_version"] == "aggregate_only_v1"
    assert payload["rows"][0]["total_tokens"] == 45
    assert "principal_id" not in json.dumps(payload)
    assert "content" not in json.dumps(payload)

    from app.services.inference.serializer import validate_export_payload

    # A well formed payload passes...
    validate_export_payload(payload)

    # ...an unknown envelope field is refused...
    with pytest.raises(ExportSchemaError):
        validate_export_payload({**payload, "unexpected": 1})

    # ...and content derived or per-call identifiers are refused at any depth.
    for forbidden in ("prompt", "response", "content_hash", "principal_id", "request_id"):
        with pytest.raises(ExportSchemaError):
            validate_export_payload({**payload, "rows": [{"rahkia_alias": "a", forbidden: "x"}]})


# ---------------------------------------------------------------------------
# A11: registry authorization and audit, over HTTP
# ---------------------------------------------------------------------------


async def _grant_local(file_db, *capabilities) -> None:
    async with file_db() as db:
        await governance.grant_platform_operator(
            db, principal_id=LEGACY_PRINCIPAL_ID, capabilities=list(capabilities)
        )
        await db.commit()


async def test_registry_mutations_require_the_inference_capability(client, file_db):
    # A tenant owner holds no platform capability at all.
    denied = await client.post(
        "/api/platform/inference/models",
        json={
            "rahkia_alias": "rahkia-balanced",
            "max_context_tokens": 1024,
            "max_output_tokens": 128,
        },
    )
    assert denied.status_code == 403

    # An ordinary platform metadata reader still cannot mutate.
    await _grant_local(file_db, PlatformCapability.METADATA_READ)
    assert (await client.get("/api/platform/inference/models")).status_code == 200
    still_denied = await client.post(
        "/api/platform/inference/models",
        json={
            "rahkia_alias": "rahkia-balanced",
            "max_context_tokens": 1024,
            "max_output_tokens": 128,
        },
    )
    assert still_denied.status_code == 403

    # The explicit operator can.
    await _grant_local(file_db, PlatformCapability.INFERENCE_ADMIN)
    created = await client.post(
        "/api/platform/inference/models",
        json={
            "rahkia_alias": "rahkia-balanced",
            "artifact_ref": "model://balanced/v1",
            "max_context_tokens": 1024,
            "max_output_tokens": 128,
        },
    )
    assert created.status_code == 201, created.text

    async with file_db() as db:
        audits = (
            await db.execute(
                select(AuditRecord).where(AuditRecord.action.like("inference.registry.%"))
            )
        ).scalars().all()
    assert any(row.action == "inference.registry.model_release.create" for row in audits)


async def test_registry_never_returns_private_endpoint_or_credential_references(client, file_db):
    await _grant_local(
        file_db, PlatformCapability.METADATA_READ, PlatformCapability.INFERENCE_ADMIN
    )
    async with file_db() as db:
        await _seed(db, tenant_id="tnt-local")

    listed = await client.get("/api/platform/inference/deployments")
    assert listed.status_code == 200
    body = listed.text
    assert "endpoint://local" not in body
    assert "cred://local" not in body
    assert "endpoint_ref" not in body
    assert "credential_ref" not in body


async def test_binding_reads_require_an_explicit_tenant_filter(client, file_db):
    await _grant_local(file_db, PlatformCapability.METADATA_READ)
    missing_filter = await client.get("/api/platform/inference/bindings")
    assert missing_filter.status_code == 422


# ---------------------------------------------------------------------------
# A12: the additive migration
# ---------------------------------------------------------------------------


def test_migration_0015_is_additive_and_reversible(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, inspect

    from app.migrations_runner import (
        _alembic_config,
        adopt_and_upgrade,
        current_revision,
        sync_url_for,
    )

    url = f"sqlite+aiosqlite:///{tmp_path / 'inf1a.db'}"
    adopt_and_upgrade(url)
    # Head-agnostic: a later package moves the chain head forward, so assert that
    # 0015 has been applied rather than pinning the current head. Pinning it has
    # broken on every package that added a revision.
    from alembic.script import ScriptDirectory

    from app.migrations_runner import _alembic_config

    assert current_revision(url) is not None
    script = ScriptDirectory.from_config(_alembic_config(sync_url_for(url)))
    chain: set[str] = set()
    cursor: str | None = script.get_current_head()
    while cursor:
        chain.add(cursor)
        revision = script.get_revision(cursor)
        cursor = revision.down_revision if revision else None
    assert "0015_inf1_inference_registry" in chain

    engine = create_engine(sync_url_for(url), future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {
            "inference_model_releases",
            "inference_runtime_profiles",
            "inference_deployments",
            "inference_tenant_bindings",
            "inference_health_observations",
            "inference_routing_decisions",
            "inference_usage_attributions",
        } <= tables
        # The accepted M1 ledger is untouched.
        assert "model_usage_events" in tables
        ddl = (
            engine.connect()
            .exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='platform_operators'"
            )
            .scalar_one()
        )
        assert "platform:inference:admin" in ddl
    finally:
        engine.dispose()

    command.downgrade(_alembic_config(sync_url_for(url)), "0014_operations_admin_capability")
    assert current_revision(url) == "0014_operations_admin_capability"
    engine = create_engine(sync_url_for(url), future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "inference_model_releases" not in tables
        ddl = (
            engine.connect()
            .exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='platform_operators'"
            )
            .scalar_one()
        )
        assert "platform:inference:admin" not in ddl
    finally:
        engine.dispose()
