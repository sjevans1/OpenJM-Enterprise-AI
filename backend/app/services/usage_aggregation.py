"""M2 bounded usage aggregation over the immutable M1 ledger (#46 M2).

This module is a read-only query layer over ``ModelUsageEvent``. It aggregates
the immutable counts M1 wrote; it never mutates a usage row, and it contains no
pricing, entitlement or billing logic.

Contract notes (from the INF1 "M2 interface agreement"):

* Aggregation left joins the one-to-one INF1-A attribution sidecar
  (``inference_usage_attributions``, unique on ``usage_event_id``) and never drops
  an unattributed row. The tenant equality between the M1 event and the sidecar
  lives in the join's ON clause, so a sidecar belonging to another tenant simply
  fails to attach and the event survives as legacy usage. The relation is one to
  one, so the join cannot fan out and cannot multiply counts or token sums.
* A sidecar-less row is reported as exactly ``legacy_unknown``. Attributed rows
  expose the recorded deployment, runtime profile, site and inference mode.
  Inventing any of those from a provider route, a model name, an endpoint or any
  other incidental string is forbidden, so this layer will not do it.
* Provider-reported and estimated coverage are kept separate. There is no single
  "provider-grade" total that silently sums the two.
* ``cached_input_tokens`` and ``reasoning_tokens`` are reported on their own and
  are never added into ``total_tokens``.
* Results are bounded: day/month windows and the number of grouped rows are
  capped, so a request can never produce an unbounded result set.

Tenant scoping is applied as a SQL ``WHERE tenant_id = ...`` predicate, never as
a post-filter in Python.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage import (
    USAGE_STATUS_FAILED,
    USAGE_STATUS_SUCCEEDED,
    UsageCallRole,
    UsageSource,
)
from app.models import InferenceUsageAttribution, ModelUsageEvent

# Version of the aggregate payload shape. Bump when the payload changes shape so
# a consumer can tell which contract it is reading.
SCHEMA_VERSION = "m2.usage.v1"

# A row with no attribution sidecar is this, exactly. It is deliberately a fixed
# literal and is never derived from a provider route, a model name or an endpoint.
LEGACY_ATTRIBUTION = "legacy_unknown"

# A row whose one-to-one sidecar is present and tenant-consistent.
ATTRIBUTED = "attributed"

# Reported when a scope holds both kinds, so a consumer can never mistake a mixed
# window for a fully attributed one.
MIXED_ATTRIBUTION = "mixed"

_PERIODS: tuple[str, ...] = ("day", "month", "total")

_DEFAULT_GROUP_BY: tuple[str, ...] = (
    "attribution",
    "model_name",
    "provider_route",
    "execution_class",
    "usage_source",
    "call_role",
    "status",
    "failure_category",
)

# Whether this row carries a tenant-consistent attribution sidecar. Derived from
# the join, never from a provider string.
_INFERRED_ATTRIBUTION = case(
    (InferenceUsageAttribution.id.is_not(None), ATTRIBUTED), else_=LEGACY_ATTRIBUTION
)

# Dimension lookup. Each key is a group_by token; the value is the expression the
# breakdown groups on. The identity dimensions coalesce to the legacy marker, so a
# sidecar-less row reads exactly ``legacy_unknown`` rather than null or a guess.
_DIMENSIONS = {
    "attribution": _INFERRED_ATTRIBUTION,
    "deployment_id": func.coalesce(
        InferenceUsageAttribution.deployment_id, LEGACY_ATTRIBUTION
    ),
    "runtime_profile_id": func.coalesce(
        InferenceUsageAttribution.runtime_profile_id, LEGACY_ATTRIBUTION
    ),
    "site_id": func.coalesce(InferenceUsageAttribution.site_id, LEGACY_ATTRIBUTION),
    "inference_mode": func.coalesce(
        InferenceUsageAttribution.inference_mode, LEGACY_ATTRIBUTION
    ),
    "model_name": ModelUsageEvent.model_name,
    "provider_route": ModelUsageEvent.provider_route,
    "execution_class": ModelUsageEvent.execution_class,
    "usage_source": ModelUsageEvent.usage_source,
    "call_role": ModelUsageEvent.call_role,
    "status": ModelUsageEvent.status,
    "failure_category": ModelUsageEvent.failure_category,
}

# Bounds. These keep every query and every payload finite regardless of how much
# history the ledger holds.
MAX_WINDOW_DAYS = 400
MAX_WINDOW_MONTHS = 60
MAX_BUCKETS = 400
MAX_GROUPED_ROWS = 5000
MAX_TENANTS_PAGE = 200
DEFAULT_TENANTS_PAGE = 50
MAX_EXPORT_ROWS = 2000
MAX_EXPORT_WINDOW_DAYS = 366


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dialect_name(db: AsyncSession) -> str:
    bind = getattr(db, "bind", None)
    if bind is None:
        try:
            bind = db.get_bind()
        except Exception:  # noqa: BLE001 - fall through to a safe default
            bind = None
    dialect = getattr(bind, "dialect", None)
    return getattr(dialect, "name", "") or "sqlite"


def _bucket_expression(period: str, dialect_name: str):
    """A dialect-portable SQL expression for the period start key (UTC).

    Day and month keys are rendered straight from the stored timestamp, so the
    bucketing happens in SQL rather than by post-processing rows in Python.
    """
    column = ModelUsageEvent.started_at
    if period == "total":
        return literal("total")
    if period == "day":
        if dialect_name.startswith("postgres"):
            return func.to_char(column, "YYYY-MM-DD")
        return func.strftime("%Y-%m-%d", column)
    if period == "month":
        if dialect_name.startswith("postgres"):
            return func.to_char(column, "YYYY-MM")
        return func.strftime("%Y-%m", column)
    raise ValueError(f"Unsupported period: {period!r}")


def _sum_int(column):
    return func.coalesce(func.sum(column), 0)


def _count_where(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _metrics_columns() -> list:
    """Counts and token sums shared by every aggregate query.

    Cached-input and reasoning sums are exposed but never folded into
    ``total_tokens``; the total comes from the stored ``total_tokens`` column.
    """
    return [
        func.count().label("attempts"),
        _count_where(ModelUsageEvent.status == USAGE_STATUS_SUCCEEDED).label("succeeded"),
        _count_where(ModelUsageEvent.status == USAGE_STATUS_FAILED).label("failed"),
        _count_where(
            ModelUsageEvent.call_role == UsageCallRole.RETRY.value
        ).label("retries"),
        _count_where(
            ModelUsageEvent.call_role == UsageCallRole.FALLBACK.value
        ).label("fallbacks"),
        _sum_int(ModelUsageEvent.input_tokens).label("input_tokens"),
        _sum_int(ModelUsageEvent.output_tokens).label("output_tokens"),
        _sum_int(ModelUsageEvent.total_tokens).label("total_tokens"),
        _sum_int(ModelUsageEvent.cached_input_tokens).label("cached_input_tokens"),
        _sum_int(ModelUsageEvent.reasoning_tokens).label("reasoning_tokens"),
        # Coverage split, available on every aggregate so each surface reports it.
        _count_where(InferenceUsageAttribution.id.is_not(None)).label("attributed_events"),
        _count_where(InferenceUsageAttribution.id.is_(None)).label("legacy_unknown_events"),
    ]


def _as_metrics(row) -> dict:
    mapping = row._mapping
    return {
        "attempts": int(mapping["attempts"] or 0),
        "succeeded": int(mapping["succeeded"] or 0),
        "failed": int(mapping["failed"] or 0),
        "retries": int(mapping["retries"] or 0),
        "fallbacks": int(mapping["fallbacks"] or 0),
        "input_tokens": int(mapping["input_tokens"] or 0),
        "output_tokens": int(mapping["output_tokens"] or 0),
        "total_tokens": int(mapping["total_tokens"] or 0),
        "cached_input_tokens": int(mapping["cached_input_tokens"] or 0),
        "reasoning_tokens": int(mapping["reasoning_tokens"] or 0),
        "attributed_events": int(mapping["attributed_events"] or 0),
        "legacy_unknown_events": int(mapping["legacy_unknown_events"] or 0),
    }


def _empty_bucket(bucket: str) -> dict:
    return {
        "bucket": bucket,
        "attribution": LEGACY_ATTRIBUTION,
        "attempts": 0,
        "succeeded": 0,
        "failed": 0,
        "retries": 0,
        "fallbacks": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "attributed_events": 0,
        "legacy_unknown_events": 0,
        "by_attribution": {},
        "by_model_name": {},
        "by_provider_route": {},
        "by_execution_class": {},
        "by_usage_source": {},
        "by_call_role": {},
        "by_status": {},
        "by_failure_category": {},
        # Present so any dimension can be requested without a KeyError; they stay
        # empty unless the caller asks for that breakdown.
        "by_deployment_id": {},
        "by_runtime_profile_id": {},
        "by_site_id": {},
        "by_inference_mode": {},
    }


def _normalize_group_by(group_by) -> tuple[str, ...]:
    if group_by is None:
        return _DEFAULT_GROUP_BY
    tokens = tuple(group_by)
    for token in tokens:
        if token not in _DIMENSIONS:
            raise ValueError(f"Unknown group_by dimension: {token!r}")
    return tokens


def _attribution_state(attributed: int, legacy: int) -> str:
    """Report attributed, legacy or mixed, rather than one blanket label."""
    if attributed and legacy:
        return MIXED_ATTRIBUTION
    if attributed:
        return ATTRIBUTED
    return LEGACY_ATTRIBUTION


def _attribution_join(stmt):
    """Left join the one-to-one attribution sidecar onto the M1 ledger.

    Tenant equality lives in the ON clause, so a sidecar belonging to another
    tenant does not attach and the M1 row is still counted as legacy usage. The
    sidecar is unique per ``usage_event_id``, so this is genuinely one to one and
    cannot fan out a row into a doubled count or token sum.
    """
    return stmt.select_from(ModelUsageEvent).outerjoin(
        InferenceUsageAttribution,
        and_(
            InferenceUsageAttribution.usage_event_id == ModelUsageEvent.id,
            InferenceUsageAttribution.tenant_id == ModelUsageEvent.tenant_id,
        ),
    )


def _filters(tenant_id: str, start: datetime | None, end: datetime | None) -> list:
    clauses = [ModelUsageEvent.tenant_id == tenant_id]
    if start is not None:
        clauses.append(ModelUsageEvent.started_at >= start)
    if end is not None:
        # The window end is exclusive so adjacent day queries never overlap.
        clauses.append(ModelUsageEvent.started_at < end)
    return clauses


def _enforce_window(period: str, start: datetime | None, end: datetime | None) -> None:
    if period == "total" or start is None or end is None:
        return
    if end < start:
        raise ValueError("Window end precedes window start")
    if period == "day":
        days = (end - start).days + 1
        if days > MAX_WINDOW_DAYS:
            raise ValueError(
                f"Window of {days} days exceeds the {MAX_WINDOW_DAYS}-day bound"
            )
    elif period == "month":
        months = (end.year - start.year) * 12 + (end.month - start.month) + 1
        if months > MAX_WINDOW_MONTHS:
            raise ValueError(
                f"Window of {months} months exceeds the {MAX_WINDOW_MONTHS}-month bound"
            )


def _bucket_of(value, period: str) -> str:
    if period == "total":
        return "total"
    return str(value)


async def _fetch_grouped(db: AsyncSession, stmt, *, label: str, limit: int):
    rows = (await db.execute(stmt.limit(limit + 1))).all()
    if len(rows) > limit:
        raise ValueError(
            f"Aggregation for {label} exceeds the {limit}-row bound; narrow the window"
        )
    return rows


async def aggregate_usage(
    db: AsyncSession,
    *,
    tenant_id: str,
    period: str = "day",
    start: datetime | None = None,
    end: datetime | None = None,
    group_by=_DEFAULT_GROUP_BY,
) -> dict:
    """Bounded, deterministic aggregate of one tenant's usage ledger.

    The result is JSON-serialisable and keyed by period start (UTC): a day key is
    ``YYYY-MM-DD``, a month key is ``YYYY-MM`` and ``total`` is a single bucket.
    Tenant scoping is a SQL predicate, so a caller can never see another
    tenant's rows.
    """
    if not tenant_id:
        raise ValueError("A tenant_id is required")
    if period not in _PERIODS:
        raise ValueError(f"Unsupported period: {period!r}")
    _enforce_window(period, start, end)
    dimensions = _normalize_group_by(group_by)
    dialect = _dialect_name(db)
    bucket_expr = _bucket_expression(period, dialect)
    clauses = _filters(tenant_id, start, end)

    # Per-bucket totals.
    totals_stmt = _attribution_join(
        select(bucket_expr.label("bucket"), *_metrics_columns())
        .where(*clauses)
        .group_by(bucket_expr)
        .order_by(bucket_expr)
    )
    totals_rows = await _fetch_grouped(db, totals_stmt, label="buckets", limit=MAX_BUCKETS)

    buckets: dict[str, dict] = {}
    order: list[str] = []
    for row in totals_rows:
        key = _bucket_of(row._mapping["bucket"], period)
        bucket = _empty_bucket(key)
        bucket.update(_as_metrics(row))
        bucket["attribution"] = _attribution_state(
            bucket["attributed_events"], bucket["legacy_unknown_events"]
        )
        buckets[key] = bucket
        order.append(key)

    # Per-dimension breakdowns, each grouped by the same period bucket.
    for token in dimensions:
        column = _DIMENSIONS[token]
        if token == "failure_category":
            value_expr = func.coalesce(column, "(none)")
        else:
            value_expr = func.coalesce(column, "(unspecified)")
        stmt = _attribution_join(
            select(
                bucket_expr.label("bucket"),
                value_expr.label("value"),
                *_metrics_columns(),
            )
            .where(*clauses)
            .group_by(bucket_expr, value_expr)
            .order_by(bucket_expr, value_expr)
        )
        rows = await _fetch_grouped(
            db, stmt, label=f"breakdown:{token}", limit=MAX_GROUPED_ROWS
        )
        target = f"by_{token}"
        for row in rows:
            key = _bucket_of(row._mapping["bucket"], period)
            bucket = buckets.get(key)
            if bucket is None:
                # A dimension value cannot exist without its bucket total; guard
                # defensively rather than inventing a bucket.
                bucket = _empty_bucket(key)
                buckets[key] = bucket
                order.append(key)
            bucket[target][str(row._mapping["value"])] = _as_metrics(row)

    ordered_buckets = [buckets[key] for key in order]

    # Overall totals across the whole (bounded) window.
    totals = _empty_bucket("all")
    totals.pop("bucket")
    for bucket in ordered_buckets:
        for field in (
            "attempts",
            "succeeded",
            "failed",
            "retries",
            "fallbacks",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
            "attributed_events",
            "legacy_unknown_events",
        ):
            totals[field] += bucket[field]
        for token in dimensions:
            target = f"by_{token}"
            for value, metrics in bucket[target].items():
                merged = totals[target].setdefault(value, _zero_metrics())
                for field, amount in metrics.items():
                    merged[field] += amount

    totals["attribution"] = _attribution_state(
        totals["attributed_events"], totals["legacy_unknown_events"]
    )

    # Provider-reported and estimated coverage stay separate by construction, and
    # the attributed/legacy split is reported next to it.
    coverage = {
        "attribution_state": totals["attribution"],
        "attributed_events": totals["attributed_events"],
        "legacy_unknown_events": totals["legacy_unknown_events"],
        "by_usage_source": totals.get("by_usage_source", {}),
    }

    return {
        "tenant_id": tenant_id,
        "period": period,
        "start": start.isoformat() if start is not None else None,
        "end": end.isoformat() if end is not None else None,
        "group_by": list(dimensions),
        "attribution": totals["attribution"],
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow().isoformat(),
        "buckets": ordered_buckets,
        "totals": totals,
        "coverage": coverage,
    }


def _zero_metrics() -> dict:
    return {
        "attempts": 0,
        "succeeded": 0,
        "failed": 0,
        "retries": 0,
        "fallbacks": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "attributed_events": 0,
        "legacy_unknown_events": 0,
    }


async def aggregate_by_tenant(
    db: AsyncSession, *, limit: int = DEFAULT_TENANTS_PAGE, offset: int = 0
) -> dict:
    """Cross-tenant aggregate metadata for the platform plane.

    Returns only aggregate counts and token sums per tenant. There is no
    per-message row, no message text, no request id and no credential anywhere in
    the payload.
    """
    limit = max(1, min(int(limit), MAX_TENANTS_PAGE))
    offset = max(0, int(offset))

    tenant_col = ModelUsageEvent.tenant_id
    stmt = _attribution_join(
        select(tenant_col.label("tenant_id"), *_metrics_columns())
        .group_by(tenant_col)
        .order_by(tenant_col)
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).all()
    total_tenants = int(
        (
            await db.execute(select(func.count(func.distinct(tenant_col))))
        ).scalar_one()
        or 0
    )
    tenants = [
        {
            "tenant_id": row._mapping["tenant_id"],
            **_as_metrics(row),
            "attribution": _attribution_state(
                int(row._mapping["attributed_events"] or 0),
                int(row._mapping["legacy_unknown_events"] or 0),
            ),
        }
        for row in rows
    ]
    attributed_total = sum(item["attributed_events"] for item in tenants)
    legacy_total = sum(item["legacy_unknown_events"] for item in tenants)
    return {
        "generated_at": utcnow().isoformat(),
        "schema_version": SCHEMA_VERSION,
        "attribution": _attribution_state(attributed_total, legacy_total),
        "coverage": {
            "attribution_state": _attribution_state(attributed_total, legacy_total),
            "attributed_events": attributed_total,
            "legacy_unknown_events": legacy_total,
        },
        "total_tenants": total_tenants,
        "limit": limit,
        "offset": offset,
        "tenants": tenants,
    }


async def export_rows(
    db: AsyncSession,
    *,
    tenant_id: str,
    period: str = "day",
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = MAX_EXPORT_ROWS,
) -> tuple[list[str], list[dict]]:
    """Bounded per-bucket rows for CSV/JSON export.

    The window is capped at ``MAX_EXPORT_WINDOW_DAYS`` and the row count at
    ``MAX_EXPORT_ROWS``; a request that exceeds either is refused with a
    ``ValueError`` (mapped to HTTP 400) rather than streaming an unbounded set.
    """
    if period == "total":
        raise ValueError("Export requires a day or month period, not 'total'")
    _enforce_window(period, start, end)
    if period == "day" and start is not None and end is not None:
        days = (end - start).days + 1
        if days > MAX_EXPORT_WINDOW_DAYS:
            raise ValueError(
                f"Export window of {days} days exceeds the "
                f"{MAX_EXPORT_WINDOW_DAYS}-day bound"
            )
    if limit < 1 or limit > MAX_EXPORT_ROWS:
        raise ValueError(
            f"Export limit must be between 1 and {MAX_EXPORT_ROWS}"
        )

    aggregate = await aggregate_usage(
        db, tenant_id=tenant_id, period=period, start=start, end=end
    )
    header = [
        "bucket",
        "attribution",
        "attributed_events",
        "legacy_unknown_events",
        "attempts",
        "succeeded",
        "failed",
        "retries",
        "fallbacks",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "provider_reported_total_tokens",
        "estimated_total_tokens",
    ]
    rows: list[dict] = []
    for bucket in aggregate["buckets"][:limit]:
        sources = bucket.get("by_usage_source", {})
        provider = sources.get(UsageSource.PROVIDER_REPORTED.value, {})
        estimated = sources.get(UsageSource.ESTIMATED.value, {})
        rows.append(
            {
                "bucket": bucket["bucket"],
                "attribution": bucket["attribution"],
                "attributed_events": bucket["attributed_events"],
                "legacy_unknown_events": bucket["legacy_unknown_events"],
                "attempts": bucket["attempts"],
                "succeeded": bucket["succeeded"],
                "failed": bucket["failed"],
                "retries": bucket["retries"],
                "fallbacks": bucket["fallbacks"],
                "input_tokens": bucket["input_tokens"],
                "output_tokens": bucket["output_tokens"],
                "total_tokens": bucket["total_tokens"],
                "cached_input_tokens": bucket["cached_input_tokens"],
                "reasoning_tokens": bucket["reasoning_tokens"],
                "provider_reported_total_tokens": int(provider.get("total_tokens", 0)),
                "estimated_total_tokens": int(estimated.get("total_tokens", 0)),
            }
        )
    return header, rows
