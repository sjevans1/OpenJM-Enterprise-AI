"""CA2-A: candidate-scoped authorization with bounded execution and safe continuation.

Additive. This module does not change the production authorization path and no existing
caller uses it yet; CA2-B migrates the callers after independent review.

Why this exists
---------------
``authorized_connector_document_ids`` answers "which documents is this principal
authorized for" by enumerating the corpus, so it needs a bound, and a bound over an
enumerated set silently omits authorized documents. Measured on the frozen baseline
(``main`` at ``8d6b7d8``): 150 resources all authorized produced exactly 100 results,
50 authorized documents unreachable, and **zero denials recorded for the 50**, because
an unexamined candidate is indistinguishable from a denied one.

This module inverts nothing and rebuilds nothing. It composes the existing per-resource
gate (:func:`authorize_resource`, including its CA1 semantics: authorization never
mutates shared content) over an explicit candidate list, and it names the four outcomes
that the old code conflated.

The security argument for the cursor
------------------------------------
Because current authorization is revalidated on **every** page, the continuation cursor
is a *scan position*, not a *capability*. Forging one gains a caller nothing: every
candidate it leads to is authorized again before any content may be served. The cursor
is therefore signed and bound to tenant, principal and query for correctness and to
prevent cross-principal scan-position inference, not because it carries authority.

There is deliberately no durable authorization cache and no per-principal scan-position
record. Continuation is stateless.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Optional

from sqlalchemy import or_, select
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

CURSOR_VERSION = 1
CURSOR_CONTEXT = b"openjm:ca2:candidate-cursor:v1"

# Defaults are EXPERIMENTAL. They are not product limits and they are below the current
# production bound of 100, so nothing here may be enabled as a default before the Phase 4
# comparison proves it does not reduce coverage. See the plan, section 3.
DEFAULT_PAGE_BUDGET = 32
DEFAULT_MAX_SCAN_ROWS = 512
DEFAULT_CURSOR_TTL_SECONDS = 900


class CandidateState(str, Enum):
    """The four outcomes a candidate can have. Conflating any two is the old defect."""

    AUTHORIZED = "authorized"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    UNEXAMINED = "unexamined"
    # Not one of the four authorization states: a row the local policy excludes before
    # any provider budget is spent. It carries no authorization meaning and is recorded
    # so that scan progress is unambiguous.
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

    ``coverage`` and ``coverage_reason`` are the only fields a caller may surface to an
    end user, and only through :meth:`user_facing_summary`. The counts are internal:
    exposing denied or unexamined counts would leak resource existence and volume.
    """

    decisions: tuple[CandidateDecision, ...] = ()
    coverage: CoverageStatus = CoverageStatus.COMPLETE
    coverage_reason: str = ""
    cursor: Optional[str] = None
    examined: int = 0
    provider_checks: int = 0

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

        Authoritative callers (pinned reports, exact-scope operations, immutable
        snapshots) must fail closed on this and must not emit a successful result.
        Exploratory callers may return the authorized subset with a partial-coverage
        indication. This module does not decide that policy; it exposes the fact, and
        CA2-B surfaces it.
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


