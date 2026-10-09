"""Commercial entitlement vocabulary (#46 M3).

This module holds the shared, dependency-free vocabulary for the M3 commercial
foundation: plan and allowance versioning, credit and reservation states, the
soft-threshold ladder and the error types the service and API layers raise.

M3 owns commercial eligibility, allowance and credit balance, reservation value
and lifetime, the billing period and commercial settlement. It deliberately does
not own runtime capacity, admission or placement: those belong to INF1-B and
never read a plan, an allowance or a credit balance to decide capacity.

Every monetary or credit amount in this programme is an integer minor unit held
in a ``BigInteger``/``Integer`` column and a Python ``int``. No ``float`` may
participate in a balance, a reservation value, a price, a conversion or a
threshold comparison. Ratios are integer comparisons against the allowance.
"""

from __future__ import annotations

from enum import Enum


class PlanStatus(str, Enum):
    ACTIVE = "active"
    RETIRED = "retired"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class BillingPeriodStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class ReservationStatus(str, Enum):
    """Lifecycle of one commercial reservation.

    A reservation is created ``reserved``. It leaves that state exactly once:
    a completed execution ``settles`` it, a pre-dispatch failure ``releases``
    it, and an unrecovered in-flight hold ``expires`` it. None of those three
    transitions may run twice for the same reservation.
    """

    RESERVED = "reserved"
    SETTLED = "settled"
    RELEASED = "released"
    EXPIRED = "expired"


class ExecutionState(str, Enum):
    """Whether a reservation's request actually reached dispatch.

    Two different authorities govern a reservation, and conflating them is a
    defect. Pre-dispatch authority is tenant state: an inactive tenant may not
    create a reservation and may not dispatch a queued one. Post-dispatch
    authority is this recorded state: once dispatch happened while the tenant was
    active, settlement depends on the record, not on the tenant's current status.
    Otherwise a suspension after execution would make consumed inference free.
    """

    UNDISPATCHED = "undispatched"
    # Recorded immediately at the trusted model-network dispatch boundary. INF1-B
    # treats its own mark_dispatched as dispatch intent at the same boundary, so a
    # crash after this point means consumption is unknown, never zero.
    DISPATCHING = "dispatching"
    DISPATCHED = "dispatched"
    UNCERTAIN = "uncertain"


EXECUTION_STATES: tuple[str, ...] = tuple(state.value for state in ExecutionState)

# States recorded only after the dispatch boundary was crossed. The hold now
# represents at least possible consumption, so none of these may be released or
# expired as though nothing had happened.
DISPATCH_INTENT_STATES: frozenset[str] = frozenset(
    {
        ExecutionState.DISPATCHING.value,
        ExecutionState.DISPATCHED.value,
        ExecutionState.UNCERTAIN.value,
    }
)

# States whose outcome is known or explicitly unknown. Settlement is allowed from
# these and from nothing else; a still-dispatching reservation has no outcome yet.
POST_DISPATCH_STATES: frozenset[str] = frozenset(
    {ExecutionState.DISPATCHED.value, ExecutionState.UNCERTAIN.value}
)

# How long a dispatching reservation may stay without a recorded outcome before
# recovery treats it as a crash and conservatively promotes it to uncertain.
DISPATCH_STALE_SECONDS = 900


class LedgerEntryType(str, Enum):
    """Every movement of available credit is appended, never edited.

    ``hold``, ``release``, ``expiry`` and ``settlement`` move the *available*
    balance: a hold subtracts the reserved amount, a release or an expiry
    returns it, and a settlement returns the unspent remainder
    (``reserved - settled``) so the available balance ends at
    ``allowance + purchased - consumed``.
    """

    GRANT = "grant"
    PURCHASE = "purchase"
    HOLD = "hold"
    RELEASE = "release"
    EXPIRY = "expiry"
    SETTLEMENT = "settlement"
    ADJUSTMENT = "adjustment"


PLAN_STATUSES: tuple[str, ...] = tuple(s.value for s in PlanStatus)
SUBSCRIPTION_STATUSES: tuple[str, ...] = tuple(s.value for s in SubscriptionStatus)
BILLING_PERIOD_STATUSES: tuple[str, ...] = tuple(s.value for s in BillingPeriodStatus)
RESERVATION_STATUSES: tuple[str, ...] = tuple(s.value for s in ReservationStatus)
LEDGER_ENTRY_TYPES: tuple[str, ...] = tuple(e.value for e in LedgerEntryType)

# The soft-threshold ladder, reported per billing period as integer percentages
# of the allowance. These are warnings, never a hard stop: the hard cap is the
# exhaustion of the allowance plus purchased credits.
SOFT_THRESHOLDS: tuple[int, ...] = (50, 75, 90, 100)

# The only tenant status for which commercial execution is allowed. A suspended
# or revoked tenant may still read history and administration, but no new
# billable execution settles for it.
TENANT_STATUS_ACTIVE = "active"


class EntitlementsError(Exception):
    """Base class for commercial-entitlement failures."""

    code = "entitlements_error"


class HardCapExceeded(EntitlementsError):
    """The billing period's allowance plus purchased credits is exhausted.

    Raised when a *new* billable execution is refused. It never blocks reading
    history, tenant administration or recovery.
    """

    code = "hard_cap_exceeded"


class EntitlementNotConfigured(EntitlementsError):
    """No active subscription or allowance exists for the tenant."""

    code = "entitlement_not_configured"


class TenantInactive(EntitlementsError):
    """The tenant is not active, so no new commercial commitment is allowed."""

    code = "tenant_inactive"


class ReservationNotFound(EntitlementsError):
    """No reservation matches the supplied server-issued key or handle."""

    code = "reservation_not_found"


class ReservationStateError(EntitlementsError):
    """The reservation cannot make the requested transition from its state."""

    code = "reservation_state_error"


class LedgerImmutabilityError(EntitlementsError):
    """A mutation of an existing credit-ledger row was refused.

    The credit ledger is append-only: history is never rewritten. This is
    enforced by the service and the ORM event guards here, and by a database
    trigger created in migration ``0016_m3_entitlements``.
    """

    code = "ledger_immutable"
