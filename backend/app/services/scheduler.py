"""VS7 bounded scheduler.

One tick claims the occurrences that are due and nothing more. Three
properties define this module and everything else follows from them:

1. **Authorization is re-proved at execution time.** The schedule row stores an
   ``owner_principal_id`` and nothing else about authority. At run time the
   owner's *current* active membership is re-read from the database and the
   operation's required permission is required again. A revoked membership, a
   disabled account or a role that no longer holds the permission refuses the
   run. There is no stored-permission snapshot and no historical path.
2. **Claiming is idempotent.** A claim is an insert into ``schedule_runs``
   keyed by (schedule_id, scheduled_for). The unique constraint arbitrates, so
   a second concurrent claimer, or the same process after a restart, loses
   cleanly instead of running the job twice.
3. **Every tick is bounded.** At most ``limit`` schedules are claimed per tick,
   due rows are selected by an indexed predicate, and the operation itself is
   one of a small closed registry. A model cannot name an operation, an
   interval or a target that the platform does not already understand.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import Principal
from app.core.permissions import Permission, permissions_for_role
from app.models import PrincipalAccount, Schedule, ScheduleRun, TenantMembership
from app.services import notifications
from app.services.connectors.base import ConnectorError, redact
from app.services.connectors.service import resolve_instance
from app.services.connectors.sync import run_sync
from app.services.identity import record_audit, utcnow

MIN_INTERVAL_SECONDS = 60

# The operation identifier is persisted and audited, so it is part of the
# stable contract and must not be renamed casually.
OPERATION_CONNECTOR_SYNC = "connector.sync"
OPERATION_CONNECTOR_INCREMENTAL_SYNC = "connector.incremental_sync"
OPERATION_CONNECTOR_RECONCILE = "connector.reconcile"
OPERATION_NOTIFICATION_RETRY = "notification.retry"
OPERATION_REPORT_RERUN = "report.rerun"

FAILURE_AUTHORIZATION_REVOKED = "authorization_revoked"
FAILURE_PRINCIPAL_DISABLED = "principal_disabled"
FAILURE_PERMISSION_DENIED = "permission_denied"
FAILURE_SCHEDULE_DISABLED = "schedule_disabled"
FAILURE_CONNECTOR_DISABLED = "connector_disabled"
FAILURE_MISSING_TARGET = "missing_target"
FAILURE_UNSUPPORTED_OPERATION = "unsupported_operation"
FAILURE_NOT_IMPLEMENTED = "operation_not_implemented"
FAILURE_OPERATION_ERROR = "operation_error"


class SchedulerError(Exception):
    """A scheduler operation was refused.

    ``code`` is a stable, safe category suitable for audit and API responses.
    """

    def __init__(self, message: str, *, code: str = "scheduler_error"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OperationSpec:
    """What one registered operation needs in order to run.

    ``required_permission`` is the single permission the owner must still hold
    at execution time. ``requires_connector`` marks an operation whose target is
    a connector instance, which is where the enabled-state check and tenant
    resolution apply.
    """

    operation: str
    description: str
    required_permission: str
    requires_connector: bool = False
    run_type: str | None = None
    implemented: bool = True


# The closed set of operations a schedule may name. An operation that is not
# here cannot be created, let alone executed.
REGISTERED_OPERATIONS: dict[str, OperationSpec] = {
    OPERATION_CONNECTOR_SYNC: OperationSpec(
        operation=OPERATION_CONNECTOR_SYNC,
        description="Run a bounded initial synchronization of a connector instance.",
        required_permission=Permission.CONNECTOR_READ.value,
        requires_connector=True,
        run_type="initial",
    ),
    OPERATION_CONNECTOR_INCREMENTAL_SYNC: OperationSpec(
        operation=OPERATION_CONNECTOR_INCREMENTAL_SYNC,
        description="Consume a connector's change feed from its persisted checkpoint.",
        required_permission=Permission.CONNECTOR_READ.value,
        requires_connector=True,
        run_type="incremental",
    ),
    OPERATION_CONNECTOR_RECONCILE: OperationSpec(
        operation=OPERATION_CONNECTOR_RECONCILE,
        description="Run a bounded reconciliation sweep of a connector instance.",
        required_permission=Permission.CONNECTOR_READ.value,
        requires_connector=True,
        run_type="reconcile",
    ),
    OPERATION_NOTIFICATION_RETRY: OperationSpec(
        operation=OPERATION_NOTIFICATION_RETRY,
        description="Retry failed notifications that still have attempts left.",
        required_permission=Permission.NOTIFICATIONS_WRITE.value,
    ),
    OPERATION_REPORT_RERUN: OperationSpec(
        operation=OPERATION_REPORT_RERUN,
        description="Re-run a saved report definition. Registered but not implemented.",
        required_permission=Permission.REPORTS_RUN.value,
        implemented=False,
    ),
}


def operation_supported(operation: str) -> bool:
    return operation in REGISTERED_OPERATIONS


def registered_operations() -> tuple[str, ...]:
    return tuple(sorted(REGISTERED_OPERATIONS))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a stored datetime to aware UTC.

    SQLite returns naive datetimes for a ``DateTime(timezone=True)`` column; the
    application always wrote UTC, so a naive value is read as UTC.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _actor_label(actor) -> str | None:
    if actor is None:
        return None
    if isinstance(actor, str):
        return actor
    return getattr(actor, "principal_id", None) or getattr(actor, "subject", None)


def _principal_of(actor) -> Principal | None:
    return actor if isinstance(actor, Principal) else None


def _safe_detail(detail: str | None) -> str | None:
    if not detail:
        return None
    return redact(detail, limit=500) or None


def _parse_target(target_json: str | None) -> dict:
    if not target_json:
        return {}
    try:
        parsed = json.loads(target_json)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _validate_timezone(timezone_name: str) -> None:
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError) as exc:
        raise SchedulerError(
            f"unknown timezone '{timezone_name}'", code="unknown_timezone"
        ) from exc


def _advance(schedule: Schedule, scheduled_for: datetime, now: datetime) -> datetime:
    """Compute the next occurrence after one run.

    The base is the occurrence that just ran plus one interval. With the ``skip``
    misfire policy a schedule whose occurrence is far in the past is advanced to
    the first occurrence strictly after ``now``, so a long outage produces one
    run rather than a burst.
    """
    interval = timedelta(seconds=int(schedule.interval_seconds))
    if interval <= timedelta(0):
        # The column has a database-level lower bound, but a defensive floor
        # keeps the misfire loop from ever becoming unbounded.
        interval = timedelta(seconds=MIN_INTERVAL_SECONDS)
    candidate = scheduled_for + interval
    if schedule.misfire_policy == "skip" and candidate <= now:
        elapsed = now - scheduled_for
        steps = int(elapsed // interval) + 1
        candidate = scheduled_for + interval * steps
        while candidate <= now:
            candidate += interval
    return candidate


async def _revalidate(
    db: AsyncSession, *, schedule: Schedule, spec: OperationSpec
) -> tuple[str, str, str] | None:
    """Re-prove the owner's current authority. Returns None when allowed.

    The membership and the account are re-read here, at execution time, so a
    revocation or a role downgrade that happened after the schedule was created
    is honoured on the very next run.
    """
    membership = (
        await db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == schedule.tenant_id,
                TenantMembership.principal_id == schedule.owner_principal_id,
                TenantMembership.status == "active",
            )
        )
    ).scalars().first()
    if membership is None:
        return (
            "failed",
            FAILURE_AUTHORIZATION_REVOKED,
            "owner has no active membership in this tenant",
        )

    account = await db.get(PrincipalAccount, schedule.owner_principal_id)
    if account is None or account.status != "active":
        return ("failed", FAILURE_PRINCIPAL_DISABLED, "owner principal is not active")

    try:
        needed = Permission(spec.required_permission)
    except ValueError:
        return (
            "failed",
            FAILURE_PERMISSION_DENIED,
            f"operation requires an unknown permission '{spec.required_permission}'",
        )
    if needed not in permissions_for_role(membership.role):
        return (
            "failed",
            FAILURE_PERMISSION_DENIED,
            f"role '{membership.role}' no longer holds '{spec.required_permission}'",
        )
    return None


async def _dispatch(
    db: AsyncSession, *, schedule: Schedule, spec: OperationSpec
) -> tuple[str, str | None, str | None]:
    """Run one operation and return (status, failure_category, detail)."""
    if not spec.implemented:
        return (
            "failed",
            FAILURE_NOT_IMPLEMENTED,
            f"operation '{spec.operation}' is registered but not implemented",
        )

    if spec.requires_connector:
        target = _parse_target(schedule.target_json)
        instance_id = target.get("connector_instance_id")
        if not instance_id:
            return (
                "failed",
                FAILURE_MISSING_TARGET,
                "connector operation requires target.connector_instance_id",
            )
        try:
            instance = await resolve_instance(
                db, tenant_id=schedule.tenant_id, connector_instance_id=instance_id
            )
        except ConnectorError as exc:
            return ("failed", exc.code, exc.detail or str(exc))
        if not instance.enabled:
            return (
                "failed",
                FAILURE_CONNECTOR_DISABLED,
                "connector instance is not enabled",
            )
        sync_run = await run_sync(
            db,
            instance=instance,
            actor=f"scheduler:{schedule.id}",
            run_type=spec.run_type or "initial",
            limit=None,
        )
        if sync_run.status == "failed":
            return (
                "failed",
                sync_run.failure_category or FAILURE_OPERATION_ERROR,
                sync_run.detail,
            )
        return ("succeeded", None, f"connector sync {sync_run.status}")

    if spec.operation == OPERATION_NOTIFICATION_RETRY:
        attempted = await notifications.retry_failed(
            db, tenant_id=schedule.tenant_id, limit=50
        )
        return ("succeeded", None, f"retried {attempted} notifications")

    return (
        "failed",
        FAILURE_NOT_IMPLEMENTED,
        f"operation '{spec.operation}' is registered but not implemented",
    )


# ---------------------------------------------------------------------------
# Schedule lifecycle
# ---------------------------------------------------------------------------


async def create_schedule(
    db: AsyncSession,
    *,
    tenant_id: str,
    owner_principal_id: str,
    name: str,
    schedule_type: str,
    operation: str,
    interval_seconds: int,
    timezone_name: str = "UTC",
    misfire_policy: str = "skip",
    max_retries: int = 2,
    target: dict | None = None,
    actor=None,
) -> Schedule:
    """Create a schedule after validating every field a schedule can carry."""
    if not operation_supported(operation):
        raise SchedulerError(
            f"operation '{operation}' is not registered", code="unsupported_operation"
        )
    try:
        interval = int(interval_seconds)
    except (TypeError, ValueError) as exc:
        raise SchedulerError("interval must be an integer", code="interval_too_small") from exc
    if interval < MIN_INTERVAL_SECONDS:
        raise SchedulerError(
            f"interval must be at least {MIN_INTERVAL_SECONDS} seconds",
            code="interval_too_small",
        )
    _validate_timezone(timezone_name)

    now = utcnow()
    schedule = Schedule(
        tenant_id=tenant_id,
        owner_principal_id=owner_principal_id,
        name=name,
        schedule_type=schedule_type,
        operation=operation,
        target_json=json.dumps(target) if target else None,
        status="active",
        enabled=True,
        timezone=timezone_name,
        interval_seconds=interval,
        next_run_at=now + timedelta(seconds=interval),
        misfire_policy=misfire_policy,
        max_retries=int(max_retries),
    )
    db.add(schedule)
    await db.flush()

    await record_audit(
        db,
        principal=_principal_of(actor),
        tenant_id=tenant_id,
        action="schedule.create",
        decision="allow",
        resource_type="schedule",
        resource_id=schedule.id,
        metadata={
            "actor": _actor_label(actor),
            "owner_principal_id": owner_principal_id,
            "operation": operation,
            "schedule_type": schedule_type,
            "interval_seconds": interval,
            "timezone": timezone_name,
        },
    )
    return schedule


async def set_schedule_state(
    db: AsyncSession,
    *,
    schedule: Schedule,
    enabled: bool | None = None,
    status: str | None = None,
    actor,
) -> Schedule:
    """Enable, disable or pause a schedule, with an audit record for the change."""
    action: str | None = None

    if enabled is not None:
        schedule.enabled = bool(enabled)
        schedule.status = "active" if enabled else "disabled"
        action = "schedule.enable" if enabled else "schedule.disable"

    if status is not None:
        schedule.status = status
        if status == "paused":
            action = "schedule.pause"
        elif status == "active":
            schedule.enabled = True
            action = "schedule.enable"
        elif status == "disabled":
            schedule.enabled = False
            action = "schedule.disable"

    schedule.updated_at = utcnow()
    await db.flush()

    if action is not None:
        await record_audit(
            db,
            principal=_principal_of(actor),
            tenant_id=schedule.tenant_id,
            action=action,
            decision="allow",
            resource_type="schedule",
            resource_id=schedule.id,
            metadata={
                "actor": _actor_label(actor),
                "status": schedule.status,
                "enabled": schedule.enabled,
            },
        )
    return schedule


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


async def list_schedules(db: AsyncSession, *, tenant_id: str) -> list[Schedule]:
    """Schedules of one tenant. A foreign schedule is indistinguishable from absent."""
    result = await db.execute(
        select(Schedule)
        .where(Schedule.tenant_id == tenant_id)
        .order_by(Schedule.name)
    )
    return list(result.scalars().all())


async def due_schedules(
    db: AsyncSession, *, now: datetime | None = None, limit: int = 20, tenant_id: str | None = None
) -> list[Schedule]:
    """Active, enabled schedules whose next occurrence has arrived.

    Ordered oldest-occurrence-first and bounded by ``limit``. ``tenant_id`` is an
    optional filter; when it is omitted the tick is deployment-wide, as a
    scheduler should be.
    """
    moment = _as_utc(now) or utcnow()
    query = (
        select(Schedule)
        .where(
            Schedule.status == "active",
            Schedule.enabled.is_(True),
            Schedule.next_run_at.is_not(None),
            Schedule.next_run_at <= moment,
        )
    )
    if tenant_id is not None:
        query = query.where(Schedule.tenant_id == tenant_id)
    query = query.order_by(Schedule.next_run_at).limit(int(limit))
    result = await db.execute(query)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Claiming and execution
# ---------------------------------------------------------------------------


async def claim_run(
    db: AsyncSession,
    *,
    schedule: Schedule,
    scheduled_for: datetime,
    claim_token: str,
) -> ScheduleRun | None:
    """Attempt to claim one occurrence. Returns None when another claimer won.

    The insert is the arbitration: ``uq_schedule_run_occurrence`` on
    (schedule_id, scheduled_for) makes a duplicate claim impossible. The
    IntegrityError is rolled back so the session remains usable for the next
    schedule in the batch.
    """
    run = ScheduleRun(
        tenant_id=schedule.tenant_id,
        schedule_id=schedule.id,
        scheduled_for=scheduled_for,
        status="claimed",
        claim_token=claim_token,
        attempt=1,
        claimed_at=utcnow(),
    )
    db.add(run)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return None
    return run


async def execute_run(
    db: AsyncSession,
    *,
    schedule: Schedule,
    run: ScheduleRun,
    claim_token: str,
    now: datetime | None = None,
) -> ScheduleRun:
    """Execute one claimed occurrence and reschedule the schedule.

    Authorization is revalidated first, then the operation is dispatched, then
    the outcome is written and audited in the same commit that advances
    ``next_run_at``. The operation runs with the owner's current permissions and
    never more. ``now`` is the tick's reference time; when given it is what the
    misfire policy advances relative to.
    """
    started = utcnow()
    moment = _as_utc(now) or started
    await db.refresh(schedule)
    await db.refresh(run)
    spec = REGISTERED_OPERATIONS.get(schedule.operation)
    occurrence = _as_utc(run.scheduled_for) or moment

    run.status = "running"
    run.started_at = started
    run.attempt = max(int(run.attempt or 1), 1)
    await db.flush()

    status = "failed"
    category: str | None = FAILURE_OPERATION_ERROR
    detail: str | None = None

    if schedule.status != "active" or not schedule.enabled:
        status, category, detail = (
            "skipped",
            FAILURE_SCHEDULE_DISABLED,
            "schedule is not active",
        )
    elif spec is None:
        status, category, detail = (
            "failed",
            FAILURE_UNSUPPORTED_OPERATION,
            f"operation '{schedule.operation}' is not registered",
        )
    else:
        denied = await _revalidate(db, schedule=schedule, spec=spec)
        if denied is not None:
            status, category, detail = denied
        else:
            try:
                status, category, detail = await _dispatch(db, schedule=schedule, spec=spec)
            except Exception as exc:  # noqa: BLE001 - one bad operation must not stop the tick
                status, category, detail = "failed", FAILURE_OPERATION_ERROR, str(exc)

    # An internal commit (the sync engine commits its own bookkeeping) may have
    # expired these instances; reload before writing the outcome.
    await db.refresh(schedule)
    await db.refresh(run)

    finished = utcnow()
    run.status = status
    run.finished_at = finished
    run.failure_category = category
    run.detail = _safe_detail(detail)

    schedule.last_run_at = finished
    schedule.last_result = status
    schedule.last_failure_category = category
    schedule.next_run_at = _advance(schedule, occurrence, moment)
    schedule.updated_at = finished
    await db.flush()

    await record_audit(
        db,
        principal=None,
        tenant_id=schedule.tenant_id,
        action="schedule.run",
        decision="allow" if status == "succeeded" else "deny",
        resource_type="schedule",
        resource_id=schedule.id,
        reason=category,
        metadata={
            "owner_principal_id": schedule.owner_principal_id,
            "operation": schedule.operation,
            "run_id": run.id,
            "occurrence": occurrence.isoformat(),
            "claim_token": claim_token,
        },
    )
    await db.commit()
    return run


async def run_due(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 20,
    claim_token: str | None = None,
) -> int:
    """One scheduler tick. Claims and executes due schedules; returns how many ran.

    Bounded by ``limit`` and duplicate-free: a restart that still sees an old
    occurrence loses the claim on the unique constraint and never runs the
    operation a second time.
    """
    moment = _as_utc(now) or utcnow()
    token = claim_token or str(uuid.uuid4())
    due = await due_schedules(db, now=moment, limit=limit)
    ran = 0
    for candidate in due:
        # Re-load by id: a commit from the previous iteration may have expired
        # the instances returned by the selection query.
        schedule = await db.get(Schedule, candidate.id)
        if schedule is None:
            continue
        if schedule.status != "active" or not schedule.enabled:
            continue
        occurrence = _as_utc(schedule.next_run_at)
        if occurrence is None or occurrence > moment:
            continue
        run = await claim_run(
            db, schedule=schedule, scheduled_for=occurrence, claim_token=token
        )
        if run is None:
            # Another claimer already owns this occurrence.
            continue
        await execute_run(
            db, schedule=schedule, run=run, claim_token=token, now=moment
        )
        ran += 1
    return ran


__all__ = [
    "FAILURE_AUTHORIZATION_REVOKED",
    "FAILURE_CONNECTOR_DISABLED",
    "FAILURE_MISSING_TARGET",
    "FAILURE_NOT_IMPLEMENTED",
    "FAILURE_OPERATION_ERROR",
    "FAILURE_PERMISSION_DENIED",
    "FAILURE_PRINCIPAL_DISABLED",
    "FAILURE_SCHEDULE_DISABLED",
    "FAILURE_UNSUPPORTED_OPERATION",
    "MIN_INTERVAL_SECONDS",
    "OPERATION_CONNECTOR_INCREMENTAL_SYNC",
    "OPERATION_CONNECTOR_RECONCILE",
    "OPERATION_CONNECTOR_SYNC",
    "OPERATION_NOTIFICATION_RETRY",
    "OPERATION_REPORT_RERUN",
    "OperationSpec",
    "REGISTERED_OPERATIONS",
    "SchedulerError",
    "claim_run",
    "create_schedule",
    "due_schedules",
    "execute_run",
    "list_schedules",
    "operation_supported",
    "registered_operations",
    "run_due",
    "set_schedule_state",
]
