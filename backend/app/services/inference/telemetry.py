"""INF1-C application/platform capacity telemetry and reconciliation.

This is the application-side telemetry layer. It builds on the accepted INF1
contracts (:mod:`app.core.inference`), the M1 immutable usage ledger
(``model_usage_events``), the INF1-A attribution sidecar
(``inference_usage_attributions``) and M2 aggregation
(:mod:`app.services.usage_aggregation`). It adds no pricing, no entitlement and
no billing logic, and it stores no content.

Design rules carried from the accepted INF1 contract and the lane brief:

* **Pinned metrics.** A measurement is named by a pinned definition
  (``metric_key`` + ``definition_revision`` + scope + value kind), never by a
  free string, so the operational surface has bounded cardinality. A
  high-cardinality definition is refused at this boundary.
* **Integers only.** Every stored value is an integer. A cost or money value is
  an integer minor unit; a GPU duration is integer milliseconds; an unobservable
  value is ``NULL`` (an explicit *missing sample*), never a measured zero. No
  float participates in a cost, a counter or a duration.
* **Idempotent, replay safe.** A sample is unique on (definition, subject kind,
  subject, window); an export is unique on its export id and installation
  sequence; an import is unique on the source export id. A restore or a replay
  therefore cannot duplicate a record or double count historical usage.
* **No one-to-many inflation.** Reconciliation reads the M1 ledger, the M2
  one-to-one attribution join and the independent sample aggregate as separate
  query results and compares them; it never joins one row to many samples, so a
  count or a token sum cannot fan out.
* **Legacy/unknown is explicit.** A usage event with no tenant-consistent
  attribution sidecar is reported exactly as ``legacy_unknown``, never inferred
  from a provider route, a model name or an endpoint.
* **Export is opt-in and off by default.** While the policy is disabled the
  exporter refuses before it reads the database or opens any socket, so a
  disabled export performs zero outbound network attempts. The serializer rejects
  prompt/response/content-hash/principal/request identifiers at every nesting
  level.
* **Non-blocking on collector outage.** Ingesting a collector batch is
  best-effort: a telemetry outage is reported explicitly and never raises into
  the local inference path.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from uuid import uuid4

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.inference import (
    ExportPolicyDisabled,
    GpuMethod,
    MeasurementOrigin,
    TelemetryError,
    ensure_aware,
    utc_now,
)
from app.core.usage import USAGE_STATUS_FAILED, USAGE_STATUS_SUCCEEDED
from app.models import (
    CapacityPool,
    InferenceMetricDefinition,
    InferenceMetricSample,
    InferenceTenantBinding,
    InferenceUsageAttribution,
    ModelUsageEvent,
    PlatformTelemetryExport,
    PlatformTelemetryImport,
)
from app.services import usage_aggregation, usage_metering
from app.services.inference import serializer

SCHEMA_VERSION = "inf1c.telemetry.v1"

METRIC_SCOPES: tuple[str, ...] = ("attempt", "pool_window")
SUBJECT_KINDS: tuple[str, ...] = ("runtime", "deployment", "pool", "tenant")
VALUE_KINDS: tuple[str, ...] = ("counter", "gauge", "duration_ms", "gpu_ms", "money_minor")
ORIGINS: tuple[str, ...] = tuple(origin.value for origin in MeasurementOrigin)
METHODS: tuple[str, ...] = tuple(method.value for method in GpuMethod)
QUALITIES: tuple[str, ...] = ("complete", "partial", "unknown")

# The fixed metric keys this lane reconciles against. Kept as constants so a
# caller cannot invent a reconciliation series by naming it.
METRIC_USAGE_ATTEMPTS = "usage.attempts"
METRIC_USAGE_TOTAL_TOKENS = "usage.total_tokens"
METRIC_POOL_BUSY_MS = "capacity.pool_busy_ms"
METRIC_POOL_COST_MINOR = "capacity.pool_cost_minor"

# Attribution coverage vocabulary, mirroring M2. A sidecar-less M1 row is this
# exact literal and is never derived from an incidental string.
LEGACY_ATTRIBUTION = usage_aggregation.LEGACY_ATTRIBUTION  # "legacy_unknown"

RATE_METRIC_KEY = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")

MAX_EXPORT_ROWS = usage_aggregation.MAX_EXPORT_ROWS
MAX_CORRELATION_ROWS = 1000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_int(value: Any, field_name: str) -> int:
    """A strict non-negative integer, rejecting booleans and floats."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TelemetryError(f"{field_name}: expected a non-negative integer")
    if value < 0:
        raise TelemetryError(f"{field_name}: negative value")
    return value


