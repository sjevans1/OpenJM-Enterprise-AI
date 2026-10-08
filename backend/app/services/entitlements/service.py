"""M3 commercial entitlement service (#46 M3).

The request path is:

    reserve  -> an opaque handle and a hold on the billing period
    finalize -> settle a completed execution exactly once
    release  -> return a hold taken for a request that failed before dispatch

Every step is idempotent on a server-issued key, and every monetary, credit and
allowance value is an integer minor unit. No float participates anywhere.

Concurrency safety
------------------
The hard cap is enforced by a single conditional ``UPDATE`` against the
billing-period row whose ``WHERE`` clause is the capacity test:

    UPDATE entitlement_billing_periods
       SET held_units = held_units + :units
     WHERE id = :period_id
       AND allowance_units + purchased_units - consumed_units - held_units >= :units

The database evaluates the test and the increment atomically, so two concurrent
reservations cannot both consume capacity that only one may hold. A row count of
one means the reservation was granted; zero means the hard cap was reached. The
same statement works on SQLite and PostgreSQL, and it is the first write in the
transaction so no read lock is held across the guard on either backend. This is
deliberately not an in-process lock: it holds across processes and across hosts.

The reservation row's state transition is guarded the same way
(``... WHERE id = :id AND status = 'reserved'``), which is what makes finalize,
release and expiry run exactly once.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements import (
    BillingPeriodStatus,
    EntitlementNotConfigured,
    HardCapExceeded,
    LedgerEntryType,
    PlanStatus,
    ReservationNotFound,
    ReservationStateError,
    ReservationStatus,
    SOFT_THRESHOLDS,
    SubscriptionStatus,
    TENANT_STATUS_ACTIVE,
    TenantInactive,
)
from app.models import (
    BillingPeriod,
    CreditLedgerEntry,
    Plan,
    PlanVersion,
    Tenant,
    TenantSubscription,
    UsageAllowance,
    UsageReservation,
    new_id,
)

# A reservation lives long enough to cover one bounded request. It is a hold,
# never an entitlement: an expired hold is released and does not consume.
DEFAULT_TTL_SECONDS = 300

_RESERVED = ReservationStatus.RESERVED.value
_SETTLED = ReservationStatus.SETTLED.value
_RELEASED = ReservationStatus.RELEASED.value
_EXPIRED = ReservationStatus.EXPIRED.value


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now(value: datetime | None) -> datetime:
    return value if value is not None else utcnow()


def _naive_utc(value: datetime) -> datetime:
    """Express a datetime as naive UTC, matching how SQLite stores it.

    SQLite persists ``DateTime(timezone=True)`` as naive ``YYYY-MM-DD HH:MM:SS``
    text, so a value read back from the database is naive while a freshly minted
    ``utcnow()`` is aware. Comparing them directly raises; normalizing both to
    naive UTC is the one comparison the storage layer and the service agree on.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _as_int(value: object, *, name: str = "amount") -> int:
    """Coerce a value to a Python ``int`` or refuse it.

    Amounts are integer minor units. A ``float`` (or a ``bool`` masquerading as
    an int) is refused rather than silently truncated, so no floating point can
    reach a balance, a reservation, a price or a threshold comparison.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer minor unit")
    return int(value)


def period_key(moment: datetime) -> str:
    """The UTC billing-period key for a moment, ``YYYY-MM``."""
    return f"{moment.year:04d}-{moment.month:02d}"


def _period_bounds(key: str) -> tuple[datetime, datetime]:
    year, month = key.split("-")
    start = datetime(int(year), int(month), 1, tzinfo=timezone.utc)
    if int(month) == 12:
        end = datetime(int(year) + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(int(year), int(month) + 1, 1, tzinfo=timezone.utc)
    return start, end


# ---------------------------------------------------------------------------
# Configuration helpers (plan, allowance, subscription, price schedule)
# ---------------------------------------------------------------------------


async def record_plan(db: AsyncSession, *, slug: str, name: str) -> Plan:
    """Return the plan for ``slug``, creating it once. Idempotent."""
    existing = (
        await db.execute(select(Plan).where(Plan.slug == slug))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    plan = Plan(slug=slug, name=name, status=PlanStatus.ACTIVE.value)
    db.add(plan)
    await db.flush()
    return plan


async def add_plan_version(
    db: AsyncSession, *, plan: Plan, allowance_units: int, overage_allowed: bool = False
) -> PlanVersion:
    """Add the next version of a plan's terms. Never edits an earlier version."""
    allowance_units = _as_int(allowance_units, name="allowance_units")
    if allowance_units < 0:
        raise ValueError("allowance_units must not be negative")
    latest = (
        await db.execute(
            select(func.max(PlanVersion.version)).where(PlanVersion.plan_id == plan.id)
        )
    ).scalar_one()
    version = int(latest or 0) + 1
    plan_version = PlanVersion(
        id=new_id(),
        plan_id=plan.id,
        version=version,
        included_allowance_units=allowance_units,
        overage_allowed=overage_allowed,
        status=PlanStatus.ACTIVE.value,
    )
    db.add(plan_version)
    await db.flush()
    return plan_version


