"""Transactional, multi-worker admission and capacity enforcement (INF1-B).

Enforcement is a conditional database update, never an in-process semaphore.
Every scope (tenant, deployment, pool) has exactly one counter row, and a slot
is taken only by an UPDATE whose WHERE clause re-checks the limit in the same
statement::

    UPDATE capacity_scopes
       SET in_flight = in_flight + 1
     WHERE scope_key = ? AND in_flight < concurrency_limit

Why this is safe with more than one API worker:

* On PostgreSQL the UPDATE takes a row lock. A second transaction blocks, then,
  under READ COMMITTED, re-reads the row and re-evaluates the WHERE predicate,
  so it only succeeds if a slot is genuinely still free. There is no read then
  write window for two workers to slip through.
* On SQLite the database serialises writers; ``busy_timeout`` makes the second
  writer wait rather than fail, and it then evaluates the predicate against the
  already-incremented counter. The counter cannot be observed between an
  increment and its commit by another writer.

A scope lock is therefore never a long held application lock: it is a single
statement, and no database lock is held across network execution, exactly as the
plan requires.

The commercial reservation is opaque. This module stores one handle string and
records the caller's statement about it. It never parses the handle, never keeps
a balance and never settles anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admission import (
    MAX_ADMISSION_ATTEMPTS,
    RESERVATION_PRESENT,
    AdmissionOutcomeKind,
    AdmissionReason,
    AdmissionRequest,
    AdmissionResult,
    CapacityLimits,
    QueuedTicket,
    bounded_retry_after_ms,
    cache_namespace_allowed,
    derive_cache_namespace,
)
from app.core.inference import (
    UNQUALIFIED_MODES,
    ensure_aware,
    utc_now,
)
from app.models import (
    AdmissionTicket,
    CapacityLease,
    CapacityPool,
    CapacityScope,
    Tenant,
)

# A caller may inject authorization and a reservation probe. Both default to the
# safe behaviour: re-read tenant state from the database and trust only the
# state recorded on the ticket. There is no network dependency on either path,
# so an air gapped deployment enforces the same rules.
AuthorizeFn = Callable[[AsyncSession, str, str], Awaitable[bool]]
ReservationProbeFn = Callable[[str | None], Awaitable[str]]


def _aware(value: datetime) -> datetime:
    return ensure_aware(value)


def tenant_scope_key(tenant_id: str) -> str:
    return f"tenant:{tenant_id}"


def deployment_scope_key(deployment_id: str) -> str:
    return f"deployment:{deployment_id}"


def pool_scope_key(pool_id: str) -> str:
    return f"pool:{pool_id}"


@dataclass(frozen=True)
class _GrantResult:
    lease: CapacityLease | None
    reason: str | None


class _NoCapacity(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


async def configure_pool(
    db: AsyncSession,
    *,
    pool_id: str,
    pool_kind: str,
    concurrency_limit: int,
    owner_tenant_id: str | None = None,
) -> CapacityPool:
    """Register a shared or dedicated pool. The DB enforces the owner split."""
    pool = await db.get(CapacityPool, pool_id)
    if pool is None:
        pool = CapacityPool(
            pool_id=pool_id,
            pool_kind=pool_kind,
            owner_tenant_id=owner_tenant_id,
            concurrency_limit=concurrency_limit,
        )
        db.add(pool)
        await db.flush()
    return pool


# ---------------------------------------------------------------------------
# Scope counters
# ---------------------------------------------------------------------------


async def _ensure_scope(
    db: AsyncSession, *, scope_key: str, limit: int, now: datetime
) -> CapacityScope:
    scope = await db.get(CapacityScope, scope_key)
    if scope is not None:
        return scope
    try:
        async with db.begin_nested():
            db.add(
                CapacityScope(
                    scope_key=scope_key,
                    in_flight=0,
                    concurrency_limit=limit,
                    next_fence=1,
                    updated_at=now,
                )
            )
            await db.flush()
    except IntegrityError:
        # Another worker created it first; fall through to re-read.
        pass
    return await db.get(CapacityScope, scope_key)


async def _try_acquire_scope(
    db: AsyncSession, *, scope_key: str, limit: int, now: datetime
) -> bool:
    """Take one slot with a single atomic conditional update.

    This is the load bearing statement for the whole workstream: the limit is
    re-checked inside the WHERE clause, so no two workers can both observe a
    free slot.
    """
    result = await db.execute(
        update(CapacityScope)
        .where(
            CapacityScope.scope_key == scope_key,
            CapacityScope.in_flight < limit,
        )
        .values(in_flight=CapacityScope.in_flight + 1, updated_at=now)
    )
    return result.rowcount == 1


async def _release_scope(db: AsyncSession, *, scope_key: str, now: datetime) -> None:
    await db.execute(
        update(CapacityScope)
        .where(CapacityScope.scope_key == scope_key, CapacityScope.in_flight > 0)
        .values(in_flight=CapacityScope.in_flight - 1, updated_at=now)
    )


async def _next_fence(db: AsyncSession, *, scope_key: str, now: datetime) -> int:
    """Return the next monotonic fence for a scope, read your writes in one tx."""
    await db.execute(
        update(CapacityScope)
        .where(CapacityScope.scope_key == scope_key)
        .values(next_fence=CapacityScope.next_fence + 1, updated_at=now)
    )
    value = await db.scalar(
        select(CapacityScope.next_fence).where(CapacityScope.scope_key == scope_key)
    )
    return int(value or 1)


async def in_flight(db: AsyncSession, scope_key: str) -> int:
    return int(await db.scalar(
        select(CapacityScope.in_flight).where(CapacityScope.scope_key == scope_key)
    ) or 0)


# ---------------------------------------------------------------------------
# Placement / isolation policy
# ---------------------------------------------------------------------------


async def _check_placement(
    db: AsyncSession, request: AdmissionRequest, limits: CapacityLimits
) -> AdmissionResult | None:
    """Enforce dedicated vs shared placement and mode qualification.

    Consistent with INF1-A: an unqualified shared mode is never activated, a
    deployment that is not shared may not be placed in a shared pool, and a
    dedicated pool belongs to exactly one tenant.
    """
    if request.inference_mode in UNQUALIFIED_MODES:
        return _reject(AdmissionReason.ISOLATION_UNQUALIFIED, release_reservation=True)

    if request.pool_id:
        pool = await db.get(CapacityPool, request.pool_id)
        if pool is None:
            return _reject(AdmissionReason.POOL_NOT_AUTHORIZED, release_reservation=True)
        if pool.pool_kind == "shared":
            # Nothing but a shared mode may use shared capacity, and no shared
            # mode is qualified yet, so every non shared mode is refused here.
            return _reject(
                AdmissionReason.DEDICATED_IN_SHARED_POOL, release_reservation=True
            )
        if pool.owner_tenant_id != request.tenant_id:
            return _reject(AdmissionReason.POOL_NOT_AUTHORIZED, release_reservation=True)
    return None


def _scopes_for(
    *, tenant_id: str, deployment_id: str | None, pool_id: str | None, limits: CapacityLimits
) -> dict[str, int]:
    scopes: dict[str, int] = {tenant_scope_key(tenant_id): limits.tenant_limit}
    if deployment_id:
        scopes[deployment_scope_key(deployment_id)] = limits.deployment_limit
    if pool_id:
        scopes[pool_scope_key(pool_id)] = limits.pool_limit
    return scopes


def _scope_reason(scope_key: str) -> str:
    if scope_key.startswith("pool:"):
        return AdmissionReason.POOL_CONCURRENCY_LIMIT.value
    if scope_key.startswith("deployment:"):
        return AdmissionReason.DEPLOYMENT_CONCURRENCY_LIMIT.value
    return AdmissionReason.TENANT_CONCURRENCY_LIMIT.value


def _release_keys_for(
    *, tenant_id: str, deployment_id: str | None, pool_id: str | None
) -> list[str]:
    keys = [tenant_scope_key(tenant_id)]
    if deployment_id:
        keys.append(deployment_scope_key(deployment_id))
    if pool_id:
        keys.append(pool_scope_key(pool_id))
    return keys


async def _grant(
    db: AsyncSession,
    *,
    tenant_id: str,
    deployment_id: str | None,
    pool_id: str | None,
    attempt_id: str,
    limits: CapacityLimits,
    now: datetime,
) -> _GrantResult:
    """Try to take every scope atomically; on any refusal roll them all back.

    The savepoint makes the acquisition all or nothing, so a request that
    cannot get its deployment slot does not silently leave a pool slot held.
    """
    scopes = _scopes_for(
        tenant_id=tenant_id, deployment_id=deployment_id, pool_id=pool_id, limits=limits
    )
    # A fixed order by scope key avoids a lock cycle between two workers taking
    # the same pair of scopes in different orders.
    ordered = sorted(scopes.items(), key=lambda item: item[0])
    try:
        async with db.begin_nested():
            for scope_key, limit in ordered:
                await _ensure_scope(db, scope_key=scope_key, limit=limit, now=now)
                if not await _try_acquire_scope(
                    db, scope_key=scope_key, limit=limit, now=now
                ):
                    raise _NoCapacity(_scope_reason(scope_key))
            fence = await _next_fence(
                db, scope_key=tenant_scope_key(tenant_id), now=now
            )
            lease = CapacityLease(
                tenant_id=tenant_id,
                deployment_id=deployment_id,
                pool_id=pool_id,
                attempt_id=attempt_id,
                lease_token=uuid4().hex,
                fence=fence,
                state="active",
                execution_certainty="not_dispatched",
                acquired_at=now,
                expires_at=now + timedelta(seconds=limits.lease_ttl_seconds),
            )
            db.add(lease)
            await db.flush()
        return _GrantResult(lease=lease, reason=None)
    except _NoCapacity as refusal:
        return _GrantResult(lease=None, reason=refusal.reason)


async def _release_lease(
    db: AsyncSession, lease: CapacityLease, *, now: datetime, state: str
) -> None:
    if lease.state != "active":
        return
    lease.state = state
    lease.released_at = now
    for scope_key in _release_keys_for(
        tenant_id=lease.tenant_id, deployment_id=lease.deployment_id, pool_id=lease.pool_id
    ):
        await _release_scope(db, scope_key=scope_key, now=now)


# ---------------------------------------------------------------------------
# Authorization and reservation revalidation
# ---------------------------------------------------------------------------


async def _default_authorize(db: AsyncSession, tenant_id: str, _attempt_id: str) -> bool:
    tenant = await db.get(Tenant, tenant_id)
    return tenant is not None and tenant.status == "active"


async def _authorized(
    db: AsyncSession, tenant_id: str, attempt_id: str, authorize: AuthorizeFn | None
) -> bool:
    fn = authorize or _default_authorize
    return bool(await fn(db, tenant_id, attempt_id))


def _reject(
    reason: AdmissionReason,
    *,
    retry_after_ms: int | None = None,
    release_reservation: bool = False,
) -> AdmissionResult:
    return AdmissionResult(
        outcome=AdmissionOutcomeKind.REJECTED.value,
        reason=reason.value,
        retry_after_ms=retry_after_ms,
        release_reservation=release_reservation,
    )


async def _ticket_for_attempt(
    db: AsyncSession, tenant_id: str, attempt_id: str
) -> AdmissionTicket | None:
    return (
        await db.execute(
            select(AdmissionTicket).where(
                AdmissionTicket.tenant_id == tenant_id,
                AdmissionTicket.attempt_id == attempt_id,
            )
        )
    ).scalar_one_or_none()


async def _queued_depth(db: AsyncSession, tenant_id: str) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(AdmissionTicket)
            .where(
                AdmissionTicket.tenant_id == tenant_id,
                AdmissionTicket.state == "queued",
            )
        )
        or 0
    )


def _result_from_ticket(ticket: AdmissionTicket) -> AdmissionResult:
    admitted = ticket.state in ("admitted", "dispatched")
    queued = ticket.state == "queued"
    reason = AdmissionReason.ADMITTED.value if admitted else (
        AdmissionReason.QUEUED.value if queued else AdmissionReason.REQUEST_ALREADY_ADMITTED.value
    )
    return AdmissionResult(
        outcome=(
            AdmissionOutcomeKind.ADMITTED.value
            if admitted
            else AdmissionOutcomeKind.QUEUED.value
            if queued
            else AdmissionOutcomeKind.REJECTED.value
        ),
        reason=reason,
        ticket_id=ticket.id,
        lease_id=ticket.lease_id,
        cache_namespace=ticket.cache_namespace,
        wait_ms=ticket.wait_ms,
        retry_after_ms=ticket.retry_after_ms,
        admitted=admitted,
        queued=queued,
    )


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------


async def admit(
    db: AsyncSession,
    request: AdmissionRequest,
    limits: CapacityLimits,
    *,
    now: datetime | None = None,
    authorize: AuthorizeFn | None = None,
) -> AdmissionResult:
    """Admit, queue or refuse one request before dispatch.

    Order of checks is fixed so a refusal is always the most specific safe
    reason: reservation presence, authorization, placement and isolation, cache
    namespace, deadline, then capacity. Nothing here touches the network.
    """
    moment = _aware(now or utc_now())

    existing = await _ticket_for_attempt(db, request.tenant_id, request.attempt_id)
    if existing is not None:
        return _result_from_ticket(existing)

    if not request.reservation_handle:
        return _reject(AdmissionReason.RESERVATION_ABSENT)
    if request.reservation_state == "released":
        return _reject(AdmissionReason.RESERVATION_RELEASED)
    if request.reservation_state == "expired":
        return _reject(AdmissionReason.RESERVATION_EXPIRED)
    if request.reservation_state != RESERVATION_PRESENT:
        # Unknown is not an allowance.
        return _reject(AdmissionReason.RESERVATION_ABSENT)

    if not await _authorized(db, request.tenant_id, request.attempt_id, authorize):
        return _reject(AdmissionReason.TENANT_INACTIVE, release_reservation=True)

    placement = await _check_placement(db, request, limits)
    if placement is not None:
        return placement

    # The namespace is derived from server facts only; the caller supplied value
    # is read here solely so a test can prove it is never used.
    namespace = derive_cache_namespace(request.tenant_id, request.deployment_id)
    if not cache_namespace_allowed(request.tenant_id, namespace):
        return _reject(AdmissionReason.CACHE_NAMESPACE_DENIED, release_reservation=True)

    if request.deadline_at is not None and _aware(request.deadline_at) <= moment:
        return _reject(AdmissionReason.DEADLINE_EXCEEDED, release_reservation=True)

    grant = await _grant(
        db,
        tenant_id=request.tenant_id,
        deployment_id=request.deployment_id,
        pool_id=request.pool_id,
        attempt_id=request.attempt_id,
        limits=limits,
        now=moment,
    )
    if grant.lease is not None:
        ticket = AdmissionTicket(
            tenant_id=request.tenant_id,
            attempt_id=request.attempt_id,
            deployment_id=request.deployment_id,
            pool_id=request.pool_id,
            inference_mode=request.inference_mode,
            cache_namespace=namespace,
            reservation_handle=request.reservation_handle,
            reservation_state=request.reservation_state,
            state="admitted",
            enqueue_seq=0,
            lease_id=grant.lease.id,
            wait_ms=0,
        )
        db.add(ticket)
        await db.flush()
        return AdmissionResult(
            outcome=AdmissionOutcomeKind.ADMITTED.value,
            reason=AdmissionReason.ADMITTED.value,
            ticket_id=ticket.id,
            lease_id=grant.lease.id,
            lease_token=grant.lease.lease_token,
            fence=grant.lease.fence,
            cache_namespace=namespace,
            wait_ms=0,
            admitted=True,
        )

    # No capacity. Queue if the bounded queue has room, else a bounded overload.
    depth = await _queued_depth(db, request.tenant_id)
    if depth >= limits.max_queue_depth:
        return _reject(
            AdmissionReason.QUEUE_FULL,
            retry_after_ms=bounded_retry_after_ms(0),
            release_reservation=True,
        )
    seq = await _next_fence(db, scope_key="queue:global", now=moment)
    ticket = AdmissionTicket(
        tenant_id=request.tenant_id,
        attempt_id=request.attempt_id,
        deployment_id=request.deployment_id,
        pool_id=request.pool_id,
        inference_mode=request.inference_mode,
        cache_namespace=namespace,
        reservation_handle=request.reservation_handle,
        reservation_state=request.reservation_state,
        state="queued",
        enqueue_seq=seq,
        retry_after_ms=bounded_retry_after_ms(depth),
        wait_ms=0,
    )
    db.add(ticket)
    await db.flush()
    return AdmissionResult(
        outcome=AdmissionOutcomeKind.QUEUED.value,
        reason=AdmissionReason.QUEUED.value,
        ticket_id=ticket.id,
        cache_namespace=namespace,
        retry_after_ms=ticket.retry_after_ms,
        wait_ms=0,
        queued=True,
    )


async def dispatch(
    db: AsyncSession,
    ticket_id: str,
    limits: CapacityLimits,
    *,
    now: datetime | None = None,
    authorize: AuthorizeFn | None = None,
    reservation_probe: ReservationProbeFn | None = None,
) -> AdmissionResult:
    """Promote one ticket to dispatch after revalidating everything.

    Waiting is not consent. A queued request is re-checked here for tenant
    state, for reservation presence and for authorization before it takes any
    capacity. A tenant revoked while queued is refused and any capacity it holds
    is released.
    """
    moment = _aware(now or utc_now())
    ticket = await db.get(AdmissionTicket, ticket_id)
    if ticket is None:
        return _reject(AdmissionReason.TENANT_INACTIVE)
    if ticket.state in ("admitted", "dispatched"):
        return _result_from_ticket(ticket)

    if not await _authorized(db, ticket.tenant_id, ticket.attempt_id, authorize):
        ticket.state = "rejected"
        ticket.failure_category = AdmissionReason.AUTHORIZATION_REVOKED.value
        if ticket.lease_id:
            lease = await db.get(CapacityLease, ticket.lease_id)
            if lease is not None:
                await _release_lease(db, lease, now=moment, state="released")
        await db.flush()
        return _reject(AdmissionReason.AUTHORIZATION_REVOKED, release_reservation=True)

    state = (
        await reservation_probe(ticket.reservation_handle)
        if reservation_probe is not None
        else ticket.reservation_state
    )
    if not ticket.reservation_handle or state != RESERVATION_PRESENT:
        ticket.state = "rejected"
        ticket.failure_category = (
            AdmissionReason.RESERVATION_RELEASED.value
            if state == "released"
            else AdmissionReason.RESERVATION_EXPIRED.value
            if state == "expired"
            else AdmissionReason.RESERVATION_ABSENT.value
        )
        await db.flush()
        return _reject(AdmissionReason(ticket.failure_category))
    ticket.reservation_state = state

    waited_ms = int((moment - _aware(ticket.created_at)).total_seconds() * 1000)
    if waited_ms >= limits.max_wait_ms:
        ticket.state = "expired"
        ticket.failure_category = AdmissionReason.DEADLINE_EXCEEDED.value
        ticket.wait_ms = waited_ms
        await db.flush()
        return _reject(AdmissionReason.DEADLINE_EXCEEDED, release_reservation=True)

    grant = await _grant(
        db,
        tenant_id=ticket.tenant_id,
        deployment_id=ticket.deployment_id,
        pool_id=ticket.pool_id,
        attempt_id=ticket.attempt_id,
        limits=limits,
        now=moment,
    )
    if grant.lease is None:
        ticket.retry_after_ms = bounded_retry_after_ms(0)
        ticket.wait_ms = waited_ms
        await db.flush()
        return AdmissionResult(
            outcome=AdmissionOutcomeKind.QUEUED.value,
            reason=AdmissionReason.QUEUED.value,
            ticket_id=ticket.id,
            cache_namespace=ticket.cache_namespace,
            retry_after_ms=ticket.retry_after_ms,
            wait_ms=waited_ms,
            queued=True,
        )

    ticket.state = "admitted"
    ticket.lease_id = grant.lease.id
    ticket.retry_after_ms = None
    ticket.wait_ms = waited_ms
    await db.flush()
    return AdmissionResult(
        outcome=AdmissionOutcomeKind.ADMITTED.value,
        reason=AdmissionReason.ADMITTED.value,
        ticket_id=ticket.id,
        lease_id=grant.lease.id,
        lease_token=grant.lease.lease_token,
        fence=grant.lease.fence,
        cache_namespace=ticket.cache_namespace,
        wait_ms=waited_ms,
        admitted=True,
    )


async def promote_queued(
    db: AsyncSession,
    limits: CapacityLimits,
    *,
    now: datetime | None = None,
    authorize: AuthorizeFn | None = None,
    reservation_probe: ReservationProbeFn | None = None,
) -> list[AdmissionResult]:
    """Attempt dispatch for queued tickets in the fair order.

    The order comes from :func:`fair_dispatch_order`, so a heavy tenant cannot
    keep a light tenant waiting behind its backlog.
    """
    from app.core.admission import fair_dispatch_order

    rows = (
        await db.execute(select(AdmissionTicket).where(AdmissionTicket.state == "queued"))
    ).scalars().all()
    ordered = fair_dispatch_order(
        [QueuedTicket(t.id, t.tenant_id, t.enqueue_seq) for t in rows]
    )
    by_id = {t.id: t for t in rows}
    results: list[AdmissionResult] = []
    for ticket_id in ordered:
        ticket = by_id[ticket_id]
        if not await _authorized(db, ticket.tenant_id, ticket.attempt_id, authorize):
            continue
        result = await dispatch(
            db,
            ticket_id,
            limits,
            now=now,
            authorize=authorize,
            reservation_probe=reservation_probe,
        )
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Fencing, release and recovery
# ---------------------------------------------------------------------------


async def verify_lease(db: AsyncSession, *, lease_token: str, fence: int) -> bool:
    """A worker may proceed only if its lease is still active at its fence.

    A reclaimed lease is no longer ``active`` or carries a greater fence, so a
    stale worker is refused instead of overbooking a live one.
    """
    lease = (
        await db.execute(
            select(CapacityLease).where(CapacityLease.lease_token == lease_token)
        )
    ).scalar_one_or_none()
    return lease is not None and lease.state == "active" and lease.fence == fence


async def mark_dispatched(
    db: AsyncSession, *, lease_token: str, fence: int, now: datetime | None = None
) -> bool:
    """Record dispatch intent so a later crash is explicit, not silent.

    On success the attempt's certainty becomes ``unknown``. Nothing is ever
    recorded as a completed zero on this path.
    """
    if not await verify_lease(db, lease_token=lease_token, fence=fence):
        return False
    lease = (
        await db.execute(
            select(CapacityLease).where(CapacityLease.lease_token == lease_token)
        )
    ).scalar_one()
    lease.execution_certainty = "unknown"
    ticket = (
        await db.execute(
            select(AdmissionTicket).where(AdmissionTicket.lease_id == lease.id)
        )
    ).scalar_one_or_none()
    if ticket is not None:
        ticket.state = "dispatched"
        ticket.execution_certainty = "unknown"
    await db.flush()
    return True


async def release(db: AsyncSession, *, lease_id: str, now: datetime | None = None) -> bool:
    """Release a lease and free its scopes exactly once."""
    moment = _aware(now or utc_now())
    lease = await db.get(CapacityLease, lease_id)
    if lease is None or lease.state != "active":
        return False
    await _release_lease(db, lease, now=moment, state="released")
    ticket = (
        await db.execute(
            select(AdmissionTicket).where(AdmissionTicket.lease_id == lease.id)
        )
    ).scalar_one_or_none()
    if ticket is not None and ticket.state in ("admitted", "dispatched"):
        ticket.state = "released"
    await db.flush()
    return True


async def reclaim_lease(
    db: AsyncSession,
    *,
    lease_id: str,
    now: datetime | None = None,
    force: bool = False,
) -> tuple[bool, str | None]:
    """Reclaim an expired lease. An unexpired lease is never stolen.

    Returns ``(reclaimed, certainty_of_old_lease)``. A lease belonging to an
    attempt that had already dispatched before the crash is marked ``uncertain``;
    one that had not dispatched is ``not_dispatched``. The distinction is what
    keeps a crash after dispatch from being reported as zero consumption.
    """
    moment = _aware(now or utc_now())
    lease = await db.get(CapacityLease, lease_id)
    if lease is None or lease.state != "active":
        return (False, None)
    if not force and _aware(lease.expires_at) > moment:
        # Still live: do not steal it from a running worker.
        return (False, None)
    dispatched = lease.execution_certainty == "unknown"
    await _release_lease(db, lease, now=moment, state="uncertain" if dispatched else "expired")
    ticket = (
        await db.execute(
            select(AdmissionTicket).where(AdmissionTicket.lease_id == lease.id)
        )
    ).scalar_one_or_none()
    if ticket is not None:
        ticket.state = "uncertain" if dispatched else "expired"
        ticket.execution_certainty = "unknown" if dispatched else "not_dispatched"
    await db.flush()
    return (True, "unknown" if dispatched else "not_dispatched")


async def expire_stale_leases(
    db: AsyncSession, *, now: datetime | None = None
) -> int:
    """Reclaim every active lease whose time bound has passed.

    Called at startup and opportunistically. It never invents a usage number:
    an interrupted attempt is either ``not_dispatched`` or ``uncertain``.
    """
    moment = _aware(now or utc_now())
    rows = (
        await db.execute(
            select(CapacityLease).where(
                CapacityLease.state == "active",
                CapacityLease.expires_at <= moment,
            )
        )
    ).scalars().all()
    count = 0
    for lease in rows:
        reclaimed, _ = await reclaim_lease(db, lease_id=lease.id, now=moment)
        if reclaimed:
            count += 1
    return count


async def uncertain_consumption(db: AsyncSession) -> int:
    """Count attempts whose consumption is explicitly unknown, never zero."""
    return int(
        await db.scalar(
            select(func.count())
            .select_from(AdmissionTicket)
            .where(AdmissionTicket.state == "uncertain")
        )
        or 0
    )


async def queued_tickets(db: AsyncSession, tenant_id: str | None = None) -> list[AdmissionTicket]:
    query = select(AdmissionTicket).where(AdmissionTicket.state == "queued")
    if tenant_id is not None:
        query = query.where(AdmissionTicket.tenant_id == tenant_id)
    return list((await db.execute(query.order_by(AdmissionTicket.enqueue_seq))).scalars().all())


def max_attempts() -> int:
    return MAX_ADMISSION_ATTEMPTS


__all__ = [
    "admit",
    "configure_pool",
    "dispatch",
    "deployment_scope_key",
    "expire_stale_leases",
    "in_flight",
    "mark_dispatched",
    "max_attempts",
    "pool_scope_key",
    "promote_queued",
    "queued_tickets",
    "reclaim_lease",
    "release",
    "tenant_scope_key",
    "uncertain_consumption",
    "verify_lease",
]
