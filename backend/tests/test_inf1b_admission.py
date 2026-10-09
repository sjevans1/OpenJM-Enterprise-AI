"""INF1-B admission, concurrency and isolation acceptance.

Covers the automatable INF1-B acceptance areas with the existing approved
test-model path: tenant and deployment/pool concurrency limits enforced across
sessions, a bounded queue with a fair policy and a safe overload response,
reservation aware admission, reauthorization after waiting, fenced capacity
leases, crash recovery that keeps uncertain consumption explicit, dedicated vs
shared placement, cache namespace isolation, a local/disconnected path with no
network, and a bounded retry indication.

The commercial reservation is treated as an opaque handle throughout: the tests
only ever supply a token and a caller reported state, never a credit value.
"""

from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app.core.admission import (
    BASE_RETRY_AFTER_MS,
    MAX_ADMISSION_ATTEMPTS,
    MAX_RETRY_AFTER_MS,
    AdmissionReason,
    AdmissionRequest,
    CapacityLimits,
    QueuedTicket,
    bounded_retry_after_ms,
    cache_namespace_allowed,
    derive_cache_namespace,
    fair_dispatch_order,
    retries_exhausted,
)
from app.models import (
    AdmissionTicket,
    CapacityLease,
    CapacityScope,
    Tenant,
)
from app.services.inference import admission as adm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _req(
    tenant_id: str,
    attempt_id: str,
    *,
    deployment_id: str | None = "dep-a",
    inference_mode: str = "customer_local",
    pool_id: str | None = None,
    handle: str | None = "res-opaque-xyz",
    reservation_state: str = "present",
    deadline_at: datetime | None = None,
    client_cache_namespace: str | None = None,
) -> AdmissionRequest:
    return AdmissionRequest(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
        deployment_id=deployment_id,
        inference_mode=inference_mode,
        reservation_handle=handle,
        reservation_state=reservation_state,
        pool_id=pool_id,
        deadline_at=deadline_at,
        client_cache_namespace=client_cache_namespace,
    )


async def _tenant(db, tenant_id: str, status: str = "active") -> Tenant:
    row = await db.get(Tenant, tenant_id)
    if row is None:
        row = Tenant(id=tenant_id, slug=tenant_id, name=tenant_id, status=status)
        db.add(row)
        await db.flush()
    else:
        row.status = status
    return row


async def _tenant_in_flight(db, tenant_id: str) -> int:
    return await adm.in_flight(db, adm.tenant_scope_key(tenant_id))


# ---------------------------------------------------------------------------
# B01: tenant concurrency limit, enforced across sessions
# ---------------------------------------------------------------------------


async def test_tenant_concurrency_limit_is_not_exceeded(file_db):
    limits = _limits(tenant_limit=2)
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        first = await adm.admit(db, _req("tnt-a", "a1"), limits)
        second = await adm.admit(db, _req("tnt-a", "a2"), limits)
        third = await adm.admit(db, _req("tnt-a", "a3"), limits)
        await db.commit()

    assert first.admitted and second.admitted
    assert third.queued, "a third concurrent request must not be admitted"
    assert third.reason == AdmissionReason.QUEUED.value

    async with file_db() as db:
        assert await _tenant_in_flight(db, "tnt-a") == 2


async def test_limit_holds_across_two_committed_sessions(file_db):
    """A second process sees the first process' committed slot."""
    limits = _limits(tenant_limit=1)
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await db.commit()

    async with file_db() as db:
        worker_one = await adm.admit(db, _req("tnt-a", "w1"), limits)
        await db.commit()
    assert worker_one.admitted

    async with file_db() as db:
        worker_two = await adm.admit(db, _req("tnt-a", "w2"), limits)
        await db.commit()
    assert worker_two.queued

    async with file_db() as db:
        assert await _tenant_in_flight(db, "tnt-a") == 1


