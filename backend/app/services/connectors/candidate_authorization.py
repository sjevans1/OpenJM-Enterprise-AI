"""CA2-A: candidate authorization. Bounded traversal, exact scope, safe revalidation.

Additive. This module does not change the production authorization path and no existing
caller uses it yet; CA2-B migrates the callers after independent review.

CANDIDATE IDENTITY
------------------
A candidate is identified by the same triple the schema uses in
``uq_external_resource_identity``::

    (connector_instance_id, resource_namespace, external_id)

scoped by tenant. ``resource_namespace`` is part of that constraint and is NOT optional
decoration: two distinct rows in the same connector instance may legitimately share an
``external_id`` and differ only by namespace. Any code that keys on
``(connector_instance_id, external_id)`` alone silently collapses those two rows into one,
which is the defect this revision corrects.

Consequently the namespace participates in every identity decision in this module:
traversal ordering, the keyset predicate and cursor position, within-request
deduplication, exact-resource lookup, and the decisions that are emitted. Where a caller
supplies an identity without a namespace and more than one row matches, the request **fails
closed** with ``reference_ambiguous`` rather than picking one, because choosing arbitrarily
would answer a question the caller did not ask.

Provider-level authorization is unaffected and stays correct: the provider knows its own
``external_id``, so two namespaced rows share one provider answer while remaining distinct
rows with distinct documents. Authorizing per row and emitting per row is what keeps that
answer from collapsing two resources.

Three operations, deliberately distinct
---------------------------------------
1. :func:`authorize_candidates` — bounded, resumable **traversal** of a candidate scope.
   Best-effort and never authoritative on its own.
2. :func:`authorize_exact_candidates` — **exact scope**. A caller-supplied set of resource or
   document identities, resolved by identity and not by enumeration, so unrelated connector
   resources cannot consume the request's budget.
3. :func:`revalidate_document_ids` — **consumption-time revalidation**.

SCOPE OF THIS MODULE: authorization traversal is NOT semantic retrieval
-----------------------------------------------------------------------
This module answers exactly one question: **which documents may this principal be authorized
for?** It does not rank, score, embed, rerank or select by relevance. A "complete" coverage
result means every candidate in scope was *considered* for authorization. It does not mean the
right documents were chosen for an answer. Under D4 there is no corpus-wide pre-retrieval
relevance score (per-document Chroma collections), so ordering here is stable identity and
exists for determinism and coverage accounting, not relevance.

Why this exists
---------------
``authorized_connector_document_ids`` answers "which documents is this principal authorized
for" by enumerating the corpus, so it needs a bound, and a bound over an enumerated set
silently omits authorized documents. Measured on the frozen baseline (``main`` at
``8d6b7d8``): 150 resources all authorized produced exactly 100 results, 50 authorized
documents unreachable, and **zero denials recorded for the 50**, because an unexamined
candidate was indistinguishable from a denied one.

DENIED versus UNAVAILABLE
-------------------------
::

    allowed                     -> AUTHORIZED
    scope == PRINCIPAL          -> DENIED       authorization is definitively refused
    scope == REQUEST            -> UNAVAILABLE  could not be proven on this request
    scope == CONNECTOR          -> UNAVAILABLE  cannot be proven for anyone
    scope == CONTENT            -> UNAVAILABLE  the content is not servable

DENIED means *definitive, principal-specific refusal*. It does **not** assert that every denial
came directly from the provider: an absent provider-user mapping is refused locally because the
absence is definitive knowledge, not uncertainty. Everything unproven, including outages,
timeouts, rate limits and contradictory responses, is UNAVAILABLE.

Traversal completeness is BEST EFFORT and is never authoritative on its own
----------------------------------------------------------------------------
* ``snapshot_consistent`` — True only when the candidate set came from a single database read,
  so no drift between pages is possible. Any resumable traversal reports False.
* ``authoritative`` — True only when coverage is COMPLETE, ``snapshot_consistent`` is True and
  ``unresolved`` is False. It describes **traversal-level completeness** and is NOT sufficient
  for a pinned report; authoritative outputs gate on :func:`require_strict_scope`.

Strict pinned-report authorization versus traversal
---------------------------------------------------
* ``coverage`` asks whether every requested source was **decided**.
* ``strict_scope_satisfied`` asks whether every requested source was **authorized**.

A pinned report that requested 65 sources, received a COMPLETE decision pass containing three
denials, and emitted 62 has silently excluded three. It must fail loudly instead, which is what
:func:`require_strict_scope` does: it returns the authorized set only when the scope is EXACT
and strictly satisfied, and raises :class:`StrictScopeError` otherwise. A catalog traversal
never satisfies a strict requirement, even when it completes in a single page.

Namespace and provider permission evidence
------------------------------------------
Identity includes ``resource_namespace``, but permission evidence does not. The established
provider authorization API answers about a provider ``external_id`` and has no notion of a
namespace, so its answer cannot distinguish the canonical row from a row that merely shares that
external_id under another namespace.

The canonical namespace for a connector instance is ``resource_namespace(instance_id)`` from
:mod:`app.services.connectors.service`, which production ingestion uses for every row an instance
owns. CA2-A therefore authorizes a row only when its namespace is that canonical value. Any other
namespace fails closed as UNAVAILABLE with ``namespace_not_canonical``, without a provider call
and without spending budget, rather than treating an external_id answer as permission to serve
alternate-namespace content.

This refuses an unsupported namespace combination. It does not redesign the provider API, change
CA1, or alter production ingestion. It does mean that synthetically retagged rows are enumerated
correctly, because they are genuinely distinct candidates, while remaining unauthorizable,
because their permission has not been proven.

Every input and every provider budget is finite
-----------------------------------------------
Traversal pages are bounded by ``budget`` provider checks and a budget-proportional SQL window,
and the parameters themselves carry ceilings (``MAX_PAGE_BUDGET``, ``MAX_SCAN_ROWS``) so a single
page cannot be asked for unbounded work. Exact scope is bounded by ``MAX_EXACT_INPUTS`` requested
identities, ``MAX_EXACT_RESOLVED_ROWS`` resolved rows, a finite ``DEFAULT_EXACT_BUDGET`` and a hard
``MAX_EXACT_BUDGET`` that is refused rather than clamped. Exact-source identity reads apply their
row bound inside the SQL, so an oversized match set is refused from a bounded result rather than
after materialising an unbounded one, and a truncated result is never treated as a small set of
matches. No path produces unbounded provider calls or unbounded database work.

The continuation cursor is opaque and authenticated-encrypted (Fernet, AEAD), not a readable
signed blob, because a base64 payload would disclose tenant, principal and resource
identifiers. There is deliberately no durable authorization cache and no per-principal
scan-position record: continuation is stateless and authorization is revalidated on every page.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Sequence

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import (
    CONNECTOR_STATUS_ACTIVE,
    ConnectorInstance,
    Document,
    EXTERNAL_STATE_ACTIVE,
    ExternalResource,
)
from app.services.connectors.authorization import (
    AuthorizationOutcome,
    DenialScope,
    authorize_resource,
)
from app.services.connectors.service import resource_namespace as canonical_resource_namespace
from app.services.document_lifecycle import retrievable_filter
from app.services.document_policy import DocumentAccess, document_is_visible

# v4: the cursor position is a 3-tuple identity including resource_namespace. A v3 cursor must
# not be reinterpreted under the new identity model, so the version is bumped and old tokens are
# rejected outright.
CURSOR_VERSION = 4
CURSOR_CONTEXT = b"openjm:ca2:candidate-cursor:v4"

# Defaults are EXPERIMENTAL. They are not product limits and they are below the current
# production bound of 100, so nothing here may be enabled as a default before the Phase 4
# comparison proves it does not reduce coverage. See the plan, section 3.
DEFAULT_PAGE_BUDGET = 32
DEFAULT_MAX_SCAN_ROWS = 512
DEFAULT_CURSOR_TTL_SECONDS = 900

# Exact-source budgets are ALWAYS finite, so an omitted or oversized budget can never turn into
# unbounded provider calls. The default covers the existing 65 to 100 source report shapes with
# headroom; the maximum is a hard refusal point, not a silent clamp.
DEFAULT_EXACT_BUDGET = 128
MAX_EXACT_BUDGET = 256

# Finite bounds on exact-source input size and database work.
MAX_EXACT_INPUTS = 512
MAX_EXACT_RESOLVED_ROWS = 4096
IDENTITY_CHUNK = 256

# Sensible maximums for traversal parameters. These are ceilings on a single page, not defaults
# and not production limits: the defaults above (32 and 512) are unaffected and the current
# production bound of 100 stays below both.
MAX_PAGE_BUDGET = 1024
MAX_SCAN_ROWS = 4096

# A page fetches at most this multiple of its provider budget, so a small budget does not drag a
# large window out of the database. This can only affect how many pages a traversal takes: the
# cursor advances only past handled rows, so shrinking the window never skips a locally
# ineligible, denied or otherwise unexamined candidate.
SCAN_WINDOW_MULTIPLIER = 2

REASON_BUDGET = "budget_exhausted"
REASON_SCAN_BOUND = "scan_bound_reached"
REASON_UNAVAILABLE = "authorization_unavailable"
REASON_CORPUS_CHANGED = "corpus_changed"
REASON_REFERENCE_UNRESOLVED = "reference_unresolved"
REASON_REFERENCE_AMBIGUOUS = "reference_ambiguous"
REASON_REFERENCE_EXCLUDED = "reference_excluded_locally"
REASON_NAMESPACE_NOT_CANONICAL = "namespace_not_canonical"

# Strict exact-scope failure reasons. Precedence is budget, ambiguous, unavailable, denied, then
# local policy, so the most structural cause is reported first.
REASON_STRICT_BUDGET = "strict_budget_incomplete"
REASON_STRICT_AMBIGUOUS = "strict_ambiguous_identity"
REASON_STRICT_UNAVAILABLE = "strict_unavailable"
REASON_STRICT_DENIED = "strict_denied"
REASON_STRICT_LOCAL_POLICY = "strict_local_policy_excluded"
REASON_STRICT_NOT_APPLICABLE = "strict_not_applicable_to_traversal"


class CandidateState(str, Enum):
    """What became of a candidate. Conflating any two of these was the old defect."""

    AUTHORIZED = "authorized"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    UNEXAMINED = "unexamined"
    # Not one of the four authorization states: a row the local policy excludes before any
    # provider budget is spent. It carries no authorization meaning and is recorded so that scan
    # progress is unambiguous.
    LOCAL_POLICY_EXCLUDED = "local_policy_excluded"


class CoverageStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class ScopeKind(str, Enum):
    """How the candidate set was determined. It decides which success condition applies."""

    # A catalog traversal: bounded, resumable, a best-effort view of a scope. It never carries a
    # caller-supplied list of sources, so it can never satisfy a strict pinned-report
    # requirement.
    TRAVERSAL = "traversal"
    # An exact-source operation: the caller named the sources. Only this kind can be strict.
    EXACT = "exact"


class StrictScopeError(Exception):
    """A strict exact-scope requirement was not met.

    Raised rather than returning a subset, so an authoritative output cannot silently exclude a
    denied, missing or locally restricted source.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class CursorError(Exception):
    """A continuation cursor that cannot be trusted. Always fail closed."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CandidateRef:
    """An explicitly identified candidate. Never an enumeration.

    ``resource_namespace`` is optional only for caller convenience. Omitting it is safe but may
    be ambiguous, and an ambiguous identity fails closed rather than resolving to an arbitrary
    namespace.
    """

    connector_instance_id: str
    external_id: str
    resource_namespace: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("connector_instance_id", self.connector_instance_id),
            ("external_id", self.external_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.resource_namespace is not None and (
            not isinstance(self.resource_namespace, str) or not self.resource_namespace.strip()
        ):
            raise ValueError("resource_namespace must be a non-empty string when given")


@dataclass(frozen=True)
class CandidateDecision:
    """One candidate's outcome. Only AUTHORIZED carries a document id.

    ``resource_namespace`` is part of the reported identity, so two rows that share an
    ``external_id`` are distinguishable in the result instead of collapsing into one entry.
    """

    connector_instance_id: str
    external_id: str
    state: CandidateState
    reason: str = ""
    document_id: str | None = None
    resource_namespace: str = ""

    def __post_init__(self) -> None:
        if self.state is not CandidateState.AUTHORIZED and self.document_id is not None:
            raise ValueError("only an authorized candidate may carry a document id")


@dataclass(frozen=True)
class CandidateAuthorizationResult:
    """The CA2-A contract result.

    ``coverage`` and ``coverage_reason`` are the only fields a caller may surface to an end user,
    and only through :meth:`user_facing_summary`. The counts are internal: exposing denied or
    unexamined counts would leak resource existence and volume.
    """

    decisions: tuple[CandidateDecision, ...] = ()
    coverage: CoverageStatus = CoverageStatus.COMPLETE
    coverage_reason: str = ""
    cursor: Optional[str] = None
    examined: int = 0
    provider_checks: int = 0
    rows_fetched: int = 0
    sql_reads: int = 0
    unresolved: bool = False
    snapshot_consistent: bool = False
    scope_kind: ScopeKind = ScopeKind.TRAVERSAL
    strict_scope_satisfied: bool = False
    strict_exclusion_reason: str = ""

    @property
    def authorized_document_ids(self) -> frozenset[str]:
        """Currently authorized ids from THIS evaluation.

        Point in time. An authoritative consumer must never combine these across pages, or reuse
        them later, without :func:`revalidate_document_ids`.
        """
        return frozenset(
            d.document_id
            for d in self.decisions
            if d.state is CandidateState.AUTHORIZED and d.document_id
        )

    def counts(self) -> dict[str, int]:
        """Internal only. Never place these counts in user-facing output."""
        out = {s.value: 0 for s in CandidateState}
        for d in self.decisions:
            out[d.state.value] += 1
        return out

    @property
    def authorization_incomplete(self) -> bool:
        """True when coverage is partial."""
        return self.coverage is CoverageStatus.PARTIAL

    @property
    def authoritative(self) -> bool:
        """Traversal-level completeness. NOT sufficient for a pinned report.

        Requires COMPLETE coverage, a single-read candidate set, and nothing unresolved. A
        resumable traversal always fails this, which is the point: a best-effort walk must not be
        mistaken for complete authorization.

        Deliberately weaker than ``strict_scope_satisfied``. It describes whether a candidate set
        was fully walked, not whether a caller's requested sources were all authorized, so
        authoritative outputs must gate on :func:`require_strict_scope` instead.
        """
        return (
            self.coverage is CoverageStatus.COMPLETE
            and self.snapshot_consistent
            and not self.unresolved
        )

    def user_facing_summary(self) -> dict[str, Any]:
        """The only shape safe to expose. No counts, titles, ids or provider detail."""
        return {
            "coverage": self.coverage.value,
            "partial": self.authorization_incomplete,
            "reason": self.coverage_reason,
        }


def _resource_key(resource: ExternalResource) -> tuple[str, str, str]:
    """The schema identity of a resource row. The only deduplication key used here."""
    return (
        resource.connector_instance_id,
        resource.resource_namespace,
        resource.external_id,
    )


# --------------------------------------------------------------------------- cursor


def _cursor_cipher() -> Fernet:
    """Authenticated encryption for cursors, keyed from the existing server secret.

    Uses the same server-owned secret source as the credential vault, with a distinct context so
    the two cryptographic uses are separated. Rotating that secret invalidates outstanding
    cursors, which is acceptable precisely because a cursor carries no authority: every page
    revalidates.

    Fails closed when no server secret is available rather than inventing one.
    """
    from app.services.credentials import credential_vault

    material = getattr(credential_vault, "_key", None)
    if not material:
        raise CursorError(
            "cursor_key_unavailable",
            "no server credential key is configured, so cursors cannot be sealed",
        )
    derived = hmac.new(bytes(material), CURSOR_CONTEXT, hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _query_fingerprint(query: str) -> str:
    return hashlib.sha256((query or "").strip().lower().encode()).hexdigest()[:32]


def _scope_fingerprint(connector_instance_ids: Sequence[str] | None) -> str:
    material = (
        "*"
        if connector_instance_ids is None
        else "\n".join(sorted(str(i) for i in connector_instance_ids))
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def encode_cursor(
    *,
    tenant_id: str,
    principal_id: str,
    query: str,
    after: list[str] | None,
    connector_instance_ids: Sequence[str] | None = None,
    corpus_digest: str = "",
    unresolved: bool = False,
    now: float | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Seal a scan position bound to tenant, principal, query and the candidate scope.

    ``after`` is a full candidate identity triple. A two-element position is rejected rather than
    widened, because a truncated identity cannot address a namespaced candidate uniquely.
    """
    settings = get_settings()
    ttl = (
        ttl_seconds
        or getattr(settings, "candidate_cursor_ttl_seconds", None)
        or DEFAULT_CURSOR_TTL_SECONDS
    )
    issued = now if now is not None else time.time()
    payload = {
        "v": CURSOR_VERSION,
        "t": tenant_id,
        "p": principal_id,
        "q": _query_fingerprint(query),
        "s": _scope_fingerprint(connector_instance_ids),
        "d": corpus_digest,
        "u": bool(unresolved),
        "k": list(after) if after else None,
        "iat": int(issued),
        "exp": int(issued + ttl),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return _cursor_cipher().encrypt(raw).decode()


def decode_cursor(
    cursor: str,
    *,
    tenant_id: str,
    principal_id: str,
    query: str,
    connector_instance_ids: Sequence[str] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Authenticate and unpack a cursor. Raises :class:`CursorError` on anything untrusted."""
    if not cursor or not isinstance(cursor, str):
        raise CursorError("cursor_invalid", "empty cursor")
    try:
        raw = _cursor_cipher().decrypt(cursor.encode())
    except InvalidToken as exc:
        # Tampering, truncation and a foreign key all land here, indistinguishably.
        raise CursorError("cursor_invalid", "token is not authentic") from exc
    try:
        payload = json.loads(raw.decode())
    except Exception as exc:  # noqa: BLE001
        raise CursorError("cursor_invalid", "unreadable payload") from exc
    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise CursorError("cursor_invalid", "unsupported version")

    moment = now if now is not None else time.time()
    if int(payload.get("exp") or 0) <= int(moment):
        raise CursorError("cursor_expired", "cursor is past its expiry")
    # Scope checks are separate codes so a caller can distinguish tampering from reuse.
    if payload.get("t") != tenant_id:
        raise CursorError("cursor_scope_mismatch", "tenant does not match")
    if payload.get("p") != principal_id:
        raise CursorError("cursor_scope_mismatch", "principal does not match")
    if payload.get("q") != _query_fingerprint(query):
        raise CursorError("cursor_query_mismatch", "query does not match")
    if payload.get("s") != _scope_fingerprint(connector_instance_ids):
        raise CursorError("cursor_scope_mismatch", "candidate scope does not match")

    key = payload.get("k")
    if key is not None and (
        not isinstance(key, list)
        or len(key) != 3
        or not all(isinstance(x, str) for x in key)
    ):
        raise CursorError("cursor_invalid", "malformed identity position")
    return payload


# ----------------------------------------------------------------- authorizing a batch


def _state_for(outcome: AuthorizationOutcome) -> tuple[CandidateState, str]:
    """Map a CA1 outcome onto a candidate state. Total, and pinned by a test.

    DENIED means definitive, principal-specific refusal. It includes a locally detected missing
    provider-user mapping, which is refused without a provider call because the absence is
    definitive rather than uncertain. Every other failure to authorize is UNAVAILABLE.
    Unexamined is never produced here: it is the state of a candidate the walk has not reached,
    and it is never reported as denied.
    """
    if outcome.allowed:
        return CandidateState.AUTHORIZED, ""
    if outcome.scope is DenialScope.PRINCIPAL:
        return CandidateState.DENIED, outcome.reason
    return CandidateState.UNAVAILABLE, outcome.reason


def _candidate_filters(
    access: DocumentAccess, connector_instance_ids: Sequence[str] | None
) -> list[Any]:
    filters: list[Any] = [
        ExternalResource.tenant_id == access.tenant_id,
        ExternalResource.lifecycle_state == EXTERNAL_STATE_ACTIVE,
        ExternalResource.document_id.is_not(None),
        ConnectorInstance.tenant_id == access.tenant_id,
        ConnectorInstance.enabled.is_(True),
        ConnectorInstance.status == CONNECTOR_STATUS_ACTIVE,
    ]
    if connector_instance_ids is not None:
        ids = [str(i) for i in connector_instance_ids]
        if ids:
            filters.append(ExternalResource.connector_instance_id.in_(ids))
        else:
            # An explicitly empty scope matches nothing rather than everything.
            filters.append(ExternalResource.connector_instance_id.is_(None))
    return filters


def _candidate_query(access: DocumentAccess, connector_instance_ids: Sequence[str] | None):
    """The traversal query. Ordered by the FULL identity triple, not a prefix of it.

    Ordering by ``(connector_instance_id, external_id)`` alone is not a total order once two
    namespaces may share an ``external_id``, so a keyset page boundary could skip or repeat rows.
    """
    return (
        select(ExternalResource)
        .join(ConnectorInstance, ConnectorInstance.id == ExternalResource.connector_instance_id)
        .where(*_candidate_filters(access, connector_instance_ids))
        .order_by(
            ExternalResource.connector_instance_id,
            ExternalResource.resource_namespace,
            ExternalResource.external_id,
        )
    )


def _after_identity(after: list[str]):
    """The keyset predicate for a full identity triple."""
    instance_id, namespace, external_id = after
    return or_(
        ExternalResource.connector_instance_id > instance_id,
        and_(
            ExternalResource.connector_instance_id == instance_id,
            ExternalResource.resource_namespace > namespace,
        ),
        and_(
            ExternalResource.connector_instance_id == instance_id,
            ExternalResource.resource_namespace == namespace,
            ExternalResource.external_id > external_id,
        ),
    )


async def _corpus_digest(
    db: AsyncSession, access: DocumentAccess, connector_instance_ids: Sequence[str] | None
) -> tuple[str, int]:
    """A version marker for the in-scope candidate set. BEST EFFORT, not proof.

    Detects: additions (count and the timestamp watermark), removals (count), any edit to the
    ``ExternalResource`` row itself (the ``updated_at`` watermark), and a balanced middle
    replacement that preserves count and key extrema (the watermark moves when the replacement
    row is written). Extrema cover the full identity triple, so a namespace-only change moves it.

    Does NOT detect, and must not be claimed to detect:

    * a replacement whose new row carries a timestamp at or below the current watermark, possible
      only if timestamps are set backwards, for example a backdated import;
    * a change to a ``Document``'s governed policy fields. ``Document`` has no ``updated_at``
      column at all, only ``created_at``, so no cheap aggregate can see such an edit. Those
      changes are handled by re-evaluating local eligibility on every page and by
      consumption-time revalidation for anything decided on an earlier page.

    **An unchanged digest is not proof of an unchanged corpus.** ``snapshot_consistent`` and
    ``authoritative`` exist so a resumable traversal is never read as authoritative regardless.
    """
    row = (
        await db.execute(
            select(
                func.count(ExternalResource.id),
                func.max(ExternalResource.updated_at),
                func.max(ExternalResource.created_at),
                func.min(ExternalResource.connector_instance_id),
                func.max(ExternalResource.connector_instance_id),
                func.min(ExternalResource.resource_namespace),
                func.max(ExternalResource.resource_namespace),
                func.min(ExternalResource.external_id),
                func.max(ExternalResource.external_id),
            )
            .select_from(ExternalResource)
            .join(ConnectorInstance, ConnectorInstance.id == ExternalResource.connector_instance_id)
            .where(*_candidate_filters(access, connector_instance_ids))
        )
    ).one()
    material = "|".join("" if v is None else str(v) for v in row)
    return hashlib.sha256(material.encode()).hexdigest()[:32], 1


async def _local_eligible(
    db: AsyncSession, access: DocumentAccess, resource: ExternalResource
) -> bool:
    """Local eligibility, evaluated BEFORE any provider budget is spent."""
    if not resource.document_id:
        return False
    document = (
        await db.execute(
            select(Document).where(
                Document.id == resource.document_id,
                Document.tenant_id == access.tenant_id,
                *retrievable_filter(),
            )
        )
    ).scalars().first()
    if document is None:
        return False
    return document_is_visible(access, document)


async def _decide(
    db: AsyncSession, access: DocumentAccess, resource: ExternalResource
) -> tuple[CandidateDecision, bool]:
    """Authorize one resolved row. Returns the decision and whether it was unavailable."""
    outcome = await authorize_resource(
        db,
        tenant_id=access.tenant_id,
        connector_instance_id=resource.connector_instance_id,
        resource=resource,
        principal_id=access.principal_id,
    )
    state, reason = _state_for(outcome)
    return (
        CandidateDecision(
            connector_instance_id=resource.connector_instance_id,
            resource_namespace=resource.resource_namespace,
            external_id=resource.external_id,
            state=state,
            reason=reason,
            document_id=resource.document_id if state is CandidateState.AUTHORIZED else None,
        ),
        state is CandidateState.UNAVAILABLE,
    )


def _namespace_is_canonical(resource: ExternalResource) -> bool:
    """Whether this row carries the namespace the connector framework assigns it.

    The canonical invariant is ``resource_namespace(connector_instance_id)`` from
    :mod:`app.services.connectors.service`, which ingestion uses to name every row a connector
    instance owns, and which ``uq_external_resource_identity`` then includes in the identity.

    This matters for permission evidence, not merely for tidiness. The established provider
    authorization API answers about a provider ``external_id`` and has no notion of a namespace.
    For a row whose namespace is not the canonical one for its instance, that answer is therefore
    not sufficient evidence to serve **that row's** content, and CA2-A must not convert it into
    an authorization. Such rows fail closed without a provider call, at no budget cost.

    This does not redesign the provider API, change CA1, or touch production ingestion; it
    refuses to treat an unsupported namespace combination as authorization.
    """
    return resource.resource_namespace == canonical_resource_namespace(
        resource.connector_instance_id
    )


async def _bounded_identity_read(
    db: AsyncSession, statement, *, budget_remaining: int
) -> tuple[list[ExternalResource], bool]:
    """One identity read under an explicit row budget.

    The bound is applied in SQL, so an oversized match set is detected from a bounded result
    rather than after materialising an unbounded one. A truncated result is never used to decide
    uniqueness or to answer: the caller must treat overflow as a refusal, not as a smaller set of
    matches.
    """
    rows = list((await db.execute(statement.limit(budget_remaining + 1))).scalars().all())
    return rows, len(rows) > budget_remaining


async def authorize_candidates(
    db: AsyncSession,
    *,
    access: DocumentAccess,
    query: str,
    budget: int = DEFAULT_PAGE_BUDGET,
    cursor: str | None = None,
    max_scan_rows: int = DEFAULT_MAX_SCAN_ROWS,
    connector_instance_ids: Sequence[str] | None = None,
    now: float | None = None,
) -> CandidateAuthorizationResult:
    """Bounded, resumable traversal of a candidate scope. Best effort, never authoritative.

    Each page walks candidates in stable **full-identity** order, applies local policy before
    spending provider budget, and stops when the budget is spent or the SQL scan window is
    reached. The cursor advances only past *handled* rows, so no eligible candidate is skipped
    and paging reaches every authorized candidate, including namespaced rows that share an
    ``external_id`` and including ones beyond the first window whose titles do not match the
    query.

    The result is never ``authoritative``: it did not come from a single snapshot, and earlier
    pages cannot be guaranteed still valid.
    """
    if budget <= 0:
        raise ValueError("budget must be positive")
    if budget > MAX_PAGE_BUDGET:
        raise ValueError(
            f"budget {budget} exceeds MAX_PAGE_BUDGET {MAX_PAGE_BUDGET}; a single page is bounded"
        )
    if max_scan_rows <= 0:
        raise ValueError("max_scan_rows must be positive")
    if max_scan_rows > MAX_SCAN_ROWS:
        raise ValueError(
            f"max_scan_rows {max_scan_rows} exceeds MAX_SCAN_ROWS {MAX_SCAN_ROWS}; "
            "a single page is bounded"
        )

    sealed = (
        decode_cursor(
            cursor,
            tenant_id=access.tenant_id,
            principal_id=access.principal_id,
            query=query,
            connector_instance_ids=connector_instance_ids,
            now=now,
        )
        if cursor
        else None
    )
    after: list[str] | None = list(sealed["k"]) if sealed and sealed.get("k") else None
    inherited_unresolved = bool(sealed.get("u")) if sealed else False
    inherited_digest = str(sealed.get("d") or "") if sealed else ""

    digest, digest_reads = await _corpus_digest(db, access, connector_instance_ids)
    corpus_changed = bool(sealed) and inherited_digest != digest

    # Budget-proportional window, capped by the hard scan bound. One extra row is fetched only so
    # that "there is more" is answerable without counting the corpus.
    window = min(max_scan_rows, max(budget, budget * SCAN_WINDOW_MULTIPLIER))
    statement = _candidate_query(access, connector_instance_ids).limit(window + 1)
    if after:
        statement = statement.where(_after_identity(after))

    decisions: list[CandidateDecision] = []
    seen: set[tuple[str, str, str]] = set()
    checks = 0
    scanned = 0
    handled: list[str] | None = after
    page_unavailable = False
    stop_reason = ""

    rows: list[ExternalResource] = list((await db.execute(statement)).scalars().all())
    rows_fetched = len(rows)
    at_scan_bound = rows_fetched > window

    for resource in rows[:window]:
        if checks >= budget:
            stop_reason = REASON_BUDGET
            break
        scanned += 1

        key = _resource_key(resource)
        if key in seen:
            continue
        seen.add(key)

        if not await _local_eligible(db, access, resource):
            decisions.append(
                CandidateDecision(
                    connector_instance_id=resource.connector_instance_id,
                    resource_namespace=resource.resource_namespace,
                    external_id=resource.external_id,
                    state=CandidateState.LOCAL_POLICY_EXCLUDED,
                    reason="local_policy_excluded",
                )
            )
            handled = list(key)
            continue

        # Permission evidence gate. The provider answers about an external_id and knows nothing
        # of namespaces, so a row outside its instance's canonical namespace cannot be
        # authorized on that answer. Fails closed, and spends no provider budget.
        if not _namespace_is_canonical(resource):
            decisions.append(
                CandidateDecision(
                    connector_instance_id=resource.connector_instance_id,
                    resource_namespace=resource.resource_namespace,
                    external_id=resource.external_id,
                    state=CandidateState.UNAVAILABLE,
                    reason=REASON_NAMESPACE_NOT_CANONICAL,
                )
            )
            page_unavailable = True
            handled = list(key)
            continue

        decision, unavailable = await _decide(db, access, resource)
        checks += 1
        page_unavailable = page_unavailable or unavailable
        decisions.append(decision)
        handled = list(key)

    if at_scan_bound and not stop_reason:
        stop_reason = REASON_SCAN_BOUND

    exhausted = not stop_reason
    # Sticky: sealed into the next cursor so a later, cleaner page cannot claim completeness.
    unresolved = inherited_unresolved or page_unavailable or corpus_changed

    if exhausted:
        if page_unavailable or inherited_unresolved:
            coverage, reason = CoverageStatus.PARTIAL, REASON_UNAVAILABLE
        elif corpus_changed:
            coverage, reason = CoverageStatus.PARTIAL, REASON_CORPUS_CHANGED
        else:
            coverage, reason = CoverageStatus.COMPLETE, ""
        next_cursor = None
    else:
        coverage, reason = CoverageStatus.PARTIAL, stop_reason
        next_cursor = encode_cursor(
            tenant_id=access.tenant_id,
            principal_id=access.principal_id,
            query=query,
            after=handled,
            connector_instance_ids=connector_instance_ids,
            corpus_digest=digest,
            unresolved=unresolved,
            now=now,
        )

    # A traversal is snapshot-consistent only if it never continued: one page, one candidate
    # read. Any resumption makes cross-page drift unprovable, whatever the digest says.
    snapshot_consistent = not sealed and exhausted

    return CandidateAuthorizationResult(
        decisions=tuple(decisions),
        coverage=coverage,
        coverage_reason=reason,
        cursor=next_cursor,
        examined=scanned,
        provider_checks=checks,
        rows_fetched=rows_fetched,
        sql_reads=digest_reads + 1,
        unresolved=unresolved,
        snapshot_consistent=snapshot_consistent,
        # A traversal is never a strict exact-source result, even when it finishes in a single
        # page: it has no caller-supplied list of sources, so it cannot satisfy a pinned-report
        # requirement, and saying so explicitly is what stops one qualifying by accident.
        scope_kind=ScopeKind.TRAVERSAL,
        strict_scope_satisfied=False,
        strict_exclusion_reason=REASON_STRICT_NOT_APPLICABLE,
    )


def _chunks(items: Sequence[Any], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def authorize_exact_candidates(
    db: AsyncSession,
    *,
    access: DocumentAccess,
    resource_refs: Sequence[CandidateRef] | None = None,
    document_ids: Sequence[str] | None = None,
    budget: int | None = None,
) -> CandidateAuthorizationResult:
    """Authorize an explicitly identified set of candidates. No enumeration.

    For pinned reports, report snapshots, exports and exact-source operations. Rows are resolved
    by identity, so resources outside the requested set never consume the request's
    authorization budget.

    Identity handling:

    * A reference carrying ``resource_namespace`` is resolved on the full identity triple.
    * A reference without one is resolved on ``(instance, external_id)``, and if more than one
      namespace matches it **fails closed** as ``reference_ambiguous`` without spending provider
      budget, because picking a namespace would answer a question the caller did not ask.
    * A ``document_id`` that resolves to more than one row fails closed the same way.

    Each distinct row is authorized at most once per call, so specifying the same resource by
    both reference and document id costs one provider check while still producing a decision for
    every requested identity.

    Malformed references raise. A reference that does not resolve inside this tenant, or that is
    not currently an eligible resource, is reported UNAVAILABLE without spending provider budget,
    and the same answer is given whether the identity is absent or belongs to another tenant.

    Input and work are bounded by ``MAX_EXACT_INPUTS`` and ``MAX_EXACT_RESOLVED_ROWS``; the
    provider budget is always finite (``DEFAULT_EXACT_BUDGET`` when omitted, refused above
    ``MAX_EXACT_BUDGET``). Coverage is COMPLETE only if every requested identity was decided, and
    ``strict_scope_satisfied`` only if every requested identity was actually **authorized**.
    Authoritative consumers read the result through :func:`require_strict_scope`.
    """
    if resource_refs is None and document_ids is None:
        raise ValueError("provide resource_refs or document_ids")
    if budget is not None and budget <= 0:
        raise ValueError("budget must be positive")
    if budget is not None and budget > MAX_EXACT_BUDGET:
        raise ValueError(
            f"budget {budget} exceeds MAX_EXACT_BUDGET {MAX_EXACT_BUDGET}; "
            "exact-source authorization is bounded by design"
        )
    effective_budget = budget if budget is not None else DEFAULT_EXACT_BUDGET

    refs: list[CandidateRef] = list(resource_refs or [])
    ids: list[str] = []
    for value in document_ids or []:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("document_ids must be non-empty strings")
        ids.append(value)

    # Deduplicate requested identities so a repeated request cannot inflate the answer.
    ref_keys = sorted(
        {(r.connector_instance_id, r.resource_namespace or "", r.external_id) for r in refs}
    )
    namespaced_refs = sorted({k for k in ref_keys if k[1]})
    bare_refs = sorted({(k[0], k[2]) for k in ref_keys if not k[1]})
    doc_keys = sorted(set(ids))
    requested = len(ref_keys) + len(doc_keys)
    if requested == 0:
        raise ValueError("no candidate identities provided")
    if requested > MAX_EXACT_INPUTS:
        raise ValueError(
            f"{requested} requested identities exceed MAX_EXACT_INPUTS {MAX_EXACT_INPUTS}; "
            "exact-source authorization is bounded by design"
        )

    rows: list[ExternalResource] = []
    reads = 0

    def _identity_select():
        return (
            select(ExternalResource)
            .join(ConnectorInstance, ConnectorInstance.id == ExternalResource.connector_instance_id)
            .where(ExternalResource.tenant_id == access.tenant_id)
        )

    async def _resolve(statement) -> None:
        """One bounded read. Raises if the row budget would be exceeded.

        The bound is in the SQL, so an oversized match set is refused from a bounded result
        instead of after materialising an unbounded one, and a truncated result is never used to
        decide uniqueness or to answer.
        """
        nonlocal reads
        fetched, overflowed = await _bounded_identity_read(
            db, statement, budget_remaining=MAX_EXACT_RESOLVED_ROWS - len(rows)
        )
        reads += 1
        if overflowed:
            raise ValueError(
                "identity resolution exceeded MAX_EXACT_RESOLVED_ROWS "
                f"{MAX_EXACT_RESOLVED_ROWS} and was refused before materialising the full match "
                "set; split the request"
            )
        rows.extend(fetched)

    for chunk in _chunks(namespaced_refs, IDENTITY_CHUNK):
        await _resolve(
            _identity_select().where(
                tuple_(
                    ExternalResource.connector_instance_id,
                    ExternalResource.resource_namespace,
                    ExternalResource.external_id,
                ).in_(chunk)
            )
        )
    for chunk in _chunks(bare_refs, IDENTITY_CHUNK):
        await _resolve(
            _identity_select().where(
                tuple_(
                    ExternalResource.connector_instance_id,
                    ExternalResource.external_id,
                ).in_(chunk)
            )
        )
    for chunk in _chunks(doc_keys, IDENTITY_CHUNK):
        await _resolve(_identity_select().where(ExternalResource.document_id.in_(chunk)))

    # De-duplicate resolved rows by row id. The same row legitimately satisfies more than one
    # resolution query (a caller may name it by reference AND by document id), and counting it
    # twice would read as two distinct namespaces and be misreported as an ambiguous identity.
    unique_rows: list[ExternalResource] = []
    seen_row_ids: set[str] = set()
    for row in rows:
        if row.id in seen_row_ids:
            continue
        seen_row_ids.add(row.id)
        unique_rows.append(row)
    rows = unique_rows

    by_triple: dict[tuple[str, str, str], ExternalResource] = {}
    by_bare: dict[tuple[str, str], list[ExternalResource]] = {}
    by_document: dict[str, list[ExternalResource]] = {}
    for row in rows:
        by_triple.setdefault(_resource_key(row), row)
        by_bare.setdefault((row.connector_instance_id, row.external_id), []).append(row)
        if row.document_id:
            by_document.setdefault(row.document_id, []).append(row)

    decisions: list[CandidateDecision] = []
    checks = 0
    unavailable = False
    ambiguous = False
    # Per-row decision cache: one provider check per distinct row, however many requested
    # identities point at it, so a resource named by both reference and document id is checked
    # once.
    row_outcome: dict[tuple[str, str, str], tuple[CandidateState, str, str | None] | None] = {}
    undecided = 0

    async def _outcome_for(resource: ExternalResource):
        """Resolve one row, at most once per call. None means the budget cannot decide it."""
        nonlocal checks, unavailable
        key = _resource_key(resource)
        if key in row_outcome:
            return row_outcome[key]
        if checks >= effective_budget:
            return None
        if not await _local_eligible(db, access, resource):
            row_outcome[key] = (CandidateState.LOCAL_POLICY_EXCLUDED, REASON_REFERENCE_EXCLUDED, None)
            return row_outcome[key]
        # Same permission-evidence gate as the traversal: the provider answers about an
        # external_id, so a row outside its instance's canonical namespace cannot be authorized
        # on that answer. Fails closed, and spends no provider budget.
        if not _namespace_is_canonical(resource):
            row_outcome[key] = (
                CandidateState.UNAVAILABLE,
                REASON_NAMESPACE_NOT_CANONICAL,
                None,
            )
            unavailable = True
            return row_outcome[key]
        decision, was_unavailable = await _decide(db, access, resource)
        checks += 1
        unavailable = unavailable or was_unavailable
        row_outcome[key] = (decision.state, decision.reason, decision.document_id)
        return row_outcome[key]

    def _emit(instance_id: str, namespace: str, external_id: str, outcome) -> None:
        state, reason, document_id = outcome
        decisions.append(
            CandidateDecision(
                connector_instance_id=instance_id,
                resource_namespace=namespace,
                external_id=external_id,
                state=state,
                reason=reason,
                document_id=document_id,
            )
        )

    for instance_id, namespace, external_id in namespaced_refs:
        resource = by_triple.get((instance_id, namespace, external_id))
        if resource is None:
            _emit(
                instance_id,
                namespace,
                external_id,
                (CandidateState.UNAVAILABLE, REASON_REFERENCE_UNRESOLVED, None),
            )
            unavailable = True
            continue
        outcome = await _outcome_for(resource)
        if outcome is None:
            undecided += 1
            continue
        _emit(instance_id, namespace, external_id, outcome)

    for instance_id, external_id in bare_refs:
        matches = by_bare.get((instance_id, external_id), [])
        if not matches:
            _emit(
                instance_id,
                "",
                external_id,
                (CandidateState.UNAVAILABLE, REASON_REFERENCE_UNRESOLVED, None),
            )
            unavailable = True
            continue
        if len(matches) > 1:
            # Fail closed: the provider cannot distinguish which namespace was meant, and
            # choosing one would answer a question the caller did not ask.
            _emit(
                instance_id,
                "",
                external_id,
                (CandidateState.UNAVAILABLE, REASON_REFERENCE_AMBIGUOUS, None),
            )
            unavailable = True
            ambiguous = True
            continue
        resource = matches[0]
        outcome = await _outcome_for(resource)
        if outcome is None:
            undecided += 1
            continue
        _emit(instance_id, resource.resource_namespace, external_id, outcome)

    for document_id in doc_keys:
        matches = by_document.get(document_id, [])
        if not matches:
            _emit(
                "",
                "",
                document_id,
                (CandidateState.UNAVAILABLE, REASON_REFERENCE_UNRESOLVED, None),
            )
            unavailable = True
            continue
        if len(matches) > 1:
            _emit(
                "",
                "",
                document_id,
                (CandidateState.UNAVAILABLE, REASON_REFERENCE_AMBIGUOUS, None),
            )
            unavailable = True
            ambiguous = True
            continue
        resource = matches[0]
        outcome = await _outcome_for(resource)
        if outcome is None:
            undecided += 1
            continue
        decisions.append(
            CandidateDecision(
                connector_instance_id=resource.connector_instance_id,
                resource_namespace=resource.resource_namespace,
                external_id=resource.external_id,
                state=outcome[0],
                reason=outcome[1],
                document_id=outcome[2],
            )
        )

    examined = sum(1 for d in decisions if d.state is not CandidateState.LOCAL_POLICY_EXCLUDED)

    if undecided > 0:
        coverage, reason = CoverageStatus.PARTIAL, REASON_BUDGET
    elif unavailable:
        coverage, reason = CoverageStatus.PARTIAL, REASON_UNAVAILABLE
    else:
        coverage, reason = CoverageStatus.COMPLETE, ""

    # STRICT success is a stronger question than coverage. Coverage asks whether every requested
    # source was *decided*; strict asks whether every requested source was actually AUTHORIZED,
    # and conflating them is how an authoritative output would silently drop a source.
    tally = {s.value: 0 for s in CandidateState}
    for decision in decisions:
        tally[decision.state.value] += 1
    if undecided > 0:
        strict_satisfied, strict_reason = False, REASON_STRICT_BUDGET
    elif ambiguous:
        strict_satisfied, strict_reason = False, REASON_STRICT_AMBIGUOUS
    elif tally[CandidateState.UNAVAILABLE.value]:
        strict_satisfied, strict_reason = False, REASON_STRICT_UNAVAILABLE
    elif tally[CandidateState.DENIED.value]:
        strict_satisfied, strict_reason = False, REASON_STRICT_DENIED
    elif tally[CandidateState.LOCAL_POLICY_EXCLUDED.value]:
        strict_satisfied, strict_reason = False, REASON_STRICT_LOCAL_POLICY
    elif tally[CandidateState.AUTHORIZED.value] == requested:
        strict_satisfied, strict_reason = True, ""
    else:
        strict_satisfied, strict_reason = False, REASON_STRICT_BUDGET

    return CandidateAuthorizationResult(
        decisions=tuple(decisions),
        coverage=coverage,
        coverage_reason=reason,
        cursor=None,
        examined=examined,
        provider_checks=checks,
        rows_fetched=len(rows),
        sql_reads=reads,
        unresolved=unavailable,
        # One read per identity class over a caller-supplied fixed set: there is no enumeration
        # to drift.
        snapshot_consistent=reads <= 3 and undecided == 0,
        scope_kind=ScopeKind.EXACT,
        strict_scope_satisfied=strict_satisfied,
        strict_exclusion_reason=strict_reason,
    )


async def revalidate_document_ids(
    db: AsyncSession,
    *,
    access: DocumentAccess,
    document_ids: Sequence[str],
    budget: int | None = None,
) -> CandidateAuthorizationResult:
    """Consumption-time revalidation for authoritative consumers.

    Pinned reports, report snapshots, exports and exact-source operations must call this
    immediately before emitting anything, passing the document ids they intend to use, and emit
    only what :func:`require_strict_scope` returns. It re-runs local policy and current provider
    authorization at the moment of use, so an authorization granted on an earlier page cannot
    survive a later revocation into an authoritative output.

    This is the smallest mechanism that closes that window: the exact-scope operation applied at
    consumption time, with no persistent allow cache and no change to CA1's non-mutation
    invariant.
    """
    return await authorize_exact_candidates(
        db, access=access, document_ids=list(document_ids), budget=budget
    )


def currently_authorized(result: CandidateAuthorizationResult) -> frozenset[str]:
    """The currently authorized subset. PERMISSIVE, and not an authoritative output.

    Returns nothing unless the result is ``authoritative``, so an incomplete or unresolved
    evaluation cannot silently degrade into a partial answer. It nonetheless returns a *subset*
    when some requested source was denied or restricted, which is fine for an exploratory
    consumer that will surface partial coverage, and wrong for a pinned report, report snapshot,
    export or exact-source output.

    Authoritative consumers must use :func:`require_strict_scope` instead.
    """
    if not result.authoritative:
        return frozenset()
    return result.authorized_document_ids


def require_strict_scope(result: CandidateAuthorizationResult) -> frozenset[str]:
    """The only accessor for authoritative outputs. Raises rather than returning a subset.

    A pinned report, report snapshot, export or exact-source output must call this and emit only
    what it returns. It raises :class:`StrictScopeError` when ANY requested source was denied,
    missing, ambiguous, locally restricted, left undecided by the budget, or unproven, so such an
    output cannot silently exclude a source.

    A catalog traversal also raises, because traversal is not exact-source authorization: it has
    no requested set, so it cannot fail to cover one, and treating it as strict would let a
    best-effort walk stand in for a pinned scope.
    """
    if result.scope_kind is not ScopeKind.EXACT:
        raise StrictScopeError(
            REASON_STRICT_NOT_APPLICABLE,
            "a catalog traversal is not exact-source authorization",
        )
    if not result.strict_scope_satisfied:
        raise StrictScopeError(
            result.strict_exclusion_reason or "strict_scope_unsatisfied",
            "not every requested source is currently authorized",
        )
    return result.authorized_document_ids
