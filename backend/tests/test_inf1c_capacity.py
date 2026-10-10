"""INF1-C application/platform capacity telemetry acceptance.

Covers the automatable INF1-C acceptance areas on the approved test-model path:
pinned bounded-cardinality metrics, integer-only samples, idempotent records,
pool-window capacity accounting with explicit idle/unattributed time, counter
reset and missing-sample handling, attempt correlation, reconciliation against
M1/M2 and the INF1 attribution sidecar with no one-to-many join inflation,
legacy/unknown attribution made explicit, an aggregate-only opt-in export that
performs zero outbound network attempts while off, idempotent import and
restore, sensitive-field serializer rejection at every nesting level, and the
separation of the platform-operator and client-administrator planes.

NOT RUN here (controlled runtime / production gates): real GPU performance
qualification, SLA proof, Rahkia runtime qualification, production shared-serving
qualification and a live telemetry collector.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.inference import ExportPolicyDisabled, TelemetryError
from app.core.platform import PlatformCapability
from app.core.tenancy import LEGACY_PRINCIPAL_ID
from app.models import (
    CapacityPool,
    InferenceMetricSample,
    InferenceUsageAttribution,
    ModelUsageEvent,
    PlatformTelemetryExport,
    PlatformTelemetryImport,
    Tenant,
)
from app.services import access_governance as governance
from app.services import usage_metering
from app.services.inference import serializer
from app.services.inference import telemetry as tel


def _window(days: int = 1):
    end = datetime.now(timezone.utc)
    return end - timedelta(days=days), end


async def _tenant(db, tenant_id: str) -> Tenant:
    row = await db.get(Tenant, tenant_id)
    if row is None:
        row = Tenant(id=tenant_id, slug=tenant_id, name=tenant_id, status="active")
        db.add(row)
        await db.flush()
    return row


async def _usage_event(
    db, *, tenant_id: str, event_id: str, total: int = 15, started_at: datetime | None = None
) -> ModelUsageEvent:
    started = started_at or datetime.now(timezone.utc)
    row = ModelUsageEvent(
        id=event_id,
        tenant_id=tenant_id,
        principal_id="p-1",
        request_id=f"req-{event_id}",
        provider_route="local",
        model_name="balanced",
        usage_source="estimated",
        input_tokens=total - 5,
        output_tokens=5,
        total_tokens=total,
        call_role="primary",
        idempotency_key=f"{event_id}-key",
        status="succeeded",
        started_at=started,
        finished_at=started,
    )
    db.add(row)
    await db.flush()
    return row


async def _attribute(db, *, usage_event_id: str, tenant_id: str, attempt_id: str) -> InferenceUsageAttribution:
    row = InferenceUsageAttribution(
        usage_event_id=usage_event_id,
        tenant_id=tenant_id,
        business_request_id="biz-1",
        logical_call_id="call-1",
        attempt_id=attempt_id,
        execution_certainty="completed",
        completeness="complete",
        count_method="runtime_reported",
    )
    db.add(row)
    await db.flush()
    return row


async def _usage_metric(db, *, scope: str = "attempt", subject_kind: str = "tenant"):
    return await tel.define_metric(
        db,
        metric_key=tel.METRIC_USAGE_ATTEMPTS,
        metric_scope=scope,
        subject_kind=subject_kind,
        value_kind="counter",
        unit="events",
        origin="collector",
    )


# ---------------------------------------------------------------------------
# Pinned, bounded-cardinality metrics and integer-only samples
# ---------------------------------------------------------------------------


async def test_metric_definition_is_pinned_and_bounded(file_db):
    async with file_db() as db:
        row = await tel.define_metric(
            db,
            metric_key=tel.METRIC_POOL_BUSY_MS,
            metric_scope="pool_window",
            subject_kind="pool",
            value_kind="duration_ms",
            unit="ms",
            origin="runtime",
            gpu_method="measured",
        )
        # Idempotent: an identical definition returns the same row.
        again = await tel.define_metric(
            db,
            metric_key=tel.METRIC_POOL_BUSY_MS,
            metric_scope="pool_window",
            subject_kind="pool",
            value_kind="duration_ms",
            unit="ms",
            origin="runtime",
            gpu_method="measured",
        )
        assert row.id == again.id

        # A high-cardinality definition is refused at the boundary.
        with pytest.raises(TelemetryError):
            await tel.define_metric(
                db,
                metric_key="usage.per_endpoint",
                metric_scope="attempt",
                subject_kind="deployment",
                value_kind="counter",
                unit="events",
                origin="gateway",
                cardinality_class="high",
            )
        # An unknown scope is refused.
        with pytest.raises(TelemetryError):
            await tel.define_metric(
                db,
                metric_key="usage.bad_scope",
                metric_scope="per_message",
                subject_kind="tenant",
                value_kind="counter",
                unit="events",
                origin="gateway",
            )
        # A conflicting redefinition is refused rather than silently changing shape.
        with pytest.raises(TelemetryError):
            await tel.define_metric(
                db,
                metric_key=tel.METRIC_POOL_BUSY_MS,
                metric_scope="pool_window",
                subject_kind="pool",
                value_kind="counter",
                unit="events",
                origin="runtime",
            )
        await db.commit()


async def test_sample_is_integer_and_idempotent_on_replay(file_db):
    start, end = _window()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await _usage_metric(db)
        first = await tel.record_sample(
            db,
            metric_key=tel.METRIC_USAGE_ATTEMPTS,
            subject_kind="tenant",
            subject_ref="tnt-a",
            tenant_id="tnt-a",
            origin="collector",
            window_start=start,
            window_end=end,
            value_int=7,
            sequence=1,
        )
        replay = await tel.record_sample(
            db,
            metric_key=tel.METRIC_USAGE_ATTEMPTS,
            subject_kind="tenant",
            subject_ref="tnt-a",
            tenant_id="tnt-a",
            origin="collector",
            window_start=start,
            window_end=end,
            value_int=7,
            sequence=1,
        )
        await db.commit()
    assert first.id == replay.id
    async with file_db() as db:
        count = await db.scalar(select(func.count()).select_from(InferenceMetricSample))
    assert int(count) == 1


async def test_money_metric_is_minor_units_and_requires_currency(file_db):
    start, end = _window()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await tel.define_metric(
            db,
            metric_key=tel.METRIC_POOL_COST_MINOR,
            metric_scope="pool_window",
            subject_kind="pool",
            value_kind="money_minor",
            unit="USD",
            origin="collector",
        )
        with pytest.raises(TelemetryError):
            await tel.record_sample(
                db,
                metric_key=tel.METRIC_POOL_COST_MINOR,
                subject_kind="pool",
                subject_ref="pool-1",
                pool_id="pool-1",
                origin="collector",
                window_start=start,
                window_end=end,
                value_int=500,
            )
        row = await tel.record_sample(
            db,
            metric_key=tel.METRIC_POOL_COST_MINOR,
            subject_kind="pool",
            subject_ref="pool-1",
            pool_id="pool-1",
            origin="collector",
            window_start=start,
            window_end=end,
            value_int=500,
            currency="USD",
        )
        # No float participates: the stored value is an exact integer minor unit.
        assert isinstance(row.value_int, int)
        assert row.value_int == 500
        await db.commit()


# ---------------------------------------------------------------------------
# Counter reset and missing-sample handling
# ---------------------------------------------------------------------------


def test_counter_reset_detection_is_explicit():
    assert tel.detect_counter_reset(1, 100, 2, 40) is True
    assert tel.detect_counter_reset(1, 100, 2, 150) is False
    # A missing observation is never read as a reset.
    assert tel.detect_counter_reset(1, None, 2, 40) is False
    assert tel.detect_counter_reset(1, 100, 2, None) is False


async def test_counter_reset_and_missing_sample_recorded(file_db):
    start, end = _window()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await _usage_metric(db)
        await tel.record_sample(
            db,
            metric_key=tel.METRIC_USAGE_ATTEMPTS,
            subject_kind="tenant",
            subject_ref="tnt-a",
            tenant_id="tnt-a",
            origin="collector",
            window_start=start,
            window_end=end,
            value_int=100,
            sequence=1,
        )
        reset = await tel.record_sample(
            db,
            metric_key=tel.METRIC_USAGE_ATTEMPTS,
            subject_kind="tenant",
            subject_ref="tnt-a",
            tenant_id="tnt-a",
            origin="collector",
            window_start=end,
            window_end=end + timedelta(hours=1),
            value_int=30,
            sequence=2,
        )
        missing = await tel.record_sample(
            db,
            metric_key=tel.METRIC_USAGE_ATTEMPTS,
            subject_kind="tenant",
            subject_ref="tnt-a",
            tenant_id="tnt-a",
            origin="collector",
            window_start=end + timedelta(hours=1),
            window_end=end + timedelta(hours=2),
            value_int=None,
            quality="unknown",
            sequence=3,
        )
        await db.commit()
    assert reset.counter_reset is True
    # A missing sample is an explicit NULL, never a measured zero.
    assert missing.value_int is None

    async with file_db() as db:
        fleet = await tel.fleet_metadata(db)
    assert fleet["counter_resets"] == 1
    assert fleet["missing_value_samples"] == 1


# ---------------------------------------------------------------------------
# Pool-window capacity accounting: explicit idle and unattributed
# ---------------------------------------------------------------------------


async def test_pool_window_reports_idle_and_unattributed_capacity(file_db):
    start, end = _window()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        db.add(
            CapacityPool(
                pool_id="pool-1", pool_kind="dedicated", owner_tenant_id="tnt-a", concurrency_limit=2
            )
        )
        await db.flush()
        await tel.define_metric(
            db,
            metric_key=tel.METRIC_POOL_BUSY_MS,
            metric_scope="pool_window",
            subject_kind="pool",
            value_kind="duration_ms",
            unit="ms",
            origin="runtime",
        )
        # A busy sample whose attempt has no attribution sidecar.
        await tel.record_sample(
            db,
            metric_key=tel.METRIC_POOL_BUSY_MS,
            subject_kind="pool",
            subject_ref="pool-1",
            pool_id="pool-1",
            tenant_id="tnt-a",
            attempt_id="att-unattributed",
            origin="runtime",
            window_start=start,
            window_end=end,
            value_int=1_000,
        )
        await db.commit()

    async with file_db() as db:
        accounting = await tel.pool_window_accounting(
            db, pool_id="pool-1", window_start=start, window_end=end
        )
    assert accounting["busy_ms"] == 1_000
    assert accounting["capacity_slot_ms"] == 2 * 86_400_000
    # Idle is reported explicitly, never omitted.
    assert accounting["idle_ms"] == accounting["capacity_slot_ms"] - 1_000
    assert accounting["unattributed_attempts"] == 1
    assert accounting["attributed_attempts"] == 0

    # Once the attempt is attributed, it moves out of the unattributed bucket.
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        event = await _usage_event(db, tenant_id="tnt-a", event_id="evt-pool")
        await _attribute(db, usage_event_id=event.id, tenant_id="tnt-a", attempt_id="att-unattributed")
        await db.commit()
    async with file_db() as db:
        accounting = await tel.pool_window_accounting(
            db, pool_id="pool-1", window_start=start, window_end=end
        )
    assert accounting["attributed_attempts"] == 1
    assert accounting["unattributed_attempts"] == 0


# ---------------------------------------------------------------------------
# Reconciliation: no one-to-many inflation, M1 totals conserved, explicit legacy
# ---------------------------------------------------------------------------


async def test_no_one_to_many_join_inflation(file_db):
    """Three samples per attempt must not multiply the M1 attempt count."""
    start, end = _window()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        event = await _usage_event(db, tenant_id="tnt-a", event_id="evt-1", total=15)
        await _usage_metric(db)
        for index in range(3):
            # Distinct windows: three observations of the same attempt counter
            # must not fan the M1 attempt count out when they are reconciled.
            await tel.record_sample(
                db,
                metric_key=tel.METRIC_USAGE_ATTEMPTS,
                subject_kind="tenant",
                subject_ref="tnt-a",
                tenant_id="tnt-a",
                origin="collector",
                window_start=start + timedelta(hours=index),
                window_end=start + timedelta(hours=index + 1),
                value_int=1,
                sequence=index + 1,
            )
        await db.commit()
        event_id = event.id

    async with file_db() as db:
        result = await tel.reconcile_usage(db, tenant_id="tnt-a")
    assert result["m1_attempts"] == 1
    assert result["m1_total_tokens"] == 15
    # The M2 join is one-to-one, so no inflation is observed.
    assert result["join_inflation"] == 0
    assert result["m1_totals_conserved"] is True
    assert result["sample_attempts"] == 3
    assert result["status"] == "variance"
    # Historical usage is untouched by reconciliation (it is read-only).
    async with file_db() as db:
        assert (await db.get(ModelUsageEvent, event_id)).total_tokens == 15


async def test_m1_totals_conserved_and_attribution_explicit(file_db):
    start, end = _window()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        first = await _usage_event(db, tenant_id="tnt-a", event_id="evt-1", total=15)
        await _usage_event(db, tenant_id="tnt-a", event_id="evt-2", total=25)
        await _attribute(db, usage_event_id=first.id, tenant_id="tnt-a", attempt_id="att-1")
        await _usage_metric(db)
        await tel.define_metric(
            db,
            metric_key=tel.METRIC_USAGE_TOTAL_TOKENS,
            metric_scope="attempt",
            subject_kind="tenant",
            value_kind="counter",
            unit="tokens",
            origin="collector",
        )
        await tel.record_sample(
            db,
            metric_key=tel.METRIC_USAGE_ATTEMPTS,
            subject_kind="tenant",
            subject_ref="tnt-a",
            tenant_id="tnt-a",
            origin="collector",
            window_start=start,
            window_end=end,
            value_int=2,
            sequence=1,
        )
        await tel.record_sample(
            db,
            metric_key=tel.METRIC_USAGE_TOTAL_TOKENS,
            subject_kind="tenant",
            subject_ref="tnt-a",
            tenant_id="tnt-a",
            origin="collector",
            window_start=start,
            window_end=end,
            value_int=40,
            sequence=1,
        )
        await db.commit()

    async with file_db() as db:
        result = await tel.reconcile_usage(db, tenant_id="tnt-a")
    assert result["m1_attempts"] == 2
    assert result["m1_total_tokens"] == 40
    assert result["attributed_events"] == 1
    assert result["legacy_unknown_events"] == 1
    assert result["attribution"] == "mixed"
    assert result["attribution_split_consistent"] is True
    assert result["m1_totals_conserved"] is True
    assert result["status"] == "matched"


async def test_legacy_attribution_is_explicit_not_inferred(file_db):
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await _usage_event(db, tenant_id="tnt-a", event_id="evt-legacy")
        await db.commit()
    async with file_db() as db:
        result = await tel.reconcile_usage(db, tenant_id="tnt-a")
    # No sidecar, no samples: the surface says so explicitly.
    assert result["attribution"] == tel.LEGACY_ATTRIBUTION
    assert result["legacy_unknown_events"] == 1
    assert result["attributed_events"] == 0
    assert result["status"] == "insufficient_samples"


async def test_attempt_correlation_marks_unmatched_explicitly(file_db):
    start, end = _window()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await _usage_metric(db)
        await tel.record_sample(
            db,
            metric_key=tel.METRIC_USAGE_ATTEMPTS,
            subject_kind="tenant",
            subject_ref="tnt-a",
            tenant_id="tnt-a",
            attempt_id="att-1",
            origin="collector",
            window_start=start,
            window_end=end,
            value_int=1,
            sequence=1,
        )
        await db.commit()

    async with file_db() as db:
        correlation = await tel.correlate_attempts(
            db, tenant_id="tnt-a", window_start=start, window_end=end
        )
    assert correlation["attempts"] == 1
    assert correlation["unmatched"] == 1
    assert correlation["rows"][0]["attribution"] == tel.LEGACY_ATTRIBUTION

    async with file_db() as db:
        event = await _usage_event(db, tenant_id="tnt-a", event_id="evt-1")
        await _attribute(db, usage_event_id=event.id, tenant_id="tnt-a", attempt_id="att-1")
        await db.commit()
    async with file_db() as db:
        correlation = await tel.correlate_attempts(
            db, tenant_id="tnt-a", window_start=start, window_end=end
        )
    assert correlation["matched"] == 1
    assert correlation["rows"][0]["attribution"] == "attributed"


# ---------------------------------------------------------------------------
# Collector outage must not block local inference
# ---------------------------------------------------------------------------


async def test_collector_outage_does_not_block_local_inference(file_db, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("telemetry must not touch the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    async with file_db() as db:
        await _tenant(db, "tnt-a")
        # A batch of samples with no usable definition: ingest is best-effort and
        # reports the outage explicitly instead of raising into the caller.
        outcome = await tel.ingest_collector_batch(
            db,
            [
                {
                    "metric_key": "usage.attempts",
                    "subject_kind": "tenant",
                    "subject_ref": "tnt-a",
                    "origin": "collector",
                    "window_start": datetime.now(timezone.utc),
                    "window_end": datetime.now(timezone.utc) + timedelta(hours=1),
                    "value_int": 1,
                }
            ],
        )
        assert outcome["recorded"] == 0
        assert outcome["failed"] == 1
        assert outcome["collector_unavailable"] is True

        # Local inference metering still succeeds while telemetry is down.
        event = await usage_metering.record_model_usage(
            db,
            context=usage_metering.UsageContext(
                tenant_id="tnt-a", request_id="req-1", provider_route="local", model_name="balanced"
            ),
            prompt_text="hello",
            completion_text="world",
        )
        await db.commit()
    assert event is not None
    assert event.total_tokens > 0


# ---------------------------------------------------------------------------
# Aggregate-only opt-in export: OFF means zero outbound network attempt
# ---------------------------------------------------------------------------


def test_export_policy_defaults_off_and_build_refuses_without_network(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("a disabled export must not open a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    with pytest.raises(ExportPolicyDisabled):
        # build_aggregate_export is the pure serializer the service delegates to;
        # it refuses before any I/O when the policy is off.
        serializer.build_aggregate_export(
            policy=serializer.ExportPolicy(),
            export_id="exp-1",
            installation_id="inst-1",
            sequence=1,
            commercial_tenant_ref="acct-1",
            window_start=datetime.now(timezone.utc) - timedelta(days=1),
            window_end=datetime.now(timezone.utc),
            rows=[serializer.ExportRow(rahkia_alias="balanced", attempts=1)],
        )


async def test_export_off_build_refuses_before_reading(file_db, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("a disabled export must not touch the database or network")

    start, end = _window()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        # Poison the session so any read would be observable.
        monkeypatch.setattr(db, "execute", _boom)
        with pytest.raises(ExportPolicyDisabled):
            await tel.build_export(
                db,
                policy=serializer.ExportPolicy(),
                installation_id="inst-1",
                commercial_tenant_ref="acct-1",
                sequence=1,
                window_start=start,
                window_end=end,
                tenant_id="tnt-a",
            )


async def test_sensitive_field_rejection_at_every_nesting_level():
    payload = {
        "schema_version": "aggregate_only_v1",
        "export_id": "exp-1",
        "installation_id": "inst-1",
        "sequence": 1,
        "commercial_tenant_ref": "acct-1",
        "window_start": datetime.now(timezone.utc).isoformat(),
        "window_end": datetime.now(timezone.utc).isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "telemetry_policy_revision": "tp-1",
        "signing_key_id": "k-1",
        "rows": [{"rahkia_alias": "balanced", "attempts": 1}],
        "correction_of": None,
        "payload_digest": None,
        "signature": None,
    }
    # A well formed payload passes.
    serializer.validate_export_payload(payload)

    # A forbidden name is refused at any depth, even nested inside a row.
    for forbidden in ("prompt", "response", "content_hash", "principal_id", "request_id", "attempt_id"):
        nested = json.loads(json.dumps(payload))
        nested["rows"][0]["deep"] = {"inner": {forbidden: "x"}}
        with pytest.raises(serializer.ExportSchemaError):
            serializer.validate_export_payload(nested)

    # An unknown envelope field is refused...
    with pytest.raises(serializer.ExportSchemaError):
        serializer.validate_export_payload({**payload, "unexpected": 1})
    # ...and so is an unknown row field.
    with pytest.raises(serializer.ExportSchemaError):
        serializer.validate_export_payload(
            {**payload, "rows": [{"rahkia_alias": "balanced", "unknown_field": 1}]}
        )


async def test_export_enabled_records_once_and_import_is_idempotent(file_db):
    start, end = _window()
    policy = serializer.ExportPolicy(
        enabled=True, telemetry_policy_revision="tp-1", signing_key_id="k-1"
    )
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await _usage_event(db, tenant_id="tnt-a", event_id="evt-1", total=15, started_at=start)
        await db.commit()

    async with file_db() as db:
        built, record = await tel.build_export(
            db,
            policy=policy,
            installation_id="inst-1",
            commercial_tenant_ref="acct-1",
            sequence=1,
            window_start=start,
            window_end=end,
            tenant_id="tnt-a",
            export_id="exp-1",
        )
        await db.commit()
    assert built["schema_version"] == "aggregate_only_v1"
    assert built["rows"][0]["total_tokens"] == 15
    assert "principal_id" not in json.dumps(built)
    assert "content" not in json.dumps(built)

    # A replay of the same export id records no second export.
    async with file_db() as db:
        _built2, record2 = await tel.build_export(
            db,
            policy=policy,
            installation_id="inst-1",
            commercial_tenant_ref="acct-1",
            sequence=1,
            window_start=start,
            window_end=end,
            tenant_id="tnt-a",
            export_id="exp-1",
        )
        await db.commit()
    assert record2.id == record.id

    async with file_db() as db:
        assert int(await db.scalar(select(func.count()).select_from(PlatformTelemetryExport))) == 1


async def test_import_and_restore_do_not_duplicate_exports_or_usage(file_db):
    start, end = _window()
    policy = serializer.ExportPolicy(enabled=True, telemetry_policy_revision="tp-1")
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await _usage_event(db, tenant_id="tnt-a", event_id="evt-1", total=15, started_at=start)
        built, _record = await tel.build_export(
            db,
            policy=policy,
            installation_id="inst-1",
            commercial_tenant_ref="acct-1",
            sequence=1,
            window_start=start,
            window_end=end,
            tenant_id="tnt-a",
            export_id="exp-1",
        )
        await db.commit()

    # Import twice (a fresh import and a restore replay).
    async with file_db() as db:
        first = await tel.import_export(db, payload=built)
        await db.commit()
    async with file_db() as db:
        again = await tel.import_export(db, payload=built)
        await db.commit()
    assert first.id == again.id

    async with file_db() as db:
        imports = int(await db.scalar(select(func.count()).select_from(PlatformTelemetryImport)))
        exports = int(await db.scalar(select(func.count()).select_from(PlatformTelemetryExport)))
        usage = int(await db.scalar(select(func.count()).select_from(ModelUsageEvent)))
    assert imports == 1, "a restore must not duplicate an imported export"
    assert exports == 1
    assert usage == 1, "historical usage must not be duplicated by an import"


# ---------------------------------------------------------------------------
# Plane separation over HTTP
# ---------------------------------------------------------------------------


async def _grant(file_db, *capabilities) -> None:
    async with file_db() as db:
        await governance.grant_platform_operator(
            db, principal_id=LEGACY_PRINCIPAL_ID, capabilities=list(capabilities)
        )
        await db.commit()


async def test_platform_plane_requires_a_platform_capability(client, file_db):
    # A tenant owner holds no platform capability and is refused.
    denied = await client.get("/api/platform/capacity/fleet")
    assert denied.status_code == 403

    await _grant(file_db, PlatformCapability.METADATA_READ)
    assert (await client.get("/api/platform/capacity/fleet")).status_code == 200

    # A metadata reader cannot mutate (pin a metric).
    forbidden = await client.post(
        "/api/platform/capacity/metrics",
        json={
            "metric_key": "usage.attempts",
            "metric_scope": "attempt",
            "subject_kind": "tenant",
            "value_kind": "counter",
            "unit": "events",
            "origin": "collector",
        },
    )
    assert forbidden.status_code == 403

    await _grant(file_db, PlatformCapability.INFERENCE_ADMIN)
    created = await client.post(
        "/api/platform/capacity/metrics",
        json={
            "metric_key": "usage.attempts",
            "metric_scope": "attempt",
            "subject_kind": "tenant",
            "value_kind": "counter",
            "unit": "events",
            "origin": "collector",
        },
    )
    assert created.status_code == 201, created.text


async def test_export_http_refuses_when_disabled(client, file_db):
    await _grant(file_db, PlatformCapability.INFERENCE_ADMIN)
    start, end = _window()
    response = await client.post(
        "/api/platform/capacity/exports",
        json={
            "installation_id": "inst-1",
            "commercial_tenant_ref": "acct-1",
            "sequence": 1,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "tenant_id": "tnt-a",
        },
    )
    assert response.status_code == 409


async def test_client_admin_sees_only_own_summary_without_cost_or_fleet(client, file_db):
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await _tenant(db, "tnt-b")
        await _usage_event(db, tenant_id="tnt-a", event_id="evt-a", total=15)
        await _usage_event(db, tenant_id="tnt-b", event_id="evt-b", total=99)
        await db.commit()

    response = await client.get("/api/client/capacity/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "tnt-local"
    rendered = json.dumps(body)
    # No fleet metadata, no cost or wholesale figure, and no other tenant.
    assert "cost" not in rendered
    assert "fleet" not in rendered
    assert "wholesale" not in rendered
    assert "tnt-a" not in rendered
    assert "tnt-b" not in rendered


# ---------------------------------------------------------------------------
# Migration 0019: additive, idempotent and reversible (head-agnostic)
# ---------------------------------------------------------------------------


def test_migration_0019_is_additive_and_reversible(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    from app.migrations_runner import (
        _alembic_config,
        adopt_and_upgrade,
        current_revision,
        sync_url_for,
    )

    url = f"sqlite+aiosqlite:///{tmp_path / 'inf1c.db'}"
    adopt_and_upgrade(url)
    # Head-agnostic: a later package moves the chain head forward, so assert that
    # 0019 has been applied rather than pinning the current head.
    assert current_revision(url) is not None
    assert len("0019_inf1c_capacity") <= 32
    script = ScriptDirectory.from_config(_alembic_config(sync_url_for(url)))
    chain: set[str] = set()
    cursor: str | None = script.get_current_head()
    while cursor:
        chain.add(cursor)
        revision = script.get_revision(cursor)
        cursor = revision.down_revision if revision else None
    assert "0019_inf1c_capacity" in chain

    engine = create_engine(sync_url_for(url), future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {
            "inference_metric_definitions",
            "inference_metric_samples",
            "platform_telemetry_exports",
            "platform_telemetry_imports",
        } <= tables
        # Earlier accepted tables are untouched.
        assert "model_usage_events" in tables
        assert "capacity_pools" in tables
        assert "inference_usage_attributions" in tables
    finally:
        engine.dispose()

    command.downgrade(_alembic_config(sync_url_for(url)), "0017_inf1b_admission")
    engine = create_engine(sync_url_for(url), future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "inference_metric_samples" not in tables
        assert "inference_metric_definitions" not in tables
        assert "platform_telemetry_exports" not in tables
        assert "platform_telemetry_imports" not in tables
        assert "capacity_pools" in tables
        assert "model_usage_events" in tables
    finally:
        engine.dispose()

    # A re-upgrade is clean.
    command.upgrade(_alembic_config(sync_url_for(url)), "head")
    assert current_revision(url) is not None