async def test_concurrent_admits_never_exceed_the_limit(file_db):
    """Independent sessions racing for slots converge on exactly the limit.

    SQLite serialises writers and a losing writer can observe a transient
    ``database is locked`` on a lock upgrade; a real worker retries it. What
    matters is that however the retries interleave, the atomic predicate never
    lets more than the limit through.
    """
    import asyncio

    from sqlalchemy.exc import OperationalError

    limits = _limits(tenant_limit=2, max_queue_depth=0)
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await db.commit()

    async def attempt(index: int) -> bool:
        for _ in range(20):
            try:
                async with file_db() as db:
                    result = await adm.admit(db, _req("tnt-a", f"race-{index}"), limits)
                    await db.commit()
                    return result.admitted
            except OperationalError:
                await asyncio.sleep(0.02)
        raise AssertionError("admission did not complete under contention")

    outcomes = await asyncio.gather(*[attempt(i) for i in range(5)])
    assert sum(1 for admitted in outcomes if admitted) == 2

    async with file_db() as db:
        assert await _tenant_in_flight(db, "tnt-a") == 2


# ---------------------------------------------------------------------------
# B01: deployment and pool limits
# ---------------------------------------------------------------------------


async def test_deployment_and_pool_limits_are_separate(file_db):
    # Deployment limit of one, with a tenant limit loose enough that only the
    # deployment scope can be the reason for a queue.
    deployment_limits = _limits(tenant_limit=5, deployment_limit=1)
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        first = await adm.admit(db, _req("tnt-a", "d1", deployment_id="dep-1"), deployment_limits)
        second = await adm.admit(db, _req("tnt-a", "d2", deployment_id="dep-1"), deployment_limits)
        await db.commit()
    assert first.admitted
    assert second.queued
    assert second.reason == AdmissionReason.QUEUED.value

    # A dedicated pool of one is exhausted by a single deployment on it even
    # though the tenant and deployment limits are loose.
    pool_limits = _limits(tenant_limit=10, deployment_limit=10, pool_limit=1)
    async with file_db() as db:
        await _tenant(db, "tnt-b")
        await adm.configure_pool(
            db, pool_id="pool-b", pool_kind="dedicated", owner_tenant_id="tnt-b", concurrency_limit=1
        )
        one = await adm.admit(
            db, _req("tnt-b", "p1", deployment_id="dep-b1", pool_id="pool-b"), pool_limits
        )
        two = await adm.admit(
            db, _req("tnt-b", "p2", deployment_id="dep-b2", pool_id="pool-b"), pool_limits
        )
        await db.commit()
    assert one.admitted
    assert two.queued

    async with file_db() as db:
        assert await adm.in_flight(db, adm.pool_scope_key("pool-b")) == 1


# ---------------------------------------------------------------------------
# B02: bounded queue and a safe overload response
# ---------------------------------------------------------------------------


async def test_queue_is_bounded_and_overload_is_explicit(file_db):
    limits = _limits(tenant_limit=1, max_queue_depth=1)
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        admitted = await adm.admit(db, _req("tnt-a", "q1"), limits)
        queued = await adm.admit(db, _req("tnt-a", "q2"), limits)
        overloaded = await adm.admit(db, _req("tnt-a", "q3"), limits)
        await db.commit()

    assert admitted.admitted
    assert queued.queued
    assert overloaded.rejected
    assert overloaded.reason == AdmissionReason.QUEUE_FULL.value
    # A bounded retry hint, never an open ended instruction.
    assert 0 < overloaded.retry_after_ms <= MAX_RETRY_AFTER_MS
    # The overload refusal happened after the reservation was accepted, so the
    # caller is told to release the hold exactly once (INF1-B never settles).
    assert overloaded.release_reservation is True


# ---------------------------------------------------------------------------
# B02: fair scheduling with no starvation
# ---------------------------------------------------------------------------


def test_fair_order_is_round_robin_across_tenants():
    tickets = [
        QueuedTicket("h1", "heavy", 1),
        QueuedTicket("h2", "heavy", 2),
        QueuedTicket("h3", "heavy", 3),
        QueuedTicket("l1", "light", 4),
    ]
    order = fair_dispatch_order(tickets)
    # The light tenant arrived last yet is served within one turn per tenant.
    assert order.index("l1") <= 1
    assert order[0] == "h1"


