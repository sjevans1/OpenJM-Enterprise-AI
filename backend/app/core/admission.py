"""INF1-B admission, capacity and isolation contracts.

This module holds the typed, dependency free part of INF1-B: the admission
outcome vocabulary, the bounded queue and backpressure limits, the deterministic
fair dispatch order and the server derived cache namespace. The database backed
enforcement lives in ``app.services.inference.admission``.

Two rules shape everything here.

* The commercial reservation is opaque. INF1-B receives one handle from M3 and
  only ever asks whether it is present and whether the caller says it is still
  live. Nothing in this module parses the handle, derives a balance from it or
  stores a credit amount. M3 owns settlement.
* Admission never admits on an uncertainty. An absent handle, a released or
  expired handle, an unknown mode or a stale authorization all deny before any
  dispatch, and every denial is bounded and safe to return to a caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class AdmissionState(str, Enum):
    """Lifecycle of one admission ticket. Monotonic except queued -> admitted."""

    QUEUED = "queued"
    ADMITTED = "admitted"
    DISPATCHED = "dispatched"
    RELEASED = "released"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNCERTAIN = "uncertain"


class LeaseState(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    UNCERTAIN = "uncertain"


class PoolKind(str, Enum):
    SHARED = "shared"
    DEDICATED = "dedicated"


class AdmissionOutcomeKind(str, Enum):
    ADMITTED = "admitted"
    QUEUED = "queued"
    REJECTED = "rejected"


class AdmissionReason(str, Enum):
    """Bounded, content free reasons. Customer responses never go beyond these."""

    ADMITTED = "admitted"
    QUEUED = "queued"
    RESERVATION_ABSENT = "reservation_absent"
    RESERVATION_RELEASED = "reservation_released"
    RESERVATION_EXPIRED = "reservation_expired"
    TENANT_INACTIVE = "tenant_inactive"
    ISOLATION_UNQUALIFIED = "isolation_unqualified"
    DEDICATED_IN_SHARED_POOL = "dedicated_in_shared_pool"
    POOL_NOT_AUTHORIZED = "pool_not_authorized"
    CACHE_NAMESPACE_DENIED = "cache_namespace_denied"
    TENANT_CONCURRENCY_LIMIT = "tenant_concurrency_limit"
    DEPLOYMENT_CONCURRENCY_LIMIT = "deployment_concurrency_limit"
    POOL_CONCURRENCY_LIMIT = "pool_concurrency_limit"
    QUEUE_FULL = "queue_full"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    AUTHORIZATION_REVOKED = "authorization_revoked"
    REQUEST_ALREADY_ADMITTED = "request_already_admitted"


ADMISSION_REASONS: tuple[str, ...] = tuple(reason.value for reason in AdmissionReason)

# The reservation states a caller may report. INF1-B does not interpret them,
# it only refuses to proceed on anything that is not plainly live.
RESERVATION_PRESENT = "present"
RESERVATION_STATES: tuple[str, ...] = (RESERVATION_PRESENT, "expired", "released", "unknown")


# ---------------------------------------------------------------------------
# Bounded backpressure
# ---------------------------------------------------------------------------

# A retry indication is a hint with a ceiling, never an open ended instruction.
BASE_RETRY_AFTER_MS = 250
MAX_RETRY_AFTER_MS = 5000
# A caller may retry a bounded number of times; there is no unbounded retry.
MAX_ADMISSION_ATTEMPTS = 3
# Default bounds. A deployment may configure tighter values, never looser ones
# for the queue depth, because an unbounded queue is the failure this prevents.
DEFAULT_MAX_QUEUE_DEPTH = 32
DEFAULT_MAX_WAIT_MS = 5000


def bounded_retry_after_ms(attempt: int, *, base_ms: int = BASE_RETRY_AFTER_MS) -> int:
    """Exponential backoff with a hard ceiling and no jitter dependency.

    ``attempt`` is zero based. The value never exceeds ``MAX_RETRY_AFTER_MS`` and
    never goes negative, so a caller cannot be told to wait forever and a storm
    of immediate retries is not invited.
    """
    if attempt < 0:
        attempt = 0
    raw = base_ms * (2 ** attempt)
    return min(raw, MAX_RETRY_AFTER_MS)


def retries_exhausted(attempt: int) -> bool:
    """True once the bounded retry budget is spent."""
    return attempt >= MAX_ADMISSION_ATTEMPTS


# ---------------------------------------------------------------------------
# Placement and isolation
# ---------------------------------------------------------------------------

_TENANT_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def derive_cache_namespace(tenant_id: str, deployment_id: str | None) -> str:
    """The only cache namespace a tenant may use.

    Derived purely from trusted server facts. A caller supplied namespace is
    never consulted, so a client cannot place itself in another tenant's cache
    namespace by asking for it.
    """
    if not _TENANT_SAFE.match(tenant_id or ""):
        raise ValueError("tenant_id must be a safe opaque identifier")
    suffix = deployment_id if deployment_id and _TENANT_SAFE.match(deployment_id) else "default"
    return f"cache:tenant:{tenant_id}:dep:{suffix}"


def cache_namespace_allowed(tenant_id: str, namespace: str) -> bool:
    """A namespace belongs to exactly the tenant whose prefix it carries."""
    return isinstance(namespace, str) and namespace.startswith(f"cache:tenant:{tenant_id}:")


class ReservationHandle:
    """An opaque M3 reservation handle.

    The value is a token. It is stored and compared, never decoded, and carries
    no credit amount. This wrapper exists so that no code path can be tempted to
    treat the raw string as a number.
    """

    __slots__ = ("_token",)

    def __init__(self, token: str | None) -> None:
        self._token = token

    @property
    def present(self) -> bool:
        return bool(self._token)

    @property
    def token(self) -> str | None:
        return self._token

    def __repr__(self) -> str:  # never render the raw handle into a log line
        return "ReservationHandle(present=%s)" % self.present


# ---------------------------------------------------------------------------
# Fair dispatch order
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueuedTicket:
    ticket_id: str
    tenant_id: str
    enqueue_seq: int


def fair_dispatch_order(tickets: list[QueuedTicket]) -> list[str]:
    """Deterministic round-robin across tenants.

    The policy is documented and stable:

    1. Group waiting tickets by tenant and order each group by arrival
       (``enqueue_seq`` then ``ticket_id``).
    2. Order tenants by their earliest ticket, then by ``tenant_id`` as the
       tie-break, so two tenants that arrived together still have a fixed order.
    3. Emit round-robin: one ticket from each tenant per round.

    A single heavy tenant therefore cannot defer a light tenant beyond one turn
    per other tenant, which is the starvation guarantee INF1-B must hold.
    """
    groups: dict[str, list[QueuedTicket]] = {}
    for ticket in tickets:
        groups.setdefault(ticket.tenant_id, []).append(ticket)
    for group in groups.values():
        group.sort(key=lambda t: (t.enqueue_seq, t.ticket_id))

    tenant_order = sorted(
        groups,
        key=lambda tenant: (groups[tenant][0].enqueue_seq, tenant),
    )

    order: list[str] = []
    rounds = max((len(group) for group in groups.values()), default=0)
    for round_index in range(rounds):
        for tenant in tenant_order:
            if round_index < len(groups[tenant]):
                order.append(groups[tenant][round_index].ticket_id)
    return order


# ---------------------------------------------------------------------------
# Typed inputs and outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapacityLimits:
    """Enforced ceilings. Every value is finite; none means unbounded."""

    tenant_limit: int
    deployment_limit: int
    pool_limit: int
    max_queue_depth: int = DEFAULT_MAX_QUEUE_DEPTH
    max_wait_ms: int = DEFAULT_MAX_WAIT_MS
    lease_ttl_seconds: int = 60

    def __post_init__(self) -> None:
        for name in ("tenant_limit", "deployment_limit", "pool_limit"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.max_queue_depth < 0:
            raise ValueError("max_queue_depth must not be negative")
        if self.lease_ttl_seconds < 1:
            raise ValueError("lease_ttl_seconds must be positive")


@dataclass(frozen=True)
class AdmissionRequest:
    """One request asking for capacity, before dispatch.

    ``reservation_handle`` is opaque. ``reservation_state`` is what the caller
    reports about it; INF1-B never inspects the token itself.
    """

    tenant_id: str
    attempt_id: str
    deployment_id: str | None
    inference_mode: str
    reservation_handle: str | None
    reservation_state: str
    pool_id: str | None = None
    deadline_at: datetime | None = None
    # Deliberately present so a test can prove it is ignored: the namespace is
    # always derived from trusted server facts.
    client_cache_namespace: str | None = None


@dataclass(frozen=True)
class AdmissionResult:
    outcome: str
    reason: str
    ticket_id: str | None = None
    lease_id: str | None = None
    lease_token: str | None = None
    fence: int | None = None
    cache_namespace: str | None = None
    retry_after_ms: int | None = None
    wait_ms: int | None = None
    admitted: bool = False
    queued: bool = False
    # True when a refusal happened after the reservation was accepted, so the
    # caller must release the commercial hold it is still holding. INF1-B never
    # settles anything itself; it only surfaces the clear rejection.
    release_reservation: bool = False

    @property
    def rejected(self) -> bool:
        return self.outcome == AdmissionOutcomeKind.REJECTED.value
