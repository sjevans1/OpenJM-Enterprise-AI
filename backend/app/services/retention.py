"""VS8 bounded, tenant-safe retention lifecycle (Workstream F).

Retention deletes *operational transient* records only. The excluded classes are
not a policy knob: audit evidence, customer source data (documents and
data-source rows), report history (saved reports, definitions, runs) and
regulatory records are never eligible for automatic deletion, whatever the
operator configures.

Every run:

* is **tenant-scoped** when a tenant is given, and deployment-wide otherwise,
  applying each tenant's own policy;
* is **bounded** by a per-class row limit so one call cannot delete an unbounded
  amount;
* is **auditable** — one ``retention.apply`` audit record per tenant, and the
  dry-run is the default so a destructive run is always an explicit choice;
* supports **dry-run** for a destructive class before it is enabled.

The eligible classes and their settings are declared in one table so the policy
surface cannot drift from the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.identity import Principal
from app.models import (
    ActionExecution,
    AuthSession,
    ConnectorSyncRun,
    Notification,
    ScheduleRun,
)
from app.services.identity import record_audit, utcnow

# Per-class bound: at most this many rows are removed in one pass. A bounded
# run repeated on a schedule converges without ever holding a long transaction.
DEFAULT_BATCH_LIMIT = 500


@dataclass(frozen=True)
class RetentionClass:
    """One eligible operational class and the settings day-count that governs it."""

    name: str
    settings_attr: str
    description: str


# The closed set of deletable classes. Adding a class here is the only way to
# make a table eligible, which is what keeps the exclusion list honest.
ELIGIBLE_CLASSES: tuple[RetentionClass, ...] = (
    RetentionClass(
        "expired_sessions",
        "retention_sessions_days",
        "OpenJM-native auth sessions past their expiry.",
    ),
    RetentionClass(
        "scheduler_run_history",
        "retention_scheduler_runs_days",
        "Terminal schedule-run rows older than the window.",
    ),
    RetentionClass(
        "connector_run_history",
        "retention_connector_run_history_days",
        "Terminal connector sync-run rows older than the window.",
    ),
    RetentionClass(
        "notification_history",
        "retention_notification_history_days",
        "Delivered/failed/suppressed notifications older than the window.",
    ),
)

# Classes that are NEVER eligible. Documented here so a reviewer can see the
# decision rather than infer it from an absence.
NEVER_DELETE = (
    "audit_records",
    "documents / external_resources (customer source data)",
    "saved_reports / report_definition_versions / report_runs (report history)",
    "action_executions (governed-action audit evidence)",
    "tenants / principal_accounts / tenant_memberships",
)


@dataclass
class RetentionOutcome:
    tenant_id: str | None
    dry_run: bool
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=max(0, int(days)))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _tenant_ids(db: AsyncSession) -> list[str]:
    from app.models import Tenant

    rows = (await db.execute(select(Tenant.id))).scalars().all()
    return list(rows)


async def _count_and_delete(
    db: AsyncSession,
    *,
    model,
    time_column,
    cutoff: datetime,
    extra_filters: list,
    tenant_id: str,
    limit: int,
    dry_run: bool,
) -> int:
    """Select the eligible ids (bounded), then delete exactly those ids.

    Deleting by an explicit id list (rather than re-running the predicate in a
    DELETE) keeps the count and the mutation in agreement and keeps the bound
    exact.
    """
    query = (
        select(model.id)
        .where(time_column < cutoff, *extra_filters)
        .limit(int(limit))
    )
    if tenant_id is not None and hasattr(model, "tenant_id"):
        query = query.where(model.tenant_id == tenant_id)
    ids = list((await db.execute(query)).scalars().all())
    if not ids or dry_run:
        return len(ids)
    await db.execute(delete(model).where(model.id.in_(ids)))
    return len(ids)


async def plan_retention(
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
    now: datetime | None = None,
    limit: int = DEFAULT_BATCH_LIMIT,
    settings: Settings | None = None,
) -> RetentionOutcome:
    """Dry-run: report how many rows each class would remove. Deletes nothing."""
    return await apply_retention(
        db, tenant_id=tenant_id, now=now, limit=limit, dry_run=True, settings=settings
    )


async def apply_retention(
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
    now: datetime | None = None,
    limit: int = DEFAULT_BATCH_LIMIT,
    dry_run: bool = True,
    actor: Principal | None = None,
    settings: Settings | None = None,
) -> RetentionOutcome:
    """Apply (or dry-run) retention for one tenant, or the whole deployment.

    ``dry_run`` defaults to True: a destructive run is always explicit.
    """
    cfg = settings or get_settings()
    moment = _as_utc(now) or utcnow()
    tenants = [tenant_id] if tenant_id is not None else await _tenant_ids(db)

    totals: dict[str, int] = {c.name: 0 for c in ELIGIBLE_CLASSES}
    for current_tenant in tenants:
        counts: dict[str, int] = {c.name: 0 for c in ELIGIBLE_CLASSES}

        # Expired sessions: delete anything past its own expiry (not a policy
        # window) older than the configured session-retention window.
        session_cutoff = _cutoff(moment, cfg.retention_sessions_days)
        counts["expired_sessions"] = await _count_and_delete(
            db,
            model=AuthSession,
            time_column=AuthSession.expires_at,
            cutoff=session_cutoff,
            extra_filters=[],
            tenant_id=current_tenant,
            limit=limit,
            dry_run=dry_run,
        )

        terminal = ("succeeded", "failed", "skipped", "interrupted")
        counts["scheduler_run_history"] = await _count_and_delete(
            db,
            model=ScheduleRun,
            time_column=ScheduleRun.finished_at,
            cutoff=_cutoff(moment, cfg.retention_scheduler_runs_days),
            extra_filters=[ScheduleRun.finished_at.is_not(None), ScheduleRun.status.in_(terminal)],
            tenant_id=current_tenant,
            limit=limit,
            dry_run=dry_run,
        )
        counts["connector_run_history"] = await _count_and_delete(
            db,
            model=ConnectorSyncRun,
            time_column=ConnectorSyncRun.finished_at,
            cutoff=_cutoff(moment, cfg.retention_connector_run_history_days),
            extra_filters=[
                ConnectorSyncRun.finished_at.is_not(None),
                ConnectorSyncRun.status.in_(("succeeded", "failed", "skipped")),
            ],
            tenant_id=current_tenant,
            limit=limit,
            dry_run=dry_run,
        )
        counts["notification_history"] = await _count_and_delete(
            db,
            model=Notification,
            time_column=Notification.created_at,
            cutoff=_cutoff(moment, cfg.retention_notification_history_days),
            extra_filters=[Notification.status.in_(("delivered", "failed", "suppressed"))],
            tenant_id=current_tenant,
            limit=limit,
            dry_run=dry_run,
        )

        for key, value in counts.items():
            totals[key] += value

        await record_audit(
            db,
            principal=actor,
            tenant_id=current_tenant,
            action="retention.apply",
            decision="dry_run" if dry_run else "allow",
            resource_type="retention",
            metadata={"dry_run": dry_run, "counts": counts, "limit": limit},
        )

    await db.commit()
    return RetentionOutcome(tenant_id=tenant_id, dry_run=dry_run, counts=totals)


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "ELIGIBLE_CLASSES",
    "NEVER_DELETE",
    "RetentionClass",
    "RetentionOutcome",
    "apply_retention",
    "plan_retention",
]
