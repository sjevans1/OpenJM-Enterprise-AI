"""CA2-A: candidate authorization. Bounded traversal, exact scope, safe revalidation.

Additive. This module does not change the production authorization path and no existing
caller uses it yet; CA2-B migrates the callers after independent review.

Three operations, deliberately distinct
---------------------------------------
1. :func:`authorize_candidates` — bounded, resumable **traversal** of a candidate scope.
   Best-effort and never authoritative on its own.
2. :func:`authorize_exact_candidates` — **exact scope**. A caller-supplied set of resource or
   document identities, resolved by identity and not by enumeration, so unrelated connector
   resources cannot consume the request's budget. This is the operation pinned reports,
   report snapshots, exports and exact-source operations need.
3. :func:`revalidate_document_ids` — **consumption-time revalidation**. Re-authorizes a set of
   document ids at the moment an authoritative consumer is about to use them.

SCOPE OF THIS MODULE: authorization traversal is NOT semantic retrieval
-----------------------------------------------------------------------
This module answers exactly one question: **which documents may this principal be authorized
for?** It does not rank, score, embed, rerank or select by relevance, and it is not a
retrieval-quality mechanism.

A "complete" coverage result means every candidate in scope was *considered* for
authorization. It does not mean the right documents were chosen for an answer, and improving
it will not improve answer quality. Under D4 there is no corpus-wide pre-retrieval relevance
score (per-document Chroma collections), so the ordering here is stable identity and exists
for determinism and coverage accounting, not relevance.

Why this exists
---------------
``authorized_connector_document_ids`` answers "which documents is this principal authorized
for" by enumerating the corpus, so it needs a bound, and a bound over an enumerated set
silently omits authorized documents. Measured on the frozen baseline (``main`` at
``8d6b7d8``): 150 resources all authorized produced exactly 100 results, 50 authorized
documents unreachable, and **zero denials recorded for the 50**, because an unexamined
candidate was indistinguishable from a denied one.

This module rebuilds nothing. It composes the existing per-resource gate
(:func:`authorize_resource`, including its CA1 semantics: authorization never mutates shared
content) over an explicit candidate list.

DENIED versus UNAVAILABLE: the reconciliation
---------------------------------------------
CA1 classifies a refusal with a :class:`DenialScope`. The mapping onto candidate states is
total and mechanical, pinned by a test rather than by prose::

    allowed                     -> AUTHORIZED
    scope == PRINCIPAL          -> DENIED       authorization is definitively refused for
                                                THIS principal
    scope == REQUEST            -> UNAVAILABLE  could not be proven on this request
    scope == CONNECTOR          -> UNAVAILABLE  the connector cannot be proven for anyone
    scope == CONTENT            -> UNAVAILABLE  the content is not servable

DENIED means *definitive, principal-specific refusal*. It does **not** assert that every
denial came directly from the provider: an absent provider-user mapping is refused locally
because the absence is definitive knowledge, not uncertainty, and it is reported as DENIED.
Everything that leaves authorization unproven, including outages, timeouts, rate limits and
contradictory provider responses, is UNAVAILABLE.

Traversal completeness is BEST EFFORT and is never authoritative on its own
----------------------------------------------------------------------------
A resumable traversal can never prove that the candidate set did not change between its
pages. :func:`_corpus_digest` detects many changes, but it cannot prove the absence of one,
and an unchanged digest must never be read as proof of an unchanged corpus.

Two fields keep that honest:

* ``snapshot_consistent`` — True only when the candidate set came from a single database
  read, so no drift between pages is possible. Any resumable traversal reports False.
* ``authoritative`` — True only when ``coverage`` is COMPLETE, ``snapshot_consistent`` is
  True, and ``unresolved`` is False. A resumable traversal therefore reports False, even
  when it walked everything, and an authoritative consumer must not treat it as complete
  authorization.

Authoritative consumers additionally call :func:`revalidate_document_ids` immediately before
use, which closes the window in which an already-authorized candidate can be revoked.

No durable authorization cache and no per-principal scan-position record exists. Continuation
is stateless, and authorization is revalidated on every page.
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
from app.services.document_lifecycle import retrievable_filter
from app.services.document_policy import DocumentAccess, document_is_visible

CURSOR_VERSION = 3
CURSOR_CONTEXT = b"openjm:ca2:candidate-cursor:v3"

# Defaults are EXPERIMENTAL. They are not product limits and they are below the current
# production bound of 100, so nothing here may be enabled as a default before the Phase 4
# comparison proves it does not reduce coverage. See the plan, section 3.
DEFAULT_PAGE_BUDGET = 32
DEFAULT_MAX_SCAN_ROWS = 512
DEFAULT_CURSOR_TTL_SECONDS = 900

# A page fetches at most this multiple of its provider budget, so a small budget does not drag
# a large window out of the database. This can only affect how many pages a traversal takes:
# the cursor advances only past handled rows, so shrinking the window never skips a locally
# ineligible, denied or otherwise unexamined candidate.
SCAN_WINDOW_MULTIPLIER = 2

# Bounds the size of a single identity-resolution read for exact scope.
IDENTITY_CHUNK = 256

REASON_BUDGET = "budget_exhausted"
REASON_SCAN_BOUND = "scan_bound_reached"
REASON_UNAVAILABLE = "authorization_unavailable"
REASON_CORPUS_CHANGED = "corpus_changed"
REASON_REFERENCE_UNRESOLVED = "reference_unresolved"
REASON_REFERENCE_EXCLUDED = "reference_excluded_locally"


class CandidateState(str, Enum):
    """What became of a candidate. Conflating any two of these was the old defect."""

    AUTHORIZED = "authorized"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    UNEXAMINED = "unexamined"
    # Not one of the four authorization states: a row the local policy excludes before any
    # provider budget is spent. It carries no authorization meaning and is recorded so that
    # scan progress is unambiguous.
    LOCAL_POLICY_EXCLUDED = "local_policy_excluded"


class CoverageStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class CursorError(Exception):
    """A continuation cursor that cannot be trusted. Always fail closed."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CandidateRef:
    """An explicitly identified candidate. Never an enumeration."""

    connector_instance_id: str
    external_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("connector_instance_id", self.connector_instance_id),
            ("external_id", self.external_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class CandidateDecision:
    """One candidate's outcome. Only AUTHORIZED carries a document id."""

    connector_instance_id: str
    external_id: str
    state: CandidateState
    reason: str = ""
    document_id: str | None = None

    def __post_init__(self) -> None:
        if self.state is not CandidateState.AUTHORIZED and self.document_id is not None:
            raise ValueError("only an authorized candidate may carry a document id")


@dataclass(frozen=True)
class CandidateAuthorizationResult:
    """The CA2-A contract result.

    ``coverage`` and ``coverage_reason`` are the only fields a caller may surface to an end
    user, and only through :meth:`user_facing_summary`. The counts are internal: exposing
    denied or unexamined counts would leak resource existence and volume.
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

    @property
    def authorized_document_ids(self) -> frozenset[str]:
        """Currently authorized ids from THIS evaluation.

        Point in time. An authoritative consumer must never combine these across pages, or
        reuse them later, without :func:`revalidate_document_ids`.
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
        """Safe to consume as complete, currently-valid authorization.

        Requires COMPLETE coverage, a single-read candidate set, and nothing unresolved. A
        resumable traversal always fails this, which is the point: an authoritative consumer
        must not mistake a best-effort walk for complete authorization.
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


# --------------------------------------------------------------------------- cursor


def _cursor_cipher() -> Fernet:
    """Authenticated encryption for cursors, keyed from the existing server secret.

    Uses the same server-owned secret source as the credential vault, with a distinct context
    so the two cryptographic uses are separated. Rotating that secret invalidates outstanding
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
    """Fingerprint the complete candidate scope the page was walked under."""
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
    """Seal a scan position bound to tenant, principal, query and the candidate scope."""
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
        not isinstance(key, list) or len(key) != 2 or not all(isinstance(x, str) for x in key)
    ):
        raise CursorError("cursor_invalid", "malformed position")
    return payload