def detect_counter_reset(
    previous_sequence: int, previous_value: int | None, sequence: int, value: int | None
) -> bool:
    """A cumulative counter that goes backwards across a sequence is a reset.

    Returning ``True`` is what keeps a reset from being read as negative
    consumption: the sample is retained and flagged rather than adjusted.
    """
    if previous_value is None or value is None:
        return False
    return sequence > previous_sequence and value < previous_value


# ---------------------------------------------------------------------------
# Pinned metric definitions
# ---------------------------------------------------------------------------


async def define_metric(
    db: AsyncSession,
    *,
    metric_key: str,
    metric_scope: str,
    subject_kind: str,
    value_kind: str,
    unit: str,
    origin: str,
    gpu_method: str | None = None,
    definition_revision: int = 1,
    cardinality_class: str = "bounded",
    description: str | None = None,
) -> InferenceMetricDefinition:
    """Pin one metric identity, or return the existing identical definition.

    A high-cardinality definition is refused: an operational surface is bounded
    by construction, so an unbounded series is never registered through it.
    """
    if not isinstance(metric_key, str) or not RATE_METRIC_KEY.match(metric_key):
        raise TelemetryError("metric_key must be a bounded lower-case identity")
    if metric_scope not in METRIC_SCOPES:
        raise TelemetryError(f"unsupported metric_scope {metric_scope!r}")
    if subject_kind not in SUBJECT_KINDS:
        raise TelemetryError(f"unsupported subject_kind {subject_kind!r}")
    if value_kind not in VALUE_KINDS:
        raise TelemetryError(f"unsupported value_kind {value_kind!r}")
    if origin not in ORIGINS:
        raise TelemetryError(f"unsupported origin {origin!r}")
    if gpu_method is not None and gpu_method not in METHODS:
        raise TelemetryError(f"unsupported gpu_method {gpu_method!r}")
    if cardinality_class != "bounded":
        raise TelemetryError("only bounded-cardinality metrics may be registered")
    if not unit or not str(unit).strip():
        raise TelemetryError("a metric requires a unit")
    _coerce_int(definition_revision, "definition_revision")

    existing = (
        await db.execute(
            select(InferenceMetricDefinition).where(
                InferenceMetricDefinition.metric_key == metric_key,
                InferenceMetricDefinition.definition_revision == definition_revision,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.metric_scope != metric_scope
            or existing.subject_kind != subject_kind
            or existing.value_kind != value_kind
            or existing.unit != unit
            or existing.origin != origin
            or existing.gpu_method != gpu_method
        ):
            raise TelemetryError("metric definition conflicts with a different one")
        return existing

    row = InferenceMetricDefinition(
        metric_key=metric_key,
        definition_revision=definition_revision,
        metric_scope=metric_scope,
        subject_kind=subject_kind,
        value_kind=value_kind,
        unit=str(unit).strip(),
        origin=origin,
        gpu_method=gpu_method,
        cardinality_class=cardinality_class,
        status="active",
        description=description,
    )
    db.add(row)
    await db.flush()
    return row


async def _active_definition(
    db: AsyncSession, *, metric_key: str, metric_revision: int | None
) -> InferenceMetricDefinition | None:
    query = select(InferenceMetricDefinition).where(
        InferenceMetricDefinition.metric_key == metric_key,
        InferenceMetricDefinition.status == "active",
    )
    if metric_revision is not None:
        query = query.where(InferenceMetricDefinition.definition_revision == metric_revision)
    query = query.order_by(InferenceMetricDefinition.definition_revision.desc()).limit(1)
    return (await db.execute(query)).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------


def _subject_ref_for(
    *, subject_kind: str, subject_ref: str | None, tenant_id: str | None,
    pool_id: str | None, deployment_id: str | None,
) -> str:
    value = subject_ref or {
        "pool": pool_id,
        "deployment": deployment_id,
        "tenant": tenant_id,
        "runtime": deployment_id,
    }.get(subject_kind)
    if not value:
        raise TelemetryError(f"a {subject_kind} sample requires a subject reference")
    if "://" in value or "/" in value:
        raise TelemetryError("subject_ref must be an opaque identifier, not a URL")
    return value


async def record_sample(
    db: AsyncSession,
    *,
    metric_key: str,
    subject_kind: str,
    origin: str,
    window_start: datetime,
    window_end: datetime,
    value_int: int | None,
    metric_revision: int | None = None,
    subject_ref: str | None = None,
    method: str = "unknown",
    quality: str = "unknown",
    tenant_id: str | None = None,
    pool_id: str | None = None,
    deployment_id: str | None = None,
    attempt_id: str | None = None,
    currency: str | None = None,
    sequence: int = 0,
    observed_at: datetime | None = None,
) -> InferenceMetricSample:
    """Record one pinned sample, idempotent on (definition, subject, window).

    ``value_int`` of ``None`` is an explicit missing sample, never a zero. A
    decreasing cumulative counter sets ``counter_reset`` on the stored row.
    """
    definition = await _active_definition(db, metric_key=metric_key, metric_revision=metric_revision)
    if definition is None:
        raise TelemetryError(f"no active metric definition for {metric_key!r}")
    if subject_kind != definition.subject_kind:
        raise TelemetryError("sample subject_kind disagrees with the metric definition")
    if origin not in ORIGINS:
        raise TelemetryError(f"unsupported origin {origin!r}")
    if method not in METHODS:
        raise TelemetryError(f"unsupported method {method!r}")
    if quality not in QUALITIES:
        raise TelemetryError(f"unsupported quality {quality!r}")
    if definition.value_kind == "money_minor" and not currency:
        raise TelemetryError("a money metric requires a currency")
    if value_int is not None:
        value_int = _coerce_int(value_int, "value_int")
    _coerce_int(sequence, "sequence")
    window_start = ensure_aware(window_start)
    window_end = ensure_aware(window_end)
    if window_end <= window_start:
        raise TelemetryError("window_end must be after window_start")

    ref = _subject_ref_for(
        subject_kind=subject_kind, subject_ref=subject_ref, tenant_id=tenant_id,
        pool_id=pool_id, deployment_id=deployment_id,
    )

    existing = (
        await db.execute(
            select(InferenceMetricSample).where(
                InferenceMetricSample.metric_definition_id == definition.id,
                InferenceMetricSample.subject_kind == subject_kind,
                InferenceMetricSample.subject_ref == ref,
                InferenceMetricSample.window_start == window_start,
                InferenceMetricSample.window_end == window_end,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    previous = (
        await db.execute(
            select(InferenceMetricSample)
            .where(
                InferenceMetricSample.metric_definition_id == definition.id,
                InferenceMetricSample.subject_kind == subject_kind,
                InferenceMetricSample.subject_ref == ref,
            )
            .order_by(InferenceMetricSample.sequence.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    reset = detect_counter_reset(
        previous.sequence if previous else 0,
        previous.value_int if previous else None,
        sequence,
        value_int,
    )

    row = InferenceMetricSample(
        metric_definition_id=definition.id,
        metric_key=definition.metric_key,
        metric_revision=definition.definition_revision,
        subject_kind=subject_kind,
        subject_ref=ref,
        tenant_id=tenant_id,
        pool_id=pool_id or (ref if subject_kind == "pool" else None),
        deployment_id=deployment_id or (ref if subject_kind == "deployment" else None),
        attempt_id=attempt_id,
        window_start=window_start,
        window_end=window_end,
        value_int=value_int,
        currency=currency,
        origin=origin,
        method=method,
        quality=quality,
        sequence=sequence,
        counter_reset=reset,
        observed_at=ensure_aware(observed_at or utc_now()),
    )
    db.add(row)
    await db.flush()
    return row


async def ingest_collector_batch(
    db: AsyncSession, batch: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Best-effort ingest of a collector batch.

    A collector outage must never block local inference, so this path never
    raises: a failed item is counted and reported explicitly rather than being
    swallowed into a plausible zero. When nothing could be recorded the batch is
    reported as an explicit collector-unavailable outcome.
    """
    recorded = 0
    failed = 0
    for item in batch or ():
        try:
            row = await record_sample(db, **dict(item))
            if row is not None:
                recorded += 1
        except Exception:  # noqa: BLE001 - a telemetry outage must not propagate
            failed += 1
    return {
        "recorded": recorded,
        "failed": failed,
        "collector_unavailable": failed > 0 and recorded == 0,
    }


# ---------------------------------------------------------------------------
# Pool-window capacity accounting
# ---------------------------------------------------------------------------


async def pool_window_accounting(
    db: AsyncSession, *, pool_id: str, window_start: datetime, window_end: datetime
) -> dict[str, Any]:
    """Pool-window capacity accounting with explicit idle and unattributed time.

    Capacity is the pool's concurrency limit multiplied by the window; busy time
    is the measured duration/gpu samples. Idle time is reported explicitly
    (never omitted) and unattributed busy time is separated from attributed busy
    time, so an operator can see exactly how much capacity was neither used nor
    attributable.
    """
    window_start = ensure_aware(window_start)
    window_end = ensure_aware(window_end)
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")
    window_ms = int((window_end - window_start).total_seconds() * 1000)

    pool = await db.get(CapacityPool, pool_id)
    limit = pool.concurrency_limit if pool is not None else None
    capacity_slot_ms = limit * window_ms if limit is not None else None

    rows = (
        await db.execute(
            select(InferenceMetricSample).where(
                InferenceMetricSample.pool_id == pool_id,
                InferenceMetricSample.window_start >= window_start,
                InferenceMetricSample.window_end <= window_end,
            )
        )
    ).scalars().all()

    busy_ms = sum(
        int(row.value_int)
        for row in rows
        if row.value_int is not None and row.metric_key in (METRIC_POOL_BUSY_MS,)
    )
    missing_value_samples = sum(1 for row in rows if row.value_int is None)
    unknown_quality = sum(1 for row in rows if row.quality == "unknown")
    counter_resets = sum(1 for row in rows if row.counter_reset)

    attempt_ids = {row.attempt_id for row in rows if row.attempt_id}
    attributed_attempts = 0
    if attempt_ids:
        attributed_attempts = int(
            (
                await db.execute(
                    select(func.count(func.distinct(InferenceUsageAttribution.attempt_id))).where(
                        InferenceUsageAttribution.attempt_id.in_(attempt_ids)
                    )
                )
            ).scalar_one()
            or 0
        )
    unattributed_attempts = max(0, len(attempt_ids) - attributed_attempts)

    idle_ms = None
    if capacity_slot_ms is not None:
        idle_ms = max(0, capacity_slot_ms - busy_ms)

    return {
        "schema_version": SCHEMA_VERSION,
        "pool_id": pool_id,
        "pool_kind": pool.pool_kind if pool is not None else None,
        "concurrency_limit": limit,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "capacity_slot_ms": capacity_slot_ms,
        "busy_ms": busy_ms,
        "idle_ms": idle_ms,
        "has_samples": len(rows) > 0,
        "sample_count": len(rows),
        "missing_value_samples": missing_value_samples,
        "unknown_quality_samples": unknown_quality,
        "counter_resets": counter_resets,
        "attributed_attempts": attributed_attempts,
        "unattributed_attempts": unattributed_attempts,
        "generated_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Reconciliation against M1/M2 and INF1 attribution
# ---------------------------------------------------------------------------


async def reconcile_usage(
    db: AsyncSession, *, tenant_id: str, window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, Any]:
    """Reconcile the tenant's M1 ledger, the M2 attribution view and telemetry.

    M1 totals are read from the immutable ledger; the attribution split comes
    from the M2 one-to-one join; the sample totals come from an independent
    aggregate query. The three are compared rather than joined, so a sample can
    never fan out a usage count or a token sum.
    """
    if not tenant_id:
        raise ValueError("a tenant_id is required")

    m1 = await usage_metering.summarize_usage(db, tenant_id=tenant_id)
    m2 = await usage_aggregation.aggregate_usage(db, tenant_id=tenant_id, period="total")
    totals = m2["totals"]

    sample_attempts = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(InferenceMetricSample.value_int), 0)).where(
                    InferenceMetricSample.metric_key == METRIC_USAGE_ATTEMPTS,
                    InferenceMetricSample.tenant_id == tenant_id,
                    InferenceMetricSample.value_int.is_not(None),
                )
            )
        ).scalar_one()
        or 0
    )
    sample_total_tokens = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(InferenceMetricSample.value_int), 0)).where(
                    InferenceMetricSample.metric_key == METRIC_USAGE_TOTAL_TOKENS,
                    InferenceMetricSample.tenant_id == tenant_id,
                    InferenceMetricSample.value_int.is_not(None),
                )
            )
        ).scalar_one()
        or 0
    )
    sample_count = int(
        (
            await db.execute(
                select(func.count()).select_from(InferenceMetricSample).where(
                    InferenceMetricSample.tenant_id == tenant_id
                )
            )
        ).scalar_one()
        or 0
    )

    m1_attempts = int(m1["events"])
    m1_total_tokens = int(m1["total_tokens"])
    attributed_events = int(totals["attributed_events"])
    legacy_unknown_events = int(totals["legacy_unknown_events"])
    # The M2 attempts count comes from the one-to-one join, so it must equal the
    # plain M1 count. A disagreement would be join inflation, and is surfaced.
    join_inflation = int(totals["attempts"]) - m1_attempts

    variance_attempts = sample_attempts - m1_attempts
    variance_tokens = sample_total_tokens - m1_total_tokens

    if sample_count == 0:
        status = "insufficient_samples"
    elif variance_attempts == 0 and variance_tokens == 0 and join_inflation == 0:
        status = "matched"
    else:
        status = "variance"

    return {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "m1_attempts": m1_attempts,
        "m1_input_tokens": int(m1["input_tokens"]),
        "m1_output_tokens": int(m1["output_tokens"]),
        "m1_total_tokens": m1_total_tokens,
        "attributed_events": attributed_events,
        "legacy_unknown_events": legacy_unknown_events,
        "attribution": totals["attribution"],
        "attribution_split_consistent": (attributed_events + legacy_unknown_events) == m1_attempts,
        "join_inflation": join_inflation,
        "m1_totals_conserved": join_inflation == 0,
        "sample_attempts": sample_attempts,
        "sample_total_tokens": sample_total_tokens,
        "variance_attempts": variance_attempts,
        "variance_tokens": variance_tokens,
        "status": status,
        "generated_at": utcnow().isoformat(),
    }


async def correlate_attempts(
    db: AsyncSession, *, tenant_id: str, window_start: datetime, window_end: datetime,
    limit: int = 500,
) -> dict[str, Any]:
    """Correlate attempt-scoped samples to the INF1 attribution sidecar.

    Correlation is only claimed where the current evidence supports it: a sample
    is ``matched`` only when an attribution sidecar records the same attempt for
    the same tenant. Anything else is reported explicitly as
    ``legacy_unknown`` rather than being guessed from a provider route.
    """
    window_start = ensure_aware(window_start)
    window_end = ensure_aware(window_end)
    limit = max(1, min(int(limit), MAX_CORRELATION_ROWS))
    rows = (
        await db.execute(
            select(InferenceMetricSample.attempt_id)
            .where(
                InferenceMetricSample.tenant_id == tenant_id,
                InferenceMetricSample.attempt_id.is_not(None),
                InferenceMetricSample.window_start >= window_start,
                InferenceMetricSample.window_end <= window_end,
            )
            .group_by(InferenceMetricSample.attempt_id)
            .order_by(InferenceMetricSample.attempt_id)
            .limit(limit + 1)
        )
    ).scalars().all()
    if len(rows) > limit:
        raise ValueError(f"correlation exceeds the {limit}-attempt bound; narrow the window")
    attempt_ids = [row for row in rows if row]

    matched: dict[str, str] = {}
    if attempt_ids:
        attributions = (
            await db.execute(
                select(InferenceUsageAttribution.attempt_id, InferenceUsageAttribution.usage_event_id).where(
                    InferenceUsageAttribution.tenant_id == tenant_id,
                    InferenceUsageAttribution.attempt_id.in_(attempt_ids),
                )
            )
        ).all()
        matched = {row[0]: row[1] for row in attributions}

    correlated = [
        {
            "attempt_id": attempt_id,
            "attribution": "attributed" if attempt_id in matched else LEGACY_ATTRIBUTION,
            "usage_event_id": matched.get(attempt_id),
        }
        for attempt_id in attempt_ids
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "attempts": len(attempt_ids),
        "matched": len(matched),
        "unmatched": max(0, len(attempt_ids) - len(matched)),
        "rows": correlated,
        "generated_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Aggregate-only export / import (opt-in, off by default)
# ---------------------------------------------------------------------------


async def rows_from_usage(
    db: AsyncSession, *, tenant_id: str, window_start: datetime, window_end: datetime
) -> list[serializer.ExportRow]:
    """Bounded aggregate rows derived from the immutable M1 ledger.

    Only counts and token sums leave this function; the recorded model identity
    populates the export's ``rahkia_alias`` dimension, and no per-call identifier
    or content is ever selected.
    """
    window_start = ensure_aware(window_start)
    window_end = ensure_aware(window_end)
    stmt = (
        select(
            ModelUsageEvent.model_name.label("model_name"),
            ModelUsageEvent.provider_route.label("provider_route"),
            ModelUsageEvent.usage_source.label("usage_source"),
            func.count().label("attempts"),
            func.coalesce(
                func.sum(case((ModelUsageEvent.status == USAGE_STATUS_SUCCEEDED, 1), else_=0)), 0
            ).label("succeeded"),
            func.coalesce(
                func.sum(case((ModelUsageEvent.status == USAGE_STATUS_FAILED, 1), else_=0)), 0
            ).label("failed"),
            func.coalesce(func.sum(ModelUsageEvent.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ModelUsageEvent.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(ModelUsageEvent.total_tokens), 0).label("total_tokens"),
        )
        .where(
            ModelUsageEvent.tenant_id == tenant_id,
            ModelUsageEvent.started_at >= window_start,
            ModelUsageEvent.started_at < window_end,
        )
        .group_by(
            ModelUsageEvent.model_name,
            ModelUsageEvent.provider_route,
            ModelUsageEvent.usage_source,
        )
        .order_by(ModelUsageEvent.model_name)
        .limit(MAX_EXPORT_ROWS + 1)
    )
    rows = (await db.execute(stmt)).all()
    if len(rows) > MAX_EXPORT_ROWS:
        raise ValueError(f"export exceeds the {MAX_EXPORT_ROWS}-row bound; narrow the window")

    result: list[serializer.ExportRow] = []
    for row in rows:
        mapping = row._mapping
        result.append(
            serializer.ExportRow(
                rahkia_alias=str(mapping["model_name"]),
                inference_mode=None,
                usage_quality=str(mapping["usage_source"]),
                attempts=int(mapping["attempts"] or 0),
                succeeded=int(mapping["succeeded"] or 0),
                failed=int(mapping["failed"] or 0),
                input_tokens=int(mapping["input_tokens"] or 0),
                output_tokens=int(mapping["output_tokens"] or 0),
                total_tokens=int(mapping["total_tokens"] or 0),
            )
        )
    return result


async def build_export(
    db: AsyncSession,
    *,
    policy: serializer.ExportPolicy,
    installation_id: str,
    commercial_tenant_ref: str,
    sequence: int,
    window_start: datetime,
    window_end: datetime,
    tenant_id: str,
    export_id: str | None = None,
    correction_of: str | None = None,
    generated_at: datetime | None = None,
) -> tuple[dict[str, Any], PlatformTelemetryExport]:
    """Build and record one aggregate-only export, or refuse when disabled.

    The policy check runs first: while export is off this function neither reads
    the database nor opens a socket, so a disabled export is a guaranteed zero
    outbound network attempt. The build is idempotent on ``export_id``.
    """
    if not policy.enabled:
        raise ExportPolicyDisabled("aggregate telemetry export is disabled")

    window_start = ensure_aware(window_start)
    window_end = ensure_aware(window_end)
    export_id = export_id or uuid4().hex
    _coerce_int(sequence, "sequence")
    if sequence < 1:
        raise ValueError("export sequence must be a positive integer")

    existing = (
        await db.execute(
            select(PlatformTelemetryExport).where(PlatformTelemetryExport.export_id == export_id)
        )
    ).scalar_one_or_none()

    rows = await rows_from_usage(db, tenant_id=tenant_id, window_start=window_start, window_end=window_end)
    payload = serializer.build_aggregate_export(
        policy=policy,
        export_id=export_id,
        installation_id=installation_id,
        sequence=sequence,
        commercial_tenant_ref=commercial_tenant_ref,
        window_start=window_start,
        window_end=window_end,
        rows=rows,
        correction_of=correction_of,
        generated_at=generated_at,
    )
    serializer.validate_export_payload(payload)
    digest = hashlib.sha256(serializer.canonical_payload_bytes(payload)).hexdigest()
    payload["payload_digest"] = digest

    if existing is not None:
        return payload, existing

    record = PlatformTelemetryExport(
        export_id=export_id,
        installation_id=installation_id,
        sequence=sequence,
        commercial_tenant_ref=commercial_tenant_ref,
        window_start=window_start,
        window_end=window_end,
        schema_version=payload["schema_version"],
        row_count=len(rows),
        payload_digest=digest,
        signing_key_id=policy.signing_key_id,
        telemetry_policy_revision=policy.telemetry_policy_revision,
        correction_of=correction_of,
        status="built",
    )
    db.add(record)
    await db.flush()
    return payload, record


async def import_export(
    db: AsyncSession, *, payload: Mapping[str, Any]
) -> PlatformTelemetryImport:
    """Validate and record one aggregate-only import, idempotent on export id.

    Validation rejects a forbidden name (prompt, response, content hash,
    principal or request identifier) at every nesting level and an unknown field
    in the envelope or a row. A replay of the same export id is a no-op, so a
    restore cannot duplicate the import or the historical usage it carries.
    """
    serializer.validate_export_payload(payload)
    export_id = payload.get("export_id")
    if not export_id:
        raise ValueError("an imported export requires an export_id")
    installation_id = payload.get("installation_id") or ""
    sequence = int(payload.get("sequence") or 0)
    row_count = len(payload.get("rows") or ())
    digest = payload.get("payload_digest") or hashlib.sha256(
        serializer.canonical_payload_bytes(payload)
    ).hexdigest()

    existing = (
        await db.execute(
            select(PlatformTelemetryImport).where(PlatformTelemetryImport.export_id == export_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    record = PlatformTelemetryImport(
        export_id=str(export_id),
        installation_id=str(installation_id),
        sequence=sequence,
        schema_version=str(payload.get("schema_version") or serializer.SCHEMA_VERSION),
        payload_digest=str(digest),
        row_count=row_count,
        received_at=utcnow(),
    )
    db.add(record)
    await db.flush()
    return record


# ---------------------------------------------------------------------------
# Fleet / client-facing summaries (no content, no lease details)
# ---------------------------------------------------------------------------


async def fleet_metadata(
    db: AsyncSession, *, window_start: datetime | None = None, window_end: datetime | None = None
) -> dict[str, Any]:
    """Authorized platform fleet metadata and cost metadata, aggregate only.

    Cost is reported as integer minor units grouped by currency; there is no
    float and no per-call row. This surface is for the platform operator, never
    for a client administrator: it carries no customer content and no endpoint
    or credential reference.
    """
    clauses = []
    if window_start is not None:
        clauses.append(InferenceMetricSample.window_start >= ensure_aware(window_start))
    if window_end is not None:
        clauses.append(InferenceMetricSample.window_end <= ensure_aware(window_end))

    origin_rows = (
        await db.execute(
            select(
                InferenceMetricSample.origin,
                func.count().label("samples"),
                func.coalesce(func.sum(InferenceMetricSample.value_int), 0).label("value_sum"),
            )
            .where(*clauses)
            .group_by(InferenceMetricSample.origin)
        )
    ).all()
    by_origin = {
        str(row._mapping["origin"]): {
            "samples": int(row._mapping["samples"] or 0),
            "value_sum": int(row._mapping["value_sum"] or 0),
        }
        for row in origin_rows
    }

    totals_row = (
        await db.execute(
            select(
                func.count().label("samples"),
                func.coalesce(
                    func.sum(
                        case((InferenceMetricSample.value_int.is_(None), 1), else_=0)
                    ),
                    0,
                ).label("missing_value_samples"),
                func.coalesce(
                    func.sum(case((InferenceMetricSample.counter_reset.is_(True), 1), else_=0)), 0
                ).label("counter_resets"),
                func.coalesce(
                    func.sum(
                        case((InferenceMetricSample.metric_key == "capacity.gpu_ms", 1), else_=0)
                    ),
                    0,
                ).label("gpu_samples"),
            ).where(*clauses)
        )
    ).one()

    cost_rows = (
        await db.execute(
            select(
                InferenceMetricSample.currency,
                func.coalesce(func.sum(InferenceMetricSample.value_int), 0).label("cost_minor_units"),
            )
            .where(
                *clauses,
                InferenceMetricSample.metric_key == METRIC_POOL_COST_MINOR,
                InferenceMetricSample.value_int.is_not(None),
            )
            .group_by(InferenceMetricSample.currency)
        )
    ).all()
    by_currency = {
        str(row._mapping["currency"] or "unknown"): int(row._mapping["cost_minor_units"] or 0)
        for row in cost_rows
    }

    pool_count = int(
        (await db.execute(select(func.count()).select_from(CapacityPool))).scalar_one() or 0
    )
    samples = int(totals_row._mapping["samples"] or 0)
    gpu_samples = int(totals_row._mapping["gpu_samples"] or 0)

    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "platform_fleet",
        "pool_count": pool_count,
        "sample_count": samples,
        "missing_value_samples": int(totals_row._mapping["missing_value_samples"] or 0),
        "counter_resets": int(totals_row._mapping["counter_resets"] or 0),
        "by_origin": by_origin,
        "gpu_coverage": {
            "gpu_samples": gpu_samples,
            "total_samples": samples,
            "coverage": "measured" if gpu_samples and gpu_samples == samples else (
                "partial" if gpu_samples else "none"
            ),
        },
        "cost_metadata": {"by_currency_minor_units": by_currency},
        "generated_at": utcnow().isoformat(),
    }


async def own_service_summary(
    db: AsyncSession, *, tenant_id: str, window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, Any]:
    """A client administrator's own approved-service and usage summary.

    Strictly tenant-scoped: every query carries a ``tenant_id`` predicate, so
    this surface can never read another tenant. It carries no fleet metadata and
    no cost or wholesale figure — those belong to the platform operator alone.
    """
    if not tenant_id:
        raise ValueError("a tenant_id is required")

    bindings = (
        await db.execute(
            select(InferenceTenantBinding).where(InferenceTenantBinding.tenant_id == tenant_id)
        )
    ).scalars().all()
    services = [
        {
            "rahkia_alias": binding.rahkia_alias,
            "revision": binding.revision,
            "status": binding.status,
            "allow_hosted": bool(binding.allow_hosted),
        }
        for binding in bindings
    ]

    m1 = await usage_metering.summarize_usage(db, tenant_id=tenant_id)
    m2 = await usage_aggregation.aggregate_usage(db, tenant_id=tenant_id, period="total")

    pools = (
        await db.execute(
            select(CapacityPool.pool_id, CapacityPool.pool_kind, CapacityPool.concurrency_limit).where(
                CapacityPool.owner_tenant_id == tenant_id
            )
        )
    ).all()
    capacity = [
        {
            "pool_id": str(row[0]),
            "pool_kind": str(row[1]),
            "concurrency_limit": int(row[2]),
        }
        for row in pools
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "tenant_self_service",
        "tenant_id": tenant_id,
        "services": services,
        "usage": {
            "attempts": int(m1["events"]),
            "input_tokens": int(m1["input_tokens"]),
            "output_tokens": int(m1["output_tokens"]),
            "total_tokens": int(m1["total_tokens"]),
        },
        "attribution": m2["totals"]["attribution"],
        "coverage": m2["coverage"],
        "capacity": capacity,
        "generated_at": utcnow().isoformat(),
    }


__all__ = [
    "LEGACY_ATTRIBUTION",
    "METRIC_POOL_BUSY_MS",
    "METRIC_POOL_COST_MINOR",
    "METRIC_SCOPES",
    "METRIC_USAGE_ATTEMPTS",
    "METRIC_USAGE_TOTAL_TOKENS",
    "METHODS",
    "ORIGINS",
    "QUALITIES",
    "SCHEMA_VERSION",
    "SUBJECT_KINDS",
    "VALUE_KINDS",
    "build_export",
    "correlate_attempts",
    "define_metric",
    "detect_counter_reset",
    "fleet_metadata",
    "import_export",
    "ingest_collector_batch",
    "own_service_summary",
    "pool_window_accounting",
    "reconcile_usage",
    "record_sample",
    "rows_from_usage",
]
