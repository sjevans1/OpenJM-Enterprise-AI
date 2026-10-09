"""E3 recovery matrix: backup/restore preserves commercial (M3) and capacity
(INF1-B) state and cannot duplicate M1/M2 accounting or attribution.

Each invariant is exercised end to end against the real ``app.ops.backup``
surface. A migrated application-metadata database is seeded through the real
services, snapshotted with :func:`create_backup`, restored into an isolated
target with :func:`restore_backup`, and the restored database is then asserted
directly. Nothing here re-implements backup/restore: the point is to prove the
shipped path preserves exactly the state that governs money and capacity.

Invariants proved here
----------------------

* **unsettled M3 reservations survive restore** — an undispatched hold is still
  present and settles exactly once; a dispatching/uncertain hold stays
  uncertain and never returns to ``undispatched``;
* **INF1 capacity/admission state recovers conservatively** — a stale
  dispatching lease recovers as ``uncertain`` and is never rendered as free
  capacity that double-books a still-possibly-running attempt;
* **M1/M2 totals are stable** — row counts and token aggregates are byte for
  byte identical across restore;
* **no double-settle, no duplicate attribution** — one ledger row and one
  sidecar row per invocation, however often a caller replays;
* **expired/released/uncertain states remain explicit** after restore.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.admission import AdmissionRequest, CapacityLimits
from app.core.config import Settings
from app.core.entitlements import ReservationStateError
from app.core.inference import RoutingDecision, normalize_usage
from app.core.tenancy import LEGACY_TENANT_ID
from app.db import enable_sqlite_foreign_keys
from app.migrations_runner import adopt_and_upgrade
from app.models import (
    AdmissionTicket,
    BillingPeriod,
    CapacityLease,
    CapacityScope,
    CreditLedgerEntry,
    InferenceUsageAttribution,
    ModelUsageEvent,
    Tenant,
    UsageReservation,
)
from app.ops.backup import create_backup, restore_backup, verify_backup
from app.services import entitlements as ent
from app.services import usage_aggregation as ua
from app.services import usage_metering as um
from app.services.inference import admission as adm
from app.services.inference.attribution import ApprovalContext, attribute_usage_event

TENANT = LEGACY_TENANT_ID
OCT = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
LATER = OCT + timedelta(minutes=30)


# ---------------------------------------------------------------------------
# Recovery harness: seed -> backup -> restore -> assert
# ---------------------------------------------------------------------------


def _pragmas(dbapi_connection, record) -> None:
    enable_sqlite_foreign_keys(dbapi_connection, record)
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _engine(url: str):
    engine = create_async_engine(url, connect_args={"timeout": 10})
    event.listen(engine.sync_engine, "connect", _pragmas)
    return engine


@dataclass
class Recovery:
    """The two ends of one backup/restore round trip."""

    cfg: Settings
    backup: Path
    source: async_sessionmaker
    restored: async_sessionmaker
    target_db: Path


@asynccontextmanager
async def _recovery_roundtrip(tmp_path, seed):
    """Seed a migrated database, back it up, restore it, yield both ends.

    ``seed`` receives the source ``async_sessionmaker`` so it can drive the real
    services exactly as production would.
    """
    src = tmp_path / "src"
    (src / "uploads").mkdir(parents=True)
    cfg = Settings(
        deployment_profile="development",
        database_url=f"sqlite+aiosqlite:///{src / 'openjm.db'}",
        upload_dir=src / "uploads",
        vector_path=src / "vector",
        credential_key_file=src / "credentials.key",
        backup_dir=tmp_path / "backups",
    )
    adopt_and_upgrade(cfg.database_url)

    source_engine = _engine(cfg.database_url)
    source = async_sessionmaker(source_engine, expire_on_commit=False)
    await seed(source)
    await source_engine.dispose()

    backup = create_backup(settings=cfg)
    verification = verify_backup(backup)
    assert verification["ok"], verification["problems"]

    target = tmp_path / "restored"
    target_db = target / "openjm.db"
    target_db_url = f"sqlite+aiosqlite:///{target_db}"
    restore_backup(
        backup,
        target_database_url=target_db_url,
        target_data_dir=target / "uploads",
    )

    restored_engine = _engine(target_db_url)
    restored = async_sessionmaker(restored_engine, expire_on_commit=False)
    try:
        yield Recovery(cfg, backup, source, restored, target_db)
    finally:
        await restored_engine.dispose()


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _ensure_tenant(maker, tenant_id: str = TENANT) -> None:
    """The VS5 identity migration already provisions the local tenant."""
    async with maker() as db:
        tenant = await db.get(Tenant, tenant_id)
        if tenant is None:
            db.add(Tenant(id=tenant_id, slug=tenant_id, name=tenant_id, status="active"))
        else:
            tenant.status = "active"
        await db.commit()


async def _seed_tenant(maker, *, allowance: int = 100, tenant_id: str = TENANT) -> None:
    await _ensure_tenant(maker, tenant_id)
    async with maker() as db:
        await ent.configure_tenant(
            db, tenant_id=tenant_id, allowance_units=allowance, plan_slug="standard"
        )
        await db.commit()


async def _reservation(maker, key: str, *, tenant_id: str = TENANT):
    async with maker() as db:
        return (
            await db.execute(
                select(UsageReservation).where(
                    UsageReservation.tenant_id == tenant_id,
                    UsageReservation.idempotency_key == key,
                )
            )
        ).scalar_one()


async def _count(maker, model) -> int:
    async with maker() as db:
        return int(
            (await db.execute(select(func.count()).select_from(model))).scalar_one()
        )


def _limits(**overrides) -> CapacityLimits:
    payload = dict(
        tenant_limit=2,
        deployment_limit=10,
        pool_limit=10,
        max_queue_depth=32,
        max_wait_ms=5_000,
        lease_ttl_seconds=60,
    )
    payload.update(overrides)
    return CapacityLimits(**payload)


def _req(tenant_id: str, attempt_id: str, **overrides) -> AdmissionRequest:
    payload = dict(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
        deployment_id="dep-a",
        inference_mode="customer_local",
        reservation_handle="res-opaque-xyz",
        reservation_state="present",
        pool_id=None,
        deadline_at=None,
        client_cache_namespace=None,
    )
    payload.update(overrides)
    return AdmissionRequest(**payload)


def _decision(tenant_id: str, attempt_id: str) -> RoutingDecision:
    return RoutingDecision(
        decision_id=f"dec-{attempt_id}",
        attempt_id=attempt_id,
        tenant_id=tenant_id,
        reason="allowed",
        decided_at=OCT,
        deployment_id="dep-a",
        deployment_revision=1,
        model_release_id="rel-a",
        runtime_profile_id="rt-a",
    )


# ---------------------------------------------------------------------------
# Invariant 1: unsettled M3 reservations survive backup/restore
# ---------------------------------------------------------------------------


async def test_undispatched_hold_survives_and_settles_exactly_once(tmp_path) -> None:
    """An undispatched hold is present after restore and settles once, not twice."""

    async def seed(maker):
        await _seed_tenant(maker, allowance=100)
        async with maker() as db:
            await ent.reserve(
                db, tenant_id=TENANT, idempotency_key="hold-a", units=40, now=OCT
            )

    async with _recovery_roundtrip(tmp_path, seed) as rec:
        # The hold survived the round trip, exactly as it was left.
        row = await _reservation(rec.restored, "hold-a")
        assert row.status == "reserved"
        assert row.execution_state == "undispatched"
        assert row.reserved_units == 40

        async with rec.restored() as db:
            assert await ent.available_units(db, tenant_id=TENANT, now=OCT) == 60

        # The restored hold can still be driven across the boundary and settled.
        async with rec.restored() as db:
            await ent.begin_dispatch(
                db, tenant_id=TENANT, idempotency_key="hold-a", now=OCT
            )
            await ent.mark_execution_dispatched(
                db, tenant_id=TENANT, idempotency_key="hold-a", now=OCT
            )
            settled = await ent.finalize(
                db, tenant_id=TENANT, idempotency_key="hold-a", settled_units=40, now=OCT
            )
            assert settled.status == "settled"
            assert settled.settled_units == 40
            settled_id = settled.id

        # Replaying settlement after restore is a no-op: one consumption, not two.
        async with rec.restored() as db:
            again = await ent.finalize(
                db, tenant_id=TENANT, idempotency_key="hold-a", settled_units=40, now=OCT
            )
            assert again.id == settled_id
            assert again.settled_units == 40
            assert await ent.available_units(db, tenant_id=TENANT, now=OCT) == 60
            settlements = (
                await db.execute(
                    select(func.count())
                    .select_from(CreditLedgerEntry)
                    .where(CreditLedgerEntry.entry_type == "settlement")
                )
            ).scalar_one()
            assert int(settlements) == 1


async def test_undispatched_hold_cannot_settle_without_a_recorded_dispatch(tmp_path) -> None:
    """Restore does not weaken the dispatch boundary: a bare hold cannot settle."""

    async def seed(maker):
        await _seed_tenant(maker, allowance=100)
        async with maker() as db:
            await ent.reserve(
                db, tenant_id=TENANT, idempotency_key="bare", units=20, now=OCT
            )

    async with _recovery_roundtrip(tmp_path, seed) as rec:
        async with rec.restored() as db:
            with pytest.raises(ReservationStateError):
                await ent.finalize(
                    db, tenant_id=TENANT, idempotency_key="bare", settled_units=20, now=OCT
                )
            await db.rollback()
        # And the surviving hold is still releasable, returning it exactly once.
        async with rec.restored() as db:
            released = await ent.release(
                db, tenant_id=TENANT, idempotency_key="bare", now=OCT
            )
            assert released.status == "released"
            assert await ent.available_units(db, tenant_id=TENANT, now=OCT) == 100


async def test_dispatching_hold_recovers_as_uncertain_never_undispatched(tmp_path) -> None:
    """A hold that crossed the boundary recovers conservatively after restore."""

    async def seed(maker):
        await _seed_tenant(maker, allowance=100)
        async with maker() as db:
            await ent.reserve(
                db, tenant_id=TENANT, idempotency_key="mid", units=30, now=OCT
            )
            await ent.begin_dispatch(
                db, tenant_id=TENANT, idempotency_key="mid", now=OCT
            )

    async with _recovery_roundtrip(tmp_path, seed) as rec:
        # Dispatch intent survived restore: the hold is still held, still dispatching.
        row = await _reservation(rec.restored, "mid")
        assert row.status == "reserved"
        assert row.execution_state == "dispatching"
        async with rec.restored() as db:
            assert await ent.available_units(db, tenant_id=TENANT, now=OCT) == 70

        # It cannot be released or expired as though nothing happened.
        async with rec.restored() as db:
            with pytest.raises(ReservationStateError):
                await ent.release(
                    db, tenant_id=TENANT, idempotency_key="mid", now=LATER
                )
            await db.rollback()
        async with rec.restored() as db:
            expired = await ent.expire_reservations(db, tenant_id=TENANT, now=LATER)
        assert expired == 0, "the expiry sweep must not free a dispatching hold"

        # Crash recovery promotes it to uncertain and never back to undispatched.
        async with rec.restored() as db:
            recovered = await ent.recover_inflight(db, tenant_id=TENANT, now=LATER)
            assert recovered == 1
        row = await _reservation(rec.restored, "mid")
        assert row.execution_state == "uncertain"
        assert row.execution_state != "undispatched"
        assert row.status == "reserved", "the worst case stays chargeable"

        # Conservative settlement of the reserved worst case, exactly once.
        async with rec.restored() as db:
            settled = await ent.finalize(
                db, tenant_id=TENANT, idempotency_key="mid", settled_units=0, now=LATER
            )
            assert settled.settled_units == 30
            assert await ent.available_units(db, tenant_id=TENANT, now=LATER) == 70


async def test_uncertain_hold_stays_uncertain_across_restore(tmp_path) -> None:
    """An explicitly uncertain hold is never silently downgraded by restore."""

    async def seed(maker):
        await _seed_tenant(maker, allowance=100)
        async with maker() as db:
            await ent.reserve(
                db, tenant_id=TENANT, idempotency_key="unc", units=25, now=OCT
            )
            await ent.begin_dispatch(
                db, tenant_id=TENANT, idempotency_key="unc", now=OCT
            )
            await ent.mark_execution_uncertain(
                db, tenant_id=TENANT, idempotency_key="unc", now=OCT
            )

    async with _recovery_roundtrip(tmp_path, seed) as rec:
        row = await _reservation(rec.restored, "unc")
        assert row.execution_state == "uncertain"
        assert row.status == "reserved"
        # A caller reporting zero does not get produced inference for free.
        async with rec.restored() as db:
            settled = await ent.finalize(
                db, tenant_id=TENANT, idempotency_key="unc", settled_units=0, now=OCT
            )
            assert settled.settled_units == 25
            assert await ent.available_units(db, tenant_id=TENANT, now=OCT) == 75


# ---------------------------------------------------------------------------
# Invariant 2: INF1 capacity/admission recovers conservatively
# ---------------------------------------------------------------------------


async def test_inf1_stale_dispatching_lease_recovers_uncertain_not_free(tmp_path) -> None:
    """A stale dispatching lease is never rendered as free, double-bookable capacity."""
    limits = _limits(tenant_limit=1)
    captured: dict = {}

    async def seed(maker):
        await _ensure_tenant(maker)
        async with maker() as db:
            admitted = await adm.admit(db, _req(TENANT, "a1"), limits, now=OCT)
            await db.commit()
        assert admitted.admitted
        captured["lease_id"] = admitted.lease_id
        # Cross the INF1 dispatch boundary: certainty becomes unknown.
        async with maker() as db:
            marked = await adm.mark_dispatched(
                db, lease_token=admitted.lease_token, fence=admitted.fence, now=OCT
            )
            await db.commit()
        assert marked is True

    async with _recovery_roundtrip(tmp_path, seed) as rec:
        # Restore did not free the still-possibly-running attempt's slot.
        async with rec.restored() as db:
            assert await adm.in_flight(db, adm.tenant_scope_key(TENANT)) == 1
            lease = await db.get(adm.CapacityLease, captured["lease_id"])
            assert lease.state == "active"
            assert lease.execution_certainty == "unknown"

        # A fresh request cannot take the slot the restored system still holds.
        async with rec.restored() as db:
            second = await adm.admit(db, _req(TENANT, "a2"), limits, now=OCT)
            await db.commit()
        assert second.queued and not second.admitted

        # Past the TTL the stale lease is reclaimed, but as explicitly uncertain:
        # the slot is bounded, the consumption is never invented as zero.
        async with rec.restored() as db:
            reclaimed = await adm.expire_stale_leases(db, now=LATER)
            await db.commit()
        assert reclaimed == 1
        async with rec.restored() as db:
            lease = await db.get(adm.CapacityLease, captured["lease_id"])
            assert lease.state == "uncertain"
            assert lease.execution_certainty == "unknown"
            assert await adm.uncertain_consumption(db) == 1
            assert await adm.in_flight(db, adm.tenant_scope_key(TENANT)) == 0


async def test_inf1_restored_admission_state_is_not_reset(tmp_path) -> None:
    """The committed slot/queue state is preserved, not reset to an empty queue."""
    limits = _limits(tenant_limit=1)

    async def seed(maker):
        await _ensure_tenant(maker)
        async with maker() as db:
            await adm.admit(db, _req(TENANT, "held"), limits, now=OCT)
            queued = await adm.admit(db, _req(TENANT, "waiting"), limits, now=OCT)
            await db.commit()
        assert queued.queued

    async with _recovery_roundtrip(tmp_path, seed) as rec:
        async with rec.restored() as db:
            assert await adm.in_flight(db, adm.tenant_scope_key(TENANT)) == 1
            queued_rows = await adm.queued_tickets(db, tenant_id=TENANT)
            assert [t.attempt_id for t in queued_rows] == ["waiting"]
            # A replay of the admitted attempt resolves to the same ticket, not a
            # second slot: restore cannot duplicate a booked slot.
            replay = await adm.admit(db, _req(TENANT, "held"), limits, now=OCT)
            await db.commit()
            assert replay.admitted
        async with rec.restored() as db:
            assert await adm.in_flight(db, adm.tenant_scope_key(TENANT)) == 1


# ---------------------------------------------------------------------------
# Invariant 3: M1/M2 totals are stable across backup/restore
# ---------------------------------------------------------------------------


async def test_m1_m2_totals_are_identical_across_backup_restore(tmp_path) -> None:
    captured: dict = {}

    async def seed(maker):
        async with maker() as db:
            for index in range(3):
                await um.record_model_usage(
                    db,
                    context=um.UsageContext(
                        tenant_id=TENANT,
                        request_id=f"req-{index}",
                        provider_route="local",
                        model_name="model-x",
                    ),
                    usage=um.ModelCallUsage(
                        input_tokens=10 + index,
                        output_tokens=5,
                        provider_reported=(index % 2 == 0),
                    ),
                )
            await db.commit()

        # Attribute two of the three events so the M1/M2 split is exercised too.
        async with maker() as db:
            events = (
                await db.execute(
                    select(ModelUsageEvent).order_by(ModelUsageEvent.request_id)
                )
            ).scalars().all()
            for event in events[:2]:
                attempt = f"att-{event.request_id}"
                await attribute_usage_event(
                    db,
                    usage_event=event,
                    context=ApprovalContext(
                        business_request_id="biz-1",
                        logical_call_id="call-1",
                        attempt_id=attempt,
                    ),
                    decision=_decision(TENANT, attempt),
                    usage=normalize_usage(
                        input_tokens=event.input_tokens,
                        output_tokens=event.output_tokens,
                        usage_source="provider_reported",
                        count_method="runtime_reported",
                    ),
                )
            await db.commit()

        captured["events"] = await _count(maker, ModelUsageEvent)
        captured["attributions"] = await _count(maker, InferenceUsageAttribution)
        async with maker() as db:
            captured["agg"] = await ua.aggregate_usage(db, tenant_id=TENANT, period="total")

    async with _recovery_roundtrip(tmp_path, seed) as rec:
        # Raw row counts are unchanged.
        assert await _count(rec.restored, ModelUsageEvent) == captured["events"] == 3
        assert (
            await _count(rec.restored, InferenceUsageAttribution)
            == captured["attributions"]
            == 2
        )

        async with rec.restored() as db:
            restored_agg = await ua.aggregate_usage(db, tenant_id=TENANT, period="total")

        source_agg = captured["agg"]
        assert restored_agg["totals"] == source_agg["totals"]
        assert restored_agg["buckets"] == source_agg["buckets"]
        assert restored_agg["coverage"]["attributed_events"] == 2
        assert restored_agg["coverage"]["legacy_unknown_events"] == 1
        assert restored_agg["totals"]["attempts"] == 3


# ---------------------------------------------------------------------------
# Invariant 4: no double-settle, no duplicate attribution
# ---------------------------------------------------------------------------


async def test_restored_system_does_not_double_settle(tmp_path) -> None:
    captured: dict = {}

    async def seed(maker):
        await _seed_tenant(maker, allowance=100)
        async with maker() as db:
            await ent.reserve(
                db, tenant_id=TENANT, idempotency_key="done", units=50, now=OCT
            )
            await ent.begin_dispatch(
                db, tenant_id=TENANT, idempotency_key="done", now=OCT
            )
            await ent.mark_execution_dispatched(
                db, tenant_id=TENANT, idempotency_key="done", now=OCT
            )
            settled = await ent.finalize(
                db, tenant_id=TENANT, idempotency_key="done", settled_units=50, now=OCT
            )
            captured["settled_id"] = settled.id

    async with _recovery_roundtrip(tmp_path, seed) as rec:
        # Replay settlement: same row, no second movement of money.
        async with rec.restored() as db:
            again = await ent.finalize(
                db, tenant_id=TENANT, idempotency_key="done", settled_units=50, now=OCT
            )
            assert again.id == captured["settled_id"]
            assert again.settled_units == 50
            # Consumed and available are unchanged by the replay.
            assert await ent.available_units(db, tenant_id=TENANT, now=OCT) == 50
            by_type = (
                await db.execute(
                    select(CreditLedgerEntry.entry_type, func.count())
                    .group_by(CreditLedgerEntry.entry_type)
                )
            ).all()
        ledger = {row[0]: int(row[1]) for row in by_type}
        assert ledger.get("settlement") == 1
        assert ledger.get("hold") == 1
        assert ledger.get("release", 0) == 0
        assert ledger.get("expiry", 0) == 0

        # The append-only guarantee survives restore: a raw UPDATE is refused by
        # the database trigger, so history cannot be rewritten after recovery.
        import sqlite3

        with sqlite3.connect(str(rec.target_db)) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE credit_ledger_entries SET amount_units = 999"
                )


async def test_restored_system_does_not_duplicate_usage_or_attribution(tmp_path) -> None:
    captured: dict = {}

    async def seed(maker):
        async with maker() as db:
            event = await um.record_model_usage(
                db,
                context=um.UsageContext(
                    tenant_id=TENANT,
                    request_id="req-dup",
                    provider_route="local",
                    model_name="model-x",
                ),
                usage=um.ModelCallUsage(input_tokens=7, output_tokens=3, provider_reported=True),
            )
            await db.commit()
            captured["event_id"] = event.id
        async with maker() as db:
            event = (
                await db.execute(
                    select(ModelUsageEvent).where(ModelUsageEvent.request_id == "req-dup")
                )
            ).scalar_one()
            row = await attribute_usage_event(
                db,
                usage_event=event,
                context=ApprovalContext(
                    business_request_id="biz-1",
                    logical_call_id="call-1",
                    attempt_id="att-dup",
                ),
                decision=_decision(TENANT, "att-dup"),
                usage=normalize_usage(
                    input_tokens=7,
                    output_tokens=3,
                    usage_source="provider_reported",
                    count_method="runtime_reported",
                ),
            )
            await db.commit()
            captured["attribution_id"] = row.id

    async with _recovery_roundtrip(tmp_path, seed) as rec:
        # Replay the M1 finalization: same row, no second invocation.
        async with rec.restored() as db:
            replayed = await um.record_model_usage(
                db,
                context=um.UsageContext(
                    tenant_id=TENANT,
                    request_id="req-dup",
                    provider_route="local",
                    model_name="model-x",
                ),
                usage=um.ModelCallUsage(input_tokens=7, output_tokens=3, provider_reported=True),
            )
            assert replayed.id == captured["event_id"]
            assert await _count_session(db, ModelUsageEvent) == 1

            event = (
                await db.execute(
                    select(ModelUsageEvent).where(ModelUsageEvent.request_id == "req-dup")
                )
            ).scalar_one()
            sidecar = await attribute_usage_event(
                db,
                usage_event=event,
                context=ApprovalContext(
                    business_request_id="biz-1",
                    logical_call_id="call-1",
                    attempt_id="att-dup",
                ),
                decision=_decision(TENANT, "att-dup"),
                usage=normalize_usage(
                    input_tokens=7,
                    output_tokens=3,
                    usage_source="provider_reported",
                    count_method="runtime_reported",
                ),
            )
            assert sidecar.id == captured["attribution_id"]
            assert await _count_session(db, InferenceUsageAttribution) == 1


async def _count_session(db, model) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


# ---------------------------------------------------------------------------
# Invariant 5: expired/released/uncertain states remain explicit after restore
# ---------------------------------------------------------------------------


async def test_expired_released_and_uncertain_states_survive_restore(tmp_path) -> None:
    captured: dict = {}

    async def seed(maker):
        await _seed_tenant(maker, allowance=100)
        async with maker() as db:
            await ent.reserve(
                db, tenant_id=TENANT, idempotency_key="exp", units=10,
                ttl_seconds=1, now=OCT,
            )
        async with maker() as db:
            await ent.reserve(
                db, tenant_id=TENANT, idempotency_key="rel", units=20, now=OCT
            )
            await ent.release(
                db, tenant_id=TENANT, idempotency_key="rel", now=OCT
            )
        async with maker() as db:
            await ent.reserve(
                db, tenant_id=TENANT, idempotency_key="unc", units=25, now=OCT
            )
            await ent.begin_dispatch(
                db, tenant_id=TENANT, idempotency_key="unc", now=OCT
            )
            await ent.mark_execution_uncertain(
                db, tenant_id=TENANT, idempotency_key="unc", now=OCT
            )
        async with maker() as db:
            released = await ent.expire_reservations(
                db, tenant_id=TENANT, now=LATER
            )
            assert released == 1
            captured["expired"] = released

    async with _recovery_roundtrip(tmp_path, seed) as rec:
        expired = await _reservation(rec.restored, "exp")
        released_row = await _reservation(rec.restored, "rel")
        uncertain = await _reservation(rec.restored, "unc")

        assert expired.status == "expired"
        assert expired.execution_state == "undispatched"
        assert released_row.status == "released"
        assert uncertain.status == "reserved"
        assert uncertain.execution_state == "uncertain"

        # The hold left over is exactly the uncertain one, and the ledger records
        # the expiry and the release as explicit, separate movements.
        async with rec.restored() as db:
            assert await ent.available_units(db, tenant_id=TENANT, now=LATER) == 75
            by_type = (
                await db.execute(
                    select(CreditLedgerEntry.entry_type, func.count())
                    .group_by(CreditLedgerEntry.entry_type)
                )
            ).all()
        ledger = {row[0]: int(row[1]) for row in by_type}
        assert ledger.get("expiry") == 1
        assert ledger.get("release") == 1
        assert ledger.get("settlement", 0) == 0

        # A terminal state cannot silently re-open after restore.
        async with rec.restored() as db:
            with pytest.raises(ReservationStateError):
                await ent.finalize(
                    db, tenant_id=TENANT, idempotency_key="exp",
                    settled_units=10, now=LATER,
                )
            await db.rollback()
        async with rec.restored() as db:
            with pytest.raises(ReservationStateError):
                await ent.finalize(
                    db, tenant_id=TENANT, idempotency_key="rel",
                    settled_units=20, now=LATER,
                )
            await db.rollback()


# ---------------------------------------------------------------------------
# Non-vacuity: the preservation assertions can actually be false
# ---------------------------------------------------------------------------


async def test_backup_taken_before_seeding_carries_no_state(tmp_path) -> None:
    """Negative control for the whole matrix.

    A backup taken *before* any commercial or capacity state exists restores to a
    database with zero reservations, billing periods, usage events and capacity
    leases. This is what makes the round-trip preservation tests above
    meaningful: they are not passing merely because the restored tables are
    always empty. The same assertions would fail against this pre-seed snapshot,
    so a green matrix run proves the state survived the round trip.
    """
    src = tmp_path / "src"
    (src / "uploads").mkdir(parents=True)
    cfg = Settings(
        deployment_profile="development",
        database_url=f"sqlite+aiosqlite:///{src / 'openjm.db'}",
        upload_dir=src / "uploads",
        vector_path=src / "vector",
        credential_key_file=src / "credentials.key",
        backup_dir=tmp_path / "backups",
    )
    adopt_and_upgrade(cfg.database_url)
    engine = _engine(cfg.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    # Snapshot first, then create the state the other tests assert survives.
    empty_backup = create_backup(destination=cfg.backup_dir / "empty", settings=cfg)

    await _seed_tenant(maker, allowance=100)
    async with maker() as db:
        await ent.reserve(
            db, tenant_id=TENANT, idempotency_key="late", units=40, now=OCT
        )
    async with maker() as db:
        await adm.admit(db, _req(TENANT, "late-attempt"), _limits(tenant_limit=2), now=OCT)
        await db.commit()
    async with maker() as db:
        await um.record_model_usage(
            db,
            context=um.UsageContext(
                tenant_id=TENANT, request_id="late-req",
                provider_route="local", model_name="model-x",
            ),
            usage=um.ModelCallUsage(input_tokens=4, output_tokens=1, provider_reported=True),
        )
        await db.commit()
    await engine.dispose()

    target = tmp_path / "restored"
    restore_backup(
        empty_backup,
        target_database_url=f"sqlite+aiosqlite:///{target / 'openjm.db'}",
        target_data_dir=target / "uploads",
    )
    restored_engine = _engine(f"sqlite+aiosqlite:///{target / 'openjm.db'}")
    restored = async_sessionmaker(restored_engine, expire_on_commit=False)
    try:
        assert await _count(restored, UsageReservation) == 0
        assert await _count(restored, BillingPeriod) == 0
        assert await _count(restored, ModelUsageEvent) == 0
        assert await _count(restored, AdmissionTicket) == 0
        assert await _count(restored, CapacityLease) == 0
        assert await _count(restored, CapacityScope) == 0
    finally:
        await restored_engine.dispose()