def test_fair_order_is_deterministic_on_a_tie():
    tickets = [QueuedTicket("b1", "beta", 1), QueuedTicket("a1", "alpha", 1)]
    # Equal arrival is broken by tenant id, so the order is stable.
    assert fair_dispatch_order(tickets) == ["a1", "b1"]


async def test_heavy_tenant_does_not_starve_a_light_tenant(file_db):
    """A three deep heavy backlog must not defer a later light request.

    The heavy tenant queues three tickets first; the light tenant queues one
    afterwards. The fair scheduler must still process the light ticket within
    the first round, so it is admitted rather than waiting behind the backlog.
    """
    limits = _limits(tenant_limit=1, deployment_limit=5, pool_limit=5)
    async with file_db() as db:
        await _tenant(db, "heavy")
        await _tenant(db, "light")
        seq = 0
        tickets = []
        for tenant, attempt in (
            ("heavy", "h1"),
            ("heavy", "h2"),
            ("heavy", "h3"),
            ("light", "l1"),
        ):
            seq += 1
            ticket = AdmissionTicket(
                tenant_id=tenant,
                attempt_id=attempt,
                deployment_id=f"dep-{tenant}",
                inference_mode="customer_local",
                cache_namespace=derive_cache_namespace(tenant, f"dep-{tenant}"),
                reservation_handle="res-opaque",
                reservation_state="present",
                state="queued",
                enqueue_seq=seq,
            )
            db.add(ticket)
            tickets.append(ticket)
        await db.commit()
        heavy_ids = [t.id for t in tickets[:3]]
        light_id = tickets[3].id

    async with file_db() as db:
        results = await adm.promote_queued(db, limits)
        await db.commit()
    processed = [r.ticket_id for r in results]
    # Round robin: one heavy ticket, then the light one, before the remaining
    # heavy backlog.
    assert processed[:2] == [heavy_ids[0], light_id]
    assert light_id in {r.ticket_id for r in results if r.admitted}


# ---------------------------------------------------------------------------
# B01 / B04: reservation aware admission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handle,state,expected",
    [
        (None, "present", AdmissionReason.RESERVATION_ABSENT.value),
        ("res-1", "expired", AdmissionReason.RESERVATION_EXPIRED.value),
        ("res-1", "released", AdmissionReason.RESERVATION_RELEASED.value),
        ("res-1", "unknown", AdmissionReason.RESERVATION_ABSENT.value),
    ],
)
async def test_reservation_must_be_present_and_live(file_db, handle, state, expected):
    limits = _limits()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        result = await adm.admit(
            db,
            _req("tnt-a", "r1", handle=handle, reservation_state=state),
            limits,
        )
        await db.commit()
    assert result.rejected
    assert result.reason == expected
    # A refusal for a reservation that was never live does not ask the caller to
    # release anything: there was no accepted hold to release.
    assert result.release_reservation is False
    # A refused request holds no capacity.
    async with file_db() as db:
        assert await _tenant_in_flight(db, "tnt-a") == 0


async def test_reservation_handle_is_never_parsed_for_meaning(file_db):
    """A handle that looks like a number is still only a token."""
    limits = _limits()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        result = await adm.admit(db, _req("tnt-a", "r1", handle="999999"), limits)
        await db.commit()
    assert result.admitted
    async with file_db() as db:
        ticket = (
            await db.execute(select(AdmissionTicket))
        ).scalars().one()
        assert ticket.reservation_handle == "999999"


# ---------------------------------------------------------------------------
# B03: reauthorization after waiting
# ---------------------------------------------------------------------------


