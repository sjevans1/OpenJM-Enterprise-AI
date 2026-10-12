"""CA2-A: candidate-scoped authorization with bounded execution and safe continuation.

Additive. This module does not change the production authorization path and no existing
caller uses it yet; CA2-B migrates the callers after independent review.

SCOPE OF THIS MODULE: authorization traversal is NOT semantic retrieval
-----------------------------------------------------------------------
This module answers exactly one question: **which documents in scope may this principal be
authorized for?** It does not rank, score, embed, rerank or select by relevance, and it is
not a retrieval-quality mechanism.

A "complete" coverage result means every candidate in scope was *considered* for
authorization. It does not mean the right documents were chosen for an answer, and improving
it will not improve answer quality. Under D4 there is no corpus-wide pre-retrieval relevance
score (per-document Chroma collections), so the ordering here is stable identity and exists
for determinism and coverage accounting, not relevance. Retrieval quality is a separate
concern and a separate programme.

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
content) over an explicit candidate list, and it names the outcomes the old code conflated.

DENIED versus UNAVAILABLE: the reconciliation
---------------------------------------------
CA1 classifies a refusal with a :class:`DenialScope`. The mapping onto candidate states is
total and mechanical, and it is pinned by a test rather than by prose, so the two vocabularies
cannot drift apart::

    allowed                     -> AUTHORIZED
    scope == PRINCIPAL          -> DENIED       the provider proved THIS principal lacks access
    scope == REQUEST            -> UNAVAILABLE  could not be proven on this request
    scope == CONNECTOR          -> UNAVAILABLE  the connector cannot be proven for anyone
    scope == CONTENT            -> UNAVAILABLE  the content is not servable

The rule in one line: **DENIED requires the provider to have proven that this principal lacks
access. Every other refusal, including every outage and timeout, is UNAVAILABLE.** That is why
an outage cannot be mistaken for a denial, and why UNAVAILABLE is the state that makes coverage
incomplete.

The security argument for the cursor
------------------------------------
Because current authorization is revalidated on **every** page, the continuation cursor is a
*scan position*, not a *capability*. Forging one gains a caller nothing: every candidate it
leads to is authorized again before any content may be served.

The cursor is nonetheless opaque and authenticated-encrypted (Fernet, AEAD), not a readable
signed blob, because a base64 payload would disclose tenant, principal and external-resource
identifiers to anyone holding it. It is additionally bound to tenant, principal, query and the
complete candidate scope, carries a corpus digest, and carries a sticky "unresolved" flag so
that a walk which has already missed something can never later claim completeness.

There is deliberately no durable authorization cache and no per-principal scan-position record.
Continuation is stateless.
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
from sqlalchemy import func, or_, select
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

CURSOR_VERSION = 2
CURSOR_CONTEXT = b"openjm:ca2:candidate-cursor:v2"

# Defaults are EXPERIMENTAL. They are not product limits and they are below the current
# production bound of 100, so nothing here may be enabled as a default before the Phase 4
# comparison proves it does not reduce coverage. See the plan, section 3.
DEFAULT_PAGE_BUDGET = 32
DEFAULT_MAX_SCAN_ROWS = 512
DEFAULT_CURSOR_TTL_SECONDS = 900

REASON_BUDGET = "budget_exhausted"
REASON_SCAN_BOUND = "scan_bound_reached"
REASON_UNAVAILABLE = "authorization_unavailable"
REASON_CORPUS_CHANGED = "corpus_changed"


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
    unresolved: bool = False

    @property
    def authorized_document_ids(self) -> frozenset[str]:
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
        """True when coverage is partial.

        Authoritative callers (pinned reports, exact-scope operations, immutable snapshots)
        must fail closed on this and must not emit a successful result. Exploratory callers
        may return the authorized subset with a partial-coverage indication. This module does
        not decide that policy; it exposes the fact, and CA2-B surfaces it.
        """
        return self.coverage is CoverageStatus.PARTIAL

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

    Fails closed when no server secret is available rather than inventing one, because a
    guessable or absent key would make the binding checks meaningless.
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
        "*" if connector_instance_ids is None else "\n".join(sorted(str(i) for i in connector_instance_ids))
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
    """Authenticate and unpack a cursor. Raises :class:`CursorError` on anything untrusted.

    Returns the sealed payload, whose ``k`` is the scan position and whose ``u`` and ``d``
    carry the unresolved flag and corpus digest of the walk so far.
    """
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

    DENIED is reserved for a provider-proved lack of access for this principal. Every other
    failure to authorize is UNAVAILABLE, because authorization could not be proven. Unexamined
    is never produced here: it is the state of a candidate the walk has not reached, and it is
    never reported as denied.
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
) -> str:
    """A cheap version marker for the in-scope candidate set.

    Binds continuation to the candidate set observed at issue time. It detects additions and
    removals of in-scope candidates (a count change, or a change at either extreme of the
    stable key order). It deliberately does NOT attempt to hash the corpus: in-place edits such
    as a reclassification are caught by re-evaluating local eligibility on every page, and
    permission changes are caught by revalidating authorization on every page. The limits of
    this digest are stated rather than implied.
    """
    row = (
        await db.execute(
            select(
                func.count(ExternalResource.id),
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
    return hashlib.sha256(material.encode()).hexdigest()[:32]


async def _local_eligible(
    db: AsyncSession, access: DocumentAccess, resource: ExternalResource
) -> bool:
    """Local eligibility, evaluated BEFORE any provider budget is spent.

    Tenant and retrievability are enforced in the query; this adds the governed policy gate
    (classification, department, tenant_visible, groups) on the document row.
    """
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
    """Authorize candidates in stable order, bounded by ``budget`` provider checks.

    Contract properties, each covered by a test:

    * local eligibility is applied before any provider check is consumed;
    * provider checks are deduplicated within the call;
    * ordering is stable identity ``(connector_instance_id, external_id)``, so repeated
      identical requests agree (D4);
    * the scan bound is applied in SQL, so the corpus is never materialised;
    * the cursor advances only past *handled* rows, so no eligible candidate is ever skipped
      and a caller paging with it reaches every authorized candidate, including ones beyond the
      first window whose titles do not match the query;
    * authorization is revalidated on every page: there is no cache;
    * coverage is COMPLETE only when the candidate set was fully walked **and** nothing was
      left unresolved. An outage on this page, an unresolved failure on an earlier page, or a
      change to the candidate set between pages all keep coverage PARTIAL, permanently for
      that walk.
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

    digest = await _corpus_digest(db, access, connector_instance_ids)
    corpus_changed = bool(sealed) and inherited_digest != digest

    statement = _candidate_query(access, connector_instance_ids).limit(max_scan_rows + 1)
    if after:
        statement = statement.where(
            or_(
                ExternalResource.connector_instance_id > after[0],
                (ExternalResource.connector_instance_id == after[0])
                & (ExternalResource.external_id > after[1]),
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
    # A row beyond the scan bound proves only that more work remains.
    at_scan_bound = rows_fetched > max_scan_rows

    for resource in rows[:max_scan_rows]:
        if checks >= budget:
            stop_reason = REASON_BUDGET
            break
        scanned += 1

        key = (resource.connector_instance_id, resource.external_id)
        if key in seen:
            continue  # deduplicate within the request
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

        outcome = await authorize_resource(
            db,
            tenant_id=access.tenant_id,
            connector_instance_id=resource.connector_instance_id,
            resource=resource,
            principal_id=access.principal_id,
        )
        checks += 1
        state, reason = _state_for(outcome)
        if state is CandidateState.UNAVAILABLE:
            page_unavailable = True
        decisions.append(
            CandidateDecision(
                connector_instance_id=resource.connector_instance_id,
                external_id=resource.external_id,
                state=state,
                reason=reason,
                document_id=resource.document_id if state is CandidateState.AUTHORIZED else None,
            )
        )
        handled = [resource.connector_instance_id, resource.external_id]

    if at_scan_bound and not stop_reason:
        stop_reason = REASON_SCAN_BOUND

    exhausted = not stop_reason
    # Any of these means the walk may have missed something, and the flag is sticky so that a
    # later page cannot quietly claim completeness.
    unresolved = inherited_unresolved or page_unavailable or corpus_changed

    if exhausted:
        # Nothing left to walk, but coverage still cannot be complete if anything was
        # unresolved. The specific cause is reported first.
        if page_unavailable or inherited_unresolved:
            coverage, reason = CoverageStatus.PARTIAL, REASON_UNAVAILABLE
        elif corpus_changed:
            coverage, reason = CoverageStatus.PARTIAL, REASON_CORPUS_CHANGED
        else:
            coverage, reason = CoverageStatus.COMPLETE, ""
        next_cursor = None
    else:
        # Mid-walk, so partial for the mechanical cause. The unresolved fact is carried in
        # `unresolved` and sealed into the next cursor.
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

    return CandidateAuthorizationResult(
        decisions=tuple(decisions),
        coverage=coverage,
        coverage_reason=reason,
        cursor=next_cursor,
        examined=scanned,
        provider_checks=checks,
        rows_fetched=rows_fetched,
        unresolved=unresolved,
    )