# ----------------------------------------------------------------- authorizing a batch


def _state_for(outcome: AuthorizationOutcome) -> tuple[CandidateState, str]:
    """Map a CA1 outcome onto a candidate state. Total, and pinned by a test.

    DENIED means definitive, principal-specific refusal. It includes a locally detected
    missing provider-user mapping, which is refused without a provider call because the
    absence is definitive rather than uncertain. Every other failure to authorize is
    UNAVAILABLE. Unexamined is never produced here: it is the state of a candidate the walk
    has not reached, and it is never reported as denied.
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
    return (
        select(ExternalResource)
        .join(ConnectorInstance, ConnectorInstance.id == ExternalResource.connector_instance_id)
        .where(*_candidate_filters(access, connector_instance_ids))
        .order_by(ExternalResource.connector_instance_id, ExternalResource.external_id)
    )


async def _corpus_digest(
    db: AsyncSession, access: DocumentAccess, connector_instance_ids: Sequence[str] | None
) -> tuple[str, int]:
    """A version marker for the in-scope candidate set. BEST EFFORT, not proof.

    Detects: additions (count and the timestamp watermark), removals (count), any edit to the
    ``ExternalResource`` row itself (the ``updated_at`` watermark), and a balanced middle
    replacement that preserves both count and key extrema (the watermark moves when the
    replacement row is written).

    Does NOT detect, and must not be claimed to detect:

    * a replacement whose new row carries a timestamp at or below the current watermark,
      possible only if timestamps are set backwards, for example a backdated import;
    * a change to a ``Document``'s governed policy fields (``classification``,
      ``tenant_visible``, ``department_id``, ``allowed_group_ids_json``). ``Document`` has no
      ``updated_at`` column at all, only ``created_at``, so no cheap aggregate can see such an
      edit. Those changes are instead handled by re-evaluating local eligibility on every page,
      which excludes the document on the page that examines it, and by consumption-time
      revalidation for anything decided on an earlier page.

    **An unchanged digest is therefore not proof of an unchanged corpus**, and no caller may
    treat it as one. The contract's ``snapshot_consistent`` and ``authoritative`` fields exist
    so that a resumable traversal is never read as authoritative regardless of what the digest
    says.

    No corpus-level generation or revision counter exists on ``ExternalResource`` or
    ``ConnectorInstance`` (checked: the only ``generation`` field in the models is an unrelated
    ``generation_ms``), and ``Document`` carries no modification timestamp, so a timestamp
    watermark over the in-scope resource rows is the smallest reliable mechanism available
    without new infrastructure or a migration. Closing the remaining gap would require an
    invasive change (a migration adding a corpus generation, or per-row modcounts) which the
    CA2-A boundary forbids, so the gap is stated rather than hidden.

    Returns the digest and the number of SQL reads it took.
    """
    row = (
        await db.execute(
            select(
                func.count(ExternalResource.id),
                func.max(ExternalResource.updated_at),
                func.max(ExternalResource.created_at),
                func.min(ExternalResource.external_id),
                func.max(ExternalResource.external_id),
                func.min(ExternalResource.connector_instance_id),
                func.max(ExternalResource.connector_instance_id),
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
    db: AsyncSession,
    access: DocumentAccess,
    resource: ExternalResource,
) -> tuple[CandidateDecision, bool]:
    """Authorize one resolved candidate. Returns the decision and whether it was unavailable."""
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
            external_id=resource.external_id,
            state=state,
            reason=reason,
            document_id=resource.document_id if state is CandidateState.AUTHORIZED else None,
        ),
        state is CandidateState.UNAVAILABLE,
    )


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

    Each page walks candidates in stable identity order, applies local policy before spending
    provider budget, and stops when the budget is spent or the SQL scan window is reached. The
    cursor advances only past *handled* rows, so no eligible candidate is skipped and paging
    reaches every authorized candidate, including ones beyond the first window whose titles do
    not match the query.

    The result is never ``authoritative``: it did not come from a single snapshot, and earlier
    pages cannot be guaranteed still valid. Authoritative consumers use
    :func:`authorize_exact_candidates` or :func:`revalidate_document_ids`.
    """
    if budget <= 0:
        raise ValueError("budget must be positive")
    if max_scan_rows <= 0:
        raise ValueError("max_scan_rows must be positive")

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
    # Only meaningful once a first page has been issued: the digest is compared against the one
    # sealed into the cursor.
    corpus_changed = bool(sealed) and inherited_digest != digest

    # Budget-proportional window, capped by the hard scan bound. One extra row is fetched only
    # so that "there is more" is answerable without counting the corpus.
    window = min(max_scan_rows, max(budget, budget * SCAN_WINDOW_MULTIPLIER))
    statement = _candidate_query(access, connector_instance_ids).limit(window + 1)
    if after:
        statement = statement.where(
            or_(
                ExternalResource.connector_instance_id > after[0],
                and_(
                    ExternalResource.connector_instance_id == after[0],
                    ExternalResource.external_id > after[1],
                ),
            )
        )

    decisions: list[CandidateDecision] = []
    seen: set[tuple[str, str]] = set()
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

        key = (resource.connector_instance_id, resource.external_id)
        if key in seen:
            continue
        seen.add(key)

        if not await _local_eligible(db, access, resource):
            decisions.append(
                CandidateDecision(
                    connector_instance_id=resource.connector_instance_id,
                    external_id=resource.external_id,
                    state=CandidateState.LOCAL_POLICY_EXCLUDED,
                    reason="local_policy_excluded",
                )
            )
            handled = [resource.connector_instance_id, resource.external_id]
            continue

        decision, unavailable = await _decide(db, access, resource)
        checks += 1
        page_unavailable = page_unavailable or unavailable
        decisions.append(decision)
        handled = [resource.connector_instance_id, resource.external_id]

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
    )