async def test_tenant_revoked_while_queued_is_not_dispatched(file_db):
    limits = _limits(tenant_limit=1)
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await adm.admit(db, _req("tnt-a", "held"), limits)
        queued = await adm.admit(db, _req("tnt-a", "waiting"), limits)
        await db.commit()
    assert queued.queued

    async with file_db() as db:
        tenant = await db.get(Tenant, "tnt-a")
        tenant.status = "suspended"
        await db.commit()

    async with file_db() as db:
        result = await adm.dispatch(db, queued.ticket_id, limits)
        await db.commit()
    assert result.rejected
    assert result.reason == AdmissionReason.AUTHORIZATION_REVOKED.value
    assert result.release_reservation is True

    async with file_db() as db:
        ticket = await db.get(AdmissionTicket, queued.ticket_id)
        assert ticket.state == "rejected"
        assert ticket.lease_id is None


async def test_reservation_released_while_queued_is_not_dispatched(file_db):
    limits = _limits(tenant_limit=1)
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await adm.admit(db, _req("tnt-a", "held"), limits)
        queued = await adm.admit(db, _req("tnt-a", "waiting"), limits)
        await db.commit()

    async def probe(_handle: str | None) -> str:
        return "released"

    async with file_db() as db:
        result = await adm.dispatch(db, queued.ticket_id, limits, reservation_probe=probe)
        await db.commit()
    assert result.rejected
    assert result.reason == AdmissionReason.RESERVATION_RELEASED.value


async def test_dispatch_releases_capacity_if_it_was_held(file_db):
    """A revoked ticket that somehow held a lease has that capacity released."""
    limits = _limits(tenant_limit=2)
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        admitted = await adm.admit(db, _req("tnt-a", "a1"), limits)
        await db.commit()
    assert admitted.admitted

    async with file_db() as db:
        tenant = await db.get(Tenant, "tnt-a")
        tenant.status = "suspended"
        await db.commit()

    async with file_db() as db:
        # Force the ticket back to a dispatchable state to exercise the release.
        ticket = await db.get(AdmissionTicket, admitted.ticket_id)
        ticket.state = "queued"
        await db.commit()

    async with file_db() as db:
        result = await adm.dispatch(db, admitted.ticket_id, limits)
        await db.commit()
    assert result.rejected
    async with file_db() as db:
        assert await _tenant_in_flight(db, "tnt-a") == 0


# ---------------------------------------------------------------------------
# B06: fenced leases
# ---------------------------------------------------------------------------


async def test_an_unexpired_lease_is_not_stolen_and_a_reclaimed_one_is_fenced(file_db):
    limits = _limits(lease_ttl_seconds=60)
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        first = await adm.admit(db, _req("tnt-a", "a1"), limits)
        await db.commit()
        first_fence = first.fence

    # A live lease is not stolen.
    async with file_db() as db:
        reclaimed, _ = await adm.reclaim_lease(db, lease_id=first.lease_id)
        await db.commit()
    assert reclaimed is False
    async with file_db() as db:
        assert await adm.verify_lease(db, lease_token=first.lease_token, fence=first_fence)

    # After expiry it is reclaimed and its fence is retired.
    future = datetime.now(timezone.utc) + timedelta(seconds=120)
    async with file_db() as db:
        reclaimed, certainty = await adm.reclaim_lease(
            db, lease_id=first.lease_id, now=future
        )
        await db.commit()
    assert reclaimed is True
    assert certainty == "not_dispatched"
    async with file_db() as db:
        assert not await adm.verify_lease(
            db, lease_token=first.lease_token, fence=first_fence
        )

    # The replacement lease gets a strictly greater fence.
    async with file_db() as db:
        second = await adm.admit(db, _req("tnt-a", "a2"), limits)
        await db.commit()
    assert second.admitted
    assert second.fence > first_fence


# ---------------------------------------------------------------------------
# B05: crash recovery without double counting
# ---------------------------------------------------------------------------


async def test_crash_before_dispatch_is_not_counted_as_consumption(file_db):
    limits = _limits()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        admitted = await adm.admit(db, _req("tnt-a", "a1"), limits)
        await db.commit()

    future = datetime.now(timezone.utc) + timedelta(seconds=120)
    async with file_db() as db:
        reclaimed = await adm.expire_stale_leases(db, now=future)
        await db.commit()
    assert reclaimed == 1

    async with file_db() as db:
        lease = await db.get(CapacityLease, admitted.lease_id)
        assert lease.state == "expired"
        assert lease.execution_certainty == "not_dispatched"
        # The slot is freed and nothing is reported as consumed.
        assert await _tenant_in_flight(db, "tnt-a") == 0
        assert await adm.uncertain_consumption(db) == 0