async def grant_allowance(
    db: AsyncSession, *, tenant_id: str, allowance_units: int, plan_version_id: str
) -> UsageAllowance:
    """Add the next version of a tenant's allowance. History is not rewritten."""
    allowance_units = _as_int(allowance_units, name="allowance_units")
    if allowance_units < 0:
        raise ValueError("allowance_units must not be negative")
    latest = (
        await db.execute(
            select(func.max(UsageAllowance.version)).where(
                UsageAllowance.tenant_id == tenant_id
            )
        )
    ).scalar_one()
    version = int(latest or 0) + 1
    allowance = UsageAllowance(
        id=new_id(),
        tenant_id=tenant_id,
        version=version,
        plan_version_id=plan_version_id,
        allowance_units=allowance_units,
    )
    db.add(allowance)
    await db.flush()
    return allowance


async def set_subscription(
    db: AsyncSession,
    *,
    tenant_id: str,
    plan_version_id: str,
    status: str = SubscriptionStatus.ACTIVE.value,
) -> TenantSubscription:
    """Bind the tenant to a plan version, or update its status. One per tenant."""
    subscription = (
        await db.execute(
            select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if subscription is None:
        subscription = TenantSubscription(
            id=new_id(),
            tenant_id=tenant_id,
            plan_version_id=plan_version_id,
            status=status,
        )
        db.add(subscription)
    else:
        subscription.plan_version_id = plan_version_id
        subscription.status = status
    await db.flush()
    return subscription


async def configure_tenant(
    db: AsyncSession,
    *,
    tenant_id: str,
    allowance_units: int,
    plan_slug: str = "standard",
    plan_name: str = "Standard",
) -> tuple[Plan, PlanVersion, UsageAllowance, TenantSubscription]:
    """Provision a tenant's plan, allowance and subscription in one call."""
    plan = await record_plan(db, slug=plan_slug, name=plan_name)
    plan_version = await add_plan_version(db, plan=plan, allowance_units=allowance_units)
    allowance = await grant_allowance(
        db, tenant_id=tenant_id, allowance_units=allowance_units, plan_version_id=plan_version.id
    )
    subscription = await set_subscription(
        db, tenant_id=tenant_id, plan_version_id=plan_version.id
    )
    return plan, plan_version, allowance, subscription


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def _get_period(
    db: AsyncSession, *, tenant_id: str, now: datetime
) -> BillingPeriod | None:
    return (
        await db.execute(
            select(BillingPeriod).where(
                BillingPeriod.tenant_id == tenant_id,
                BillingPeriod.period_key == period_key(now),
            )
        )
    ).scalar_one_or_none()


async def _latest_allowance(db: AsyncSession, *, tenant_id: str) -> UsageAllowance | None:
    return (
        await db.execute(
            select(UsageAllowance)
            .where(UsageAllowance.tenant_id == tenant_id)
            .order_by(UsageAllowance.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _get_or_create_period(
    db: AsyncSession, *, tenant_id: str, now: datetime
) -> BillingPeriod:
    """Open the current period for a tenant, creating it once.

    The period snapshots the allowance version and amount it opened with, so a
    later allowance version never rewrites an existing period.
    """
    existing = await _get_period(db, tenant_id=tenant_id, now=now)
    if existing is not None:
        return existing

    subscription = (
        await db.execute(
            select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if subscription is None or subscription.status != SubscriptionStatus.ACTIVE.value:
        raise EntitlementNotConfigured("No active subscription for tenant")
    allowance = await _latest_allowance(db, tenant_id=tenant_id)
    if allowance is None:
        raise EntitlementNotConfigured("No allowance configured for tenant")

    key = period_key(now)
    start, end = _period_bounds(key)
    period = BillingPeriod(
        id=new_id(),
        tenant_id=tenant_id,
        period_key=key,
        period_start=start,
        period_end=end,
        plan_version_id=subscription.plan_version_id,
        allowance_id=allowance.id,
        allowance_version=allowance.version,
        allowance_units=allowance.allowance_units,
        status=BillingPeriodStatus.OPEN.value,
    )
    try:
        db.add(period)
        await db.flush()
    except IntegrityError:
        # A concurrent opener won the unique (tenant_id, period_key) race.
        await db.rollback()
        reopened = await _get_period(db, tenant_id=tenant_id, now=now)
        if reopened is None:
            raise
        return reopened
    await _append_ledger(
        db,
        period_id=period.id,
        tenant_id=tenant_id,
        entry_type=LedgerEntryType.GRANT,
        amount=allowance.allowance_units,
        idempotency_key=f"grant:{period.id}",
        now=now,
        reason=f"allowance v{allowance.version}",
    )
    return period


async def _period_available(db: AsyncSession, period_id: str) -> int:
    row = (
        await db.execute(
            select(
                BillingPeriod.allowance_units,
                BillingPeriod.purchased_units,
                BillingPeriod.consumed_units,
                BillingPeriod.held_units,
            ).where(BillingPeriod.id == period_id)
        )
    ).one()
    return int(row[0] + row[1] - row[2] - row[3])


async def _append_ledger(
    db: AsyncSession,
    *,
    period_id: str,
    tenant_id: str,
    entry_type: LedgerEntryType,
    amount: int,
    idempotency_key: str,
    now: datetime,
    reservation_id: str | None = None,
    reason: str | None = None,
) -> CreditLedgerEntry:
    amount = _as_int(amount)
    balance_after = await _period_available(db, period_id)
    entry = CreditLedgerEntry(
        id=new_id(),
        tenant_id=tenant_id,
        billing_period_id=period_id,
        entry_type=entry_type.value,
        amount_units=amount,
        balance_after_units=balance_after,
        reservation_id=reservation_id,
        idempotency_key=idempotency_key,
        reason=reason,
    )
    db.add(entry)
    await db.flush()
    return entry


async def _find_reservation(
    db: AsyncSession, tenant_id: str, idempotency_key: str
) -> UsageReservation | None:
    return (
        await db.execute(
            select(UsageReservation).where(
                UsageReservation.tenant_id == tenant_id,
                UsageReservation.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()


async def _require_active_tenant(db: AsyncSession, tenant_id: str) -> None:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.status != TENANT_STATUS_ACTIVE:
        raise TenantInactive(f"Tenant {tenant_id!r} is not active")


def _reserve_capacity_clause(table, units: int):
    """The hard-cap test. Kept in one function so a mutation test can weaken it.

    The clause is an integer comparison against the allowance plus purchased
    credits, minus what is already consumed or held. No float is involved.
    """
    return (
        table.allowance_units
        + table.purchased_units
        - table.consumed_units
        - table.held_units
        >= units
    )


# ---------------------------------------------------------------------------
# Reserve / finalize / release
# ---------------------------------------------------------------------------


async def reserve(
    db: AsyncSession,
    *,
    tenant_id: str,
    idempotency_key: str,
    units: int,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> UsageReservation:
    """Reserve allowance for one request. Returns the reservation row.

    The returned ``handle`` is opaque; it carries no capacity, placement or
    isolation meaning. Idempotent on ``idempotency_key``: reserving twice with
    the same key returns the same reservation.
    """
    units = _as_int(units, name="units")
    if units <= 0:
        raise ValueError("units must be positive")
    if not idempotency_key:
        raise ValueError("an idempotency key is required")
    now = _now(now)

    await _require_active_tenant(db, tenant_id)
    period = await _get_or_create_period(db, tenant_id=tenant_id, now=now)
    expires_at = now + timedelta(seconds=int(ttl_seconds))
    reservation_id = new_id()

    guard = (
        update(BillingPeriod)
        .where(
            BillingPeriod.id == period.id,
            _reserve_capacity_clause(BillingPeriod, units),
        )
        .values(held_units=BillingPeriod.held_units + units)
        .execution_options(synchronize_session=False)
    )
    try:
        result = await db.execute(guard)
        if result.rowcount != 1:
            # Either the hard cap is reached, or this key already reserved.
            await db.rollback()
            existing = await _find_reservation(db, tenant_id, idempotency_key)
            if existing is not None:
                return existing
            raise HardCapExceeded(
                "Allowance plus purchased credits is exhausted for the period"
            )
        reservation = UsageReservation(
            id=reservation_id,
            tenant_id=tenant_id,
            billing_period_id=period.id,
            handle=new_id(),
            idempotency_key=idempotency_key,
            status=_RESERVED,
            reserved_units=units,
            expires_at=expires_at,
        )
        db.add(reservation)
        await db.flush()
        await _append_ledger(
            db,
            period_id=period.id,
            tenant_id=tenant_id,
            entry_type=LedgerEntryType.HOLD,
            amount=-units,
            idempotency_key=f"hold:{reservation_id}",
            now=now,
            reservation_id=reservation_id,
        )
        await db.commit()
        return reservation
    except IntegrityError:
        # A concurrent reserve with the same key won. Return the winner.
        await db.rollback()
        existing = await _find_reservation(db, tenant_id, idempotency_key)
        if existing is None:
            raise
        return existing


async def finalize(
    db: AsyncSession,
    *,
    tenant_id: str,
    idempotency_key: str,
    settled_units: int,
    now: datetime | None = None,
) -> UsageReservation:
    """Settle one completed execution exactly once against the allowance.

    Finalizing twice settles once; the second call returns the settled row. An
    inactive tenant never settles: the reservation is left untouched and the
    caller is refused.
    """
    settled_units = _as_int(settled_units, name="settled_units")
    if settled_units < 0:
        raise ValueError("settled_units must not be negative")
    now = _now(now)

    reservation = await _find_reservation(db, tenant_id, idempotency_key)
    if reservation is None:
        raise ReservationNotFound("No reservation for that key")
    if reservation.status == _SETTLED:
        return reservation
    if reservation.status in (_RELEASED, _EXPIRED):
        raise ReservationStateError(
            f"Reservation is {reservation.status} and cannot settle"
        )
    if _naive_utc(now) >= _naive_utc(reservation.expires_at):
        await _apply_transition(
            db, reservation, status=_EXPIRED, reason="expired", now=now
        )
        raise ReservationStateError("Reservation expired before it could settle")
    if settled_units > reservation.reserved_units:
        raise ValueError("settled_units exceed the reserved units")

    # A suspended or revoked tenant must not settle, even holding a reservation.
    await _require_active_tenant(db, tenant_id)

    transition = (
        update(UsageReservation)
        .where(UsageReservation.id == reservation.id, UsageReservation.status == _RESERVED)
        .values(status=_SETTLED, settled_units=settled_units, finalized_at=now)
        .execution_options(synchronize_session=False)
    )
    outcome = await db.execute(transition)
    if outcome.rowcount != 1:
        await db.rollback()
        current = await _find_reservation(db, tenant_id, idempotency_key)
        if current is not None and current.status == _SETTLED:
            return current
        raise ReservationStateError("Reservation could not be settled")

    adjustment = (
        update(BillingPeriod)
        .where(
            BillingPeriod.id == reservation.billing_period_id,
            BillingPeriod.held_units >= reservation.reserved_units,
        )
        .values(
            held_units=BillingPeriod.held_units - reservation.reserved_units,
            consumed_units=BillingPeriod.consumed_units + settled_units,
        )
        .execution_options(synchronize_session=False)
    )
    period_outcome = await db.execute(adjustment)
    if period_outcome.rowcount != 1:
        await db.rollback()
        raise ReservationStateError("Held units no longer cover the reservation")

    await _append_ledger(
        db,
        period_id=reservation.billing_period_id,
        tenant_id=tenant_id,
        entry_type=LedgerEntryType.SETTLEMENT,
        amount=reservation.reserved_units - settled_units,
        idempotency_key=f"settle:{reservation.id}",
        now=now,
        reservation_id=reservation.id,
    )
    await db.commit()
    await db.refresh(reservation)
    return reservation


async def release(
    db: AsyncSession,
    *,
    tenant_id: str,
    idempotency_key: str,
    now: datetime | None = None,
    reason: str = "released",
) -> UsageReservation:
    """Return a hold taken for a request that failed before dispatch.

    Releasing twice releases once; the second call returns the released row. A
    released hold never settles.
    """
    now = _now(now)
    reservation = await _find_reservation(db, tenant_id, idempotency_key)
    if reservation is None:
        raise ReservationNotFound("No reservation for that key")
    if reservation.status in (_RELEASED, _EXPIRED):
        return reservation
    if reservation.status == _SETTLED:
        raise ReservationStateError("A settled reservation cannot be released")
    await _apply_transition(db, reservation, status=_RELEASED, reason=reason, now=now)
    await db.refresh(reservation)
    return reservation


async def _apply_transition(
    db: AsyncSession,
    reservation: UsageReservation,
    *,
    status: str,
    reason: str,
    now: datetime,
) -> bool:
    """Move one reserved hold to released or expired, exactly once."""
    transition = (
        update(UsageReservation)
        .where(UsageReservation.id == reservation.id, UsageReservation.status == _RESERVED)
        .values(status=status, released_at=now)
        .execution_options(synchronize_session=False)
    )
    outcome = await db.execute(transition)
    if outcome.rowcount != 1:
        await db.rollback()
        return False

    adjustment = (
        update(BillingPeriod)
        .where(
            BillingPeriod.id == reservation.billing_period_id,
            BillingPeriod.held_units >= reservation.reserved_units,
        )
        .values(held_units=BillingPeriod.held_units - reservation.reserved_units)
        .execution_options(synchronize_session=False)
    )
    period_outcome = await db.execute(adjustment)
    if period_outcome.rowcount != 1:
        await db.rollback()
        raise ReservationStateError("Held units no longer cover the reservation")

    entry_type = (
        LedgerEntryType.EXPIRY if status == _EXPIRED else LedgerEntryType.RELEASE
    )
    await _append_ledger(
        db,
        period_id=reservation.billing_period_id,
        tenant_id=reservation.tenant_id,
        entry_type=entry_type,
        amount=reservation.reserved_units,
        idempotency_key=f"{status}:{reservation.id}",
        now=now,
        reservation_id=reservation.id,
        reason=reason,
    )
    await db.commit()
    return True


async def expire_reservations(
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
    now: datetime | None = None,
) -> int:
    """Release every reservation that is past its expiry. Returns the count.

    Called by recovery: an in-flight hold left by an interrupted process is
    released exactly once, and never settles.
    """
    now = _now(now)
    query = select(UsageReservation).where(
        UsageReservation.status == _RESERVED,
        UsageReservation.expires_at <= _naive_utc(now),
    )
    if tenant_id is not None:
        query = query.where(UsageReservation.tenant_id == tenant_id)
    rows = (await db.execute(query)).scalars().all()
    released = 0
    for reservation in rows:
        if await _apply_transition(
            db, reservation, status=_EXPIRED, reason="expired", now=now
        ):
            released += 1
    return released


async def recover_inflight(
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
    now: datetime | None = None,
) -> int:
    """Crash recovery: release expired in-flight holds without double movement.

    Idempotent and safe to run repeatedly. A reservation whose hold is released
    here cannot later settle, so recovery never double counts.
    """
    return await expire_reservations(db, tenant_id=tenant_id, now=now)


# ---------------------------------------------------------------------------
# Purchased credits
# ---------------------------------------------------------------------------


async def purchase_credits(
    db: AsyncSession,
    *,
    tenant_id: str,
    units: int,
    idempotency_key: str,
    now: datetime | None = None,
) -> CreditLedgerEntry:
    """Add purchased credits to the current period. Idempotent on the key."""
    units = _as_int(units, name="units")
    if units <= 0:
        raise ValueError("units must be positive")
    if not idempotency_key:
        raise ValueError("an idempotency key is required")
    now = _now(now)

    existing = (
        await db.execute(
            select(CreditLedgerEntry).where(
                CreditLedgerEntry.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    period = await _get_or_create_period(db, tenant_id=tenant_id, now=now)
    statement = (
        update(BillingPeriod)
        .where(BillingPeriod.id == period.id)
        .values(purchased_units=BillingPeriod.purchased_units + units)
        .execution_options(synchronize_session=False)
    )
    await db.execute(statement)
    entry = await _append_ledger(
        db,
        period_id=period.id,
        tenant_id=tenant_id,
        entry_type=LedgerEntryType.PURCHASE,
        amount=units,
        idempotency_key=idempotency_key,
        now=now,
    )
    await db.commit()
    return entry


# ---------------------------------------------------------------------------
# Balances, thresholds and eligibility
# ---------------------------------------------------------------------------


async def available_units(
    db: AsyncSession, *, tenant_id: str, now: datetime | None = None
) -> int | None:
    """Available units for the current period, or None when none is open."""
    period = await _get_period(db, tenant_id=tenant_id, now=_now(now))
    if period is None:
        return None
    return await _period_available(db, period.id)


async def soft_thresholds(
    db: AsyncSession, *, tenant_id: str, now: datetime | None = None
) -> dict:
    """Report the 50/75/90/100% soft thresholds for the current period.

    Every comparison is an integer percentage of the allowance
    (``consumed * 100 >= allowance * threshold``), never a float fraction.
    """
    now = _now(now)
    period = await _get_period(db, tenant_id=tenant_id, now=now)
    if period is None:
        return {
            "billing_period": None,
            "allowance_units": None,
            "consumed_units": None,
            "available_units": None,
            "thresholds": [],
        }
    allowance = int(period.allowance_units)
    consumed = int(period.consumed_units)
    available = await _period_available(db, period.id)
    reached = [
        {
            "threshold": threshold,
            "reached": bool(allowance > 0 and consumed * 100 >= allowance * threshold),
        }
        for threshold in SOFT_THRESHOLDS
    ]
    return {
        "billing_period": period.period_key,
        "allowance_units": allowance,
        "consumed_units": consumed,
        "available_units": available,
        "thresholds": reached,
    }


async def can_start_execution(
    db: AsyncSession,
    *,
    tenant_id: str,
    units: int = 1,
    now: datetime | None = None,
) -> bool:
    """Whether a new billable execution of ``units`` may start right now."""
    units = _as_int(units, name="units")
    now = _now(now)
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.status != TENANT_STATUS_ACTIVE:
        return False
    period = await _get_period(db, tenant_id=tenant_id, now=now)
    if period is None:
        return False
    return await _period_available(db, period.id) >= units


async def entitlement_summary(
    db: AsyncSession, *, tenant_id: str, now: datetime | None = None
) -> dict:
    """A bounded, tenant-scoped view of plan, allowance and period state.

    Every query is scoped by ``tenant_id`` in SQL, so one tenant can never read
    another's plan, allowance or credits.
    """
    now = _now(now)
    subscription = (
        await db.execute(
            select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()

    plan_block: dict | None = None
    if subscription is not None:
        plan_version = await db.get(PlanVersion, subscription.plan_version_id)
        plan = await db.get(Plan, plan_version.plan_id) if plan_version else None
        plan_block = {
            "plan_id": plan.id if plan else None,
            "plan_slug": plan.slug if plan else None,
            "plan_name": plan.name if plan else None,
            "plan_version": plan_version.version if plan_version else None,
            "included_allowance_units": (
                int(plan_version.included_allowance_units) if plan_version else None
            ),
        }

    period = await _get_period(db, tenant_id=tenant_id, now=now)
    allowance = await _latest_allowance(db, tenant_id=tenant_id)

    allowance_block = None
    billing_block = None
    if period is not None:
        allowance_block = {
            "allowance_version": int(period.allowance_version),
            "allowance_units": int(period.allowance_units),
            "purchased_units": int(period.purchased_units),
            "held_units": int(period.held_units),
            "consumed_units": int(period.consumed_units),
            "available_units": int(await _period_available(db, period.id)),
        }
        billing_block = {
            "period_key": period.period_key,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
            "status": period.status,
        }

    thresholds = await soft_thresholds(db, tenant_id=tenant_id, now=now)
    return {
        "tenant_id": tenant_id,
        "subscription_status": subscription.status if subscription else None,
        "plan": plan_block,
        "allowance": allowance_block,
        "billing_period": billing_block,
        "latest_allowance_version": int(allowance.version) if allowance else None,
        "soft_thresholds": thresholds["thresholds"],
    }


async def platform_entitlement_metadata(db: AsyncSession) -> dict:
    """Aggregate, cross-tenant entitlement metadata. No customer content.

    Counts and totals only: no tenant identifier, no prompt, no response and no
    per-tenant row. This backs the platform metadata plane.
    """
    subscription_rows = (
        await db.execute(
            select(TenantSubscription.status, func.count()).group_by(
                TenantSubscription.status
            )
        )
    ).all()
    subscriptions_by_status = {str(status): int(count) for status, count in subscription_rows}

    plan_rows = (
        await db.execute(
            select(Plan.slug, func.count())
            .select_from(TenantSubscription)
            .join(PlanVersion, PlanVersion.id == TenantSubscription.plan_version_id)
            .join(Plan, Plan.id == PlanVersion.plan_id)
            .group_by(Plan.slug)
        )
    ).all()
    tenants_by_plan = {str(slug): int(count) for slug, count in plan_rows}

    period_row = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(BillingPeriod.allowance_units), 0),
                func.coalesce(func.sum(BillingPeriod.purchased_units), 0),
                func.coalesce(func.sum(BillingPeriod.consumed_units), 0),
                func.coalesce(func.sum(BillingPeriod.held_units), 0),
            )
        )
    ).one()

    reservation_row = (
        await db.execute(
            select(UsageReservation.status, func.count()).group_by(UsageReservation.status)
        )
    ).all()
    reservations_by_status = {str(status): int(count) for status, count in reservation_row}

    ledger_count = (
        await db.execute(select(func.count()).select_from(CreditLedgerEntry))
    ).scalar_one()

    return {
        "tenant_count": int(
            (await db.execute(select(func.count()).select_from(Tenant))).scalar_one()
        ),
        "subscriptions_by_status": subscriptions_by_status,
        "tenants_by_plan": tenants_by_plan,
        "billing_periods": {
            "count": int(period_row[0] or 0),
            "allowance_units_total": int(period_row[1]),
            "purchased_units_total": int(period_row[2]),
            "consumed_units_total": int(period_row[3]),
            "held_units_total": int(period_row[4]),
        },
        "reservations_by_status": reservations_by_status,
        "credit_ledger_entries": int(ledger_count or 0),
    }