async def authorize_exact_candidates(
    db: AsyncSession,
    *,
    access: DocumentAccess,
    resource_refs: Sequence[CandidateRef] | None = None,
    document_ids: Sequence[str] | None = None,
    budget: int | None = None,
) -> CandidateAuthorizationResult:
    """Authorize an explicitly identified set of candidates. No enumeration.

    For pinned reports, report snapshots, exports and exact-source operations. Rows are
    resolved by identity, so resources outside the requested set never consume the request's
    authorization budget.

    Malformed references raise ``ValueError``. A reference that does not resolve inside this
    tenant, or that is not currently an eligible resource, is reported UNAVAILABLE without
    spending provider budget, and the same answer is given whether the identity is absent or
    belongs to another tenant, so nothing is disclosed.

    Coverage is COMPLETE only if every requested identity received a decision. If the budget
    cannot cover the request, the result is PARTIAL with no cursor: exact-scope authorization
    does not paginate, and an authoritative consumer must treat an incomplete exact result as a
    failure rather than a partial answer.
    """
    if resource_refs is None and document_ids is None:
        raise ValueError("provide resource_refs or document_ids")
    if budget is not None and budget <= 0:
        raise ValueError("budget must be positive")

    refs: list[CandidateRef] = list(resource_refs or [])
    ids: list[str] = []
    for value in document_ids or []:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("document_ids must be non-empty strings")
        ids.append(value)

    # Deduplicate so a repeated identity cannot consume budget twice.
    ref_keys = sorted({(r.connector_instance_id, r.external_id) for r in refs})
    doc_keys = sorted(set(ids))
    requested = len(ref_keys) + len(doc_keys)
    if requested == 0:
        return CandidateAuthorizationResult(
            coverage=CoverageStatus.COMPLETE, snapshot_consistent=True
        )

    rows: list[ExternalResource] = []
    reads = 0
    for start in range(0, len(ref_keys), IDENTITY_CHUNK):
        chunk = ref_keys[start : start + IDENTITY_CHUNK]
        statement = (
            select(ExternalResource)
            .join(
                ConnectorInstance,
                ConnectorInstance.id == ExternalResource.connector_instance_id,
            )
            .where(
                ExternalResource.tenant_id == access.tenant_id,
                tuple_(
                    ExternalResource.connector_instance_id, ExternalResource.external_id
                ).in_(chunk),
            )
        )
        rows.extend((await db.execute(statement)).scalars().all())
        reads += 1
    for start in range(0, len(doc_keys), IDENTITY_CHUNK):
        chunk = doc_keys[start : start + IDENTITY_CHUNK]
        statement = (
            select(ExternalResource)
            .join(
                ConnectorInstance,
                ConnectorInstance.id == ExternalResource.connector_instance_id,
            )
            .where(
                ExternalResource.tenant_id == access.tenant_id,
                ExternalResource.document_id.in_(chunk),
            )
        )
        rows.extend((await db.execute(statement)).scalars().all())
        reads += 1

    # Resolve only within the tenant and only to rows the caller asked for.
    resolved: dict[tuple[str, str], ExternalResource] = {}
    for row in rows:
        key = (row.connector_instance_id, row.external_id)
        if key in ref_keys or (row.document_id and row.document_id in set(doc_keys)):
            resolved.setdefault(key, row)

    effective_budget = budget if budget is not None else max(requested, 1)
    decisions: list[CandidateDecision] = []
    checks = 0
    unavailable = False
    unresolved_refs = 0

    # Requested identities in stable order, so an exact result is deterministic.
    for instance_id, external_id in ref_keys:
        resource = resolved.get((instance_id, external_id))
        if resource is None:
            decisions.append(
                CandidateDecision(
                    connector_instance_id=instance_id,
                    external_id=external_id,
                    state=CandidateState.UNAVAILABLE,
                    reason=REASON_REFERENCE_UNRESOLVED,
                )
            )
            unavailable = True
            unresolved_refs += 1
            continue
        if checks >= effective_budget:
            continue
        if not await _local_eligible(db, access, resource):
            decisions.append(
                CandidateDecision(
                    connector_instance_id=instance_id,
                    external_id=external_id,
                    state=CandidateState.LOCAL_POLICY_EXCLUDED,
                    reason=REASON_REFERENCE_EXCLUDED,
                )
            )
            continue
        decision, was_unavailable = await _decide(db, access, resource)
        checks += 1
        unavailable = unavailable or was_unavailable
        decisions.append(decision)

    for document_id in doc_keys:
        resource = next(
            (r for r in resolved.values() if r.document_id == document_id), None
        )
        if resource is None:
            decisions.append(
                CandidateDecision(
                    connector_instance_id="",
                    external_id=document_id,
                    state=CandidateState.UNAVAILABLE,
                    reason=REASON_REFERENCE_UNRESOLVED,
                )
            )
            unavailable = True
            unresolved_refs += 1
            continue
        if checks >= effective_budget:
            continue
        if not await _local_eligible(db, access, resource):
            decisions.append(
                CandidateDecision(
                    connector_instance_id=resource.connector_instance_id,
                    external_id=resource.external_id,
                    state=CandidateState.LOCAL_POLICY_EXCLUDED,
                    reason=REASON_REFERENCE_EXCLUDED,
                )
            )
            continue
        decision, was_unavailable = await _decide(db, access, resource)
        checks += 1
        unavailable = unavailable or was_unavailable
        decisions.append(decision)

    # Every requested identity must have received a decision for this to be complete. A
    # budget that could not cover the request leaves identities undecided, which is incomplete
    # rather than a partial answer.
    decided = len(decisions)
    undecided = requested - decided
    examined = sum(
        1 for d in decisions if d.state is not CandidateState.LOCAL_POLICY_EXCLUDED
    )

    if undecided > 0:
        coverage, reason = CoverageStatus.PARTIAL, REASON_BUDGET
    elif unavailable:
        coverage, reason = CoverageStatus.PARTIAL, REASON_UNAVAILABLE
    else:
        coverage, reason = CoverageStatus.COMPLETE, ""

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
        # One read per identity class, over a caller-supplied fixed set: there is no
        # enumeration to drift.
        snapshot_consistent=reads <= 2 and undecided == 0,
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
    immediately before emitting anything, passing the document ids they intend to use, and
    emit only what comes back AUTHORIZED. It re-runs local policy and current provider
    authorization at the moment of use, so an authorization granted on an earlier page cannot
    survive a later revocation into an authoritative output.

    This is the smallest mechanism that closes that window: it is the exact-scope operation
    applied at consumption time, with no persistent allow cache and no change to CA1's
    non-mutation invariant.
    """
    return await authorize_exact_candidates(
        db, access=access, document_ids=list(document_ids), budget=budget
    )


def currently_authorized(result: CandidateAuthorizationResult) -> frozenset[str]:
    """The ids safe to use from a revalidation result.

    Returns nothing unless the result is authoritative: an incomplete or unresolved
    revalidation must not silently degrade into a partial answer.
    """
    if not result.authoritative:
        return frozenset()
    return result.authorized_document_ids