async def test_crash_after_dispatch_stays_uncertain_not_zero(file_db):
    limits = _limits()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        admitted = await adm.admit(db, _req("tnt-a", "a1"), limits)
        await db.commit()

    async with file_db() as db:
        marked = await adm.mark_dispatched(
            db, lease_token=admitted.lease_token, fence=admitted.fence
        )
        await db.commit()
    assert marked is True

    # The process dies here: no response, no usage recorded. Recovery must not
    # invent a zero.
    future = datetime.now(timezone.utc) + timedelta(seconds=120)
    async with file_db() as db:
        await adm.expire_stale_leases(db, now=future)
        await db.commit()

    async with file_db() as db:
        lease = await db.get(CapacityLease, admitted.lease_id)
        assert lease.state == "uncertain"
        assert lease.execution_certainty == "unknown"
        assert await adm.uncertain_consumption(db) == 1
        # Capacity is reclaimed so the slot is not leaked.
        assert await _tenant_in_flight(db, "tnt-a") == 0


async def test_replayed_admission_does_not_double_count(file_db):
    limits = _limits()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        first = await adm.admit(db, _req("tnt-a", "a1"), limits)
        replay = await adm.admit(db, _req("tnt-a", "a1"), limits)
        await db.commit()
    assert first.admitted and replay.admitted
    assert first.ticket_id == replay.ticket_id
    async with file_db() as db:
        count = await db.scalar(select(func.count()).select_from(AdmissionTicket))
        assert int(count) == 1
        assert await _tenant_in_flight(db, "tnt-a") == 1


# ---------------------------------------------------------------------------
# B07 / B08: dedicated vs shared placement and cache isolation
# ---------------------------------------------------------------------------


async def test_unqualified_shared_mode_is_refused(file_db):
    limits = _limits()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        result = await adm.admit(
            db, _req("tnt-a", "s1", inference_mode="hosted_shared"), limits
        )
        await db.commit()
    assert result.rejected
    assert result.reason == AdmissionReason.ISOLATION_UNQUALIFIED.value


async def test_dedicated_deployment_cannot_use_a_shared_pool(file_db):
    limits = _limits()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await adm.configure_pool(
            db, pool_id="pool-shared", pool_kind="shared", owner_tenant_id=None, concurrency_limit=10
        )
        result = await adm.admit(
            db, _req("tnt-a", "a1", pool_id="pool-shared"), limits
        )
        await db.commit()
    assert result.rejected
    assert result.reason == AdmissionReason.DEDICATED_IN_SHARED_POOL.value


async def test_a_pool_owned_by_another_tenant_is_refused(file_db):
    limits = _limits()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        await _tenant(db, "tnt-b")
        await adm.configure_pool(
            db, pool_id="pool-b", pool_kind="dedicated", owner_tenant_id="tnt-b", concurrency_limit=10
        )
        result = await adm.admit(db, _req("tnt-a", "a1", pool_id="pool-b"), limits)
        await db.commit()
    assert result.rejected
    assert result.reason == AdmissionReason.POOL_NOT_AUTHORIZED.value


async def test_a_client_cannot_select_another_tenants_cache_namespace(file_db):
    limits = _limits()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        result = await adm.admit(
            db,
            _req(
                "tnt-a",
                "a1",
                client_cache_namespace="cache:tenant:tnt-other:dep:dep-x",
            ),
            limits,
        )
        await db.commit()
    assert result.admitted
    assert result.cache_namespace == derive_cache_namespace("tnt-a", "dep-a")
    assert "tnt-other" not in (result.cache_namespace or "")
    assert not cache_namespace_allowed("tnt-a", "cache:tenant:tnt-other:dep:dep-x")


# ---------------------------------------------------------------------------
# B10: local / disconnected path uses no network
# ---------------------------------------------------------------------------