def _cursor_key() -> bytes:
    """Server-owned signing material, derived with a context string.

    Uses the same server-owned secret source as the credential vault, with a distinct
    context so the two cryptographic uses are separated. Rotating that secret invalidates
    outstanding cursors, which is acceptable precisely because a cursor carries no
    authority: every page revalidates.

    Fails closed when no server secret is available rather than inventing one, because a
    guessable or absent key would make the binding checks meaningless.
    """
    from app.services.credentials import credential_vault

    material = getattr(credential_vault, "_key", None)
    if not material:
        raise CursorError(
            "cursor_key_unavailable",
            "no server credential key is configured, so cursors cannot be signed",
        )
    return hmac.new(bytes(material), CURSOR_CONTEXT, hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _query_fingerprint(query: str) -> str:
    return hashlib.sha256((query or "").strip().lower().encode()).hexdigest()[:32]


def _key_tuple(connector_instance_id: str, external_id: str) -> list[str]:
    return [connector_instance_id, external_id]


def encode_cursor(
    *,
    tenant_id: str,
    principal_id: str,
    query: str,
    after: list[str] | None,
    now: float | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Sign a scan position bound to tenant, principal and query."""
    settings = get_settings()
    ttl = ttl_seconds or getattr(settings, "candidate_cursor_ttl_seconds", None) or (
        DEFAULT_CURSOR_TTL_SECONDS
    )
    issued = now if now is not None else time.time()
    payload = {
        "v": CURSOR_VERSION,
        "t": tenant_id,
        "p": principal_id,
        "q": _query_fingerprint(query),
        "k": list(after) if after else None,
        "iat": int(issued),
        "exp": int(issued + ttl),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(_cursor_key(), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(sig)}"


def decode_cursor(
    cursor: str,
    *,
    tenant_id: str,
    principal_id: str,
    query: str,
    now: float | None = None,
) -> list[str] | None:
    """Verify and unpack a cursor. Raises :class:`CursorError` on anything untrusted."""
    if not cursor or not isinstance(cursor, str):
        raise CursorError("cursor_invalid", "empty cursor")
    parts = cursor.split(".")
    if len(parts) != 2:
        raise CursorError("cursor_invalid", "malformed envelope")
    try:
        raw = _unb64(parts[0])
        given_sig = _unb64(parts[1])
    except Exception as exc:  # noqa: BLE001 - any decode failure is invalid
        raise CursorError("cursor_invalid", "undecodable") from exc

    expected = hmac.new(_cursor_key(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, given_sig):
        raise CursorError("cursor_invalid", "signature mismatch")
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

    key = payload.get("k")
    if key is None:
        return None
    if not isinstance(key, list) or len(key) != 2 or not all(isinstance(x, str) for x in key):
        raise CursorError("cursor_invalid", "malformed position")
    return [str(key[0]), str(key[1])]


# ----------------------------------------------------------------- authorizing a batch


def _state_for(outcome: AuthorizationOutcome) -> tuple[CandidateState, str]:
    """Map a CA1 outcome onto a candidate state.

    DENIED is reserved for a provider-proved lack of access for this principal. Every
    other failure to authorize is UNAVAILABLE, because authorization could not be
    proven. Unexamined is never produced here.
    """
    if outcome.allowed:
        return CandidateState.AUTHORIZED, ""
    if outcome.scope is DenialScope.PRINCIPAL:
        return CandidateState.DENIED, outcome.reason
    return CandidateState.UNAVAILABLE, outcome.reason


async def _local_eligible(db: AsyncSession, access: DocumentAccess, resource: ExternalResource) -> bool:
    """Local eligibility, evaluated BEFORE any provider budget is spent.

    Tenant and retrievability are enforced in the query; this adds the governed policy
    gate (classification, department, tenant_visible, groups) on the document row.
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
    now: float | None = None,
) -> CandidateAuthorizationResult:
    """Authorize candidates in stable order, bounded by ``budget`` provider checks.

    Contract properties, each covered by a test:

    * local eligibility is applied before any provider check is consumed;
    * provider checks are deduplicated within the call;
    * ordering is stable identity ``(connector_instance_id, external_id)``, so repeated
      identical requests agree (D4);
    * the cursor advances only past *handled* rows, so no eligible candidate is ever
      skipped and a caller paging with it reaches every authorized candidate, including
      ones beyond the first window whose titles do not match the query;
    * authorization is revalidated on every page: there is no cache;
    * coverage is COMPLETE only when the candidate set was fully walked.
    """
    if budget <= 0:
        raise ValueError("budget must be positive")
    if max_scan_rows <= 0:
        raise ValueError("max_scan_rows must be positive")

    after = (
        decode_cursor(cursor, tenant_id=access.tenant_id, principal_id=access.principal_id,
                      query=query, now=now)
        if cursor
        else None
    )

    statement = (
        select(ExternalResource)
        .join(ConnectorInstance, ConnectorInstance.id == ExternalResource.connector_instance_id)
        .where(
            ExternalResource.tenant_id == access.tenant_id,
            ExternalResource.lifecycle_state == EXTERNAL_STATE_ACTIVE,
            ExternalResource.document_id.is_not(None),
            ConnectorInstance.tenant_id == access.tenant_id,
            ConnectorInstance.enabled.is_(True),
            ConnectorInstance.status == CONNECTOR_STATUS_ACTIVE,
        )
        .order_by(ExternalResource.connector_instance_id, ExternalResource.external_id)
    )
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
    exhausted = True
    stop_reason = ""

    rows: Iterable[ExternalResource] = (await db.execute(statement)).scalars().all()
    for resource in rows:
        if checks >= budget:
            exhausted = False
            stop_reason = "budget_exhausted"
            break
        if scanned >= max_scan_rows:
            exhausted = False
            stop_reason = "scan_window_reached"
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
            handled = _key_tuple(resource.connector_instance_id, resource.external_id)
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
        decisions.append(
            CandidateDecision(
                connector_instance_id=resource.connector_instance_id,
                external_id=resource.external_id,
                state=state,
                reason=reason,
                document_id=resource.document_id if state is CandidateState.AUTHORIZED else None,
            )
        )
        handled = _key_tuple(resource.connector_instance_id, resource.external_id)

    coverage = CoverageStatus.COMPLETE if exhausted else CoverageStatus.PARTIAL
    next_cursor = (
        None
        if exhausted
        else encode_cursor(
            tenant_id=access.tenant_id,
            principal_id=access.principal_id,
            query=query,
            after=handled,
            now=now,
        )
    )
    return CandidateAuthorizationResult(
        decisions=tuple(decisions),
        coverage=coverage,
        coverage_reason=stop_reason,
        cursor=next_cursor,
        examined=scanned,
        provider_checks=checks,
    )
