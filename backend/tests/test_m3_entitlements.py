"""M3 commercial entitlement foundation (#46 M3).

Red->green coverage for the accepted M3 scope: fixed-precision integer
accounting, an append-only credit ledger, versioned plans and allowances, a
concurrency-safe hard cap, idempotent reserve/finalize/release, expiry and crash
recovery, billing-exhaustion gating that never blocks history/admin/recovery,
tenant-scoped client administration and an explicit platform metadata boundary.

The service is exercised directly for the accounting behaviour and through the
ASGI app for the two read surfaces. The database trigger for ledger immutability
is created by migration ``0016_m3_entitlements``, not by ``create_all``, so the
migration test runs against a real migrated database.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, inspect, select, text

from app.core.entitlements import (
    HardCapExceeded,
    LedgerImmutabilityError,
    ReservationStateError,
    SOFT_THRESHOLDS,
    TenantInactive,
)
from app.core.platform import PlatformCapability
from app.core.tenancy import LEGACY_PRINCIPAL_ID, LEGACY_TENANT_ID
from app.models import CreditLedgerEntry, PriceSchedule, Tenant, UsageReservation
from app.services import access_governance as governance
from app.services import entitlements as ent
from app.services.entitlements import service as service_module

TENANT_B = "tnt-tenant-b"
OCT = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
NOV = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)


async def _configure(maker, *, tenant_id=LEGACY_TENANT_ID, allowance=100, plan_slug="standard"):
    async with maker() as db:
        await ent.configure_tenant(
            db, tenant_id=tenant_id, allowance_units=allowance, plan_slug=plan_slug
        )
        await db.commit()


async def _add_tenant(maker, tenant_id):
    async with maker() as db:
        db.add(Tenant(id=tenant_id, slug=tenant_id, name=tenant_id, status="active"))
        await db.commit()


async def _dispatch(maker, *, key, tenant_id=LEGACY_TENANT_ID, now=None, uncertain=False):
    """Record trusted dispatch evidence so settlement is authorized."""
    async with maker() as db:
        marker = (
            ent.mark_execution_uncertain if uncertain else ent.mark_execution_dispatched
        )
        row = await marker(db, tenant_id=tenant_id, idempotency_key=key, now=now)
        return row


async def _settle(maker, *, tenant_id=LEGACY_TENANT_ID, key, units, settled=None, now=None):
    async with maker() as db:
        await ent.reserve(
            db, tenant_id=tenant_id, idempotency_key=key, units=units, now=now
        )
        await ent.mark_execution_dispatched(
            db, tenant_id=tenant_id, idempotency_key=key, now=now
        )
        return await ent.finalize(
            db,
            tenant_id=tenant_id,
            idempotency_key=key,
            settled_units=units if settled is None else settled,
            now=now,
        )


async def _ledger(maker, entry_type=None):
    async with maker() as db:
        query = select(CreditLedgerEntry)
        if entry_type is not None:
            query = query.where(CreditLedgerEntry.entry_type == entry_type)
        return (await db.execute(query)).scalars().all()


# ---------------------------------------------------------------------------
# Idempotency: reserve / finalize / release keyed on a server-issued key
# ---------------------------------------------------------------------------


async def test_reserve_twice_with_same_key_returns_one_reservation(file_db):
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        first = await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", units=40, now=OCT
        )
        second = await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", units=40, now=OCT
        )
    assert first.id == second.id
    assert first.handle == second.handle
    # The hold was taken once, not twice.
    assert await _available(file_db) == 60


async def test_finalize_twice_settles_once(file_db):
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", units=40, now=OCT
        )
        await ent.mark_execution_dispatched(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", now=OCT
        )
        first = await ent.finalize(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", settled_units=25, now=OCT
        )
        second = await ent.finalize(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", settled_units=25, now=OCT
        )
    assert first.id == second.id
    assert second.settled_units == 25
    # Settled once: consumed is 25, not 50, and the hold is gone.
    assert await _available(file_db) == 75
    settlements = await _ledger(file_db, "settlement")
    assert len(settlements) == 1


async def test_release_twice_releases_once(file_db):
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", units=40, now=OCT
        )
        first = await ent.release(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", now=OCT
        )
        second = await ent.release(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", now=OCT
        )
    assert first.id == second.id
    assert second.status == "released"
    assert await _available(file_db) == 100
    releases = await _ledger(file_db, "release")
    assert len(releases) == 1


async def test_failed_request_before_dispatch_releases_and_never_settles(file_db):
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        reserved = await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="attempt", units=30, now=OCT
        )
        released = await ent.release(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="attempt", now=OCT
        )
    assert released.status == "released"
    assert reserved.id == released.id
    # The hold is returned exactly once and no settlement exists.
    assert await _available(file_db) == 100
    assert await _ledger(file_db, "settlement") == []
    # A released reservation can no longer settle.
    async with file_db() as db:
        with pytest.raises(ReservationStateError):
            await ent.finalize(
                db, tenant_id=LEGACY_TENANT_ID, idempotency_key="attempt",
                settled_units=30, now=OCT,
            )


# ---------------------------------------------------------------------------
# Fixed precision: integer minor units everywhere, no float
# ---------------------------------------------------------------------------


async def test_no_float_in_balances_reservations_or_thresholds(file_db):
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        reservation = await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", units=40, now=OCT
        )
        assert type(reservation.reserved_units) is int
        await ent.mark_execution_dispatched(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", now=OCT
        )
        settled = await ent.finalize(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", settled_units=25, now=OCT
        )
        assert type(settled.settled_units) is int
        available = await ent.available_units(db, tenant_id=LEGACY_TENANT_ID, now=OCT)
        thresholds = await ent.soft_thresholds(db, tenant_id=LEGACY_TENANT_ID, now=OCT)
    assert type(available) is int and not isinstance(available, bool)
    assert type(thresholds["allowance_units"]) is int
    assert type(thresholds["consumed_units"]) is int
    for row in thresholds["thresholds"]:
        assert type(row["threshold"]) is int
        assert isinstance(row["reached"], bool)

    # A float is refused rather than silently truncated.
    async with file_db() as db:
        with pytest.raises(ValueError):
            await ent.reserve(
                db, tenant_id=LEGACY_TENANT_ID, idempotency_key="float", units=1.5, now=OCT
            )

    # Every persisted amount is an integer column value.
    for entry in await _ledger(file_db):
        assert type(entry.amount_units) is int
        assert type(entry.balance_after_units) is int


async def test_price_schedule_is_versioned_integer_only(file_db):
    async with file_db() as db:
        db.add(
            PriceSchedule(
                schedule_key="default",
                version=1,
                currency_code="USD",
                minor_unit_scale=6,
                rate_units=250,
                unit_basis="per_1000_tokens",
            )
        )
        db.add(
            PriceSchedule(
                schedule_key="default",
                version=2,
                currency_code="USD",
                minor_unit_scale=6,
                rate_units=300,
                unit_basis="per_1000_tokens",
            )
        )
        await db.commit()
    async with file_db() as db:
        rows = (
            await db.execute(
                select(PriceSchedule).order_by(PriceSchedule.version)
            )
        ).scalars().all()
    assert [r.version for r in rows] == [1, 2]
    assert all(type(r.rate_units) is int for r in rows)


# ---------------------------------------------------------------------------
# Soft thresholds at 50/75/90/100 by integer comparison
# ---------------------------------------------------------------------------


async def test_soft_thresholds_are_integer_comparisons(file_db):
    await _configure(file_db, allowance=100)
    await _settle(file_db, key="a", units=50, settled=50, now=OCT)
    assert await _reached(file_db) == {50}

    await _settle(file_db, key="b", units=25, settled=25, now=OCT)
    assert await _reached(file_db) == {50, 75}

    await _settle(file_db, key="c", units=15, settled=15, now=OCT)
    assert await _reached(file_db) == {50, 75, 90}

    await _settle(file_db, key="d", units=10, settled=10, now=OCT)
    assert await _reached(file_db) == {50, 75, 90, 100}


async def _reached(maker, tenant_id=LEGACY_TENANT_ID):
    async with maker() as db:
        report = await ent.soft_thresholds(db, tenant_id=tenant_id, now=OCT)
    return {row["threshold"] for row in report["thresholds"] if row["reached"]}


async def _available(maker, tenant_id=LEGACY_TENANT_ID, now=OCT):
    async with maker() as db:
        return await ent.available_units(db, tenant_id=tenant_id, now=now)


# ---------------------------------------------------------------------------
# Concurrency-safe hard cap
# ---------------------------------------------------------------------------


async def test_hard_cap_concurrency_allows_exactly_one_reservation(file_db):
    await _configure(file_db, allowance=100)

    async def attempt(key):
        async with file_db() as db:
            try:
                reservation = await ent.reserve(
                    db, tenant_id=LEGACY_TENANT_ID, idempotency_key=key, units=60, now=OCT
                )
                return ("reserved", reservation.id)
            except HardCapExceeded:
                return ("hard_cap", None)

    results = await asyncio.gather(attempt("concurrent-a"), attempt("concurrent-b"))
    outcomes = sorted(outcome for outcome, _ in results)
    assert outcomes == ["hard_cap", "reserved"]
    # The guard prevented over-commitment: the period never goes negative.
    assert await _available(file_db) == 40


async def test_hard_cap_guard_is_load_bearing(file_db, monkeypatch):
    """The hard-cap clause is load-bearing.

    With the clause deliberately weakened, both concurrent reservations pass and
    the tenant is over-committed, which is exactly what the guard prevents in
    the test above.
    """
    await _configure(file_db, allowance=100)

    from sqlalchemy import true as sa_true

    monkeypatch.setattr(
        service_module, "_reserve_capacity_clause", lambda table, units: sa_true()
    )

    async def attempt(key):
        async with file_db() as db:
            try:
                await ent.reserve(
                    db, tenant_id=LEGACY_TENANT_ID, idempotency_key=key, units=60, now=OCT
                )
                return "reserved"
            except HardCapExceeded:
                return "hard_cap"

    results = await asyncio.gather(attempt("weakened-a"), attempt("weakened-b"))
    assert results == ["reserved", "reserved"]
    # Over-committed: the mutation is detectable, so the guard is load-bearing.
    assert await _available(file_db) == -20


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_tenant_isolation_of_allowance_and_credits(file_db):
    await _add_tenant(file_db, TENANT_B)
    await _configure(file_db, tenant_id=LEGACY_TENANT_ID, allowance=100, plan_slug="a")
    await _configure(file_db, tenant_id=TENANT_B, allowance=50, plan_slug="b")

    async with file_db() as db:
        # The same idempotency key in two tenants yields two reservations.
        reservation_a = await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="shared", units=100, now=OCT
        )
        reservation_b = await ent.reserve(
            db, tenant_id=TENANT_B, idempotency_key="shared", units=50, now=OCT
        )
    assert reservation_a.id != reservation_b.id

    # Tenant A's full reservation does not touch tenant B's allowance.
    assert await _available(file_db, tenant_id=TENANT_B) == 0
    assert await _available(file_db, tenant_id=LEGACY_TENANT_ID) == 0

    async with file_db() as db:
        summary_a = await ent.entitlement_summary(db, tenant_id=LEGACY_TENANT_ID, now=OCT)
        summary_b = await ent.entitlement_summary(db, tenant_id=TENANT_B, now=OCT)
    assert summary_a["allowance"]["allowance_units"] == 100
    assert summary_b["allowance"]["allowance_units"] == 50
    assert summary_a["plan"]["plan_slug"] == "a"
    assert summary_b["plan"]["plan_slug"] == "b"

    # Tenant B is exhausted by its own reservation, not by A's.
    async with file_db() as db:
        with pytest.raises(HardCapExceeded):
            await ent.reserve(
                db, tenant_id=TENANT_B, idempotency_key="b2", units=1, now=OCT
            )


# ---------------------------------------------------------------------------
# Expiry and crash recovery
# ---------------------------------------------------------------------------


async def test_expired_reservation_is_not_usable_and_releases_the_hold(file_db):
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="exp", units=40,
            ttl_seconds=1, now=OCT,
        )
    assert await _available(file_db) == 60

    later = OCT.replace(minute=30)
    async with file_db() as db:
        released = await ent.expire_reservations(db, tenant_id=LEGACY_TENANT_ID, now=later)
    assert released == 1
    assert await _available(file_db, now=later) == 100
    assert await _ledger(file_db, "expiry") != []
    assert await _ledger(file_db, "settlement") == []

    # An expired reservation cannot settle.
    async with file_db() as db:
        with pytest.raises(ReservationStateError):
            await ent.finalize(
                db, tenant_id=LEGACY_TENANT_ID, idempotency_key="exp",
                settled_units=40, now=later,
            )
    assert await _ledger(file_db, "settlement") == []


async def test_crash_recovery_releases_inflight_without_double_movement(file_db):
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="inflight", units=40,
            ttl_seconds=1, now=OCT,
        )
    later = OCT.replace(minute=30)

    async with file_db() as db:
        first = await ent.recover_inflight(db, tenant_id=LEGACY_TENANT_ID, now=later)
        second = await ent.recover_inflight(db, tenant_id=LEGACY_TENANT_ID, now=later)
    assert first == 1
    assert second == 0  # idempotent: no second movement
    assert await _available(file_db, now=later) == 100
    expiries = await _ledger(file_db, "expiry")
    assert len(expiries) == 1
    assert await _ledger(file_db, "settlement") == []


# ---------------------------------------------------------------------------
# Revoked/suspended tenant while holding a reservation
# ---------------------------------------------------------------------------


async def test_dispatched_then_suspended_still_settles_once(file_db):
    """Post-dispatch settlement does not depend on the tenant's current status.

    This replaces the earlier assertion that an inactive tenant never settles.
    That rule is right before dispatch and wrong after it: once execution was
    dispatched while the tenant was active, the usage exists and refusing to
    settle it would hand produced inference away for free.
    """
    from app.core.entitlements import ReservationStateError

    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="held", units=40, now=OCT
        )
        dispatched = await ent.mark_execution_dispatched(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="held", now=OCT
        )
    assert dispatched.execution_state == "dispatched"

    # Suspend the tenant AFTER dispatch.
    async with file_db() as db:
        tenant = await db.get(Tenant, LEGACY_TENANT_ID)
        tenant.status = "suspended"
        await db.commit()

    # The already-dispatched usage still settles, exactly once.
    async with file_db() as db:
        settled = await ent.finalize(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="held",
            settled_units=40, now=OCT,
        )
        again = await ent.finalize(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="held",
            settled_units=40, now=OCT,
        )
    assert settled.status == "settled" and settled.settled_units == 40
    assert again.id == settled.id
    assert await _available(file_db) == 60, "settled once, not twice"

    # And the reservation cannot be released out from under the settled usage.
    async with file_db() as db:
        with pytest.raises(ReservationStateError):
            await ent.release(
                db, tenant_id=LEGACY_TENANT_ID, idempotency_key="held", now=OCT
            )


async def test_suspended_tenant_cannot_reserve(file_db):
    """Pre-dispatch authority: an inactive tenant must not take a new hold."""
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        tenant = await db.get(Tenant, LEGACY_TENANT_ID)
        tenant.status = "suspended"
        await db.commit()
    async with file_db() as db:
        with pytest.raises(TenantInactive):
            await ent.reserve(
                db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", units=10, now=OCT
            )


async def test_suspended_while_queued_cannot_be_dispatched_and_releases_once(file_db):
    """A tenant suspended while holding a hold cannot reach settlement.

    The M3 half of the queued case: dispatch evidence cannot be recorded for an
    inactive tenant, so a queued request can never become dispatchable and
    therefore can never settle. INF1-B independently refuses to dispatch and
    revalidates before dispatch.
    """
    from app.core.entitlements import ReservationStateError

    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="queued", units=30, now=OCT
        )
    async with file_db() as db:
        tenant = await db.get(Tenant, LEGACY_TENANT_ID)
        tenant.status = "suspended"
        await db.commit()

    async with file_db() as db:
        with pytest.raises(TenantInactive):
            await ent.mark_execution_dispatched(
                db, tenant_id=LEGACY_TENANT_ID, idempotency_key="queued", now=OCT
            )
        with pytest.raises(TenantInactive):
            await ent.mark_execution_uncertain(
                db, tenant_id=LEGACY_TENANT_ID, idempotency_key="queued", now=OCT
            )
        await db.rollback()

    # The undispatched hold is released exactly once, restoring the allowance.
    async with file_db() as db:
        first = await ent.release(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="queued", now=OCT
        )
        second = await ent.release(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="queued", now=OCT
        )
    assert first.status == "released" and second.id == first.id
    assert await _available(file_db) == 100
    async with file_db() as db:
        with pytest.raises(ReservationStateError):
            await ent.finalize(
                db, tenant_id=LEGACY_TENANT_ID, idempotency_key="queued",
                settled_units=30, now=OCT,
            )


async def test_uncertain_post_dispatch_consumption_is_never_settled_as_zero(file_db):
    """Uncertain consumption is conservative and explicit, never silently zero."""
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="unc", units=40, now=OCT
        )
        marked = await ent.mark_execution_uncertain(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="unc", now=OCT
        )
    assert marked.execution_state == "uncertain"

    # A caller reporting zero for an uncertain dispatch does not get it free.
    async with file_db() as db:
        settled = await ent.finalize(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="unc",
            settled_units=0, now=OCT,
        )
    assert settled.settled_units == 40, "uncertain must settle the reserved worst case"
    assert await _available(file_db) == 60


async def test_caller_cannot_settle_an_undispatched_reservation(file_db):
    """No caller claim can substitute for recorded dispatch evidence."""
    from app.core.entitlements import ReservationStateError

    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="never", units=10, now=OCT
        )
    async with file_db() as db:
        with pytest.raises(ReservationStateError):
            await ent.finalize(
                db, tenant_id=LEGACY_TENANT_ID, idempotency_key="never",
                settled_units=10, now=OCT,
            )
        await db.rollback()
    # The hold is untouched and still releasable.
    assert await _available(file_db) == 90
    async with file_db() as db:
        released = await ent.release(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="never", now=OCT
        )
    assert released.status == "released"


async def test_dispatched_reservation_cannot_be_released(file_db):
    """Release is a pre-dispatch operation; releasing after dispatch would be free usage."""
    from app.core.entitlements import ReservationStateError

    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="d1", units=20, now=OCT
        )
        await ent.mark_execution_dispatched(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="d1", now=OCT
        )
    async with file_db() as db:
        with pytest.raises(ReservationStateError):
            await ent.release(
                db, tenant_id=LEGACY_TENANT_ID, idempotency_key="d1", now=OCT
            )
        await db.rollback()


async def test_expiry_does_not_free_a_dispatched_hold(file_db):
    """A dispatched hold represents produced usage, so the expiry sweep skips it."""
    from datetime import timedelta

    now = OCT
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="late", units=25,
            ttl_seconds=1, now=now,
        )
        await ent.mark_execution_dispatched(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="late", now=now
        )
    later = now + timedelta(minutes=30)
    async with file_db() as db:
        expired = await ent.expire_reservations(db, tenant_id=LEGACY_TENANT_ID, now=later)
    assert expired == 0, "a dispatched hold is not expired away"
    async with file_db() as db:
        settled = await ent.finalize(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="late",
            settled_units=25, now=later,
        )
    assert settled.status == "settled"

# ---------------------------------------------------------------------------
# Billing exhaustion: blocks new execution, not history/admin/recovery
# ---------------------------------------------------------------------------


async def test_billing_exhaustion_blocks_execution_but_not_history_or_recovery(
    file_db, client
):
    from datetime import timedelta

    now = ent.utcnow()
    later = now + timedelta(minutes=30)
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        # An in-flight hold that will be recovered later.
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="inflight", units=30,
            ttl_seconds=1, now=now,
        )
        # Consume the rest, settling fully.
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="full", units=70, now=now
        )
        await ent.mark_execution_dispatched(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="full", now=now
        )
        await ent.finalize(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="full",
            settled_units=70, now=now,
        )

    # New billable execution is refused.
    async with file_db() as db:
        assert await ent.can_start_execution(db, tenant_id=LEGACY_TENANT_ID, now=now) is False
        with pytest.raises(HardCapExceeded):
            await ent.reserve(
                db, tenant_id=LEGACY_TENANT_ID, idempotency_key="new", units=1, now=now
            )

    # Reading history is not blocked.
    history = await client.get("/api/admin/usage")
    assert history.status_code == 200, history.text

    # Tenant administration is not blocked.
    admin = await client.get("/api/admin/entitlements")
    assert admin.status_code == 200, admin.text
    body = admin.json()
    assert body["allowance"]["consumed_units"] == 70
    assert body["allowance"]["available_units"] == 0

    # Recovery is not blocked: the expired in-flight hold is released.
    async with file_db() as db:
        released = await ent.recover_inflight(db, tenant_id=LEGACY_TENANT_ID, now=later)
    assert released == 1
    assert await _available(file_db, now=later) == 30
    # History is still readable after recovery.
    assert (await client.get("/api/admin/usage")).status_code == 200


# ---------------------------------------------------------------------------
# Append-only ledger: service guard and database trigger
# ---------------------------------------------------------------------------


async def test_ledger_row_cannot_be_updated_through_the_service(file_db):
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", units=10, now=OCT
        )
    async with file_db() as db:
        entry = (await db.execute(select(CreditLedgerEntry))).scalars().first()
        entry.amount_units = 999
        with pytest.raises(LedgerImmutabilityError):
            await db.flush()


def test_migration_creates_ledger_immutability_trigger(tmp_path):
    from app.migrations_runner import (
        _alembic_config,
        adopt_and_upgrade,
        current_revision,
        sync_url_for,
    )
    from alembic import command

    url = f"sqlite+aiosqlite:///{tmp_path / 'm3_trigger.db'}"
    adopt_and_upgrade(url)
    sync_url = sync_url_for(url)
    engine = create_engine(sync_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO credit_ledger_entries "
                    "(id, tenant_id, billing_period_id, entry_type, amount_units, "
                    "balance_after_units, idempotency_key, created_at) "
                    "VALUES ('l1','t','p','grant',10,10,'k1',CURRENT_TIMESTAMP)"
                )
            )
        with pytest.raises(Exception):
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE credit_ledger_entries SET amount_units = 99 WHERE id = 'l1'")
                )
        with pytest.raises(Exception):
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM credit_ledger_entries WHERE id = 'l1'"))
        # The row survives both refused mutations.
        with engine.begin() as conn:
            stored = conn.execute(
                text("SELECT amount_units FROM credit_ledger_entries WHERE id = 'l1'")
            ).scalar_one()
        assert stored == 10
    finally:
        engine.dispose()


def test_migration_upgrades_and_downgrades_cleanly(tmp_path):
    from app.migrations_runner import (
        _alembic_config,
        adopt_and_upgrade,
        current_revision,
        sync_url_for,
    )
    from alembic import command

    url = f"sqlite+aiosqlite:///{tmp_path / 'm3_migration.db'}"
    adopt_and_upgrade(url)
    assert current_revision(url) == "0016_m3_entitlements"

    sync_url = sync_url_for(url)
    engine = create_engine(sync_url, future=True)
    try:
        inspector = inspect(engine)
        for table in (
            "entitlement_plans",
            "entitlement_billing_periods",
            "credit_ledger_entries",
            "usage_reservations",
        ):
            assert inspector.has_table(table), table

        config = _alembic_config(sync_url)
        command.downgrade(config, "0015_inf1_inference_registry")
        assert current_revision(url) == "0015_inf1_inference_registry"
        inspector = inspect(engine)
        for table in (
            "entitlement_plans",
            "entitlement_billing_periods",
            "credit_ledger_entries",
            "usage_reservations",
        ):
            assert not inspector.has_table(table), table

        command.upgrade(config, "0016_m3_entitlements")
        assert current_revision(url) == "0016_m3_entitlements"
        assert inspect(engine).has_table("credit_ledger_entries")
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Plan and allowance versioning
# ---------------------------------------------------------------------------


async def test_plan_and_allowance_versioning_keeps_period_version(file_db):
    await _configure(file_db, allowance=100)
    # Open an October period pinned to allowance version 1.
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="oct", units=10, now=OCT
        )
        await ent.release(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="oct", now=OCT
        )
        october = await ent.entitlement_summary(db, tenant_id=LEGACY_TENANT_ID, now=OCT)
    assert october["allowance"]["allowance_version"] == 1
    assert october["allowance"]["allowance_units"] == 100

    # Change the terms: a new plan version and a new allowance version.
    async with file_db() as db:
        plan = await ent.record_plan(db, slug="standard", name="Standard")
        plan_version_2 = await ent.add_plan_version(db, plan=plan, allowance_units=150)
        await ent.grant_allowance(
            db, tenant_id=LEGACY_TENANT_ID, allowance_units=150,
            plan_version_id=plan_version_2.id,
        )
        await ent.set_subscription(
            db, tenant_id=LEGACY_TENANT_ID, plan_version_id=plan_version_2.id
        )
        await db.commit()

    # The existing October period keeps the version it started with.
    async with file_db() as db:
        october_again = await ent.entitlement_summary(
            db, tenant_id=LEGACY_TENANT_ID, now=OCT
        )
        assert october_again["allowance"]["allowance_version"] == 1
        assert october_again["allowance"]["allowance_units"] == 100

        # A freshly opened period uses the new allowance version.
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="nov", units=10, now=NOV
        )
        november = await ent.entitlement_summary(db, tenant_id=LEGACY_TENANT_ID, now=NOV)
    assert november["allowance"]["allowance_version"] == 2
    assert november["allowance"]["allowance_units"] == 150


# ---------------------------------------------------------------------------
# Tenant-scoped client administration
# ---------------------------------------------------------------------------


async def test_client_admin_entitlements_are_tenant_scoped(file_db, client):
    await _configure(file_db, allowance=100)
    # Open the current period so the read surface has allowance and thresholds.
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="open", units=10
        )
    response = await client.get("/api/admin/entitlements")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant_id"] == LEGACY_TENANT_ID
    assert body["plan"]["plan_slug"] == "standard"

    usage = await client.get("/api/admin/usage")
    assert usage.status_code == 200, usage.text
    payload = usage.json()
    assert payload["plan"] is not None
    assert payload["entitlements"]["tenant_id"] == LEGACY_TENANT_ID
    assert len(payload["entitlements"]["soft_thresholds"]) == len(SOFT_THRESHOLDS)


# ---------------------------------------------------------------------------
# Explicit platform metadata boundary
# ---------------------------------------------------------------------------


async def test_platform_entitlement_metadata_requires_platform_capability(client, file_db):
    # A tenant owner holds no platform capability and is refused.
    assert (await client.get("/api/platform/entitlements/metadata")).status_code == 403

    async with file_db() as db:
        await governance.grant_platform_operator(
            db, principal_id=LEGACY_PRINCIPAL_ID,
            capabilities=[PlatformCapability.METADATA_READ],
        )
        await db.commit()

    response = await client.get("/api/platform/entitlements/metadata")
    assert response.status_code == 200, response.text
    body = response.json()
    # Aggregate metadata only: no tenant identifier and no customer content.
    assert "tenant_id" not in body
    assert "subscriptions_by_status" in body
    assert "credit_ledger_entries" in body


async def test_platform_entitlement_metadata_is_aggregate_only(file_db):
    await _configure(file_db, allowance=100)
    async with file_db() as db:
        await ent.reserve(
            db, tenant_id=LEGACY_TENANT_ID, idempotency_key="k1", units=10, now=OCT
        )
        await db.commit()
    async with file_db() as db:
        metadata = await ent.platform_entitlement_metadata(db)
    assert type(metadata["credit_ledger_entries"]) is int
    assert metadata["subscriptions_by_status"].get("active") == 1
    assert "tenant_id" not in metadata