async def test_admission_path_uses_no_network(file_db, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("admission must not touch the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    limits = _limits()
    async with file_db() as db:
        await _tenant(db, "tnt-a")
        admitted = await adm.admit(db, _req("tnt-a", "a1"), limits)
        await db.commit()
    assert admitted.admitted

    async with file_db() as db:
        released = await adm.release(db, lease_id=admitted.lease_id)
        await db.commit()
    assert released is True


# ---------------------------------------------------------------------------
# B02 / B09: bounded, safe retry indication
# ---------------------------------------------------------------------------


def test_retry_indication_is_bounded_and_never_unbounded():
    assert bounded_retry_after_ms(0) == BASE_RETRY_AFTER_MS
    assert bounded_retry_after_ms(100) == MAX_RETRY_AFTER_MS
    assert bounded_retry_after_ms(-5) == BASE_RETRY_AFTER_MS
    assert retries_exhausted(MAX_ADMISSION_ATTEMPTS) is True
    assert retries_exhausted(0) is False


# ---------------------------------------------------------------------------
# Mutation guard: the limit predicate is load bearing
# ---------------------------------------------------------------------------


async def test_removing_the_limit_predicate_over_admits(file_db, monkeypatch):
    """A mutation that drops the WHERE clause must visibly break the limit.

    This proves the conditional predicate, not some incidental check, is what
    enforces the concurrency bound.
    """
    limits = _limits(tenant_limit=2, max_queue_depth=100)

    async def unconditional(db, *, scope_key, limit, now):
        # The mutation: increment with no capacity predicate at all.
        await db.execute(
            update(CapacityScope)
            .where(CapacityScope.scope_key == scope_key)
            .values(in_flight=CapacityScope.in_flight + 1, updated_at=now)
        )
        return True

    monkeypatch.setattr(adm, "_try_acquire_scope", unconditional)

    async with file_db() as db:
        await _tenant(db, "tnt-a")
        admitted = 0
        for index in range(3):
            result = await adm.admit(db, _req("tnt-a", f"m{index}"), limits)
            admitted += 1 if result.admitted else 0
        await db.commit()

    # With the predicate removed the ceiling of two is overrun, so the real
    # predicate is what holds the limit.
    assert admitted == 3
    async with file_db() as db:
        assert await _tenant_in_flight(db, "tnt-a") == 3


# ---------------------------------------------------------------------------
# Migration 0017: additive and reversible
# ---------------------------------------------------------------------------


def test_migration_0017_is_additive_and_reversible(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, inspect

    from app.migrations_runner import (
        _alembic_config,
        adopt_and_upgrade,
        current_revision,
        sync_url_for,
    )

    url = f"sqlite+aiosqlite:///{tmp_path / 'inf1b.db'}"
    adopt_and_upgrade(url)
    # Head-agnostic: a later package moves the chain head forward, so assert that
    # 0017 has been applied rather than pinning the current head. Pinning it has
    # broken on every package that added a revision.
    from alembic.script import ScriptDirectory

    assert current_revision(url) is not None
    script = ScriptDirectory.from_config(_alembic_config(sync_url_for(url)))
    chain: set[str] = set()
    cursor: str | None = script.get_current_head()
    while cursor:
        chain.add(cursor)
        revision = script.get_revision(cursor)
        cursor = revision.down_revision if revision else None
    assert "0017_inf1b_admission" in chain

    engine = create_engine(sync_url_for(url), future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {
            "capacity_pools",
            "capacity_scopes",
            "capacity_leases",
            "admission_tickets",
        } <= tables
        # The accepted INF1-A and M1 tables are untouched.
        assert "inference_deployments" in tables
        assert "model_usage_events" in tables
    finally:
        engine.dispose()

    command.downgrade(_alembic_config(sync_url_for(url)), "0015_inf1_inference_registry")
    assert current_revision(url) == "0015_inf1_inference_registry"
    engine = create_engine(sync_url_for(url), future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "admission_tickets" not in tables
        assert "capacity_leases" not in tables
        assert "inference_deployments" in tables
    finally:
        engine.dispose()
